"""Which version is running, whether a newer public release exists, and the owner's "Update now".

The version is `TICO_VERSION` (the Docker image sets it), else a `VERSION` file at the
repository root, else "dev". The newest release comes from
GitHub's public API, at most once per `TTL`, and never on a request's path: a read returns what is
cached and, when that is stale, starts one background refresh. Any failure keeps the last answer.

Applying an update is the job of a separate updater service (`TICO_UPDATER_URL`); this process
never restarts itself. Without one the owner is told the command to run by hand. With `TICO_UPDATES=tico` Tico runs
every update from outside: the notice says `managed`, the app offers no Update now, and an update asked for here is
refused with `managed_update`. A cloud company on Tico's shared host is not that: it has an updater of its own
(setup/cell_updater.py, docs/cell.md), which this module calls as it calls the sidecar.

The check asks Tico HQ first (`GET <TICO_HQ_URL>/v1/latest`), which is also how an install is counted anonymously
(backend/census.py, PRIVACY.md). With counting off, with `TICO_RELEASES_URL` set (a mirror), or when HQ does not
answer, it asks GitHub directly and sends no id.

HQ's answer also carries short messages for given versions and the known issues by version (docs/tico-hq.md). The
install keeps them with the release and shows its own version's: a message once in the app, and the known issues in
Help. Nothing more is sent for them; an answer from GitHub has none, and then Help says it could not check.
"""
import logging
import json
import os
import re
import threading
import time
from pathlib import Path

import httpx

from .census import hq_url
from .replication import rehearsal_on
from .store import Problem

log = logging.getLogger("tico.releases")
ROOT = Path(__file__).resolve().parents[1]
LATEST_URL = "https://api.github.com/repos/ticoteam/tico/releases/latest"
TTL = 6 * 3600
CHECK_WAIT = 10          # the owner's "Check for updates" waits this long for the fresh answer
FORCE_GAP = 60           # the owner's "Check for updates" may reach GitHub at most this often
RETRY = 15 * 60          # after a failed check; a rate-limited or offline box should not hammer GitHub
MANUAL_COMMAND = "docker compose pull && docker compose up -d"
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
HQ_WAIT = 3              # HQ is a convenience: when it is slow the check goes to GitHub instead
MAX_LISTED = 50
VERSIONS = re.compile(r"^[0-9]{1,4}\.[0-9]{1,4}(?:\.[0-9]{1,4})?$")
# Replaced in tests with an httpx.MockTransport so nothing reaches the network.
TRANSPORT = None


def version():
    value = os.environ.get("TICO_VERSION", "").strip()
    if not value:
        try:
            value = (ROOT / "VERSION").read_text().strip()
        except OSError:
            value = ""
    return re.sub(r"^v(?=\d)", "", value) or "dev"  # the image is built from a v1.2.3 tag


HISTORY = "release-history"


def record_start(c, now):
    """The releases this server actually ran, oldest first, written at startup when the version changes. An update
    done outside the in-app updater (a manual pull, a rollout script) leaves the updater's `from` stale; this does not."""
    current = version()
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (HISTORY,)).fetchone()
    seen = json.loads(row[0]) if row else []
    if seen and seen[-1].get("version") == current:
        return seen
    seen = (seen + [{"version": current, "since": now}])[-20:]
    c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (HISTORY, json.dumps(seen)))
    return seen


def history(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (HISTORY,)).fetchone()
    return json.loads(row[0]) if row else []


