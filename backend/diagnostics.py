"""Support diagnostics: the redacted bundle a person may attach to a support request (PRIVACY.md, docs/support.md).

Two rules make it safe to send. It is built from an allowlist: every fact below is written out by name, so a field nobody
listed cannot appear, and no task, message, doc, meeting or ticket text is ever read. And every string in it passes the
`Redactor` on the way out: emails, keys and tokens, addresses, URL query strings, the company's domain and other
hostnames become placeholders, and each bot and person becomes a label (`bot-3`, `person-1`) that is the same all through
one bundle and means nothing outside it.

The preview is the bundle. `GET /api/v2/support/diagnostics` builds one and remembers it for a few minutes under its
SHA-256; the request that files the ticket names that digest and the server sends the remembered bytes, so what a person
looked at is what leaves, not a second build that could differ.
"""
import collections
import hashlib
import json
import logging
import math
import os
import platform
import re
import threading
import time

import httpx

from . import releases
# The text rules live in redaction.py, standard library only, so the setup wizard applies the same ones.
from .redaction import (ACTOR, AUTHORITY, CONTROL, EMAIL, HOST, IPV4, IPV6, KNOWN_HOSTS, LONG, QUERY, SECRET_FIELD,  # noqa: F401
                        SECRETS, TLDS, UUID, WORD_MIN, Redactor, _ipv6, _looks_secret, known_host)
from .store import H

FORMAT = 1
MAX_BYTES = 256 * 1024            # HQ refuses more (docs/telemetry.md, Support tickets)
LOG_LINES = 200                   # server WARN and ERROR lines
RUNNER_LINES = 50
APP_LINES = 200                   # the end of the desktop app's log, when the page runs in the app
LINE = 300                        # characters of one log line
KEEP_S = 15 * 60                  # a preview may be sent for this long
KEEP_N = 20
UPDATER_WAIT = 3


class LogRing(logging.Handler):
    """Bounded failure evidence. Adjacent repeats share a slot; exception values and locals never enter it."""

    def __init__(self, size=400):
        super().__init__(logging.WARNING)
        self.lines = collections.deque(maxlen=size)
        self.started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.total = self.dropped = self.repeated = 0
        self.previous = None
        self.count = 0

    def emit(self, record):
        try:
            message = "%s %s: %s" % (record.levelname, record.name, record.getMessage())
            if record.exc_info and record.exc_info[0]:
                message += " (" + record.exc_info[0].__name__ + ")"
                frames, tb = [], record.exc_info[2]
                while tb:
                    module = tb.tb_frame.f_globals.get("__name__", "")
                    if re.fullmatch(r"(?:backend|runner|clients)\.[A-Za-z0-9_.]+", module):
                        frames.append("%s:%s" % (module, tb.tb_lineno))
                    tb = tb.tb_next
                message += " " + " > ".join(frames[-3:])
            message = message.replace("\n", " ")[:LINE - 65]
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created))
            self.total += 1
            if self.lines and self.previous == message:
                self.count += 1
                self.repeated += 1
                self.lines[-1] = "%s %s [x%d; latest]" % (stamp, message, self.count)
            else:
                if len(self.lines) == self.lines.maxlen:
                    self.dropped += 1
                self.previous, self.count = message, 1
                self.lines.append(stamp + " " + message)
        except Exception:
            pass  # diagnostics must never interrupt work

    def snapshot(self):
        with self.lock:
            return list(self.lines), {"server_started": self.started, "server_events": self.total,
                                     "server_repeats": self.repeated, "server_evicted": self.dropped,
                                     "retention": "Memory only; resets on server restart"}


RING = LogRing()


def watch_logs():
    """Attach the ring to the root logger once per process."""
    root = logging.getLogger()
    if RING not in root.handlers:
        root.addHandler(RING)


def request_failure(request, status, exc=None):
    """Only server failures, using a registered route template; no URLs, request data or exception messages."""
    route = getattr(request.scope.get("route"), "path", "unmatched")
    method = request.method if request.method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS") else "OTHER"
    # Capture in the bounded ring only: a failing poll must not flood the ordinary process log.
    record = logging.LogRecord("tico.request", logging.ERROR, "", 0, "%s %s HTTP %d%s",
        (method, route, status, " " + type(exc).__name__ if exc else ""),
        (type(exc), exc, exc.__traceback__) if exc else None)
    RING.handle(record)


