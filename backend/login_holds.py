"""A usage limit belongs to a provider login, not to the bot that met it.

A login is (computer, subscription profile or "" for the computer's own login, runtime). When a turn ends on that
login's usage limit, a hold is saved here, in the database, so it outlives a restart of the runner or the server:
- until the reset the provider gave (Claude says it in words: "resets 2:40am (UTC)"), honored even when it is days
  away, as long as it is in the future and within LIMIT_RESET_MAX;
- otherwise for BACKOFF[n] after the n-th limit in a row on that login (30, 60, then 120 minutes at most);
- for CAPACITY_WAIT only when the provider said the model is at capacity or overloaded for now (`capacity`): the
  provider's busy moment, not the login's quota, so it neither waits out a backoff nor counts in the streak.
While it holds, no bot starts a turn on that login: a bot with a fallback that is ready and not held itself runs
there instead (Dev 137), and only one with none waits. When it ends, one queued job (the probe) may start on the login and
every other job on it waits for that turn's result: a success clears the hold, another limit saves a longer one. So a
login sees at most one turn per hold window, however many jobs and bots wait on it. Try now ends the hold early the
same way (`try_now`): one probe, and the cut-short window back if it meets the limit again.

A company subscription given to computers (backend/model_subscriptions.py) is one login wherever it runs: its login is
`sub:<id>` (SUBSCRIPTION) on every computer it is given to, and its hold is keyed without a computer, so a limit met on
one computer holds every computer using it until the reset.
"""

import json
import re
from datetime import timedelta, timezone

from clients import gateway

from .store import H

PREFIX = "login-hold:"
BACKOFF = (1800, 3600, 7200)      # seconds held after the 1st, 2nd and later limits in a row with no reset given
LIMIT_RESET_MAX = 8 * 86400       # a reset further off than a weekly window is not believed
PROBE_STALE = 900                 # a probe job that has not started within this long stops holding the others back
LIVE_JOB = ("leased", "running")
CAPACITY_WAIT = 120               # seconds held after a temporary model-capacity refusal
CAPACITY_KIND = "temporary model capacity"
REFUSED_KIND = "refused the request"

# A provider out of capacity for the moment (runner-rs/src/hosts/mod.rs capacity_re): "Selected model is at capacity.
# Please try a different model.", "overloaded_error", HTTP 503, "rate limited, try again in 20s". Quota or billing words
# in the same notice win (_QUOTA_RE): "Quota exceeded. Check your plan and billing details." is the plan's, not a busy
# moment.
_CAPACITY_RE = re.compile(r"model is at capacity|no capacity available|\boverloaded(?:_error)?\b|"
                          r"\b(?:code|status|http) 503\b|(?:retry|try again) (?:after|in) \d+(?:\.\d+)? ?"
                          r"(?:s|secs?|seconds)\b", re.I)
_QUOTA_RE = re.compile(r"hit your (?:usage|session|weekly) limit|usage limit|usage_limit_reached|quota|spend(?:ing)? "
                       r"limit|monthly (?:usage |spend(?:ing)? )?limit|out_of_credit|billing|\bresets?\b", re.I)

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), 1)}
# Claude Code's limit notice: "You've hit your session limit · resets 2:40am (UTC)", "resets 9pm (Europe/Paris)",
# "resets Oct 24, 9am (UTC)" or "resets Oct 24 at 9am (UTC)".
_RESET_RE = re.compile(r"\bresets\s+(?:(?P<month>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2})(?:,|\s+at)?\s+)?"
                       r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]m)\b\s*(?:\((?P<zone>[^)]+)\))?",
                       re.I)
# An API's own words with a timestamp: "Your usage window resets at 2026-10-19T00:00:00Z".
_WINDOW_RE = re.compile(r"usage window resets at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", re.I)


# A provider turning the request down: the turn may carry the login's limit, but this line is what stopped it.
_REFUSED_RE = re.compile(r"\bAPI Error: 4(?!29)\d\d\b|does not support this model", re.I)


def capacity(notice):
    """Whether a limit notice is a provider's temporary model capacity, not a login's quota or plan."""
    notice = str(notice or "")
    return bool(_CAPACITY_RE.search(notice)) and not _QUOTA_RE.search(notice) and not gateway.run_code(notice)


