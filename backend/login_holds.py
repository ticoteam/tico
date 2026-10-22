"""A usage limit belongs to a provider login, not to the bot that met it.

A login is (computer, subscription profile or "" for the computer's own login, runtime). When a turn ends on that
login's usage limit, a hold is saved here, in the database, so it outlives a restart of the runner or the server:
- until the reset the provider gave (Claude says it in words: "resets 2:40am (UTC)"), honored even when it is days
  away, as long as it is in the future and within LIMIT_RESET_MAX;
- otherwise for BACKOFF[n] after the n-th limit in a row on that login (30, 60, then 120 minutes at most).
While it holds, no bot starts a turn on that login: a bot with a fallback that is ready and not held itself runs
there instead (Dev 137), and only one with none waits. When it ends, one queued job (the probe) may start on the login and
every other job on it waits for that turn's result: a success clears the hold, another limit saves a longer one. So a
login sees at most one turn per hold window, however many jobs and bots wait on it.
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

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), 1)}
# Claude Code's limit notice: "You've hit your session limit · resets 2:40am (UTC)", "resets 9pm (Europe/Paris)",
# "resets Oct 24, 9am (UTC)" or "resets Oct 24 at 9am (UTC)".
_RESET_RE = re.compile(r"\bresets\s+(?:(?P<month>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2})(?:,|\s+at)?\s+)?"
                       r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]m)\b\s*(?:\((?P<zone>[^)]+)\))?",
                       re.I)
# An API's own words with a timestamp: "Your usage window resets at 2026-10-19T00:00:00Z".
_WINDOW_RE = re.compile(r"usage window resets at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", re.I)


def _iso(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def key(runner_id, profile, runtime):
    return f"{PREFIX}{runner_id}|{profile or ''}|{runtime}"


def canonical(c, runner_id, profile, runtime):
    """The login a profile's turns on `runtime` sign in with, as holds key it: "" (the computer's own) for a profile
    the computer reports shares it (`shared_login`, runner-rs/src/fallback.rs `login`), else the profile."""
    if not profile:
        return ""
    from .store import readiness_document
    row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (runner_id,)).fetchone()
    ready = readiness_document(row["readiness_json"]) if row else {}
    report = ((ready.get("runtimes") or {}).get(runtime) or {})
    report = (report.get("profiles") or {}).get(profile) or report
    return "" if report.get("shared_login") is True else profile


def get(c, runner_id, profile, runtime):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?",
                    (key(runner_id, profile, runtime),)).fetchone()
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
    login, even when the bot's work went on to finish on a fallback: the next turn goes straight there."""
    old = get(c, runner_id, profile, runtime)
    at = believable(reset, hit)
    hit_at = H.parse_ts(hit)
    if old and (old.get("until") or "") > _iso(hit_at):
        # Met while the hold was still in force (a person's one try, or a turn sent to a fallback that was not ready
        # after all): the same window, so neither the streak nor the end moves earlier.
        streak = int(old.get("streak") or 1)
        until = max(at or hit_at, H.parse_ts(old["until"]))
    else:
        streak = int(old.get("streak") or 0) + 1 if old else 1
        until = at or hit_at + timedelta(seconds=BACKOFF[min(streak, len(BACKOFF)) - 1])
    hold = {"runner_id": runner_id, "profile": profile or "", "runtime": runtime, "since": _iso(hit_at),
            "until": _iso(until), "streak": streak, "reset": bool(at), "attempt_id": attempt_id, "bot": bot,
            "error": (error or "")[:300], "probe_job": None, "probe_at": None}
    _save(c, hold)
    return hold


def clear(c, runner_id, profile, runtime):
    """A turn worked on the login, or a person asked to try now: it is no longer held."""
    c.execute("DELETE FROM registry_metadata WHERE key=?", (key(runner_id, profile, runtime),))


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
    """Every saved hold on the computer's logins, oldest first."""
    out = []
    for (value,) in c.execute("SELECT value_json FROM registry_metadata WHERE key LIKE ? ORDER BY key",
                              (f"{PREFIX}{runner_id}|%",)):
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


_HOP_RE = re.compile(r"running this turn on (\S+)")


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
        moved = _HOP_RE.search(text)
        if not moved:
            continue
        if "hit its usage limit; running this turn on" in text and not steps[-1][2]:
            steps[-1][1:] = ["", True]
        steps.append([runtime_of(moved[1]), None, False])
    if final and steps[-1][0] != runtime_of(final):
        steps.append([runtime_of(final), None, False])
    return steps


def _field(result, name):
    return result.get(name) if isinstance(result, dict) else getattr(result, name, None)


