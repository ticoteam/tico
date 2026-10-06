"""The nightly learning run and the Learnings page (docs/learnings.md, Nightly learning run).

Once a night per company, after 03:00 local, one coordinator reads the day's activity once: what people
said in chats and on tasks, finished tasks' notes, Slack channel messages, mail a teammate sent from their
own mailbox, and live company meetings. The decision model scores each item once: is it durable enough to
learn from, and which recipients is it relevant to. Each recipient (a working bot, and the Librarian for
docs and the market) gets one task carrying its packet, and changes its own memory the usual way. No bot
reads Slack or mail for this itself.

What a recipient changed is never copied here: the page reads it back from where it already lives (the
bot's memory commits made by that task's runs, the Librarian's doc versions and market events while the
task was open). The packet's text is shown only to a reader who may read each item's source today.
"""

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
from . import providers
from . import task_privacy as privacy
from .judge import fallback_engine
from .statuses import PARKED_SQL
from .store import H, Problem
from .updates import ZONE
from .views import human_only

LOG = logging.getLogger("tico.learnings")

SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_runs(
 id TEXT PRIMARY KEY, night TEXT NOT NULL UNIQUE, status TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
 read_count INTEGER NOT NULL DEFAULT 0, routed_count INTEGER NOT NULL DEFAULT 0, detail_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS learning_items(
 run_id TEXT NOT NULL, item_key TEXT NOT NULL, source TEXT NOT NULL, ref_json TEXT NOT NULL,
 author TEXT NOT NULL DEFAULT '', label TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL,
 learnable REAL, scores_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_id, item_key));
CREATE TABLE IF NOT EXISTS learning_deliveries(
 run_id TEXT NOT NULL, recipient TEXT NOT NULL, task_id TEXT NOT NULL, item_keys_json TEXT NOT NULL,
 created TEXT NOT NULL, PRIMARY KEY(run_id, recipient));
CREATE TABLE IF NOT EXISTS learning_cursors(source TEXT PRIMARY KEY, last_ts TEXT NOT NULL);
"""

SOURCES = ("chat", "task", "slack", "mail", "meeting")
HOUR = 3                        # local hour the night's run starts at
LOOKBACK_HOURS = 24             # a source with no cursor yet is read this far back
ITEM_CHARS = 1500
MEETING_CHARS = 2000
MAX_ITEMS = 600                 # per night; the oldest beyond it are dropped and counted
PER_RECIPIENT = 40
WORKERS = 6
QUESTION_SET = "learnings-route"
HELPERS = ("botops", "goal-manager")     # with the assistant: they work for people, they are not the org chart
LIBRARIAN = "librarian"
LIBRARIAN_PROFILE = ("Librarian: how the company works (processes, policies, FAQ, docs) and the market "
                     "(competitors, their products, pricing, launches)")
REL = "This item is relevant to: "
TITLE = "Nightly learnings: "
PAGE = 30
KIND_WORDS = {"chat": "Chat", "task": "Task", "slack": "Slack", "mail": "Email", "meeting": "Meeting"}
SAW_WORDS = {"task": ("task", "tasks"), "chat": ("chat", "chats"), "slack": ("Slack message", "Slack messages"),
             "mail": ("email", "emails"), "meeting": ("meeting", "meetings")}

LEARN_TEXT = """Tonight's packet: what people said and decided yesterday that scored as relevant to you. Turn the durable parts into memory.

