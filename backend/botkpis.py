"""Automatic bot KPIs: measures Tico computes from its own data, so no steward is needed.

Each bot has five, and each is a virtual KPI: nothing is stored, the value is worked out when it is
read, and it has an id (`auto:<bot>:<metric>`) that a goal links to like any other KPI, with a target
on the link. Their readings are the same measure at the end of each of the last fourteen days, so a
sparkline and a pace work the way they do for a KPI a person or the Strategy Bot keeps.

  tasks_done_7d       tasks the bot owns that were finished in the last seven days
  first_response_min  median minutes from a message to the bot to its first reply in that conversation (7 days)
  approval_rate_30d   the share of its approval requests a person approved, over the last thirty days
  failed_runs_7d      runs that ended failed in the last seven days
  cost_7d             what its runs cost in the last seven days, estimated from their tokens (usage.py); runs on a
                      subscription sign-in are left out

A metric with nothing to measure over its window (no message was sent to it, no approval was decided,
no run recorded a cost) has no reading, and no reading is missing data, never zero.
"""

import statistics
from datetime import datetime, timedelta, timezone

from . import hubdb as H

AUTO = "auto:"
DAYS = 14        # readings behind a sparkline: the measure at each of the last fourteen day-ends

METRICS = {
    "tasks_done_7d": {"name": "Tasks completed (7d)", "unit": "tasks", "direction": "up", "window": 7,
                      "definition": "Tasks this bot owns that were marked done in the last 7 days."},
    "first_response_min": {"name": "Median time to first response", "unit": "min", "direction": "down", "window": 7,
                           "definition": "Median minutes from a message to this bot to its first reply in that "
                                         "conversation, over the last 7 days."},
    "approval_rate_30d": {"name": "Approval rate (30d)", "unit": "%", "direction": "up", "window": 30,
                          "definition": "Share of this bot's approval requests that a person approved, of those "
                                        "decided in the last 30 days."},
    "failed_runs_7d": {"name": "Failed runs (7d)", "unit": "runs", "direction": "down", "window": 7,
                       "definition": "Runs of this bot that ended failed in the last 7 days."},
    "cost_7d": {"name": "Model cost (7d)", "unit": "$", "direction": "down", "window": 7,
                "definition": "Estimated list-price cost of this bot's runs in the last 7 days, from their tokens; runs on a "
                                    "ChatGPT or Claude sign-in are not counted."},
}


def ident(slug, key):
    return f"{AUTO}{slug}:{key}"


def parse(kpi_id):
    """(bot slug, metric key) of an auto KPI id, or None."""
    rest = str(kpi_id or "")[len(AUTO):] if str(kpi_id or "").startswith(AUTO) else ""
    slug, _, key = rest.rpartition(":")
    return (slug, key) if slug and key in METRICS else None


def _now():
    return H.parse_ts(H.now())


def _stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def private_history(conn, slug):
    actor = H.bot_actor(slug)
    # Mixed task/turn metrics have no stored participant provenance. Do not publish
    # a bot's aggregate history when it includes private work, even to its manager.
    return bool(conn.execute("SELECT 1 FROM tasks t WHERE (t.private IS NULL OR t.private<>0) AND "
                    "(t.owner=? OR t.requester=? OR t.carried_by IN (SELECT id FROM attempts WHERE bot=?) "
                    "OR t.id IN (SELECT " + H.MESSAGE_TASK_SQL + " FROM jobs j JOIN messages m ON m.id=j.message_id "
                    "JOIN conversations cv ON cv.id=m.conversation_id WHERE j.bot=?) "
                    "OR t.id IN (SELECT v.value FROM events e JOIN attempts a ON a.id=e.target "
                    "JOIN json_each(e.detail,'$.tasks') v WHERE a.bot=? AND e.action='task.next-run.carried')) LIMIT 1",
                    (actor, actor, slug, slug, slug)).fetchone())