def turn_steps(c, attempt_id, runtime, result):
    """The logins a finished turn ran on, in order, each [runtime, its first limit notice or None, limited]: `chain`,
    with what the runner's result says on top. A turn whose fallback ran with no note naming the hop (an older runner)
    puts its limit notices on that fallback unless the result says the primary was limited: a fallback's quota text
    must not hold the bot's own login (Dev 279)."""
    final, limited = _field(result, "fallback"), bool(_field(result, "limited"))
    if _field(result, "primary_held") and limited and not final:
        # Sent away from its held login, no fallback ran it: the turn ran nowhere, so no login met a limit.
        return [[runtime, None, False]]
    texts = limit_texts(c, attempt_id)
    steps = chain(texts, runtime, final)
    if (final and len(steps) == 2 and not _field(result, "primary_limited")
            and not any(kind == "note" and _HOP_RE.search(text) for kind, text in texts)):
        steps = [[runtime, None, False], [steps[1][0], steps[0][1], steps[0][2]]]
    if _field(result, "primary_limited"):
        steps[0][2] = True
    if limited:
        steps[-1][2] = True
    return steps


def window(notice):
    """Which kind of limit a notice is: an API key's quota or billing refusal, or the plan's usage window."""
    from .execution import BILLING_LIMIT_RE
    if gateway.out_of_credit(notice):
        return "Tico models paused, out of credit"
    if BILLING_LIMIT_RE.search(notice or ""):
        return "API quota or billing limit"
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
    used, fallback = _field(result, "profile_used"), _field(result, "fallback")
    own = (used if used is not None and not fallback else profile) or ""
    hop = (used if used is not None and fallback else profile) or ""
    if runner_id:
        own, hop = canonical(c, runner_id, own, runtime), canonical(c, runner_id, hop, steps[-1][0])
    held_kind = window(held.get("error")) if held else ""
    if _field(result, "primary_held") and _field(result, "limited") and not fallback:
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
        out["primary"] = ("held" if _field(result, "primary_held") else "limited" if steps[0][2] else "not limited")
        out["primary_kind"] = held_kind if _field(result, "primary_held") else window(steps[0][1]) if steps[0][2] else ""
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


def label(src, at="", primary_until=""):
    """One line a person reads: "Fallback codex (the work login) API quota or billing limit at 20:05; primary claude
    not limited", "Primary claude plan usage limit (5-hour window)". `at` is when it was met; `primary_until` when the
    primary's own hold ends, for a fallback's limit met while the primary is held too."""
    login = f" (the {src['profile']} login)" if src.get("profile") else ""
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


def after_turn(c, runner_id, bot, profile, runtime, body, attempt_id, hit):
    """Save or clear the logins' holds a finished turn shows, and return the hold that keeps the bot waiting, or {}.
    `profile` and `runtime` are the bot's own login. Every login the turn met a usage limit on is held, with the reset
    its own notice names, whatever the turn's outcome: an intermediate fallback that ran out stays held after a later
    one finishes the turn. A login the turn completed on is cleared, but only by a turn claimed after its hold began:
    a lane leased before the limit says nothing about the login now."""
    # Sent away from its held login, no fallback ran it (none was ready, or it failed before it acknowledged the
    # turn): the turn ran nowhere, so no login met a limit; the hop its notes name only failed to start.
    unstarted = body.primary_held and body.limited and not body.fallback
    steps = turn_steps(c, attempt_id, runtime, body)
    hop_profile = body.profile_used if body.fallback and body.profile_used is not None else profile
    claimed = (c.execute("SELECT created FROM attempts WHERE id=?", (attempt_id,)).fetchone() or [""])[0] or ""
    kept = {}
    for n, (rt, notice, limited) in enumerate(steps):
        login = canonical(c, runner_id, profile if n == 0 else hop_profile, rt)
        last = n == len(steps) - 1
        if limited:
            if n == 0 and body.primary_held:
                continue                # never tried: its hold stands as it is
            reset = reset_from_text(notice, hit) or (body.limit_resets_at if last else None)
            if gateway.out_of_credit(notice):
                # The Tico AI Gateway paused the company's Tico models for want of credit: the company's wait, not a
                # spent login, so the login is held only for the Gateway's Retry-After (or its minute), never the
                # backoff. A turn that ended here waits that minute too, even while its primary's own hold is longer.
                reset = (body.limit_resets_at if last else None) or H.shift(hit, seconds=gateway.OUT_OF_CREDIT_WAIT)
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
    if unstarted:
        # The bot waits out this hold rather than being sent to that fallback again (fallback_choice).
        own = canonical(c, runner_id, profile, runtime)
        hold = get(c, runner_id, own, runtime)
        if hold:
            _save(c, {**hold, "stuck": sorted({*hold.get("stuck", []), bot})})
            kept = get(c, runner_id, own, runtime)
    return kept if body.limited else {}