1. Read `MEMORY.md` (or `memory/learnings.md` if there is no MEMORY.md). Then, for each packet item, decide: write only an explicit human statement, a human correcting a bot, or a pattern seen 3+ times (check memory for earlier sightings). Skip one-off inferences and chit-chat.
2. Before writing, search your memory, knowledge and playbooks for a related entry and choose ONE action: update it (preferred), merge near-duplicates, replace it when a newer source contradicts it, add a new one-line entry, or skip if already known.
3. Entry format: `- <claim> [source: <link or source label>; added: YYYY-MM-DD]`. Quote the source; never paraphrase a person's words into something stronger.
4. Personal preferences of a specific human do NOT go into this repository (docs/conversation.md).
5. If a learning needs a change to your instructions (AGENT.md) or settings, file it with `hub task create --owner botops --title "<one line>" --body "<the change and the packet item>"`; BotOps makes the change.
6. Commit with a clear one-line subject per change and the trailer `Tico-Run: <run id>`, push, and finish this task with a one-line note: what changed, or "Nothing new"."""

LIBRARIAN_TEXT = """
7. Docs: edit them directly with `hub doc write` (every edit is a version). You may edit human-written docs when the packet shows they are out of date; say what changed in the version note.
8. Market facts: `hub market report`, then curate as usual (`playbooks/curate-the-market.md`)."""


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
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


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
def roster(c, settings):
    """{slug: {name, profile}}: the active bots that learn, the Librarian among them."""
    skip = {*HELPERS, settings.assistant_bot}
    out = {}
    for row in c.execute("SELECT b.slug,b.display_name,coalesce(bc.description,'') AS description FROM bots b "
                         "LEFT JOIN bot_config bc ON bc.bot=b.slug WHERE b.state='active' "
                         "AND coalesce(json_extract(bc.config_json,'$.shared_from'),'')='' "
                         "AND coalesce(bc.onboarding_state,'') NOT IN " + PARKED_SQL + " ORDER BY b.slug"):
        if row["slug"] in skip:
            continue
        name = row["display_name"] or row["slug"]
        profile = LIBRARIAN_PROFILE if row["slug"] == LIBRARIAN else (
            f"{name}: {row['description']}" if row["description"].strip() else name)
        out[row["slug"]] = {"name": name, "profile": profile}
    return out


def names(c):
    people = {"human:" + r["id"]: r["name"] or r["id"] for r in c.execute("SELECT id,name FROM humans")}
    bots = {"bot:" + r["slug"]: r["display_name"] or r["slug"] for r in c.execute("SELECT slug,display_name FROM bots")}
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
                task_audience[tid] = {a[4:] for a in (row["owner"], row["requester"]) if a and a.startswith("bot:")}
            else:
                task_audience[tid] = None
        return task_audience[tid]

    delivered = {r[0] for r in c.execute("SELECT task_id FROM learning_deliveries")}
    for m in c.execute(
            "SELECT m.id,m.conversation_id,m.from_actor,m.body,m.created,m.refs_json,cv.task_id AS conv_task,"
            "cv.participants_json,cv.subject FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
            "WHERE m.from_actor LIKE 'human:%' AND m.deleted_at IS NULL AND coalesce(m.kind,'')<>'notice' "
            "AND coalesce(cv.scope,'direct')<>'personal' AND m.created>? AND m.created<=? ORDER BY m.created",
            (min(since["chat"], since["task"]), now)).fetchall():
        text = clip(m["body"])
        if not text:
            continue
        refs = H._json(m["refs_json"], {}) or {}
        tid = H.message_task_id({"refs": refs if isinstance(refs, dict) else {}}, {"task_id": m["conv_task"]})
        author = who.get(m["from_actor"], m["from_actor"])
        if tid:
            if m["created"] <= since["task"] or tid in delivered:
                continue
            task = H.task(c, tid)
            items.append({"key": "task:m:" + m["id"], "source": "task", "author": m["from_actor"], "created": m["created"],
                          "ref": {"message": m["id"], "task": tid},
                          "label": f"{(task or {}).get('title') or 'Task'} · {author}", "text": text,
                          "audience": task_parties(tid)})
            continue
        if m["created"] <= since["chat"]:
            continue
        bots = _bots_in(H._json(m["participants_json"], []) or [], team)
        place = ("Chat with " + ", ".join(team[b]["name"] for b in sorted(bots))) if bots else (m["subject"] or "Chat")
        items.append({"key": "chat:" + m["id"], "source": "chat", "author": m["from_actor"], "created": m["created"],
                      "ref": {"message": m["id"], "conversation": m["conversation_id"],
                              **({"bot": next(iter(bots))} if len(bots) == 1 else {})},
                      "label": f"{place} · {author}", "text": text, "audience": bots or None})
    for t in c.execute("SELECT id,title,note,owner,coalesce(done_at,closed_at) AS at FROM tasks "
                       "WHERE status IN ('done','closed') AND trim(coalesce(note,''))<>'' "
                       "AND coalesce(done_at,closed_at)>? AND coalesce(done_at,closed_at)<=? ORDER BY at",
                       (since["task"], now)).fetchall():
        if t["id"] in delivered:
            continue              # a packet's own "Nothing new" is not tomorrow's activity
        items.append({"key": "task:" + t["id"], "source": "task", "author": t["owner"] or "", "created": t["at"],
                      "ref": {"task": t["id"]}, "label": f"{t['title']} · done",
                      "text": clip(t["note"]), "audience": task_parties(t["id"])})
    channels = {r["channel_id"]: r["name"] for r in c.execute("SELECT channel_id,name FROM slack_channels")} \
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='slack_channels'").fetchone() else {}
    for s in c.execute("SELECT event_id,channel,channel_kind,thread_ts,ts,text,author_name,user_id,received "
                       "FROM slack_events WHERE author='human' AND deleted IS NULL AND channel_kind<>'im' "
                       "AND received>? AND received<=? ORDER BY received", (since["slack"], now)).fetchall():
        text = clip(s["text"])
        if not text:
            continue
        place = "#" + (channels.get(s["channel"]) or s["channel"])
        items.append({"key": "slack:" + s["event_id"], "source": "slack", "author": s["author_name"] or s["user_id"],
                      "created": s["received"],
                      "ref": {"channel": s["channel"], "ts": s["ts"], "thread_ts": s["thread_ts"]},
                      "label": f"{place} · {s['author_name'] or 'Slack'}", "text": text, "audience": None})
    people = {}
    for r in c.execute("SELECT id,name,email FROM humans WHERE coalesce(email,'')<>''"):
        people.setdefault(r["email"].strip().lower(), r)
    if people:
        low, high = int(H.parse_ts(since["mail"]).timestamp()), int(H.parse_ts(now).timestamp())
        marks = ",".join("?" * len(people))
        for m in c.execute("SELECT mailbox,msg_id,thread_id,epoch,from_addr,subject,body,to_json FROM mail_messages "
                           f"WHERE deleted_at IS NULL AND lower(from_addr) IN ({marks}) AND lower(mailbox)=lower(from_addr) "
                           "AND labels_json LIKE '%\"SENT\"%' AND epoch>? AND epoch<=? ORDER BY epoch",
                           (*people, low, high)).fetchall():
            person = people[m["from_addr"].strip().lower()]
            body = unquoted(m["body"])
            text = clip(f"{m['subject']}\n\n{body}" if m["subject"] else body)
            if not text:
                continue
            items.append({"key": f"mail:{m['mailbox']}:{m['msg_id']}", "source": "mail", "author": "human:" + person["id"],
                          "created": utc(datetime.fromtimestamp(m["epoch"], timezone.utc)),
                          "ref": {"mailbox": m["mailbox"].lower(), "msg_id": m["msg_id"], "thread": m["thread_id"]},
                          "label": f"{m['subject'] or 'Email'} · {person['name'] or person['id']}",
                          "text": text, "audience": None})
    for r in c.execute("SELECT m.id,m.title,m.notes,m.metadata_json,m.created FROM meetings m "
                       "LEFT JOIN media_control mc ON mc.meeting_id=m.id WHERE m.review_state='live' "
                       "AND mc.deleted_at IS NULL AND m.created>? AND m.created<=? ORDER BY m.created",
                       (since["meeting"], now)).fetchall():
        meta = H._json(r["metadata_json"], {}) or {}
        if meta.get("kind") != "meeting" or meta.get("private"):
            continue
        summary = meta.get("source_summary") or r["notes"] or ""
        text = clip(f"{r['title']}\n\n{summary}".strip(), MEETING_CHARS)
        items.append({"key": "meeting:" + r["id"], "source": "meeting", "author": "", "created": r["created"],
                      "ref": {"meeting": r["id"]}, "label": r["title"] or "Meeting", "text": text, "audience": None})
    items.sort(key=lambda i: i["created"])
    dropped = max(0, len(items) - MAX_ITEMS)
    return items[dropped:], dropped


# ----------------------------------------------------------------------------- route
def engine_for(c, settings):
    """The decision model: TypeSafe when the server holds the key, else the company's own provider, else None."""
    if settings.rehearsal:
        return None
    if settings.typesafe_api_key:
        return J.direct(settings.typesafe_api_key)
    return fallback_engine(providers.load(c, settings))


