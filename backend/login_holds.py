"""A usage limit belongs to a provider login, not to the bot that met it.

A login is (computer, subscription profile or "" for the computer's own login, runtime). When a turn ends on that
login's usage limit, a hold is saved here, in the database, so it outlives a restart of the runner or the server:
- until the reset the provider gave (Claude says it in words: "resets 2:40am (UTC)"), honored even when it is days
  away, as long as it is in the future and within LIMIT_RESET_MAX;
- otherwise for BACKOFF[n] after the n-th limit in a row on that login (30, 60, then 120 minutes at most).
While it holds, no bot on that login starts a turn. When it ends, one queued job (the probe) may start on the login and
every other job on it waits for that turn's result: a success clears the hold, another limit saves a longer one. So a
login sees at most one turn per hold window, however many jobs and bots wait on it.
"""

import json
import re
from datetime import timedelta, timezone

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


def record(c, runner_id, profile, runtime, hit, reset=None, attempt_id=None, bot=None, full=True, error=""):
    """Save a hold on the login after a turn ended on its usage limit at `hit`, and return it. A hold still saved
    means no turn has worked on the login since, so this limit is the next in a row. `full` is False when the turn
    went on to finish on a fallback login: the hold then keeps the runner's fallback choice off the login but holds
    back no bot, since the bot's work still gets done."""
    old = get(c, runner_id, profile, runtime)
    streak = int(old.get("streak") or 0) + 1 if old else 1
    at = believable(reset, hit)
    hit_at = H.parse_ts(hit)
    until = at or hit_at + timedelta(seconds=BACKOFF[min(streak, len(BACKOFF)) - 1])
    hold = {"runner_id": runner_id, "profile": profile or "", "runtime": runtime, "since": _iso(hit_at),
            "until": _iso(until), "streak": streak, "reset": bool(at), "attempt_id": attempt_id, "bot": bot,
            "full": bool(full), "error": (error or "")[:300], "probe_job": None, "probe_at": None}
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
    if not hold or not hold.get("full", True):
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
    """The hold an attempt's limit saved, while it is still the login's latest."""
    if not attempt_id:
        return {}
    for (value,) in c.execute("SELECT value_json FROM registry_metadata WHERE key LIKE ? AND value_json LIKE ?",
                              (PREFIX + "%", f'%"{attempt_id}"%')):
        try:
            hold = json.loads(value)
        except ValueError:
            continue
        if isinstance(hold, dict) and hold.get("attempt_id") == attempt_id:
            return hold
    return {}


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


def after_turn(c, runner_id, bot, profile, runtime, body, attempt_id, hit):
    """Save or clear the logins' holds a finished turn shows, and return the hold that keeps the bot waiting (its
    own login's when that one is spent, else the fallback's), or {}.
    `profile` and `runtime` are the bot's own login; a turn the runner moved to a fallback harness after a usage
    limit names that harness in `body.fallback` and its login in the last usage segment."""
    texts = limit_texts(c, attempt_id)
    hopped = any(kind == "note" and "usage limit; running this turn on" in text for kind, text in texts)
    segments = (body.usage.segments or [body.usage]) if body.usage else []
    last = segments[-1] if segments else None
    fallback = None
    if body.fallback:
        fallback = ((last.runtime if last and last.runtime else body.fallback),
                    (last.profile_used if last else None) or "")
    limits = [text for kind, text in texts if kind == "limit"]
    primary_spent = body.limited and not fallback or bool(fallback) and (body.primary_limited or hopped)
    fallback_spent = bool(fallback) and body.limited
    kept = {}
    if primary_spent:
        said = next((at for at in (reset_from_text(text, hit) for text in limits[:1]) if at), None)
        reset = said or (body.limit_resets_at if not fallback else None)
        kept = record(c, runner_id, profile, runtime, hit, reset, attempt_id, bot, full=body.limited,
                      error=limits[0] if limits else "")
    elif not body.limited and body.outcome == "completed" and not fallback:
        clear(c, runner_id, profile, runtime)
    if fallback_spent:
        said = next((at for at in (reset_from_text(text, hit) for text in limits[1:]) if at), None)
        hold = record(c, runner_id, fallback[1], fallback[0], hit, said or body.limit_resets_at, attempt_id, bot,
                      error=limits[-1] if limits else "")
        kept = kept or hold
    elif fallback and body.outcome == "completed":
        clear(c, runner_id, fallback[1], fallback[0])
    return kept if body.limited else {}