def _iso(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


SUBSCRIPTION = "sub:"


def key(runner_id, profile, runtime):
    if str(profile or "").startswith(SUBSCRIPTION):
        return f"{PREFIX}{profile}|{runtime}"     # one login on every computer it is given to
    return f"{PREFIX}{runner_id}|{profile or ''}|{runtime}"


def subscription_login(subscription_id):
    return SUBSCRIPTION + subscription_id


def canonical(c, runner_id, profile, runtime, subscription=True):
    """The login a profile's turns on `runtime` sign in with, as holds key it: "" (the computer's own) for a profile
    the computer reports shares it (`shared_login`, runner-rs/src/fallback.rs `login`), else the profile.
    Inside a transaction it is answered once: a claim scan asks it per bot and per job, and the runner's readiness
    document is parsed each time. Writes clear the read cache (backend/read_cache.py), so a changed runner is re-read.
    The computer's subscription for `runtime`, while one is in effect there, is the login whatever the profile
    (`subscription=False` asks for the computer's own login)."""
    from .read_cache import cache
    if subscription:
        from .model_subscriptions import in_effect
        found = in_effect(c, runner_id, runtime)
        if found:
            return subscription_login(found["id"])
    values = cache(c)
    if values is None:
        return _canonical(c, runner_id, profile, runtime)
    key = ("login_holds.canonical", runner_id, profile, runtime)
    if key not in values:
        values[key] = _canonical(c, runner_id, profile, runtime)
    return values[key]


def _canonical(c, runner_id, profile, runtime):
    from .store import readiness_document
    from .read_cache import one
    row = one(c, "SELECT readiness_json,capabilities_json FROM runners WHERE id=?", (runner_id,))
    ready = readiness_document(row["readiness_json"]) if row else {}
    from .subscriptions import computer_logins
    current = row and computer_logins(row, ready)
    if not profile and current:
        # Keep the physical home as the identity: existing holds on a retained default profile still apply.
        profile = ready['computer_profile']
    if current and profile != ready['computer_profile']:
        return profile     # an older in-flight turn on another home is never folded into the new default
    if not profile:
        return ""
    report = ((ready.get("runtimes") or {}).get(runtime) or {})
    report = (report.get("profiles") or {}).get(profile) or report
    return "" if report.get("shared_login") is True else profile


def get(c, runner_id, profile, runtime):
    from .read_cache import metadata
    row = metadata(c, key(runner_id, profile, runtime))
    try:
        value = json.loads(row[0]) if row else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _save(c, hold):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (key(hold["runner_id"], hold["profile"], hold["runtime"]), json.dumps(hold)))