def question_set(settings):
    return J.load_set(QUESTION_SET, registry_dir=settings.registry_dir)


def score(engine, qset, item, team):
    """(learnable, {slug: relevance}) for one item, asking only about the recipients it may reach."""
    audience = [s for s in team if item["audience"] is None or s in item["audience"]]
    if not audience:
        return None, {}
    learnable_q = qset["questions"]["learnable"]
    chunks, room = [], J.MAX_QUESTIONS - 1
    for start in range(0, len(audience), room):
        chunks.append(audience[start:start + room])
    learnable, scores = None, {}
    for n, chunk in enumerate(chunks):
        questions = {"learnable": learnable_q} if n == 0 else {}
        questions.update({"rel_" + s: {"type": "noul", "instructions": REL + team[s]["profile"]} for s in chunk})
        state = {"item": {"source": item["source"], "label": item["label"], "text": item["text"]},
                 "roster": {s: team[s]["profile"] for s in chunk}}
        answers = engine(state, questions, qset["label"])["answers"]
        if n == 0:
            learnable = J.noul(answers, "learnable")
        scores.update({s: round(J.noul(answers, "rel_" + s), 3) for s in chunk})
    return round(learnable, 3), scores


def route(engine, qset, items, team):
    """Score every item in parallel. A failed call skips that item; the run goes on."""
    def one(item):
        try:
            return score(engine, qset, item, team), None
        except Exception as exc:            # one item's failure never stops the night
            return (None, {}), getattr(exc, "code", type(exc).__name__)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, items))
    failed = Counter(err for _, err in results if err)
    for item, ((learnable, scores), err) in zip(items, results):
        item["learnable"], item["scores"], item["failed"] = learnable, scores, bool(err)
    return dict(failed)


