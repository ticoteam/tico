"""Learn scoring, pending Update deliveries and the Learnings page (docs/learnings.md)."""

from typing import Any
import difflib
import json
import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import Request

from clients import judge as J
from . import models as M
from . import task_privacy as privacy
from .harnesses import is_external
from .judge import over_spend_cap, primary_engine, server_engine
from .media import effective_audience
from .statuses import PARKED_SQL
from .store import H, Problem
from .updates import ZONE
from .views import human_only
from . import bot_settings as BS
from . import decide_groups as DG

LOG = logging.getLogger("tico.learnings")

SOURCES = ("chat", "task", "slack", "mail", "meeting")
HOUR = 3  # local hour of the demo's learning runs
LOOKBACK_HOURS = 24  # a source with no cursor yet is read this far back
ITEM_CHARS = 1500
MEETING_CHARS = 2000
MAX_ITEMS = 600  # per night; the oldest beyond it are dropped and counted
PER_RECIPIENT = 40
WORKERS = 6
# Items asked about in one call (backend/decide_groups.py). One until the replay through real Jev shows grouped
# verdicts match the ones given alone (no more than 2 in 100 changed); then DG.MOST.
GROUP = 1
QUESTION_SET = "learnings-route"
HELPERS = ("botops", "strategy-owner")  # with the assistant: they work for people, they are not the org chart
LIBRARIAN = "librarian"
LIBRARIAN_PROFILE = (
    "Librarian: how the company works (processes, policies, FAQ, docs) and the market "
    "(competitors, their products, pricing, launches)"
)
REL = "This item is relevant to: "
PAGE = 30
KIND_WORDS = {"chat": "Chat", "task": "Task", "slack": "Slack", "mail": "Email", "meeting": "Meeting"}
SAW_WORDS = {
    "task": ("task", "tasks"),
    "chat": ("chat", "chats"),
    "slack": ("Slack message", "Slack messages"),
    "mail": ("email", "emails"),
    "meeting": ("meeting", "meetings"),
}


# ----------------------------------------------------------------------------- time
def local(at):
    return at.astimezone(ZONE)


def night_of(at):
    return local(at).date().isoformat()


def day_words(ts):
    at = H.parse_ts(ts)
    return f"{local(at):%b} {local(at).day}" if at else ""


