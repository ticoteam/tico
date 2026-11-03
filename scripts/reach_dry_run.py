#!/usr/bin/env python3
"""Reach dry run: what nearby reach (docs/reach.md) would have refused in a hub's recent history.

    python3 scripts/reach_dry_run.py --db HUB.db [--days 7] [--json] [--top 40]

It opens the database read-only and replays, oldest first, every bot write to a teammate in the last N days that
enforcement checks: the tasks a bot made for or handed to someone (task_cap_log, the log the task routes keep), and the
messages a bot sent that reached another teammate (message_deliveries addressed, mentioned, in a DM, on a Home or asked;
not digests, sub-thread followers or routing). Each goes through `reach.decide`, the one decision enforcement uses, with
the same facts: the task they share (as it stood then), whether the receiver wrote to the bot first, and today's counts
(taken from the replayed history itself). The chart, settings and lines are today's, so the report says what turning
nearby on now would refuse for work like that.

It reports counts by sender → receiver, how many land on people, and totals by kind. A refusal it reports may still be
one a system flow made in the bot's name (a routine posting as the bot): it errs toward reporting. Use a copy
(`python -m backend.devcopy`) or the live file; it never writes either.
"""
import argparse
import json
from contextlib import closing
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import reach as R  # noqa: E402
from backend import groups as G  # noqa: E402
from backend.store import H  # noqa: E402
from backend.updates import ZONE  # noqa: E402

CHECKED = ("dm", "mention", "ask", "assign", "addressed", "home")    # what threads.post's reach check covers
TS = "%Y-%m-%dT%H:%M:%S.%fZ"