def packets(items, qset):
    """{slug: [item, ...]}: every recipient at or over the relevance bar, unless the item is noise."""
    bar, floor = qset["thresholds"].get("relevant", 0.6), qset["thresholds"].get("noise", 0.2)
    cap = int(qset["thresholds"].get("per_recipient", PER_RECIPIENT))
    out = {}
    for item in items:
        if item["learnable"] is None or item["learnable"] < floor:
            continue
        for slug, rel in item["scores"].items():
            if rel >= bar:
                out.setdefault(slug, []).append(item)
    return {slug: sorted(rows, key=lambda i: (-i["scores"][slug], i["created"]))[:cap] for slug, rows in out.items()}


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


def packet_body(slug, rows, team, run_id):
    text = LEARN_TEXT + (LIBRARIAN_TEXT if slug == LIBRARIAN else "")
    lines = [text, "", f"Run: {run_id}", "", "## Packet"]
    for n, item in enumerate(rows, 1):
        others = [team[s]["name"] for s, rel in sorted(item["scores"].items(), key=lambda kv: -kv[1])
                  if s != slug and s in item.get("sent_to", ())]
        lines += ["", f"### {n}. {item['label']}",
                  f"Source: {KIND_WORDS[item['source']]} · {item['label']} · {day_words(item['created'])}"]
        if link(item):
            lines.append(f"Link: {link(item)}")
        lines += ["> " + line if line.strip() else ">" for line in item["text"].splitlines()]
        lines.append(f"Relevance: {item['scores'][slug]:.2f}" + (f" · Also sent to: {', '.join(others)}" if others else ""))
    return "\n".join(lines)