def utc(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def clip(text, n=ITEM_CHARS):
    text = str(text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def unquoted(body):
    """A sent mail without the message it replies to: the quoted lines and everything after `On … wrote:`."""
    out = []
    for line in str(body or "").splitlines():
        if re.match(r"^\s*On .{4,200} wrote:\s*$", line) or line.strip() in ("-----Original Message-----",):
            break
        if not line.lstrip().startswith(">"):
            out.append(line)
    return "\n".join(out).strip()


# ----------------------------------------------------------------------------- recipients
def roster(c, settings, now=None):
    """{id: {name, profile}}: the active bots that learn, the Librarian among them.

    A packet is work only a bot that runs can do. A bot on an external agent's platform never gets a
    run here, and one that had no run in the last day would only stack nights up unread: neither gets one.
    A stateless bot keeps nothing from one task to the next (hubdb.stateless), so a night's packet would be kept nowhere.
    """
    skip = {*HELPERS, settings.assistant_bot}
    since = utc(H.parse_ts(now or H.now()) - timedelta(hours=LOOKBACK_HOURS))
    ran = {r[0] for r in c.execute("SELECT DISTINCT bot_id FROM attempts WHERE started_at>=?", (since,))}
    out = {}
    for row in c.execute(
        "SELECT b.id,b.slug,b.display_name,coalesce(bc.description,'') AS description,"
        f"{BS.CONFIG} AS config FROM bots b "
        f"LEFT JOIN bot_config bc ON bc.bot=b.id {BS.JOIN} WHERE b.state='active' "
        f"AND coalesce(s.shared_from,'')='' "
        "AND coalesce(bc.onboarding_state,'') NOT IN " + PARKED_SQL + " ORDER BY b.slug"
    ):
        config = H._json(row["config"], {}) or {}
        if row["id"] in skip or row["id"] not in ran or is_external(config) or H.stateless(config):
            continue
        name = row["display_name"] or row["slug"]
        profile = (
            LIBRARIAN_PROFILE
            if row["id"] == LIBRARIAN
            else (f"{name}: {row['description']}" if row["description"].strip() else name)
        )
        out[row["id"]] = {"name": name, "profile": profile}
    return out


def names(c):
    people = {"human:" + r["id"]: r["name"] or r["id"] for r in c.execute("SELECT id,name FROM humans")}
    bots = {
        "bot:" + r["id"]: r["display_name"] or r["slug"] for r in c.execute("SELECT id,slug,display_name FROM bots")
    }
    return {**people, **bots, H.KEEPER: "Tico"}


# ----------------------------------------------------------------------------- collect
def cursors(c, now):
    start = utc(H.parse_ts(now) - timedelta(hours=LOOKBACK_HOURS))
    found = {r["source"]: r["last_ts"] for r in c.execute("SELECT source,last_ts FROM learning_cursors")}
    return {s: found.get(s) or start for s in SOURCES}


def _bots_in(participants, team):
    return {p[4:] for p in participants if str(p).startswith("bot:") and p[4:] in team}


def collect(c, settings, now, team):
    """Every item newer than its source's cursor, up to `now`, oldest first. One read snapshot."""
    since, who, items = cursors(c, now), names(c), []
    task_audience = {}

    def task_parties(tid):
        if tid not in task_audience:
            row = H.task(c, tid)
            if not row:
                task_audience[tid] = set()
            elif H.task_private(c, row):
                task_audience[tid] = {
                    a[4:] for a in (row["owner_id"], row["requester_id"]) if a and a.startswith("bot:")
                }
            else:
                task_audience[tid] = None
        return task_audience[tid]

    delivered = {r[0] for r in c.execute("SELECT task_id FROM learning_deliveries")}
    for m in c.execute(
        "SELECT m.id,m.conversation_id,m.sender_id,m.body,m.created_at,m.refs,cv.task_id AS conv_task,"
        "cv.participants,cv.subject FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE m.sender_id LIKE 'human:%' AND m.deleted_at IS NULL AND coalesce(m.kind,'')<>'notice' "
        "AND coalesce(cv.scope,'direct')<>'personal' AND m.created_at>? AND m.created_at<=? ORDER BY m.created_at",
        (min(since["chat"], since["task"]), now),
    ).fetchall():
        text = clip(m["body"])
        if not text:
            continue
        refs = H._json(m["refs"], {}) or {}
        tid = H.message_task_id({"refs": refs if isinstance(refs, dict) else {}}, {"task_id": m["conv_task"]})
        author = who.get(m["sender_id"], m["sender_id"])
        if tid:
            if m["created_at"] <= since["task"] or tid in delivered:
                continue
            task = H.task(c, tid)
            items.append(
                {
                    "key": "task:m:" + m["id"],
                    "source": "task",
                    "author": m["sender_id"],
                    "created_at": m["created_at"],
                    "ref": {"message": m["id"], "task": tid},
                    "label": f"{(task or {}).get('title') or 'Task'} · {author}",
                    "text": text,
                    "audience": task_parties(tid),
                }
            )
            continue
        if m["created_at"] <= since["chat"]:
            continue
        bots = _bots_in(H._json(m["participants"], []) or [], team)
        place = ("Chat with " + ", ".join(team[b]["name"] for b in sorted(bots))) if bots else (m["subject"] or "Chat")
        items.append(
            {
                "key": "chat:" + m["id"],
                "source": "chat",
                "author": m["sender_id"],
                "created_at": m["created_at"],
                "ref": {
                    "message": m["id"],
                    "conversation": m["conversation_id"],
                    **({"bot": next(iter(bots))} if len(bots) == 1 else {}),
                },
                "label": f"{place} · {author}",
                "text": text,
                "audience": bots or None,
            }
        )
    for t in c.execute(
        "SELECT id,title,note,owner_id,coalesce(done_at,closed_at) AS at FROM tasks "
        "WHERE status IN ('done','closed') AND trim(coalesce(note,''))<>'' "
        "AND coalesce(done_at,closed_at)>? AND coalesce(done_at,closed_at)<=? ORDER BY at",
        (since["task"], now),
    ).fetchall():
        if t["id"] in delivered:
            continue  # a packet's own "Nothing new" is not tomorrow's activity
        items.append(
            {
                "key": "task:" + t["id"],
                "source": "task",
                "author": t["owner_id"] or "",
                "created_at": t["at"],
                "ref": {"task": t["id"]},
                "label": f"{t['title']} · done",
                "text": clip(t["note"]),
                "audience": task_parties(t["id"]),
            }
        )
    channels = (
        {r["channel_id"]: r["name"] for r in c.execute("SELECT channel_id,name FROM slack_channels")}
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='slack_channels'").fetchone()
        else {}
    )
    for s in c.execute(
        "SELECT event_id,channel,channel_kind,thread_ts,ts,text,author_name,user_id,received_at "
        "FROM slack_events WHERE author='human' AND deleted_at IS NULL AND channel_kind<>'im' "
        "AND received_at>? AND received_at<=? ORDER BY received_at",
        (since["slack"], now),
    ).fetchall():
        text = clip(s["text"])
        if not text:
            continue
        place = "#" + (channels.get(s["channel"]) or s["channel"])
        items.append(
            {
                "key": "slack:" + s["event_id"],
                "source": "slack",
                "author": s["author_name"] or s["user_id"],
                "created_at": s["received_at"],
                "ref": {"channel": s["channel"], "ts": s["ts"], "thread_ts": s["thread_ts"]},
                "label": f"{place} · {s['author_name'] or 'Slack'}",
                "text": text,
                "audience": None,
            }
        )
    people = {}
    for r in c.execute("SELECT id,name,email FROM humans WHERE coalesce(email,'')<>''"):
        people.setdefault(r["email"].strip().lower(), r)
    if people:
        low, high = int(H.parse_ts(since["mail"]).timestamp()), int(H.parse_ts(now).timestamp())
        marks = ",".join("?" * len(people))
        for m in c.execute(
            "SELECT mailbox,msg_id,thread_id,epoch,from_addr,subject,body,to_addresses FROM mail_messages "
            f"WHERE deleted_at IS NULL AND lower(from_addr) IN ({marks}) AND lower(mailbox)=lower(from_addr) "
            "AND labels LIKE '%\"SENT\"%' AND epoch>? AND epoch<=? ORDER BY epoch",
            (*people, low, high),
        ).fetchall():
            person = people[m["from_addr"].strip().lower()]
            body = unquoted(m["body"])
            text = clip(f"{m['subject']}\n\n{body}" if m["subject"] else body)
            if not text:
                continue
            items.append(
                {
                    "key": f"mail:{m['mailbox']}:{m['msg_id']}",
                    "source": "mail",
                    "author": "human:" + person["id"],
                    "created_at": utc(datetime.fromtimestamp(m["epoch"], timezone.utc)),
                    "ref": {"mailbox": m["mailbox"].lower(), "msg_id": m["msg_id"], "thread": m["thread_id"]},
                    "label": f"{m['subject'] or 'Email'} · {person['name'] or person['id']}",
                    "text": text,
                    "audience": None,
                }
            )
    for r in c.execute(
        "SELECT m.id,m.title,m.notes,m.metadata,m.created_at FROM meetings m "
        "LEFT JOIN media_control mc ON mc.meeting_id=m.id WHERE m.review_state='live' "
        "AND mc.deleted_at IS NULL AND m.created_at>? AND m.created_at<=? ORDER BY m.created_at",
        (since["meeting"], now),
    ).fetchall():
        meta = H._json(r["metadata"], {}) or {}
        if meta.get("kind") != "meeting" or effective_audience(meta) != "team":
            continue
        summary = meta.get("source_summary") or r["notes"] or ""
        text = clip(f"{r['title']}\n\n{summary}".strip(), MEETING_CHARS)
        items.append(
            {
                "key": "meeting:" + r["id"],
                "source": "meeting",
                "author": "",
                "created_at": r["created_at"],
                "ref": {"meeting": r["id"]},
                "label": r["title"] or "Meeting",
                "text": text,
                "audience": None,
            }
        )
    items.sort(key=lambda i: i["created_at"])
    dropped = max(0, len(items) - MAX_ITEMS)
    return items[dropped:], dropped


# ----------------------------------------------------------------------------- route
def engine_for(store, c):
    """The server's decision model (backend/judge.py `server_engine`): Jev on the Tico AI Gateway, then on an own
    TypeSafe key, each in one short try; None when there is neither, or the month's decision spend is at its cap."""
    return server_engine(store, c, primary_engine(store.settings, store))


class Tally:
    """An engine that adds up what its calls cost, for the run's one audit row. Calls run on several threads."""

    def __init__(self, engine):
        self.engine, self.lock = engine, threading.Lock()
        self.calls, self.fallbacks, self.models = 0, 0, Counter()
        self.usage, self.cost, self.routes, self.fallback = Counter(), 0.0, Counter(), None
        self.answers = []  # one `J.summary` per call, for the readout's answer spread

    def __call__(self, state, questions, label=None):
        try:
            result = self.engine(state, questions, label)
        except Exception as exc:
            with self.lock:
                self.cost += getattr(exc, "cost_usd", 0.0) or 0.0
            raise
        with self.lock:
            self.calls += 1
            self.fallbacks += bool(result.get("fallback"))
            from .thread_rank import route_of

            self.routes[route_of(result)] += 1
            if result.get("fallback") and not self.fallback:
                self.fallback = {
                    "code": result["fallback"],
                    "from": route_of({"provider": result.get("fallback_from")}),
                }
            self.models[str(result.get("model") or "")] += 1
            self.usage.update(
                {k: v for k, v in (result.get("usage") or {}).items() if isinstance(v, int) and not isinstance(v, bool)}
            )
            self.cost += result.get("cost_usd") or 0.0
            self.answers.append(J.summary(result.get("answers")))
        return result

    def detail(self):
        return {
            "calls": self.calls,
            "fallbacks": self.fallbacks,
            "models": dict(self.models),
            "model": self.models.most_common(1)[0][0] if self.models else "",
            "usage": dict(self.usage),
            "cost_usd": round(self.cost, 6),
            "answers": list(self.answers),
        }


def question_set(settings):
    return J.load_set(QUESTION_SET, registry_dir=settings.registry_dir)


def _calls(qset, item, team):
    """[(recipients, questions, state)] for one item, asking only about the recipients it may reach: every recipient in
    one call, so the item's text is read once; more calls only when the recipients' profiles do not fit beside it in
    one (`J.pack`), with `learnable` asked in the first. [] when it may reach no one."""
    audience = [s for s in team if item["audience"] is None or s in item["audience"]]
    if not audience:
        return []
    learnable_q = qset["questions"]["learnable"]
    text = {"source": item["source"], "label": item["label"], "text": item["text"]}

    def build(chunk):
        # `learnable` goes in the call that starts with the first recipient only, so `J.pack` sizes the later calls
        # without it and makes the fewest calls (79 recipients: 40 + 39 questions, not 40 + 39 + 1).
        questions = {"learnable": learnable_q} if chunk[0] == audience[0] else {}
        questions.update({"rel_" + s: {"type": "noul", "instructions": REL + team[s]["profile"]} for s in chunk})
        return questions, {"item": text, "roster": {s: team[s]["profile"] for s in chunk}}

    return J.pack(audience, build) or [(audience, *build(audience))]  # too big even alone: the call refuses it


def _read(calls, replies):
    """(learnable, {id: relevance}) from each call's answers, in `_calls` order."""
    learnable, scores = None, {}
    for n, ((chunk, _questions, _state), answers) in enumerate(zip(calls, replies, strict=True)):
        if n == 0:
            learnable = J.noul(answers, "learnable")
        scores.update({s: round(J.noul(answers, "rel_" + s), 3) for s in chunk})
    return round(learnable, 3), scores


def score(engine, qset, item, team):
    """(learnable, {id: relevance}) for one item, asked on its own (`_calls`)."""
    calls = _calls(qset, item, team)
    if not calls:
        return None, {}
    return _read(calls, [engine(state, questions, qset["label"])["answers"] for _chunk, questions, state in calls])


def route(engine, qset, items, team) -> dict[str, int]:
    """Score every item, calls in parallel. An item that fits one call alone shares a call with up to GROUP-1 other
    such items (backend/decide_groups.py: each item gets only its own answers, a missing answer is asked again alone);
    an item whose recipients need more than one call is asked on its own, as before. A failed call skips its items;
    the run goes on."""
    results: list[tuple[Any, Any]] = [((None, {}), None)] * len(items)
    alone, pairs, places = [], [], []
    for n, item in enumerate(items):
        calls = _calls(qset, item, team)
        if len(calls) == 1:
            _chunk, questions, state = calls[0]
            pairs.append((state, questions))
            places.append((n, calls))
        elif calls:
            alone.append(n)

    def one(n):
        try:
            return n, score(engine, qset, items[n], team), None
        except Exception as exc:  # noqa: BLE001 - one item's failure never stops the night
            return n, (None, {}), getattr(exc, "code", type(exc).__name__)

    def run(chunk):
        out = []
        replies = DG.ask_run(engine, [pairs[k] for k in chunk], qset["label"])
        for k, reply in zip(chunk, replies, strict=True):
            n, calls = places[k]
            if isinstance(reply, BaseException):
                out.append((n, (None, {}), getattr(reply, "code", type(reply).__name__)))
                continue
            try:
                out.append((n, _read(calls, [reply["answers"]]), None))
            except Exception as exc:  # noqa: BLE001 - an answer that cannot be read is that item's failure
                out.append((n, (None, {}), getattr(exc, "code", type(exc).__name__)))
        return out

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        jobs = [pool.submit(run, chunk) for chunk in DG.packs(pairs, GROUP)]
        jobs += [pool.submit(lambda n=n: [one(n)]) for n in alone]
        for job in jobs:
            for n, scored, err in job.result():
                results[n] = (scored, err)
    failed = Counter(err for _, err in results if err)
    for item, ((learnable, scores), err) in zip(items, results, strict=True):
        item["learnable"], item["scores"], item["failed"] = learnable, scores, bool(err)
    return dict(failed)


def packets(items, qset, limit=None):
    """{id: [item, ...]}: every recipient at or over the relevance bar, unless the item is noise."""
    bar, floor = qset["thresholds"].get("relevant", 0.6), qset["thresholds"].get("noise", 0.2)
    cap = int(qset["thresholds"].get("per_recipient", PER_RECIPIENT)) if limit is None else limit
    out = {}
    for item in items:
        if item["learnable"] is None or item["learnable"] < floor:
            continue
        for slug, rel in item["scores"].items():
            if rel >= bar:
                out.setdefault(slug, []).append(item)
    return {slug: sorted(rows, key=lambda i: (-i["scores"][slug], i["created_at"]))[:cap] for slug, rows in out.items()}


# ----------------------------------------------------------------------------- dispatch
def link(item):
    ref = item["ref"] if isinstance(item["ref"], dict) else {}
    if ref.get("task"):
        return f"#/task/{ref['task']}"
    if ref.get("meeting"):
        return f"#/meetings?meeting={ref['meeting']}"
    if ref.get("mailbox"):
        return f"#/mail?mailbox={ref['mailbox']}&thread={ref.get('thread') or ''}&tbox={ref['mailbox']}"
    if ref.get("channel"):
        from .slack_gateway import permalink

        return permalink(None, ref["channel"], ref["ts"], ref.get("thread_ts"))
    if ref.get("bot"):
        return f"#/bot/{ref['bot']}"
    return ""


def dispatch(c, run_id, night, items, chosen, team, detail):
    """Keep each bot's top items for its next Update, without waking it."""
    chosen = {slug: rows[:15] for slug, rows in chosen.items()}
    routed = {item["key"]: item for rows in chosen.values() for item in rows}
    for item in routed.values():
        c.execute(
            "INSERT OR IGNORE INTO learning_items(run_id,item_key,source,ref,author,label,text,created_at,"
            "learnable,scores) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                item["key"],
                item["source"],
                json.dumps(item["ref"]),
                item["author"],
                item["label"],
                item["text"],
                item["created_at"],
                item["learnable"],
                json.dumps(item["scores"]),
            ),
        )
    for slug, rows in chosen.items():
        c.execute(
            "INSERT OR IGNORE INTO learning_deliveries(run_id,recipient,item_keys,created_at) VALUES(?,?,?,?)",
            (run_id, slug, json.dumps([i["key"] for i in rows]), H.now()),
        )
    detail.update(items_read=len(items), items_sent={}, saved=0, dropped=0, cost_usd=detail.get("cost_usd", 0.0))
    return len(chosen), len(routed)


def shared_item(c, item, bot):
    """Recheck a source before putting its words in a company Update session."""
    from . import teammate_access, threads, updates

    ref = H._json(item["ref"], {}) if isinstance(item["ref"], str) else item["ref"]
    shared = updates.shared_tasks_sql(updates._hidden_for(c, bot))
    ids = {ref["task"]} if ref.get("task") else set()
    if ref.get("message"):
        message = H.message(c, ref["message"])
        if not message or message.get("deleted_at"):
            return False
        room = threads.root_of(c, message["conversation_id"])
        if not room:
            return False
        audience = threads.audience_of(room)
        if audience == "home":
            owner = threads.home_owner(room) or ""
            if (
                not owner.startswith("bot:")
                or not H.bot(c, owner[4:])
                or not teammate_access.document(c, owner)["read"]["everyone"]
            ):
                return False
        elif audience == "task":
            if not room.get("task_id"):
                return False
            ids.add(room["task_id"])
        elif audience != "company":
            return False
        if threads.message_narrower(c, message):
            return False
        try:
            ids.update(privacy.message_tasks(c, message, current=True))
        except Problem:
            return False
    for tid in ids:
        if not c.execute(f"SELECT 1 FROM tasks WHERE id=? AND {shared}", (tid,)).fetchone():
            return False
    if item["source"] in ("task", "chat"):
        return bool(ids or ref.get("message"))
    if item["source"] == "slack":
        return bool(
            c.execute(
                "SELECT 1 FROM slack_events WHERE channel=? AND ts=? AND deleted_at IS NULL "
                "AND channel_kind='channel' AND event_type='message.channels'",
                (ref.get("channel"), ref.get("ts")),
            ).fetchone()
        )
    if item["source"] == "mail":
        return False  # Mailbox grants never establish a company-wide audience.
    if item["source"] == "meeting":
        row = c.execute(
            "SELECT m.metadata FROM meetings m LEFT JOIN media_control mc ON mc.meeting_id=m.id "
            "WHERE m.id=? AND m.review_state='live' AND mc.deleted_at IS NULL "
            "AND json_extract(m.metadata,'$.kind')='meeting'",
            (ref.get("meeting"),),
        ).fetchone()
        return bool(row and effective_audience(H._json(row['metadata'], {})) == "team")
    return False


def pending(c, bot, batch_id=None):
    """The top fifteen items across pending runs, or the items already sent in this batch."""
    now = H.now()
    since = H.shift(now, days=-7)
    # Read-only previews exclude old items too; only the store's write transactions record expiry.
    if getattr(c, "write_transaction", False):
        for row in c.execute(
            "SELECT run_id FROM learning_deliveries WHERE recipient=? AND task_id IS NULL "
            "AND update_batch_id IS NULL AND expired_at IS NULL AND created_at<? "
            "ORDER BY created_at LIMIT 100",
            (bot, since),
        ).fetchall():
            c.execute(
                "UPDATE learning_deliveries SET expired_at=? WHERE run_id=? AND recipient=?", (now, row["run_id"], bot)
            )
            refresh_details(c, row["run_id"])
    sent = (
        batch_id
        and c.execute(
            "SELECT 1 FROM learning_deliveries WHERE recipient=? AND update_batch_id=? LIMIT 1", (bot, batch_id)
        ).fetchone()
    )
    where = (
        "d.update_batch_id=?" if sent else ("d.update_batch_id IS NULL AND d.expired_at IS NULL AND d.created_at>=?")
    )
    keys = "d.sent_item_keys" if sent else "d.item_keys"
    rows = c.execute(
        "SELECT i.* FROM learning_deliveries d JOIN learning_items i ON i.run_id=d.run_id "
        f"JOIN json_each({keys}) k ON k.value=i.item_key WHERE d.recipient=? AND d.task_id IS NULL AND {where} "
        "ORDER BY coalesce(json_extract(i.scores,?),0) DESC,i.created_at,i.run_id,i.item_key LIMIT 100",
        (bot, batch_id if sent else since, '$.' + json.dumps(bot)),
    )
    out = []
    for row in rows:
        if shared_item(c, row, bot):
            item = dict(row)
            item["ref"] = json.loads(item["ref"])
            text = clip(" ".join(item["text"].split()), 500)
            label = clip(" ".join(item["label"].split()), 160)
            item["line"] = f"{label}: {text}" + (f" [source: {link(item)}]" if link(item) else "")
            out.append(item)
            if len(out) == 15:
                break
    return out


def delivery_details(c, run_id):
    """Counts only; an absent report stays unknown instead of counting as dropped."""
    sent, saved, dropped, unreported, not_sent, expired = {}, 0, 0, 0, 0, 0
    for row in c.execute("SELECT * FROM learning_deliveries WHERE run_id=? AND task_id IS NULL", (run_id,)):
        if row["expired_at"] is not None:
            expired += len(json.loads(row["item_keys"]))
            continue
        count = len(json.loads(row["sent_item_keys"] or "[]"))
        if row["update_batch_id"] is not None:
            sent[row["recipient"]] = count
            not_sent += len(json.loads(row["item_keys"])) - count
        if row["learnings_saved"] is None:
            unreported += count
        else:
            saved += row["learnings_saved"]
            dropped += count - row["learnings_saved"]
    return {
        "items_sent": sent,
        "saved": saved,
        "dropped": dropped,
        "unreported": unreported,
        "not_sent": not_sent,
        "expired": expired,
    }


def refresh_details(c, run_id):
    row = c.execute("SELECT detail FROM learning_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return
    counts = delivery_details(c, run_id)
    detail = {**json.loads(row["detail"]), **counts}
    c.execute("UPDATE learning_runs SET detail=? WHERE id=?", (json.dumps(detail), run_id))
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_run_steps'").fetchone():
        for step in c.execute(
            "SELECT id,details FROM maintenance_run_steps WHERE maintenance_run_id=? AND step='learn'", (run_id,)
        ).fetchall():
            c.execute(
                "UPDATE maintenance_run_steps SET details=? WHERE id=?",
                (json.dumps({**json.loads(step["details"] or "{}"), **counts}), step["id"]),
            )


def mark_sent(c, bot, batch_id, message_id, items):
    """Bind the exact request and its items in the message's transaction; retries keep that batch."""
    if c.execute(
        "SELECT 1 FROM learning_deliveries WHERE recipient=? AND update_batch_id=? LIMIT 1", (bot, batch_id)
    ).fetchone():
        # A retry reports on its own turn; the original message still anchors history.
        c.execute(
            "UPDATE learning_deliveries SET feedback_message_id=?, "
            "settled_at=CASE WHEN learnings_saved IS NULL THEN NULL ELSE settled_at END "
            "WHERE recipient=? AND update_batch_id=?",
            (message_id, bot, batch_id),
        )
        return
    by_run = {}
    for item in items[:15]:
        by_run.setdefault(item["run_id"], []).append(item["item_key"])
    for row in c.execute(
        "SELECT run_id FROM learning_deliveries WHERE recipient=? AND task_id IS NULL "
        "AND update_batch_id IS NULL AND expired_at IS NULL AND created_at>=?",
        (bot, H.shift(H.now(), days=-7)),
    ).fetchall():
        keys = by_run.get(row["run_id"], [])
        c.execute(
            "UPDATE learning_deliveries SET update_batch_id=?,message_id=?,feedback_message_id=?,sent_item_keys=?,sent_at=? "
            "WHERE run_id=? AND recipient=?",
            (batch_id, message_id, message_id, json.dumps(keys), H.now() if keys else None, row["run_id"], bot),
        )
        refresh_details(c, row["run_id"])


def record_saved(c, bot, batch_id, saved):
    """A batch reports a count, so attribute saved items to its oldest runs first."""
    rows = c.execute(
        "SELECT * FROM learning_deliveries WHERE recipient=? AND update_batch_id=? "
        "AND task_id IS NULL ORDER BY created_at,run_id",
        (bot, batch_id),
    ).fetchall()
    total = sum(len(json.loads(r["sent_item_keys"] or "[]")) for r in rows)
    if (
        not rows
        or any(r["learnings_saved"] is not None for r in rows)
        or type(saved) is not int
        or not 0 <= saved <= total
    ):
        return
    for row in rows:
        kept = min(saved, len(json.loads(row["sent_item_keys"] or "[]")))
        saved -= kept
        c.execute(
            "UPDATE learning_deliveries SET learnings_saved=?,settled_at=? WHERE run_id=? AND recipient=?",
            (kept, H.now(), row["run_id"], bot),
        )
        refresh_details(c, row["run_id"])


def settle_delivery(c, bot, batch_id):
    """A completed turn with no count stays unreported; a later structured receipt can still fill it."""
    c.execute(
        "UPDATE learning_deliveries SET settled_at=coalesce(settled_at,?) "
        "WHERE recipient=? AND update_batch_id=? AND task_id IS NULL",
        (H.now(), bot, batch_id),
    )


def beta_stats(c, days=7):
    """Learn's recent reads, deliveries, outcomes and cost, without source text."""
    days = max(1, min(int(days), 30))
    since = utc(H.parse_ts(H.now()) - timedelta(days=days))
    stats = {
        "days": days,
        "runs": 0,
        "items_read": 0,
        "items_sent": {},
        "saved": 0,
        "dropped": 0,
        "unreported": 0,
        "not_sent": 0,
        "expired": 0,
        "cost_usd": 0.0,
    }
    for row in c.execute("SELECT id,read_count,detail FROM learning_runs WHERE started_at>=?", (since,)):
        detail = json.loads(row["detail"])
        stats["runs"] += 1
        stats["items_read"] += row["read_count"]
        stats["cost_usd"] += detail.get("cost_usd", 0.0)
        counts = delivery_details(c, row["id"])
        for bot, count in counts["items_sent"].items():
            stats["items_sent"][bot] = stats["items_sent"].get(bot, 0) + count
        for key in ("saved", "dropped", "unreported", "not_sent", "expired"):
            stats[key] += counts[key]
    stats["cost_usd"] = round(stats["cost_usd"], 6)
    return stats


def advance(c, items, now):
    newest = {}
    for item in items:
        newest[item["source"]] = max(newest.get(item["source"], ""), item["created_at"])
    for source in SOURCES:
        c.execute(
            "INSERT INTO learning_cursors(source,last_ts) VALUES(?,?) ON CONFLICT(source) DO UPDATE SET "
            "last_ts=max(learning_cursors.last_ts,excluded.last_ts)",
            (source, newest.get(source) or now),
        )


def finish(store, run_id, status, detail, read=0, routed=0):
    with store.transaction() as c:
        c.execute(
            "UPDATE learning_runs SET status=?,finished_at=?,read_count=?,routed_count=?,detail=? WHERE id=?",
            (status, H.now(), read, routed, json.dumps(detail), run_id),
        )


def run(store, run_id):
    """Collect, route, dispatch. The decision model is called with no transaction open."""
    settings, started = store.settings, time.monotonic()
    detail = {}
    try:
        with store.read() as c:
            c.execute("BEGIN")
            row = c.execute("SELECT night,status FROM learning_runs WHERE id=?", (run_id,)).fetchone()
            if not row or row["status"] != "running":
                return
            night, now = row["night"], H.now()
            engine = engine_for(store, c)
            capped = engine is None and over_spend_cap(c, settings) is not None
            team = roster(c, settings, now)
            if engine is not None:
                items, dropped = collect(c, settings, now, team)
            c.execute("COMMIT")
        if engine is None:
            finish(
                store,
                run_id,
                *(
                    ("capped", {"reason": "monthly decision spend cap"})
                    if capped
                    else ("unconfigured", {"reason": "no decision model"})
                ),
            )
            return
        detail = {"read": dict(Counter(i["source"] for i in items)), "source_dropped": dropped, "recipients": len(team)}
        chosen, failed = {}, {}
        if items:
            qset = question_set(settings)
            routed_at = time.monotonic()
            engine = Tally(engine)
            failed = route(engine, qset, items, team)
            chosen = packets(items, qset)
            detail.update(
                {
                    "failed": failed,
                    "route_ms": round((time.monotonic() - routed_at) * 1000),
                    "routes": dict(engine.routes),
                    "fallback": engine.fallback,
                    "cost_usd": round(engine.cost, 6),
                }
            )
        capped = bool(failed.get("spend_cap"))
        with store.transaction() as c:
            if c.execute("SELECT status FROM learning_runs WHERE id=?", (run_id,)).fetchone()["status"] != "running":
                return
            if capped:
                # The month's spend reached the cap during the night: carry on as if Decide were off. Nothing is
                # sent and the cursors stay, so the same items are read again once calls are allowed.
                chosen, detail["reason"] = {}, "monthly decision spend cap"
            delivered, routed = dispatch(c, run_id, night, items, chosen, team, detail) if chosen else (0, 0)
            if not capped:
                advance(c, items, now)
            if items:
                # One audit row for the night's calls, as POST /api/v2/decisions keeps one per call: counts, never text.
                H.event(
                    c,
                    H.KEEPER,
                    "judge.call",
                    f"{QUESTION_SET}@run",
                    {
                        "label": f"{QUESTION_SET}@run",
                        "run": run_id,
                        "items": len(items),
                        "failed": failed,
                        "routed": routed,
                        "deliveries": delivered,
                        "ms": detail.get("route_ms", 0),
                        **(engine.detail() if isinstance(engine, Tally) else {}),
                    },
                )
            detail["deliveries"] = delivered
            detail["ms"] = round((time.monotonic() - started) * 1000)
            c.execute(
                "UPDATE learning_runs SET status=?,finished_at=?,read_count=?,routed_count=?,detail=? WHERE id=?",
                (
                    "capped" if capped else "done" if delivered else "quiet",
                    H.now(),
                    len(items),
                    routed,
                    json.dumps(detail),
                    run_id,
                ),
            )
    except Exception as exc:
        LOG.exception("Nightly learning run %s failed", run_id)
        finish(store, run_id, "failed", {**detail, "error": type(exc).__name__})


def claim(c, night):
    """A new running row for this night, or None when the night already has one."""
    if c.execute("SELECT 1 FROM learning_runs WHERE night=?", (night,)).fetchone():
        return None
    run_id = H.new_id()
    c.execute(
        "INSERT INTO learning_runs(id,night,status,started_at) VALUES(?,?,?,?)", (run_id, night, "running", H.now())
    )
    return run_id


# ----------------------------------------------------------------------------- the page
SOURCE_RE = re.compile(r"\[source:\s*([^\];]+)")
KIND_RE = re.compile(r"^(chat|task|slack|mail|email|meeting|doc|commit)\b", re.I)


def run_attempts(c, task_id):
    """The runs that worked this task: started by one of its messages, or handed one as an input."""
    if not task_id:
        return []
    return [
        r[0]
        for r in c.execute(
            "SELECT a.id FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
            f"LEFT JOIN conversations cv ON cv.id=m.conversation_id WHERE {H.MESSAGE_TASK_SQL}=? UNION "
            "SELECT i.attempt_id FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
            f"LEFT JOIN conversations cv ON cv.id=m.conversation_id WHERE {H.MESSAGE_TASK_SQL}=?",
            (task_id, task_id),
        )
    ]


def delivery_attempts(c, delivery):
    if delivery["task_id"]:
        return run_attempts(c, delivery["task_id"])
    # The receipt keeps its original message after an Update redo or queue cleanup.
    return [
        r[0]
        for r in c.execute(
            "SELECT a.id FROM jobs j "
            "JOIN attempts a ON a.job_id=j.id OR a.id=j.attempt_id WHERE j.message_id=? AND a.bot_id=?",
            (delivery["message_id"], delivery["recipient"]),
        )
    ]


def sources_of(diff):
    out = []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for found in SOURCE_RE.findall(line):
            text = found.strip()
            kind = KIND_RE.match(text)
            url = re.search(r"(#/[\w/?=&.%-]+|https?://\S+)", text)
            quote = re.search(r"[\"“]([^\"”]{3,})[\"”]", text)
            out.append(
                {
                    "kind": (kind.group(1).lower().replace("email", "mail") if kind else ""),
                    "label": text,
                    "quote": quote.group(1) if quote else "",
                    "link": url.group(1) if url else "",
                }
            )
    return out


def line_diff(old, new):
    return "\n".join(
        l
        for l in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1)
        if not l.startswith(("---", "+++"))
    )


def window(c, delivery):
    if delivery["task_id"]:
        task = H.task(c, delivery["task_id"]) or {}
        return delivery["created_at"], task.get("done_at") or task.get("closed_at") or H.now()
    turn = c.execute(
        "SELECT m.created_at,max(coalesce(a.finished_at,?)) AS finished_at FROM messages m "
        "LEFT JOIN jobs j ON j.message_id=m.id AND j.bot_id=? "
        "LEFT JOIN attempts a ON (a.job_id=j.id OR a.id=j.attempt_id) AND a.bot_id=? "
        "WHERE m.id=? GROUP BY m.id",
        (H.now(), delivery["recipient"], delivery["recipient"], delivery["message_id"]),
    ).fetchone()
    return (turn["created_at"], turn["finished_at"]) if turn else ("9999", "9999")


def saw(c, run_id, keys):
    if not keys:
        return ""
    marks = ",".join("?" * len(keys))
    counts = Counter(
        r[0]
        for r in c.execute(
            f"SELECT source FROM learning_items WHERE run_id=? AND item_key IN ({marks})", (run_id, *keys)
        )
    )
    parts = [f"{n} {SAW_WORDS[s][n != 1]}" for s, n in counts.most_common() if s in SAW_WORDS]
    return "Saw " + ", ".join(parts) if parts else ""


def bot_entries(c, delivery):
    ids = delivery_attempts(c, delivery)
    if not ids:
        return []
    rows = c.execute(
        f"SELECT * FROM bot_memory_updates WHERE attempt_id IN ({','.join('?' * len(ids))}) ORDER BY committed_at", ids
    ).fetchall()
    return [
        {
            "title": r["subject"],
            "sources": sources_of(r["diff"]),
            "change": {"where": ", ".join(json.loads(r["files"])), "diff": r["diff"]},
        }
        for r in rows
    ]


def doc_subjects(c, delivery, saw_text):
    begin, end = window(c, delivery)
    out = {}
    for v in c.execute(
        "SELECT * FROM doc_versions WHERE actor=? AND created_at>=? AND created_at<=? ORDER BY created_at",
        ("bot:" + LIBRARIAN, begin, end),
    ).fetchall():
        prev = c.execute(
            "SELECT body FROM doc_versions WHERE doc_id=? AND version<? ORDER BY version DESC LIMIT 1",
            (v["doc_id"], v["version"]),
        ).fetchone()
        subject = out.setdefault(
            v["doc_id"],
            {"id": v["doc_id"], "name": v["title"], "saw": saw_text, "task_id": delivery["task_id"], "entries": []},
        )
        subject["entries"].append(
            {
                "title": v["note"] or v["title"],
                "sources": [],
                "change": {
                    "where": f"{v['title']} · v{v['version']}",
                    "diff": line_diff(prev["body"] if prev else "", v["body"]),
                },
            }
        )
    return list(out.values())


def market_subjects(c, delivery, saw_text):
    from . import market

    begin, end = window(c, delivery)
    out = {}
    for e in c.execute(
        f"SELECT {market.EVENT_COLUMNS} FROM history_events WHERE actor_id=? AND created_at>=? "
        "AND created_at<=? AND subject_type IN ('market_entity','market_edge') ORDER BY created_at",
        ("bot:" + LIBRARIAN, begin, end),
    ).fetchall():
        entity_id = (
            e["subject_id"] if e["subject_kind"] == "entity" else (market.edge(c, e["subject_id"]) or {}).get("src")
        )
        entity = market.entity(c, entity_id) if entity_id else None
        if not entity:
            continue
        subject = out.setdefault(
            entity["id"],
            {
                "id": entity["id"],
                "name": entity["name"],
                "saw": saw_text,
                "task_id": delivery["task_id"],
                "entries": [],
            },
        )
        lines = ([f"-{e['old']}"] if e["old"] not in (None, "") else []) + (
            [f"+{e['new']}"] if e["new"] not in (None, "") else []
        )
        subject["entries"].append(
            {
                "title": e["note"] or f"{entity['name']}: {e['field']}",
                "sources": [],
                "change": {"where": f"{entity['name']} · {e['field']}", "diff": "\n".join(lines)},
            }
        )
    return list(out.values())


def _items(n):
    return f"{n} item" if n == 1 else f"{n} items"


def night_view(c, auth, who, run, team_names):
    sections = {"bots": [], "docs": [], "market": []}
    for d in c.execute("SELECT * FROM learning_deliveries WHERE run_id=? ORDER BY recipient", (run["id"],)).fetchall():
        keys = json.loads(d["item_keys"])
        saw_text = saw(c, run["id"], keys)
        if d["recipient"] == LIBRARIAN:
            sections["docs"] += doc_subjects(c, d, saw_text)
            sections["market"] += market_subjects(c, d, saw_text)
        if not auth.bot_access(c, who, d["recipient"])["read"]:
            continue
        entries = bot_entries(c, d)
        title = ""
        if not d["task_id"]:
            count = len(json.loads(d["sent_item_keys"] or "[]"))
            title = (
                f"{_items(count)} sent in Update" if d["update_batch_id"] else f"{_items(len(keys))} waiting for Update"
            )
            if d["expired_at"] is not None:
                title = f"{_items(len(keys))} expired"
            if d["learnings_saved"] is not None:
                title += f"; {d['learnings_saved']} saved, {count - d['learnings_saved']} dropped"
        if entries or not d["task_id"]:
            sections["bots"].append(
                {
                    "id": d["recipient"],
                    "name": team_names.get("bot:" + d["recipient"], d["recipient"]),
                    "saw": saw_text,
                    "task_id": d["task_id"],
                    "entries": entries,
                    "delivery_status": title,
                }
            )
    return sections


def readable(c, auth, who, item, mailboxes):
    """Whether this reader may read the item's source today; the packet shows its text only then."""
    from . import media

    ref = json.loads(item["ref"])
    try:
        if item["source"] in ("chat", "task") and ref.get("message"):
            message = H.message(c, ref["message"])
            if not message or not privacy.message_readable(c, privacy.actor(who), message):
                return False
            if item["source"] == "chat":
                auth.conversation(c, who, message["conversation_id"])
                return True
        if ref.get("task"):
            task = H.task(c, ref["task"])
            return bool(task) and privacy.task_readable(c, who, task)
        if item["source"] == "mail":
            return ref.get("mailbox") in mailboxes()
        if item["source"] == "meeting":
            media.authorized(c, who, ref["meeting"])
            return True
        if item["source"] == "slack":
            return not c.execute(
                "SELECT 1 FROM slack_events WHERE channel=? AND ts=? AND deleted_at IS NOT NULL",
                (ref.get("channel"), ref.get("ts")),
            ).fetchone()
    except Problem:
        return False
    return False


def install(app, store, auth, mutate):
    @app.post("/api/v2/learnings/run")
    def run_now(request: Request, body: M.Empty):
        """An owner or admin starts Learn through Maintenance; an off step stays off."""
        from . import maintenance

        who = request.state.identity
        maintenance.may_change(auth, who)
        run, started = maintenance.run_now(
            store, auth, who.actor, step="learn", key=request.headers.get("idempotency-key")
        )
        return {"run": run, "started": started}

    @app.get("/api/v2/learnings")
    def nights(request: Request, before: str = "", limit: int = PAGE):
        """Nights newest first, with how many changes each made that the reader may see."""
        who = request.state.identity
        human_only(who)
        limit = max(1, min(limit, 60))
        with store.read() as c:
            team_names = names(c)
            cursor = before.split("|", 2)
            if len(cursor) == 3:
                where, args = "(night,started_at,id)<(?,?,?)", tuple(cursor)
            else:
                where, args = "(?='' OR night<?)", (before, before)
            rows = c.execute(
                "SELECT * FROM learning_runs WHERE " + where + " ORDER BY night DESC,started_at DESC,id DESC LIMIT ?",
                (*args, limit + 1),
            ).fetchall()
            out = []
            for r in rows[:limit]:
                sections = night_view(c, auth, who, r, team_names)
                out.append(
                    {
                        "id": r["id"],
                        "night": r["night"],
                        "status": r["status"],
                        "updates": sum(len(s["entries"]) for group in sections.values() for s in group),
                        "recipients": c.execute(
                            "SELECT count(*) FROM learning_deliveries WHERE run_id=?", (r["id"],)
                        ).fetchone()[0],
                    }
                )
            last = rows[limit - 1] if len(rows) > limit else None
            return {
                "nights": out,
                "next_before": "|".join((last["night"], last["started_at"], last["id"])) if last else None,
            }

    def one(c, run_id):
        row = c.execute("SELECT * FROM learning_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise Problem("not_found", "No such learning run", 404)
        return row

    @app.get("/api/v2/learnings/{run_id}")
    def night(request: Request, run_id: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            row = one(c, run_id)
            return {
                "id": row["id"],
                "night": row["night"],
                "status": row["status"],
                "sections": night_view(c, auth, who, row, names(c)),
            }

    @app.get("/api/v2/learnings/{run_id}/packets/{slug}")
    def packet(request: Request, run_id: str, slug: str):
        """What the decision model sent one recipient, highest score first; text only where the reader may read the source."""
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            row = one(c, run_id)
            slug = (H.resolve_bot(c, slug) or {}).get("id", slug)
            if not H.bot(c, slug):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, slug)
            delivery = c.execute(
                "SELECT * FROM learning_deliveries WHERE run_id=? AND recipient=?", (run_id, slug)
            ).fetchone()
            if not delivery:
                raise Problem("not_found", "No packet for that bot that night", 404)
            keys = json.loads(delivery["item_keys"])
            team_names = names(c)
            sent = {}
            for d in c.execute("SELECT recipient,item_keys FROM learning_deliveries WHERE run_id=?", (run_id,)):
                for k in json.loads(d["item_keys"]):
                    sent.setdefault(k, []).append(d["recipient"])
            ids = delivery_attempts(c, delivery)
            commits = (
                c.execute(
                    "SELECT subject,diff FROM bot_memory_updates WHERE attempt_id IN ("
                    + ",".join("?" * len(ids))
                    + ")",
                    ids,
                ).fetchall()
                if ids
                else []
            )
            boxes = []

            def mailboxes():
                if not boxes:
                    from .mail import visible_addresses

                    boxes.append(set(visible_addresses(c, who)))
                return boxes[0]

            items, hidden = [], 0
            marks = ",".join("?" * len(keys)) or "''"
            for item in c.execute(
                f"SELECT * FROM learning_items WHERE run_id=? AND item_key IN ({marks})", (run_id, *keys)
            ).fetchall():
                if not readable(c, auth, who, item, mailboxes):
                    hidden += 1
                    continue
                data = {
                    "key": item["item_key"],
                    "source": item["source"],
                    "ref": json.loads(item["ref"]),
                    "label": item["label"],
                    "created_at": item["created_at"],
                }
                url = link(data)
                used = next((m["subject"] for m in commits if url and url in m["diff"]), None)
                items.append(
                    {
                        "source": item["source"],
                        "label": item["label"],
                        "when": item["created_at"],
                        "link": url,
                        "text": item["text"],
                        "score": json.loads(item["scores"]).get(slug, 0),
                        "also_sent_to": [
                            team_names.get("bot:" + s, s) for s in sent.get(item["item_key"], []) if s != slug
                        ],
                        "used": used,
                    }
                )
            items.sort(key=lambda i: -i["score"])
            counts = Counter(
                r[0]
                for r in c.execute(
                    f"SELECT source FROM learning_items WHERE run_id=? AND item_key IN ({marks})", (run_id, *keys)
                )
            )
            return {
                "night": row["night"],
                "bot": slug,
                "name": team_names.get("bot:" + slug, slug),
                "read": row["read_count"],
                "count": len(keys),
                "sources": dict(counts),
                "items": items,
                "hidden": hidden,
            }