def connect(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def has(c, table):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def context(c):
    """reach.context, or the same with no settings and lines on a hub from before the reach tables."""
    if has(c, "reach_settings"):
        return R.context(c)
    return {"c": c, "roster": G._roster(c), "configs": G._configs(c), "settings": {}, "lines": [], "owner": R.owner(c),
            "led": {}, "bots": {b["id"]: b for b in H.bots(c)}}


def day(ts):
    return H.parse_ts(ts).astimezone(ZONE).date().isoformat()


def task_writes(c, since):
    if not has(c, "task_cap_log"):
        return []
    out = []
    parent = ("(SELECT to_task FROM task_relations WHERE from_task=s.task_id AND kind='parent' LIMIT 1)"
              if has(c, "task_relations") else "NULL")
    for r in c.execute(f"SELECT s.task_id, s.sender, s.receiver, s.sent_at, t.requester_id, {parent} AS parent_id, "
                       "t.created_at FROM task_cap_log s LEFT JOIN tasks t ON t.id=s.task_id "
                       "WHERE s.sender LIKE 'bot:%' AND s.sent_at>=? ORDER BY s.sent_at", (since,)):
        made = r["requester_id"] == r["sender"] and (r["created_at"] or "")[:19] == r["sent_at"][:19]
        # A new task is checked against its parent (what the route does); a hand-off against the task itself.
        out.append({"kind": "task", "at": r["sent_at"], "sender": r["sender"], "receiver": r["receiver"],
                    "task_id": r["parent_id"] if made else r["task_id"], "what": "made" if made else "handed",
                    "conversation_id": None, "in_reply_to": None})
    return out


def chat_writes(c, since):
    if not has(c, "message_deliveries"):
        return []
    out = []
    marks = ",".join("?" * len(CHECKED))
    for r in c.execute(
            "SELECT m.id, m.sender_id, m.created_at, m.conversation_id, m.in_reply_to, v.task_id, v.owner_id, "
            "group_concat(DISTINCT d.recipient) AS receivers FROM messages m "
            "JOIN message_deliveries d ON d.message_id=m.id LEFT JOIN conversations v ON v.id=m.conversation_id "
            f"WHERE m.sender_id LIKE 'bot:%' AND m.created_at>=? AND d.reason IN ({marks}) "
            "AND d.recipient<>m.sender_id AND (d.recipient LIKE 'bot:%' OR d.recipient LIKE 'human:%') "
            "GROUP BY m.id ORDER BY m.created_at", (since, *CHECKED)):
        if r["owner_id"] == r["sender_id"]:
            continue                                    # the bot's own Home: its own place to talk
        for receiver in sorted(set(r["receivers"].split(","))):
            out.append({"kind": "chat", "at": r["created_at"], "sender": r["sender_id"], "receiver": receiver,
                        "task_id": r["task_id"], "what": "message", "conversation_id": r["conversation_id"],
                        "in_reply_to": r["in_reply_to"]})
    return out


def replay(c, ctx, writes):
    """Each write through reach.decide in order; counts per sender and per line build up as the history did."""
    sent, lined = Counter(), Counter()
    refused = []
    for w in sorted(writes, key=lambda w: w["at"]):
        today = day(w["at"])
        line = R.line_for(ctx, w["sender"], w["receiver"])

        def count(scope, w=w, today=today):
            if scope[0] == "line":
                return lined[(scope[1]["id"], today)]
            return sent[(w["sender"], scope[0], today)]

        found = R.decide(ctx, w["sender"], w["receiver"], w["kind"], count=count)
        if not found.allowed:
            shared = w["task_id"] and R.on_task(c, w["task_id"], w["sender"], w["receiver"], before=w["at"])
            asked = shared or R.solicited(c, w["sender"], w["receiver"], w["conversation_id"], w["in_reply_to"],
                                          before=w["at"])
            if not asked:
                refused.append({**w, "code": found.why, "via": found.via})
        sent[(w["sender"], w["kind"], today)] += 1
        if line is not None:
            lined[(line["id"], today)] += 1
    return refused


def report(ctx, writes, refused, days, top):
    pairs = Counter((r["sender"], r["receiver"]) for r in refused)
    people = [r for r in refused if r["receiver"].startswith("human:")]
    by_kind = Counter(f"{r['kind']}:{r['what']}" for r in refused)
    return {
        "days": days,
        "checked": len(writes),
        "refused": len(refused),
        "refused_to_people": len(people),
        "people_hit": len({r["receiver"] for r in people}),
        "by_kind": dict(by_kind),
        "by_code": dict(Counter(r["code"] for r in refused)),
        "senders": dict(Counter(R.name(ctx, r["sender"]) for r in refused).most_common(top)),
        "pairs": [{"sender": s, "receiver": rcv, "sender_name": R.name(ctx, s), "receiver_name": R.name(ctx, rcv),
                   "count": n, "via": R._managers(ctx).get(s)} for (s, rcv), n in pairs.most_common(top)],
    }


def text(out):
    lines = [f"Last {out['days']} days: {out['checked']} bot writes to teammates checked, {out['refused']} would be "
             f"refused ({out['refused_to_people']} to {out['people_hit']} people)."]
    if out["by_kind"]:
        lines.append("By kind: " + ", ".join(f"{k} {n}" for k, n in sorted(out["by_kind"].items())))
        lines.append("By code: " + ", ".join(f"{k} {n}" for k, n in sorted(out["by_code"].items())))
        lines.append("")
        lines.append(f"{'count':>6}  sender -> receiver (manager it would go through)")
        for p in out["pairs"]:
            via = f"  (via {p['via']})" if p["via"] else "  (no manager)"
            lines.append(f"{p['count']:>6}  {p['sender_name']} -> {p['receiver_name']}{via}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="the hub's SQLite file (opened read-only)")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=40, help="sender → receiver pairs to list")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime(TS)
    with closing(connect(args.db)) as c:
        ctx = context(c)
        writes = task_writes(c, since) + chat_writes(c, since)
        refused = replay(c, ctx, writes)
        out = report(ctx, writes, refused, args.days, args.top)
    print(json.dumps(out, indent=2) if args.json else text(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