def _zone(name):
    name = (name or "").strip()
    if not name or name.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def reset_from_text(text, hit):
    """The reset a limit notice names in words, as a UTC datetime, or None. A time with no date is its next
    occurrence after `hit`, unless that is most of a day away: then the time named has just passed."""
    hit = H.parse_ts(hit) if isinstance(hit, str) else hit
    stamped = _WINDOW_RE.search(text or "")
    if stamped and hit:
        return H.parse_ts(stamped[1])
    match = _RESET_RE.search(text or "")
    if not match or not hit:
        return None
    zone = _zone(match["zone"])
    if zone is None:
        return None
    hour, minute = int(match["hour"]), int(match["minute"] or 0)
    if not (1 <= hour <= 12 and minute < 60):
        return None
    hour = hour % 12 + (12 if match["ampm"].lower() == "pm" else 0)
    local = hit.astimezone(zone)
    if match["month"]:
        month = _MONTHS.get(match["month"][:3].lower())
        if not month:
            return None
        try:
            at = local.replace(month=month, day=int(match["day"]), hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None
        if at < local - timedelta(days=180):
            at = at.replace(year=at.year + 1)
        return at.astimezone(timezone.utc)
    at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= local:
        at += timedelta(days=1)
    if at - local > timedelta(hours=20):
        return None
    return at.astimezone(timezone.utc)


def believable(reset, hit):
    """`reset` (an ISO string or datetime) when it is after `hit` and no further than LIMIT_RESET_MAX, else None: a
    reset already past would be a hold of no time at all, and one weeks away is not a usage window."""
    reset = H.parse_ts(reset) if isinstance(reset, str) else reset
    hit = H.parse_ts(hit) if isinstance(hit, str) else hit
    if not reset or not hit or not reset.tzinfo:
        return None
    return reset if timedelta(0) < reset - hit <= timedelta(seconds=LIMIT_RESET_MAX) else None


def record(c, runner_id, profile, runtime, hit, reset=None, attempt_id=None, bot=None, error=""):
    """Save a hold on the login after a turn ended on its usage limit at `hit`, and return it. A hold still saved
    means no turn has worked on the login since, so this limit is the next in a row. A hold keeps every turn off the
    login, even when the bot's work went on to finish on a fallback: the next turn goes straight there. A capacity
    notice holds for CAPACITY_WAIT and keeps the quota streak as it was: a busy provider says nothing of the plan."""
    old = get(c, runner_id, profile, runtime)
    busy = capacity(error)
    if busy:
        reset = H.shift(hit, seconds=CAPACITY_WAIT)
    at = believable(reset, hit)
    hit_at = H.parse_ts(hit)
    if hit_at is None:
        raise ValueError(f"unreadable limit time: {hit!r}")
    # A window Try now cut short (`try_now`) still counts as in force for a limit its probe meets.
    end = max(old.get("until") or "", old.get("cut") or "") if old else ""
    if old and end > _iso(hit_at) and (busy or not old.get("capacity")):
        # Met while the hold was still in force (a person's one try, or a turn sent to a fallback that was not ready
        # after all): the same window, so neither the streak nor the end moves earlier, and a quota hold stays one, its
        # end too: a busy model says nothing of the plan's reset. A quota notice during a capacity hold starts its own
        # quota hold.
        streak = int(old.get("streak") or (0 if busy else 1))
        ended = H.parse_ts(end)
        if ended is None:
            raise ValueError(f"unreadable hold end: {end!r}")
        until = max(at or hit_at, ended)
        if busy and not old.get("capacity"):
            busy, at, error, until = False, old.get("reset"), old.get("error") or "", H.parse_ts(end)
    else:
        streak = int(old.get("streak") or 0) + (0 if busy else 1) if old else (0 if busy else 1)
        until = at or hit_at + timedelta(seconds=BACKOFF[min(streak, len(BACKOFF)) - 1])
    # A turn that started before the hold and ends on the limit late leaves another job's live probe in place: only
    # that probe's own result decides the login, so no second turn starts beside it, and the hold keeps the start the
    # probe was claimed after, so the probe's success still clears it.
    probe = old.get("probe_job") if old else None
    keep = probe and _probe_live(c, old, H.now()) and not c.execute(
        "SELECT 1 FROM jobs WHERE id=? AND attempt_id IS ?", (probe, attempt_id)).fetchone()
    since = old.get("since") if keep and old.get("since") else _iso(hit_at)
    hold = {"runner_id": runner_id, "profile": profile or "", "runtime": runtime, "since": since,
            "until": _iso(until), "streak": streak, "reset": bool(at) and not busy, "capacity": busy,
            "attempt_id": attempt_id, "bot": bot, "error": (error or "")[:300], "probe_job": probe if keep else None,
            "probe_at": old.get("probe_at") if keep else None}
    if old.get("cut") and keep:
        hold["cut"] = old["cut"]
    _save(c, hold)
    return hold


def clear(c, runner_id, profile, runtime):
    """A turn worked on the login: it is no longer held."""
    c.execute("DELETE FROM registry_metadata WHERE key=?", (key(runner_id, profile, runtime),))


def try_now(c, runner_id, profile, runtime, now=None):
    """A person asked to try now: the hold ends at once, and as when it runs out, one queued job (the probe) starts on
    the login and every other job on it, whichever bot it is for, waits for that turn's result. The end it cut short
    is kept (`cut`), so a probe that meets the limit again puts the same window back rather than a shorter one. A hold
    already ended keeps its probe."""
    hold = get(c, runner_id, profile, runtime)
    now = now or H.now()
    if not hold or (hold.get("until") or "") <= now:
        return
    live = _probe_live(c, hold, now)
    _save(c, {**hold, "until": now, "cut": max(hold["until"], hold.get("cut") or ""),
              "probe_job": hold.get("probe_job") if live else None, "probe_at": hold.get("probe_at") if live else None})


def probing(c, hold, now=None):
    """Whether a probe turn is out on the login now, so Try now has nothing more to start."""
    return bool(hold) and _probe_live(c, hold, now or H.now())


def _probe_live(c, hold, now):
    job = hold.get("probe_job")
    if not job:
        return False
    row = c.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()
    if not row:
        return False
    if row["state"] in LIVE_JOB:
        return True
    # Back in the queue (its runner restarted mid-turn): still the probe for a while, so a restarted runner's stale
    # or repeated claim cannot start a second job on the login in its place.
    return row["state"] == "queued" and (hold.get("probe_at") or "") > H.shift(now, seconds=-PROBE_STALE)


def holding(c, runner_id, profile, runtime, job_id=None, now=None):
    """Why no turn may start on the login now: "held" before the hold ends, "probing" while another job's probe turn
    is out after it; None when one may (with `job_id`, when that job may)."""
    hold = get(c, runner_id, profile, runtime)
    if not hold:
        return None
    now = now or H.now()
    if (hold.get("until") or "") > now:
        return "held"
    if hold.get("probe_job") != job_id and _probe_live(c, hold, now):
        return "probing"
    return None


def take_probe(c, runner_id, profile, runtime, job_id, now=None):
    """A job starts on a login whose hold has ended: it is the probe for this hold window."""
    hold = get(c, runner_id, profile, runtime)
    now = now or H.now()
    if not hold or (hold.get("until") or "") > now or hold.get("probe_job") == job_id:
        return
    if _probe_live(c, hold, now):
        return
    _save(c, {**hold, "probe_job": job_id, "probe_at": now})


def on_runner(c, runner_id):
    """Every saved hold on the computer's logins, oldest first, and on the subscriptions it is given."""
    from .model_subscriptions import given_to
    out = []
    keys = [key("", subscription_login(sid), runtime) for runtime, sid in given_to(c, runner_id)]
    rows = c.execute("SELECT value_json FROM registry_metadata WHERE key LIKE ? ORDER BY key",
                     (f"{PREFIX}{runner_id}|%",)).fetchall()
    rows += [r for k in keys for r in c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (k,))]
    for (value,) in rows:
        try:
            hold = json.loads(value)
        except ValueError:
            continue
        if isinstance(hold, dict):
            out.append(hold)
    return out