def dispatch(c, run_id, night, items, chosen, team, detail):
    """One task per recipient; the routed items, deliveries and cursors in the same transaction."""
    sent_to = {}
    for slug, rows in chosen.items():
        for item in rows:
            sent_to.setdefault(item["key"], set()).add(slug)
    for item in items:
        item["sent_to"] = sent_to.get(item["key"], set())
    title = TITLE + day_words(utc(datetime.combine(datetime.fromisoformat(night).date(), datetime.min.time(), ZONE)
                                  + timedelta(hours=12)))
    delivered, refused = 0, {}
    for slug, rows in sorted(chosen.items()):
        c.execute("SAVEPOINT learning_delivery")
        try:
            task = H.task_create(c, H.KEEPER, title, packet_body(slug, rows, team, run_id), "bot:" + slug,
                                 deduplicate=False, lint=False, private=True)
        except H.Refused as exc:
            c.execute("ROLLBACK TO learning_delivery")
            c.execute("RELEASE learning_delivery")
            refused[slug] = exc.rule
            for item in rows:
                item["sent_to"].discard(slug)
            continue
        c.execute("RELEASE learning_delivery")
        c.execute("INSERT INTO learning_deliveries(run_id,recipient,task_id,item_keys_json,created) VALUES(?,?,?,?,?)",
                  (run_id, slug, task["id"], json.dumps([i["key"] for i in rows]), H.now()))
        delivered += 1
    routed = [i for i in items if i["sent_to"]]
    for item in routed:
        c.execute("INSERT OR REPLACE INTO learning_items(run_id,item_key,source,ref_json,author,label,text,created,"
                  "learnable,scores_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (run_id, item["key"], item["source"], json.dumps(item["ref"]), item["author"], item["label"],
                   item["text"], item["created"], item["learnable"], json.dumps(item["scores"])))
    if refused:
        detail["refused"] = refused
    return delivered, len(routed)


def advance(c, items, now):
    newest = {}
    for item in items:
        newest[item["source"]] = max(newest.get(item["source"], ""), item["created"])
    for source in SOURCES:
        c.execute("INSERT INTO learning_cursors(source,last_ts) VALUES(?,?) ON CONFLICT(source) DO UPDATE SET "
                  "last_ts=max(learning_cursors.last_ts,excluded.last_ts)", (source, newest.get(source) or now))


def finish(store, run_id, status, detail, read=0, routed=0):
    with store.transaction() as c:
        c.execute("UPDATE learning_runs SET status=?,finished=?,read_count=?,routed_count=?,detail_json=? WHERE id=?",
                  (status, H.now(), read, routed, json.dumps(detail), run_id))


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
            engine = engine_for(c, settings)
            team = roster(c, settings)
            if engine is not None:
                items, dropped = collect(c, settings, now, team)
            c.execute("COMMIT")
        if engine is None:
            finish(store, run_id, "unconfigured", {"reason": "no decision model"})
            return
        detail = {"read": dict(Counter(i["source"] for i in items)), "dropped": dropped, "recipients": len(team)}
        chosen, failed = {}, {}
        if items:
            qset = question_set(settings)
            routed_at = time.monotonic()
            failed = route(engine, qset, items, team)
            chosen = packets(items, qset)
            detail.update({"failed": failed, "route_ms": round((time.monotonic() - routed_at) * 1000)})
        with store.transaction() as c:
            if c.execute("SELECT status FROM learning_runs WHERE id=?", (run_id,)).fetchone()["status"] != "running":
                return
            delivered, routed = dispatch(c, run_id, night, items, chosen, team, detail) if chosen else (0, 0)
            advance(c, items, now)
            if items:
                # One audit row for the night's calls, as POST /api/v2/decisions keeps one per call: counts, never text.
                H.event(c, H.KEEPER, "judge.call", f"{QUESTION_SET}@run", {
                    "label": f"{QUESTION_SET}@run", "run": run_id, "items": len(items), "failed": failed,
                    "routed": routed, "deliveries": delivered, "ms": detail.get("route_ms", 0)})
            detail["deliveries"] = delivered
            detail["ms"] = round((time.monotonic() - started) * 1000)
            c.execute("UPDATE learning_runs SET status=?,finished=?,read_count=?,routed_count=?,detail_json=? WHERE id=?",
                      ("done" if delivered else "quiet", H.now(), len(items), routed, json.dumps(detail), run_id))
    except Exception as exc:
        LOG.exception("Nightly learning run %s failed", run_id)
        finish(store, run_id, "failed", {**detail, "error": type(exc).__name__})