def value(conn, slug, key, at=None, *, _privacy_checked=False):
    """The metric over the window that ends at `at`, or None when there was nothing to measure."""
    if not _privacy_checked and private_history(conn, slug):
        return None
    at = at or _now()
    end = _stamp(at)
    since = _stamp(at - timedelta(days=METRICS[key]["window"]))
    actor = H.bot_actor(slug)
    if key == "tasks_done_7d":
        return float(conn.execute("SELECT count(*) FROM tasks WHERE owner=? AND done_at>? AND done_at<=?",
                                  (actor, since, end)).fetchone()[0])
    if key == "first_response_min":
        gaps = []
        for created, answered in conn.execute(
                "SELECT m.created_at, (SELECT min(r.created_at) FROM messages r WHERE r.conversation_id=m.conversation_id "
                "AND r.from_actor=? AND r.created_at>m.created_at) FROM messages m WHERE m.to_actor=? AND m.from_actor!=? "
                "AND m.kind IN ('say','ask') AND m.created_at>? AND m.created_at<=? ORDER BY m.created_at DESC LIMIT 500",
                (actor, actor, actor, since, end)).fetchall():
            first, then = H.parse_ts(created), H.parse_ts(answered)
            if first and then:
                gaps.append((then - first).total_seconds() / 60)
        return round(statistics.median(gaps), 1) if gaps else None
    if key == "approval_rate_30d":
        rows = conn.execute("SELECT decision FROM approvals WHERE requested_by=? AND decision IN ('approved','declined') "
                            "AND decided_at>? AND decided_at<=?", (actor, since, end)).fetchall()
        return round(100 * sum(r[0] == "approved" for r in rows) / len(rows), 1) if rows else None
    if key == "failed_runs_7d":
        return float(conn.execute("SELECT count(*) FROM attempts WHERE bot=? AND state='failed' "
                                  "AND coalesce(finished_at, created_at)>? AND coalesce(finished_at, created_at)<=?",
                                  (slug, since, end)).fetchone()[0])
    # The estimate from each run's measured token counts (backend/usage.py).
    # A subscription run costs nothing extra, so only runs billed by the API count.
    row = conn.execute("SELECT sum(est_cost_usd), count(est_cost_usd) FROM turns "
                       "WHERE bot=? AND started_at>? AND started_at<=? AND usage_reason IS NULL "
                       "AND coalesce(billing, 'api') != 'subscription'",
                       (slug, since, end)).fetchone()
    return round(row[0], 4) if row and row[1] else None


def kpi(conn, kpi_id):
    """The virtual KPI as a row shaped like a stored one, or None for a bot or metric that does not exist."""
    parsed = parse(kpi_id)
    if not parsed or not H.bot(conn, parsed[0]):
        return None
    slug, key = parsed
    spec = METRICS[key]
    return {"id": kpi_id, "slug": f"{slug}-{key}", "goal_id": "", "name": spec["name"], "unit": spec["unit"],
            "direction": spec["direction"], "cadence": "daily", "owner": H.bot_actor(slug),
            "definition": spec["definition"], "source_note": "Computed from Tico's own data", "definition_version": 1,
            "created_at": None, "created_by": "keeper", "updated_at": None, "target": None}


def readings(conn, kpi_id, at=None):
    """The measure at the end of each of the last fourteen days, oldest first; days with nothing to measure
    are left out. Rows carry the fields a stored reading does."""
    parsed = parse(kpi_id)
    if not parsed:
        return []
    slug, key = parsed
    if private_history(conn, slug):
        return []
    at = at or _now()
    out = []
    for back in range(DAYS - 1, -1, -1):
        end = at - timedelta(days=back) if back else at
        amount = value(conn, slug, key, end, _privacy_checked=True)
        if amount is None:
            continue
        stamp = _stamp(end)
        out.append({"id": f"{kpi_id}@{stamp[:10]}", "kpi_id": kpi_id, "ts": stamp, "value": amount,
                    "actor": "keeper", "source": "tico", "note": "", "created_at": stamp, "period_start":
                    _stamp(end - timedelta(days=METRICS[key]["window"])), "period_end": stamp, "collected_at": stamp,
                    "evidence": "", "quality": "measured", "definition_version": 1, "supersedes": None,
                    "superseded_by": None})
    return out


def for_bot(conn, slug, at=None):
    """The five KPIs of one bot with today's value and the trend, for the bot page's row."""
    from . import kpis as K
    out = []
    for key in METRICS:
        record = kpi(conn, ident(slug, key))
        if record:
            out.append(K.view(record, readings(conn, record["id"], at), None, at))
    return out