def for_attempt(c, attempt_id):
    """The hold an attempt's limit saved, while it is still the login's latest. A turn that met limits on two logins
    saved two; the one it ended on comes first, so the bot's wait does not depend on which row is read first."""
    if not attempt_id:
        return {}
    found = {}
    for (value,) in c.execute("SELECT value_json FROM registry_metadata WHERE key LIKE ? AND value_json LIKE ?",
                              (PREFIX + "%", f'%"{attempt_id}"%')):
        try:
            hold = json.loads(value)
        except ValueError:
            continue
        if isinstance(hold, dict) and hold.get("attempt_id") == attempt_id:
            if hold.get("ended"):
                return hold
            found = found or hold
    return found


def limit_texts(c, attempt_id):
    """The attempt's limit errors and fallback notes in order: (kind, text)."""
    out = []
    for row in c.execute("SELECT kind,payload_json FROM attempt_events WHERE attempt_id=? AND kind IN "
                         "('error','diagnostic') ORDER BY seq LIMIT 50", (attempt_id,)):
        try:
            payload = json.loads(row["payload_json"])
        except ValueError:
            continue
        if row["kind"] == "error" and payload.get("limit"):
            out.append(("limit", str(payload.get("error") or "")))
        elif row["kind"] == "diagnostic":
            out.append(("note", str(payload.get("text") or "")))
    return out


HOP_RE = re.compile(r"running this turn on (\S+)")


def chain(texts, runtime, final=None):
    """The logins a turn ran on, in order, and the first limit notice each met: [(runtime, notice or None)]. A limit
    belongs to the login running when it came; the runner's note "<x> hit its usage limit; running this turn on <y>"
    moves the turn on (and says <x> was limited even when its notice carried no text); "<x> login held ..." and
    "unavailable" move it on without a limit. `final` is the harness the runner says ended the turn."""
    from .harnesses import runtime_of
    steps = [[runtime, None, False]]
    for kind, text in texts:
        if kind == "limit":
            if not steps[-1][2]:
                steps[-1][1:] = [text, True]
            continue
        moved = HOP_RE.search(text)
        if not moved:
            continue
        if "hit its usage limit; running this turn on" in text and not steps[-1][2]:
            steps[-1][1:] = ["", True]
        steps.append([runtime_of(moved[1]), None, False])
    if final and steps[-1][0] != runtime_of(final):
        steps.append([runtime_of(final), None, False])
    return steps


def field(result, name):
    return result.get(name) if isinstance(result, dict) else getattr(result, name, None)