def start(store, run_id):
    """In the background: the decision model's calls take seconds, and nothing may wait on them."""
    thread = threading.Thread(target=run, args=(store, run_id), name="learnings", daemon=True)
    thread.start()
    return thread


def claim(c, night):
    """A new running row for this night, or None when the night already has one."""
    if c.execute("SELECT 1 FROM learning_runs WHERE night=?", (night,)).fetchone():
        return None
    run_id = H.new_id()
    c.execute("INSERT INTO learning_runs(id,night,status,started) VALUES(?,?,?,?)", (run_id, night, "running", H.now()))
    return run_id


def nightly(store, at):
    """From the scheduler's tick: once a night, at or after 03:00 local. Off in rehearsal and test servers."""
    settings = store.settings
    if settings.rehearsal or settings.test_identities or local(at).hour < HOUR:
        return None
    night = night_of(at)
    # The tick comes every few seconds: once this process has seen the night claimed, it never asks again.
    if store.__dict__.get("learnings_night") == night:
        return None
    with store.transaction() as c:
        run_id = claim(c, night)
    store.__dict__["learnings_night"] = night
    if run_id:
        start(store, run_id)
    return run_id


# ----------------------------------------------------------------------------- the page
SOURCE_RE = re.compile(r"\[source:\s*([^\];]+)")
KIND_RE = re.compile(r"^(chat|task|slack|mail|email|meeting|doc|commit)\b", re.I)


def run_attempts(c, task_id):
    """The runs that worked this task: started by one of its messages, or handed one as an input."""
    return [r[0] for r in c.execute(
        "SELECT a.id FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
        f"LEFT JOIN conversations cv ON cv.id=m.conversation_id WHERE {H.MESSAGE_TASK_SQL}=? UNION "
        "SELECT i.attempt_id FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
        f"LEFT JOIN conversations cv ON cv.id=m.conversation_id WHERE {H.MESSAGE_TASK_SQL}=?", (task_id, task_id))]


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
            out.append({"kind": (kind.group(1).lower().replace("email", "mail") if kind else ""), "label": text,
                        "quote": quote.group(1) if quote else "", "link": url.group(1) if url else ""})
    return out


def line_diff(old, new):
    return "\n".join(l for l in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1)
                     if not l.startswith(("---", "+++")))


def window(c, delivery):
    task = H.task(c, delivery["task_id"]) or {}
    return delivery["created"], task.get("done_at") or task.get("closed_at") or H.now()


def saw(c, run_id, keys):
    if not keys:
        return ""
    marks = ",".join("?" * len(keys))
    counts = Counter(r[0] for r in c.execute(
        f"SELECT source FROM learning_items WHERE run_id=? AND item_key IN ({marks})", (run_id, *keys)))
    parts = [f"{n} {SAW_WORDS[s][n != 1]}" for s, n in counts.most_common() if s in SAW_WORDS]
    return "Saw " + ", ".join(parts) if parts else ""


def bot_entries(c, slug, task_id):
    ids = run_attempts(c, task_id)
    if not ids:
        return []
    rows = c.execute(f"SELECT * FROM bot_memory_updates WHERE attempt_id IN ({','.join('?' * len(ids))}) "
                     "ORDER BY committed", ids).fetchall()
    return [{"title": r["subject"], "sources": sources_of(r["diff"]),
             "change": {"where": ", ".join(json.loads(r["files_json"])), "diff": r["diff"]}} for r in rows]


