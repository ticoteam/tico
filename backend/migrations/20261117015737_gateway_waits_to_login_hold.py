"""The Tico AI Gateway's waits an older server kept outside the login holds become the Gateway's one login hold.

The claim reads the login holds alone now (backend/login_holds.py), and the Gateway is one login there
(`login-hold:gateway|pi`). An older server kept the Gateway's cap and its address block only in the Rate limited status
of the bot whose turn met it, and an outage in a row per bot (`gateway-unavailable:<bot>`). Without this, a cap whose
month ends weeks from now would let every Tico turn try again at once after the upgrade.

1. A bot shown Rate limited whose latest limited turn (not a check's) ended on the Gateway's cap holds the Gateway until
   the next UTC month, and at least 30 minutes after that turn, as the older server's cooldown did. One that ended on
   the Gateway's rate limit holds it until the reset its runner reported, else an hour after the turn.
2. A `gateway-unavailable:<bot>` row holds it until that row's end. Every such row is deleted.
3. The latest of those ends still ahead is the Gateway's hold, unless a hold already saved ends later.

Running this again changes nothing: the rows are gone and a later or equal hold is kept.
"""

import json
from datetime import datetime, timedelta, timezone

KEY = "login-hold:gateway|pi"
LIMITED = (
    "SELECT a.id,a.bot_id,a.runner_id,a.finished_at,json_extract(a.result,'$.limit_resets_at') AS resets_at "
    "FROM attempts a WHERE a.bot_id=? AND a.state IN ('failed','interrupted') AND a.finished_at IS NOT NULL "
    "AND json_extract(a.result,'$.limited')=1 AND json_extract(a.result,'$.check') IS NULL "
    "ORDER BY a.finished_at DESC LIMIT 1"
)
ERRORS = (
    "SELECT payload FROM attempt_events WHERE attempt_id=? AND kind='error' "
    "AND json_extract(payload,'$.limit')=1 ORDER BY seq"
)


def _ts(text):
    try:
        at = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else None


def _iso(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _month_after(at):
    at = at.astimezone(timezone.utc)
    year, month = (at.year + 1, 1) if at.month == 12 else (at.year, at.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _status_wait(conn, bot):
    """The Gateway wait the older server kept in `bot`'s Rate limited status, as a hold, or None."""
    last = conn.execute(LIMITED, (bot,)).fetchone()
    finished = _ts(last[3]) if last else None
    if not finished:
        return None
    for (raw,) in conn.execute(ERRORS, (last[0],)).fetchall():
        try:
            text = str((json.loads(raw) or {}).get("error") or "")
        except (ValueError, AttributeError):
            continue
        if "cap_reached" in text:
            until = max(finished + timedelta(seconds=1800), _month_after(finished) + timedelta(seconds=1))
        elif "(rate_limited)" in text and "Tico AI Gateway" in text:
            reported = _ts(last[4])
            ok = reported and timedelta(0) < reported - finished <= timedelta(days=8)
            until = reported if ok else finished + timedelta(seconds=3600)
        else:
            continue
        return {
            "runner_id": last[2] or "",
            "since": _iso(finished),
            "until": _iso(until),
            "reset": True,
            "attempt_id": last[0],
            "bot": bot,
            "error": text[:300],
        }
    return None


def up(conn, now=None):
    now = _iso(now or datetime.now(timezone.utc))
    waits = []
    for (bot,) in conn.execute("SELECT bot FROM bot_status WHERE state='limited' ORDER BY bot").fetchall():
        wait = _status_wait(conn, bot)
        if wait:
            waits.append(wait)
    for key, raw in conn.execute(
        "SELECT key,value FROM registry_metadata WHERE key LIKE 'gateway-unavailable:%'"
    ).fetchall():
        try:
            row = json.loads(raw) or {}
        except ValueError:
            row = {}
        if isinstance(row, dict) and row.get("until"):
            waits.append(
                {
                    "runner_id": "",
                    "since": row.get("since") or now,
                    "until": str(row["until"]),
                    "reset": False,
                    "attempt_id": None,
                    "bot": key.split(":", 1)[1],
                    "error": "",
                }
            )
    conn.execute("DELETE FROM registry_metadata WHERE key LIKE 'gateway-unavailable:%'")
    ahead = [wait for wait in waits if wait["until"] > now]
    if not ahead:
        return
    wait = max(ahead, key=lambda w: w["until"])
    saved = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        old = json.loads(saved[0]) if saved else {}
    except ValueError:
        old = {}
    if isinstance(old, dict) and str(old.get("until") or "") >= wait["until"]:
        return
    hold = {
        **wait,
        "profile": "gateway",
        "runtime": "pi",
        "streak": 1,
        "capacity": False,
        "probe_job": None,
        "probe_at": None,
    }
    conn.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, json.dumps(hold)),
    )