def turn_steps(c, attempt_id, runtime, result):
    """The logins a finished turn ran on, in order, each [runtime, its first limit notice or None, limited]: `chain`,
    with what the runner's result says on top. A turn whose fallback ran with no note naming the hop (an older runner)
    puts its limit notices on that fallback unless the result says the primary was limited: a fallback's quota text
    must not hold the bot's own login (Dev 279)."""
    final, limited = field(result, "fallback"), bool(field(result, "limited"))
    if field(result, "primary_held") and limited and not final:
        # Sent away from its held login, no fallback ran it: the turn ran nowhere, so no login met a limit.
        return [[runtime, None, False]]
    texts = limit_texts(c, attempt_id)
    steps = chain(texts, runtime, final)
    if (final and len(steps) == 2 and not field(result, "primary_limited")
            and not any(kind == "note" and HOP_RE.search(text) for kind, text in texts)):
        steps = [[runtime, None, False], [steps[1][0], steps[0][1], steps[0][2]]]
    if field(result, "primary_limited"):
        steps[0][2] = True
    if limited:
        steps[-1][2] = True
    return steps


# This residual's display contract is deliberately narrower than the existing hold parser.
_FALLBACK_RESET_RE = re.compile(r"\bresets ([0-9]{1,2}:[0-9]{2}[ap]m) \(UTC\)")