def doc_subjects(c, delivery, saw_text):
    begin, end = window(c, delivery)
    out = {}
    for v in c.execute("SELECT * FROM doc_versions WHERE actor=? AND created>=? AND created<=? ORDER BY created",
                       ("bot:" + LIBRARIAN, begin, end)).fetchall():
        prev = c.execute("SELECT body FROM doc_versions WHERE doc_id=? AND version<? ORDER BY version DESC LIMIT 1",
                         (v["doc_id"], v["version"])).fetchone()
        subject = out.setdefault(v["doc_id"], {"id": v["doc_id"], "name": v["title"], "saw": saw_text,
                                               "task_id": delivery["task_id"], "entries": []})
        subject["entries"].append({"title": v["note"] or v["title"], "sources": [],
                                   "change": {"where": f"{v['title']} · v{v['version']}",
                                              "diff": line_diff(prev["body"] if prev else "", v["body"])}})
    return list(out.values())


def market_subjects(c, delivery, saw_text):
    from . import market
    begin, end = window(c, delivery)
    out = {}
    for e in c.execute("SELECT * FROM market_events WHERE actor=? AND ts>=? AND ts<=? "
                       "AND subject_kind IN ('entity','edge') ORDER BY ts", ("bot:" + LIBRARIAN, begin, end)).fetchall():
        entity_id = e["subject_id"] if e["subject_kind"] == "entity" else (market.edge(c, e["subject_id"]) or {}).get("src")
        entity = market.entity(c, entity_id) if entity_id else None
        if not entity:
            continue
        subject = out.setdefault(entity["id"], {"id": entity["id"], "name": entity["name"], "saw": saw_text,
                                                "task_id": delivery["task_id"], "entries": []})
        lines = ([f"-{e['old']}"] if e["old"] not in (None, "") else []) + ([f"+{e['new']}"] if e["new"] not in (None, "") else [])
        subject["entries"].append({"title": e["note"] or f"{entity['name']}: {e['field']}", "sources": [],
                                   "change": {"where": f"{entity['name']} · {e['field']}", "diff": "\n".join(lines)}})
    return list(out.values())


def night_view(c, auth, who, run, team_names):
    sections = {"bots": [], "docs": [], "market": []}
    for d in c.execute("SELECT * FROM learning_deliveries WHERE run_id=? ORDER BY recipient", (run["id"],)).fetchall():
        keys = json.loads(d["item_keys_json"])
        saw_text = saw(c, run["id"], keys)
        if d["recipient"] == LIBRARIAN:
            sections["docs"] += doc_subjects(c, d, saw_text)
            sections["market"] += market_subjects(c, d, saw_text)
        if not auth.bot_access(c, who, d["recipient"])["read"]:
            continue
        entries = bot_entries(c, d["recipient"], d["task_id"])
        if entries:
            sections["bots"].append({"id": d["recipient"], "name": team_names.get("bot:" + d["recipient"], d["recipient"]),
                                     "saw": saw_text, "task_id": d["task_id"], "entries": entries})
    return sections


def readable(c, auth, who, item, mailboxes):
    """Whether this reader may read the item's source today; the packet shows its text only then."""
    from . import media
    ref = json.loads(item["ref_json"])
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
            return not c.execute("SELECT 1 FROM slack_events WHERE channel=? AND ts=? AND deleted IS NOT NULL",
                                 (ref.get("channel"), ref.get("ts"))).fetchone()
    except Problem:
        return False
    return False