# ---------------------------------------------------------------------- the allowlist
# Every path a bundle may hold. A key that is not here is dropped by `allowed()`, whatever a builder put there; the
# tests read this table to prove no content field can appear.
ALLOWED = {
    "format": None, "created_at": None,
    "capture": {"server_started": None, "server_events": None, "server_repeats": None,
                "server_evicted": None, "retention": None, "truncated": None, "edited": None},
    "versions": {"tico": None, "server": None, "runners": None, "updater": None, "app": None},
    "system": {"os": None, "arch": None, "docker": None, "compose": None, "in_docker": None},
    "containers": [{"name": None, "state": None, "health": None, "restarts": None}],
    "update": {"state": None, "from": None, "to": None, "message": None, "restored": None},
    "health": [{"name": None, "status": None}],
    "runners": [{"label": None, "online": None, "last_seen_at": None, "platform": None, "kind": None, "release": None, "update": None,
                 "update_error": None, "runtimes": [{"name": None, "installed": None, "version": None, "ready": None,
                                                     "state": None, "detail": None}],
                 "bots": None, "bots_ready": None, "problems": None, "log": None}],
    "database": {"migration": None, "cloud_migration": None},
    "features": {},
    # The Beta features this install has on or in use (backend/beta.py), by id.
    "beta": [None],
    "counts": {"bots": None, "people": None, "routines": None},
    "logs": {"server": None, "updater": None, "app": None},
    "browser": [{"kind": None, "at": None, "file": None, "line": None, "column": None, "count": None}],
    # The flight recorder's file, last 24 hours (backend/flight_file.py `bundle`): route templates, timings, sizes,
    # stall classes and code locations (file:line function); no paths with ids, no values.
    "flight": {"since": None, "drops": None, "stalls_total": None,
               "routes": [{"route": None, "n": None, "errors": None, "total_ms": None, "p95": None, "max_ms": None,
                           "sql_ms": None, "wait_ms": None, "avg_bytes": None, "bytes_max": None}],
               "slow": [{"ts": None, "route": None, "ms": None, "status": None, "sql_ms": None, "wait_ms": None}],
               "stalls": [{"ts": None, "seconds": None, "class": None, "usage": None, "threads": None}],
               "hours": [{"ts": None, "cpu_max": None, "rss_max": None, "lag_max": None, "wait_max": None,
                          "locked": None, "requests": None}]},
}
# A computer's performance window, when the person attaching diagnostics keeps it (backend/performance.py): numbers,
# process names, bot labels and models; `points` rows follow performance.COLUMNS.
ALLOWED["performance"] = {"hours": None, "computers": [{
    "label": None, "scope": None, "mem_total": None, "disk_total": None, "span": None, "points": None, "summary": None,
    "overloads": [{"started_at": None, "ended": None, "minutes": None, "reasons": None, "cpu_max": None, "mem_max": None,
                   "runs_max": None, "bots": [{"bot": None, "model": None, "runs": None}],
                   "procs": [{"name": None, "cpu": None, "mem": None}]}],
    "bots": [{"bot": None, "model": None, "runs": None}],
    "processes": [{"name": None, "cpu_minutes": None, "cpu_max": None, "mem_max": None}]}]}
# What a person reports from a screen (Report this) or from Help's Setup check (backend/support.py Report): the kind
# of problem, what the screen said and the check rows. Every string goes through the redactor like the rest.
ALLOWED["report"] = {"kind": None, "title": None, "detail": None, "ref": None, "group": None,
                     "checks": [{"name": None, "status": None, "detail": None, "fix": None, "source": None}]}
FEATURES = ("demo", "updater", "update_check", "usage_count", "backups", "github_app", "slack", "sign_in_proxy",
            "blob_storage", "scheduler", "observability", "assistant", "librarian")


def allowed(value, shape=ALLOWED):
    """`value` cut down to the keys `shape` names."""
    if isinstance(shape, list):
        return [allowed(item, shape[0]) for item in value] if isinstance(value, list) else []
    if isinstance(shape, dict):
        if not isinstance(value, dict):
            return {}
        if shape == {} and value is not None:            # a map of booleans
            return {str(k): bool(v) for k, v in value.items() if k in FEATURES}
        return {k: allowed(v, shape[k]) for k, v in value.items() if k in shape}
    return value