def fallback_reset_notice(c, attempt_id):
    """The fallback's original limit notice and named UTC reset, pinned to when this run ended."""
    row = c.execute("SELECT result_json,handed_runtime,finished FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    result = json.loads(row['result_json'] or '{}') if row else {}
    if not row or not result.get('fallback'):
        return None
    _, notice, limited = turn_steps(c, attempt_id, row['handed_runtime'], result)[-1]
    # A reply may quote a reset without being the provider error that stopped this login.
    notice = notice or ''
    match = _FALLBACK_RESET_RE.search(notice)
    if not limited or not match:
        return None
    reset = believable(reset_from_text(match[0], row['finished']), row['finished'])
    return {'notice': notice, 'reset': _iso(reset)} if reset else None


def fallback_resume_reason(c, attempt_id):
    """A live named fallback hold's visible reason; never invent a reset for other errors."""
    detail = fallback_reset_notice(c, attempt_id)
    hold = for_attempt(c, attempt_id) if detail else {}
    if hold.get('reset') and (hold.get('until') or '') > H.now():
        until = hold['until']
        return f"resumes ~{until[11:16]} UTC" + (f" on {until[:10]}" if until[:10] != H.now()[:10] else '')
    return None


def window(notice):
    """Which kind of limit a notice is: an API key's quota or billing refusal, or the plan's usage window."""
    from .execution import BILLING_LIMIT_RE
    if gateway.out_of_credit(notice):
        return "Tico models paused, out of credit"
    if gateway.rate_limited(notice):
        return "Tico AI Gateway rate limit"
    if capacity(notice):
        return CAPACITY_KIND
    if BILLING_LIMIT_RE.search(notice or ""):
        return "API quota or billing limit"
    if _REFUSED_RE.search(notice or ""):
        return f"{REFUSED_KIND} ({' '.join(notice.split())[:160]})"
    if re.search(r"\bweekly\b", notice or "", re.I):
        return "plan usage limit (weekly)"
    if re.search(r"\bsession limit\b|5-hour|five-hour", notice or "", re.I):
        return "plan usage limit (5-hour window)"
    return "plan usage limit"


def source(c, attempt_id, runtime, result, profile="", runner_id=None, held=None):
    """Whose limit ended a turn: the login it came from (primary or fallback, runtime, login), which kind of limit it
    was, and how the primary fared. Worked out when the turn finishes, with the runtime and login it ran on (`profile`
    is the bot's own, `profile_used` the one the runner reports) and kept with the attempt (`limit_source`), so a
    later settings change or another turn's hold does not rename it. `held` is the primary's hold when the claim sent
    the turn away from it: a turn that ran nowhere waits on that hold and keeps its kind."""
    steps = turn_steps(c, attempt_id, runtime, result)
    used, fallback = field(result, "profile_used"), field(result, "fallback")
    own = (used if used is not None and not fallback else profile) or ""
    hop = (used if used is not None and fallback else profile) or ""
    if runner_id:
        from .model_subscriptions import handed

        def login(name, rt):
            given = handed(c, attempt_id, rt)
            return (subscription_login(given["subscription_id"]) if given
                    else canonical(c, runner_id, name, rt, subscription=False))
        own, hop = login(own, runtime), login(hop, steps[-1][0])
    held_kind = window(held.get("error")) if held else ""
    if field(result, "primary_held") and field(result, "limited") and not fallback:
        # Sent away from its held login and no fallback started: nothing new was refused.
        return {"role": "primary", "runtime": runtime, "profile": own, "kind": held_kind or "plan usage limit",
                "billing": held_kind.startswith("API"), "notice": ((held or {}).get("error") or "")[:300],
                "primary_runtime": runtime, "waiting": True}
    n = max((i for i, step in enumerate(steps) if step[2]), default=len(steps) - 1)
    rt, notice, _ = steps[n]
    out = {"role": "fallback" if n else "primary", "runtime": rt, "profile": hop if n else own,
           "kind": window(notice), "billing": window(notice).startswith("API"), "notice": (notice or "")[:300],
           "primary_runtime": runtime, "primary_profile": own}
    if n:
        out["primary"] = ("held" if field(result, "primary_held") else "limited" if steps[0][2] else "not limited")
        out["primary_kind"] = held_kind if field(result, "primary_held") else window(steps[0][1]) if steps[0][2] else ""
    return out


def recorded(c, attempt_id, result, runtime):
    """The source a limited turn kept (`source`), or for a run from before it was kept, worked out again with the
    runtime its limit event named at the time, so a later settings change does not rename the provider."""
    if isinstance(result.get("limit_source"), dict):
        return result["limit_source"]
    # Keeper's events by action and target: the events_task_origin index.
    row = c.execute("SELECT json_extract(detail_json,'$.runtime') FROM events WHERE actor=? AND action='attempt.limited' "
                    "AND target=? LIMIT 1", (H.KEEPER, attempt_id)).fetchone()
    return source(c, attempt_id, (row and row[0]) or runtime or "primary", result)


def failure_line(result):
    """A failed run's reason in plain words from the limit source its attempt kept (`limit_source`): "failed: quota
    (Codex fallback, login work, model gpt-5.5)" or "failed: usage limit (Claude, primary)". None when the attempt
    did not end on a limit, so its own error text is used."""
    src = result.get("limit_source") if isinstance(result, dict) else None
    if not isinstance(src, dict) or not src.get("runtime"):
        return None
    kind = src.get("kind") or ""
    what = ("model capacity" if kind == CAPACITY_KIND else "quota" if src.get("billing")
            else "request refused" if kind.startswith(REFUSED_KIND) else "usage limit")
    who = str(src["runtime"]).capitalize() + (" fallback" if src.get("role") == "fallback" else "")
    parts = [who + ("" if src.get("role") == "fallback" else ", primary")]
    if str(src.get("profile") or "").startswith(SUBSCRIPTION):
        parts.append("a saved subscription")
    elif src.get("profile"):
        parts.append(f"login {src['profile']}")
    if src.get("model"):
        parts.append(f"model {src['model']}")
    return f"failed: {what} ({', '.join(parts)})"


def label(src, at="", primary_until=""):
    """One line a person reads: "Fallback codex (the work login) API quota or billing limit at 20:05; primary claude
    not limited", "Primary claude plan usage limit (5-hour window)". `at` is when it was met; `primary_until` when the
    primary's own hold ends, for a fallback's limit met while the primary is held too."""
    login = (" (a saved subscription)" if str(src.get("profile") or "").startswith(SUBSCRIPTION)
             else f" (the {src['profile']} login)" if src.get("profile") else "")
    said = f"{src['role'].capitalize()} {src['runtime']}{login} {src['kind']}"
    if src.get("waiting"):
        return said + ", held from an earlier turn; no fallback could take this one"
    said += f" at {at}" if at else ""
    if src["role"] == "fallback":
        own = f" (the {src['primary_profile']} login)" if src.get("primary_profile") else ""
        said += f"; primary {src['primary_runtime']}{own} " + {
            "held": "login held from an earlier limit" + (f" ({src['primary_kind']})" if src.get("primary_kind") else ""),
            "limited": "also limited" + (f" ({src['primary_kind']})" if src.get("primary_kind") else ""),
        }.get(src.get("primary"), "not limited")
        if primary_until and src.get("primary") in ("limited", "held"):
            said += f" until {primary_until}"
    return said


def after_turn(c, runner_id, bot, profile, runtime, body, attempt_id, hit, hopped=None, subscriptions_only=False):
    """Save or clear the logins' holds a finished turn shows, and return the hold that keeps the bot waiting, or {}.
    `profile` and `runtime` are the bot's own login. Every login the turn met a usage limit on is held, with the reset
    its own notice names, whatever the turn's outcome: an intermediate fallback that ran out stays held after a later
    one finishes the turn. A login the turn completed on is cleared, but only by a turn claimed after its hold began:
    a lane leased before the limit says nothing about the login now. `hopped` is the bot's fallback (execution.fallback_mark)
    when it met the limit and could not start the turn (execution.hop_failed): the bot waits out its own hold rather than
    being sent there again, until it names another fallback. `subscriptions_only` (a check's turn) holds or clears only
    the company subscriptions the turn signed in with: one is a login shared by every computer given it, while the
    check keeps its own schedule (execution.complete) and the computer's own logins are left as they are."""
    # Sent away from its held login, no fallback ran it (none was ready, or it failed before it acknowledged the
    # turn): the turn ran nowhere, so no login met a limit; the hop its notes name only failed to start.
    unstarted = body.primary_held and body.limited and not body.fallback
    steps = turn_steps(c, attempt_id, runtime, body)
    # The home is pinned across primary and fallback work. An older in-flight turn may finish on a retired home
    # after the server's upgrade: its reported login, not the new default, owns that limit.
    if body.profile_used is not None:
        profile = body.profile_used
    hop_profile = body.profile_used if body.fallback and body.profile_used is not None else profile
    claimed = (c.execute("SELECT created FROM attempts WHERE id=?", (attempt_id,)).fetchone() or [""])[0] or ""
    kept = {}
    from .model_subscriptions import handed
    for n, (rt, notice, limited) in enumerate(steps):
        # A step signed in with a company subscription met its limit (or worked) on that subscription, wherever it
        # runs; any other step on the computer's own login.
        given = handed(c, attempt_id, rt)
        if subscriptions_only and not given:
            continue
        login = (subscription_login(given["subscription_id"]) if given
                 else canonical(c, runner_id, profile if n == 0 else hop_profile, rt, subscription=False))
        last = n == len(steps) - 1
        if limited:
            if n == 0 and body.primary_held:
                continue                # never tried: its hold stands as it is
            if gateway.out_of_credit(notice) or gateway.rate_limited(notice):
                # The Tico AI Gateway paused the company's Tico models for want of credit, or the key met its minute's
                # rate limit: the Gateway's wait, not a spent login, so the login is held only for its Retry-After (or a
                # minute), never the backoff. A turn that ended here waits that minute too, even while its primary's own
                # hold is longer. A reset already past (a runner's clock behind) counts as none reported. With none, a
                # minute, except a rate limit the turn ended on: that keeps execution.reset_cooldown's safe hour.
                reported = believable(body.limit_resets_at, hit) if last else None
                unsaid = last and not gateway.out_of_credit(notice)
                reset = reported or H.shift(hit, seconds=gateway.RETRY_AFTER if unsaid else gateway.GATEWAY_WAIT)
            else:
                reset = reset_from_text(notice, hit) or (body.limit_resets_at if last else None)
            hold = record(c, runner_id, login, rt, hit, reset, attempt_id, bot, error=notice or "")
            if last:
                # The login the turn ended on: the one its bot waits for (for_attempt), when a turn held two.
                hold = {**hold, "ended": True}
                _save(c, hold)
            if last or n == 0:
                kept = hold
        elif last and body.outcome == "completed" and not (n == 0 and body.primary_held):
            old = get(c, runner_id, login, rt)
            if old and claimed >= (old.get("since") or ""):
                clear(c, runner_id, login, rt)
    if subscriptions_only:
        return {}
    if unstarted:
        # The bot waits out this hold rather than being sent to that fallback again (fallback_choice).
        own = canonical(c, runner_id, profile, runtime)
        hold = get(c, runner_id, own, runtime)
        if hold:
            _save(c, {**hold, "stuck": sorted({*hold.get("stuck", []), bot})})
            kept = get(c, runner_id, own, runtime)
    if hopped is not None:
        own = canonical(c, runner_id, profile, runtime)
        hold = get(c, runner_id, own, runtime)
        if hold:
            _save(c, {**hold, "failed_fallback": {**(hold.get("failed_fallback") or {}), bot: hopped}})
            kept = get(c, runner_id, own, runtime)
    return kept if body.limited or hopped is not None else {}