def install(app, store, auth, mutate):
    @app.post("/api/v2/learnings/run")
    def run_now(request: Request, body: M.Empty):
        """Owner only: tonight's learning run, now. 409 when tonight's has already started."""
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner starts the learning run", 403)

        def work(c):
            night = night_of(datetime.now(timezone.utc))
            found = c.execute("SELECT id,status FROM learning_runs WHERE night=?", (night,)).fetchone()
            if found and found["status"] == "running":
                raise Problem("conflict", "Tonight's learning run is still running", 409)
            if found and found["status"] not in ("unconfigured", "failed"):
                raise Problem("conflict", "Tonight's learning run already ran", 409)
            if found:          # an unconfigured or failed night is tried again under the same key
                c.execute("UPDATE learning_runs SET status='running',started=?,finished=NULL,detail_json='{}' WHERE id=?",
                          (H.now(), found["id"]))
                return {"id": found["id"], "night": night}
            return {"id": claim(c, night), "night": night}
        result = mutate(request, body, work)
        start(store, result["id"])
        return result

    @app.get("/api/v2/learnings")
    def nights(request: Request, before: str = "", limit: int = PAGE):
        """Nights newest first, with how many changes each made that the reader may see."""
        who = request.state.identity
        human_only(who)
        limit = max(1, min(limit, 60))
        with store.read() as c:
            team_names = names(c)
            rows = c.execute("SELECT * FROM learning_runs WHERE (?='' OR night<?) ORDER BY night DESC LIMIT ?",
                             (before, before, limit + 1)).fetchall()
            out = []
            for r in rows[:limit]:
                sections = night_view(c, auth, who, r, team_names)
                out.append({"id": r["id"], "night": r["night"], "status": r["status"],
                            "updates": sum(len(s["entries"]) for group in sections.values() for s in group),
                            "recipients": c.execute("SELECT count(*) FROM learning_deliveries WHERE run_id=?",
                                                    (r["id"],)).fetchone()[0]})
            return {"nights": out, "next_before": rows[limit - 1]["night"] if len(rows) > limit else None}

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
            return {"id": row["id"], "night": row["night"], "status": row["status"],
                    "sections": night_view(c, auth, who, row, names(c))}

    @app.get("/api/v2/learnings/{run_id}/packets/{slug}")
    def packet(request: Request, run_id: str, slug: str):
        """What the decision model sent one recipient, highest score first; text only where the reader may read the source."""
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            row = one(c, run_id)
            if not H.bot(c, slug):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, slug)
            delivery = c.execute("SELECT * FROM learning_deliveries WHERE run_id=? AND recipient=?",
                                 (run_id, slug)).fetchone()
            if not delivery:
                raise Problem("not_found", "No packet for that bot that night", 404)
            keys = json.loads(delivery["item_keys_json"])
            team_names = names(c)
            sent = {}
            for d in c.execute("SELECT recipient,item_keys_json FROM learning_deliveries WHERE run_id=?", (run_id,)):
                for k in json.loads(d["item_keys_json"]):
                    sent.setdefault(k, []).append(d["recipient"])
            ids = run_attempts(c, delivery["task_id"])
            commits = c.execute("SELECT subject,diff FROM bot_memory_updates WHERE attempt_id IN (" +
                                ",".join("?" * len(ids)) + ")", ids).fetchall() if ids else []
            boxes = []

            def mailboxes():
                if not boxes:
                    from .mail import visible_addresses
                    boxes.append(set(visible_addresses(c, who)))
                return boxes[0]
            items, hidden = [], 0
            marks = ",".join("?" * len(keys)) or "''"
            for item in c.execute(f"SELECT * FROM learning_items WHERE run_id=? AND item_key IN ({marks})",
                                  (run_id, *keys)).fetchall():
                if not readable(c, auth, who, item, mailboxes):
                    hidden += 1
                    continue
                data = {"key": item["item_key"], "source": item["source"], "ref": json.loads(item["ref_json"]),
                        "label": item["label"], "created": item["created"]}
                url = link(data)
                used = next((m["subject"] for m in commits if url and url in m["diff"]), None)
                items.append({"source": item["source"], "label": item["label"], "when": item["created"], "link": url,
                              "text": item["text"], "score": json.loads(item["scores_json"]).get(slug, 0),
                              "also_sent_to": [team_names.get("bot:" + s, s) for s in sent.get(item["item_key"], []) if s != slug],
                              "used": used})
            items.sort(key=lambda i: -i["score"])
            counts = Counter(r[0] for r in c.execute(
                f"SELECT source FROM learning_items WHERE run_id=? AND item_key IN ({marks})", (run_id, *keys)))
            return {"night": row["night"], "bot": slug, "name": team_names.get("bot:" + slug, slug),
                    "read": row["read_count"], "count": len(keys), "sources": dict(counts),
                    "items": items, "hidden": hidden}