def fit(bundle):
    """Bound the preview and wire representation; explicitly report missing evidence."""
    def size():
        return len(canonical(bundle).encode())
    capture = bundle.setdefault("capture", {})
    capture.setdefault("truncated", 0)
    while size() > MAX_BYTES:
        lists = [bundle.get("logs", {}).get(k, []) for k in ("server", "updater", "app")]
        lists += [r.get("log", []) for r in bundle.get("runners", [])]
        longest = max(lists, key=len, default=[])
        if longest:
            n = max(1, len(longest) // 2)
            del longest[:n]
            capture["truncated"] += n
            continue
        # Then every other performance point, densest computer first.
        computers = (bundle.get("performance") or {}).get("computers") or []
        densest = max(computers, key=lambda x: len(x.get("points") or []), default=None)
        if densest and len(densest.get("points") or []) > 24:
            n = len(densest["points"]) // 2
            densest["points"] = densest["points"][::2]
            capture["truncated"] += n
            continue
        # Extremely large fleets must still fit; missing rows are never silently hidden.
        rows = bundle.get("runners") or bundle.get("containers") or []
        if rows:
            rows.pop()
            capture["truncated"] += 1
            continue
        raise ValueError("Diagnostics exceed the attachment limit")
    return bundle


def validate_edit(value, original):
    """Edits may remove fields/items or change scalar values, never introduce new data shapes."""
    if isinstance(original, dict):
        if not isinstance(value, dict) or set(value) - set(original):
            raise ValueError("Keep the existing field names, or remove fields you do not want to send.")
        for key, item in value.items():
            validate_edit(item, original[key])
    elif isinstance(original, list):
        if not isinstance(value, list) or len(value) > len(original):
            raise ValueError("Lists may be edited or shortened, not extended.")
        for item in value:
            # Rows can have different optional fields or log lengths. Match an existing shape,
            # rather than using the last runner's empty log as the template for every runner.
            for template in original:
                try:
                    validate_edit(item, template)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError("Keep existing list item types and fields, or remove the item.")
    elif type(value) is not type(original):
        raise ValueError("Keep the original value types, or remove the field.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Use finite numbers.")
    if isinstance(value, str) and len(value) > MAX_BYTES:
        raise ValueError("Text is too long.")


def canonical(bundle):
    return json.dumps(bundle, sort_keys=True, indent=2, ensure_ascii=False)


def digest(bundle):
    return hashlib.sha256(canonical(bundle).encode()).hexdigest()


# ---------------------------------------------------------------------- the facts
def _domains(settings, roster):
    found = set()
    for value in (settings.owner_email, settings.local_owner_email):
        if "@" in str(value or ""):
            found.add(str(value).rsplit("@", 1)[1])
    host = re.sub(r"^[a-z]+://", "", str(settings.public_url or "")).split("/")[0].split(":")[0]
    if host and not re.fullmatch(r"[\d.]+|localhost", host):
        found.add(host)
    found.update(str(d) for d in (settings.oidc_allowed_domains or ()))
    for person in roster.get("people") or []:
        email = str((person or {}).get("email") or "")
        if "@" in email:
            found.add(email.rsplit("@", 1)[1])
    # Webmail is not the company's own domain; only its addresses are people, and those are labelled already.
    return {d for d in found if d.lower() not in ("gmail.com", "outlook.com", "icloud.com", "yahoo.com", "hotmail.com")}


def _updater_report(settings):
    url, token = releases._updater()
    if not url:
        return {}
    try:
        with httpx.Client(timeout=UPDATER_WAIT, transport=releases.TRANSPORT, follow_redirects=False) as http:
            answer = http.get(url + "/diagnostics", headers={"Authorization": "Bearer " + token})
        return answer.json() if answer.status_code == 200 and isinstance(answer.json(), dict) else {}
    except (httpx.HTTPError, ValueError):
        return {}


def _update_result():
    try:
        return releases.status()
    except Exception:
        return {}


def _version(value):
    value = str(value or "")
    return value if re.fullmatch(r"[0-9A-Za-z._+-]{1,40}", value) else ""


def _lines(lines, keep):
    return [str(x)[:LINE] for x in list(lines or [])[-keep:]]


def bot_label(c, slug):
    """The label a bundle built now gives the bot `slug` (`bot-N`, as `Redactor` numbers the bots), or None."""
    slugs = sorted(b["slug"] for b in H.bots(c))
    return "bot-%d" % (slugs.index(slug) + 1) if slug in slugs else None


RUNNERS_SQL = "SELECT id,label,last_seen_at,version,platform,readiness FROM runners WHERE revoked_at IS NULL ORDER BY id"


def runner_label(c, runner_id):
    """The label a bundle built now gives the computer `runner_id` (`runner-N`, as `_runner_rows`), or None."""
    ids = [r["id"] for r in c.execute(RUNNERS_SQL)]
    return "runner-%d" % (ids.index(runner_id) + 1) if runner_id in ids else None


def _runner_rows(c, fleet, online_ids):
    from .store import readiness_document
    from . import runner_versions
    rows = []
    for index, row in enumerate(c.execute(RUNNERS_SQL), 1):
        doc = readiness_document(row["readiness"])
        bots = doc.get("bots") or {}
        problems = []
        for slug, b in bots.items():
            for text in (b or {}).get("problems") or []:
                if str(text).startswith(str(slug) + ": "):        # the runner leads a bot's problem with its slug: an exact reference
                    text = "bot:" + str(text)
                if text not in problems:
                    problems.append(text)
        update = runner_versions.view(fleet.get(row["id"]))
        runtimes = []
        for name, v in sorted((doc.get("runtimes") or {}).items()):
            if not (v or {}).get("installed"):
                continue
            runtimes.append({"name": name, "installed": True, "version": _version(v.get("version")) or str(v.get("version") or "")[:60],
                             "ready": v.get("authenticated") == "ready", "state": str(v.get("authenticated") or ""),
                             "detail": str(v.get("detail") or "")[:200]})
        rows.append({"label": "runner-%d" % index, "online": row["id"] in online_ids, "last_seen_at": row["last_seen_at"], "platform": str(row["platform"] or ""),
                     "kind": update["kind"], "release": update["release"] or _version(row["version"]),
                     "update": update["state"], "update_error": update["error"][:200], "runtimes": runtimes,
                     "bots": len(bots), "bots_ready": sum(1 for b in bots.values() if (b or {}).get("ready")),
                     "problems": problems[:20], "log": _lines(doc.get("recent_errors"), RUNNER_LINES)})
    return rows


def _performance(c, hours):
    """Each computer's performance window, labelled like `_runner_rows` (`runner-N`) and with bots as `bot:<slug>`,
    which the redactor turns into `bot-N`. Ten-minute points for a day or two, hourly ones beyond."""
    from . import performance
    out = []
    for index, row in enumerate(c.execute("SELECT id FROM runners WHERE revoked_at IS NULL ORDER BY id"), 1):
        seen = performance.view(c, row["id"], hours, label=lambda b: "bot:" + b if b else "")
        if not seen["points"]:
            continue
        span = 600 if hours <= 48 else 3600
        points, last = [], None
        for p in seen["points"]:             # the bucket's first point stands for it: a support window is a sketch
            if last is None or p[0] // span != last:
                points.append(p)
                last = p[0] // span
        out.append({"label": "runner-%d" % index, "scope": seen["scope"], "mem_total": seen["mem_total"],
                    "disk_total": seen["disk_total"], "span": span, "points": points, "summary": seen["summary"],
                    "overloads": seen["overloads"][-20:], "bots": seen["busiest"]["bots"][:10],
                    "processes": seen["busiest"]["processes"][:10]})
    return {"hours": hours, "computers": out}


def build(store, settings, auth, who, census, github=None, config=None, performance_hours=0, shell=None, report=None):
    """The redacted bundle for this install, as a dictionary (`fit`-ted, allowlisted, every string redacted). A
    person attaching diagnostics chooses `performance_hours` (0 leaves performance out). `shell` is the desktop app the
    page runs in ({version, lines, mac}: support.AppShell), whose version and log go in labelled `app`. `report` is what
    the person reports (support.Report), redacted with the rest."""
    from . import beta, health, onboarding, replication, runner_versions
    from .getting_started import _online_runners
    from .views import roster
    updater = _updater_report(settings)
    result = _update_result()
    with store.read() as c:
        people = roster(c)
        bot_rows = [(b["slug"], [b.get("display_name")]) for b in H.bots(c)]
        humans = [((p or {}).get("id") or "", [(p or {}).get("name"), (p or {}).get("email")]) for p in people.get("people") or []]
        redactor = Redactor(_domains(settings, people), bot_rows, humans)
        config = config or onboarding.config_view(c, settings, who)
        checks = health.view(c, who, settings, auth, github, config)["checks"]
        online_ids = {r["id"] for r in _online_runners(c)}
        runners = _runner_rows(c, runner_versions.load(c), online_ids)
        bots_total = sum(1 for b in H.bots(c) if b.get("state") != "archived")
        routines = c.execute("SELECT count(*) FROM schedules").fetchone()[0]
        migration = c.execute("PRAGMA user_version").fetchone()[0]
        cloud = c.execute("SELECT coalesce(max(version),0) FROM cloud_migrations").fetchone()[0]
        backup = replication.status()
        slack = bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='slack_credentials'").fetchone()
                     and c.execute("SELECT 1 FROM slack_credentials WHERE id='app'").fetchone())
        assistant = H.bot(c, settings.assistant_bot)
        librarian = H.bot(c, "librarian")
        github_on = bool(github and github.row(c) and github.row(c)["installation_id"])
        perf = _performance(c, performance_hours) if performance_hours else None
        beta_on = beta.turned_on(c, settings, shell)
    features = {"demo": bool(settings.demo), "updater": bool(releases._updater()[0]),
                "update_check": releases.Checker.enabled(), "usage_count": census.enabled(),
                "backups": backup.get("mode") not in (None, "off"), "github_app": github_on, "slack": slack,
                "sign_in_proxy": bool(settings.proxy_kind), "blob_storage": bool(settings.blob_bucket),
                "scheduler": bool(settings.scheduler_enabled),
                "observability": bool(settings.sentry_dsn or settings.posthog_key),
                "assistant": bool(assistant and assistant.get("state") == "active"),
                "librarian": bool(librarian and librarian.get("state") == "active")}
    from . import flight_file
    try:
        folder = flight_file.directory(settings.db_path)
        flight = flight_file.bundle(folder) if folder.is_dir() else {}
    except Exception:
        flight = {}
    server_lines, capture = RING.snapshot()
    capture["truncated"] = max(0, len(server_lines) - LOG_LINES)
    bundle = {
        "capture": capture,
        "format": FORMAT, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "versions": {"tico": _version(releases.version()), "server": _version(releases.version()),
                     "runners": sorted({r["release"] for r in runners if r["release"]}),
                     "updater": _version(updater.get("version")),
                     **({"app": _version(shell.get("version"))} if shell else {})},
        "system": {"os": platform.system(), "arch": platform.machine(), "docker": _version(updater.get("docker")),
                   "compose": _version(updater.get("compose")), "in_docker": onboarding.running_in_docker()},
        "containers": [{"name": str(x.get("name") or ""), "state": str(x.get("state") or ""),
                        "health": str(x.get("health") or ""), "restarts": int(x.get("restarts") or 0)}
                       for x in (updater.get("containers") or [])[:50] if isinstance(x, dict)],
        "update": {"state": str(result.get("state") or ""), "from": _version(result.get("from")),
                   "to": _version(result.get("to")), "message": str(result.get("message") or "")[:300],
                   "restored": bool(result.get("restored"))},
        "health": [{"name": x["label"], "status": x["status"]} for x in checks],
        "runners": runners,
        "database": {"migration": migration, "cloud_migration": cloud},
        "features": features,
        "beta": [x for x in beta_on if x in beta.IDS],
        "counts": {"bots": bots_total, "people": len(people.get("people") or []), "routines": routines},
        "logs": {"server": _lines(server_lines, LOG_LINES), "updater": _lines(updater.get("errors"), RUNNER_LINES),
                 **({"app": _lines(shell.get("lines"), APP_LINES)} if shell else {})},
        "flight": flight,
        **({"performance": perf} if perf else {}),
        **({"report": report} if report else {}),
    }
    return fit(redactor.clean(allowed(bundle)))


class Previews:
    """The bundles people have looked at, by digest, for a few minutes: what is sent is what was shown."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.lock, self.items = clock, threading.Lock(), collections.OrderedDict()

    def keep(self, actor, bundle):
        key = digest(bundle)
        with self.lock:
            self.items[(actor, key)] = (self.clock(), bundle)
            while len(self.items) > KEEP_N:
                self.items.popitem(last=False)
        return key

    def take(self, actor, key):
        with self.lock:
            found = self.items.get((actor, str(key or "")))
        if found and self.clock() - found[0] <= KEEP_S:
            return found[1]
        return None