def parse(value):
    """(major, minor, patch, prerelease-key) or None when `value` is not a semantic version."""
    match = SEMVER.match(str(value or "").strip())
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    # A release outranks its own prereleases; prerelease identifiers compare part by part.
    key = (1,) if not pre else (0, *((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")))
    return int(major), int(minor), int(patch), key


def newer(latest, current):
    a, b = parse(latest), parse(current)
    return bool(a and b and a > b)


def covers(versions, current):
    """Whether HQ's message for `versions` ("0.2", or one release "0.3.50") is for `current`."""
    have = parse(current)
    want = [int(p) for p in versions.split(".")]
    return bool(have) and list(have[:len(want)]) == want


def affected(issue, current):
    """Whether a known issue is in `current`: from its `since` release until the one that fixed it."""
    have, since, fixed = parse(current), parse(issue["since"]), parse(issue["fixed_in"])
    return bool(have and since) and since[:3] <= have[:3] and not (fixed and have[:3] >= fixed[:3])


def _https(value):
    value = str(value or "")
    return value if re.fullmatch(r"https://[^\s<>\"']{1,490}", value) else ""


def extras(body):
    """HQ's messages and known issues from its answer, checked field by field; anything malformed is left out."""
    notices, issues = [], []
    for n in (body.get("notices") or [])[:MAX_LISTED] if isinstance(body.get("notices"), list) else []:
        if isinstance(n, dict) and isinstance(n.get("id"), int) and VERSIONS.match(str(n.get("versions") or "")) \
                and isinstance(n.get("body"), str) and 0 < len(n["body"].strip()) <= 280:
            notices.append({"id": n["id"], "versions": n["versions"], "body": n["body"].strip(), "url": _https(n.get("url"))})
    for i in (body.get("known_issues") or [])[:MAX_LISTED] if isinstance(body.get("known_issues"), list) else []:
        if isinstance(i, dict) and isinstance(i.get("id"), int) and isinstance(i.get("title"), str) \
                and 0 < len(i["title"].strip()) <= 200 and parse(i.get("since")):
            fixed = str(i.get("fixed_in") or "")
            issues.append({"id": i["id"], "title": i["title"].strip(), "since": i["since"],
                           "fixed_in": fixed if parse(fixed) else "", "url": _https(i.get("url"))})
    return {"notices": notices, "known_issues": issues}


def _client(timeout=5):
    return httpx.Client(timeout=timeout, transport=TRANSPORT, follow_redirects=False)


class Checker:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.etag = ""
        self.release = None
        self.extras = None   # HQ's messages and known issues, or None when the last answer was GitHub's
        self.checked = 0.0
        self.retry_at = 0.0
        self.running = False
        self.forced = 0.0
        self.census = None   # set by create_app: the counting policy and this install's payload

    def bind(self, census):
        self.census = census

    @staticmethod
    def enabled():
        return (not rehearsal_on()
                and os.environ.get("TICO_UPDATE_CHECK", "").strip().lower() not in ("off", "0", "false", "no"))

    def refresh(self, force=False):
        """One request, conditional unless `force`. Runs in a background thread; tests call it directly."""
        mirror = os.environ.get("TICO_RELEASES_URL", "").strip()
        url = mirror or LATEST_URL
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "tico-update-check"}
        if force:
            headers["Cache-Control"] = "no-cache"   # the owner asked because a release just went out
        elif self.etag and self.release:
            headers["If-None-Match"] = self.etag
        try:
            with _client(CHECK_WAIT if force else 5) as http:
                r = self._ask_hq(http) if not mirror else None
                from_hq = r is not None
                if r is None:
                    r = http.get(url, headers=headers)
            if r.status_code == 304:
                pass
            elif r.status_code == 200:
                body = r.json()
                self.release = {"tag": str(body.get("tag_name") or ""), "url": str(body.get("html_url") or ""),
                                "published_at": str(body.get("published_at") or ""),
                                "name": str(body.get("name") or body.get("tag_name") or "")}
                self.extras = extras(body) if from_hq else None
                self.etag = r.headers.get("etag", "")
            else:
                raise httpx.HTTPError("status %s" % r.status_code)
            self.checked, self.retry_at = self.clock(), 0.0
        except Exception as exc:
            log.debug("Release check failed: %s", type(exc).__name__)
            self.retry_at = self.clock() + RETRY
        finally:
            self.running = False

    def _ask_hq(self, http):
        """HQ's answer (the same body as GitHub's), or None: counting is off, or HQ did not give a usable one."""
        if self.census is None:
            return None
        try:
            query = self.census.query(version())
            if query is None:
                return None
            r = http.get(hq_url() + "/v1/latest", params=query, timeout=HQ_WAIT,
                         headers={"Accept": "application/json", "User-Agent": "tico-update-check"})
            if r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("tag_name"):
                return r
        except Exception as exc:
            log.debug("HQ did not answer: %s", type(exc).__name__)
        return None

    def check_now(self):
        """The owner's "Check for updates": one request now, however fresh the cache is, and the answer to it.

        Waits up to CHECK_WAIT seconds for the lookup. Returns True when it finished, False when it is still
        running (the caller says "still checking"; a later view() has the answer)."""
        if not self.enabled():
            raise Problem("check_disabled", "Update checks are turned off on this server (TICO_UPDATE_CHECK).", 409)
        with self.lock:
            wait = FORCE_GAP - (self.clock() - self.forced)
            if wait > 0:
                raise Problem("rate_limited", "Checked a moment ago. Try again in %d seconds." % (int(wait) + 1), 429,
                              retryable=True, extra={"retry_after": int(wait) + 1})
            self.forced = self.clock()
        with self.lock:
            self.running = True
        worker = threading.Thread(target=self.refresh, kwargs={"force": True}, daemon=True, name="release-check-now")
        worker.start()
        worker.join(CHECK_WAIT)
        if worker.is_alive():
            return False
        # refresh() swallows failures and schedules a retry; that is how a failed check shows here.
        if self.retry_at > self.clock():
            raise Problem("check_failed", "Could not reach GitHub to look for a new release. Try again later.", 502,
                          retryable=True)
        return True

    def _refresh_if_stale(self):
        now = self.clock()
        if self.running or now < self.retry_at or (self.checked and now - self.checked < TTL):
            return
        with self.lock:
            if self.running:
                return
            self.running = True
        threading.Thread(target=self.refresh, daemon=True, name="release-check").start()

    def view(self, current):
        """The notice payload. Returns at once with whatever is cached. `message` is HQ's newest message for this
        version (or None), `known_issues` the issues HQ lists for it, and `known_issues_checked` whether HQ answered."""
        out = {"current": current, "latest": "", "available": False, "url": "", "published_at": "", "name": "",
               "message": None, "known_issues": [], "known_issues_checked": False}
        if not self.enabled() or parse(current) is None:
            return out
        self._refresh_if_stale()
        release, more = self.release, self.extras
        if release:
            latest = release["tag"].lstrip("v")
            out.update(latest=latest, url=release["url"], published_at=release["published_at"], name=release["name"],
                       available=newer(latest, current))
        if more is not None:
            out["message"] = next((n for n in more["notices"] if covers(n["versions"], current)), None)
            out["known_issues"] = [i for i in more["known_issues"] if affected(i, current)]
            out["known_issues_checked"] = True
        return out


CHECKER = Checker()


def managed():
    """Tico updates this server from outside (TICO_UPDATES=tico): there is nothing for the owner to run."""
    return os.environ.get("TICO_UPDATES", "").strip().lower() == "tico"


def notice():
    return {**CHECKER.view(version()), "managed": managed()}


def check_now():
    done = CHECKER.check_now()
    return {**notice(), "checking": not done}


def _updater():
    url = "" if rehearsal_on() else os.environ.get("TICO_UPDATER_URL", "").strip().rstrip("/")
    return url, os.environ.get("TICO_UPDATER_TOKEN", "").strip()


def _call(method, path, body=None):
    url, token = _updater()
    try:
        with _client(10) as http:
            r = http.request(method, url + path, json=body, headers={"Authorization": "Bearer " + token})
        if r.status_code >= 400:
            raise httpx.HTTPError("status %s" % r.status_code)
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Updater call %s %s failed: %s", method, path, type(exc).__name__)
        raise Problem("updater_unreachable", "The updater did not answer. Try again, or update by hand.", 502,
                      retryable=True, extra={"command": MANUAL_COMMAND}) from None


def updater_answer(method, path, body=None):
    """The updater's status and answer, for a caller that tells its refusals apart (sleep's 409 busy is not an outage).
    Status 0 when it did not answer. `_call` is for the calls where any refusal is a failure."""
    url, token = _updater()
    if not url:
        return 0, {}
    try:
        with _client(10) as http:
            r = http.request(method, url + path, json=body, headers={"Authorization": "Bearer " + token})
    except httpx.HTTPError:
        return 0, {}
    try:
        answer = r.json()
    except ValueError:
        answer = {}
    return r.status_code, answer if isinstance(answer, dict) else {}


def status():
    if managed():
        return {"configured": False, "state": "unavailable", "managed": True}
    if not _updater()[0]:
        return {"configured": False, "state": "unavailable", "command": MANUAL_COMMAND}
    data = _call("GET", "/status")
    return {"configured": True, "state": str(data.get("state") or ""), "from": str(data.get("from") or ""),
            "to": str(data.get("to") or ""), "message": str(data.get("message") or ""),
            "snapshot": str(data.get("snapshot") or ""), "restored": bool(data.get("restored"))}


def start(target):
    if parse(target) is None:
        raise Problem("invalid_version", "That is not a release version", 422)
    if managed():
        raise Problem("managed_update", "Tico updates this server. There is nothing to run here.", 409)
    if not _updater()[0]:
        raise Problem("manual_update", "This installation has no updater. Run this on the server: " + MANUAL_COMMAND,
                      409, extra={"command": MANUAL_COMMAND})
    # "from" is the release this server is running, not whatever an earlier update left in the updater's status.
    _call("POST", "/update", {"version": str(target).lstrip("v"), "from": version()})
    log.info("Update to %s requested", target)
    return status()
