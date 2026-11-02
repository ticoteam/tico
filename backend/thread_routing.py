"""Routing a person's message (contract §4.3 rule 4): when it names nobody in a thread with several bots, the decision
model the Slack gateway uses reads it and picks who it is for, three bots at most. Each is then woken through the wake
rule, as a mention is.

A post must not wait on a model, so it only writes one row here (`enqueue`, in its own transaction, so a rolled-back post
routes nothing). The scheduler's tick starts the worker on its own thread when a row is due, and only then (`kick`).

Who may be picked: the bots in the thread, by membership (a group's bots too), by following it, or by having posted lately
in it or its root, each still active and still in its audience; muted and mentions-only followers are left out. With fewer than two
there is nothing to choose, and no model is called. With no decision model, the month's spend at its cap or the person's
daily decision cap reached, nobody is woken: the message stays in the thread, read by whoever reads it. A failed call
retries for ROUTE_GIVE_UP_SECONDS, one `judge.call` however many tries it took. A bot's own unaddressed post is not routed:
bots posting into a routed thread would pay for a model call per post and wake each other.
"""
import logging
import threading

from clients import judge as J

from . import hubdb as H
from . import judge as B
from . import threads

LOG = logging.getLogger("tico.thread_routing")
JUDGE_SET = J.load_set("slack-route")
JUDGE_LABEL = JUDGE_SET["label"]
MAX_RECIPIENTS = 3
MAX_CANDIDATES = 37       # with the set's three questions, one call of J.MAX_QUESTIONS
BATCH = 20                # messages one run routes
POSTERS_WINDOW = 200      # recent messages read for who posted
CONTEXT = 8               # earlier messages the model reads
CONTEXT_CHARS = 1000
RETRY_SECONDS = 60
ROUTE_GIVE_UP_SECONDS = 600

_running = threading.Lock()


def enqueue(c, message, thread):
    """Inside the post's transaction."""
    now = H.now()
    c.execute("INSERT OR IGNORE INTO thread_routings(message_id,thread_id,next_attempt_at,created_at,updated_at) "
              "VALUES(?,?,?,?,?)", (message["id"], thread["id"], now, now, now))


def candidates(c, thread, author):
    """The bots this message could be for, in slug order, without duplicates (see the module docstring)."""
    root = threads.root_of(c, thread)
    found = {str(a) for a in (root.get("participants") or []) if str(a).startswith("bot:")}
    members = [r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=?", (root["id"],))]
    found |= {a for a in members if a.startswith("bot:")}
    groups = [a[6:] for a in members if a.startswith("group:")]
    if groups:
        from . import teammate_access
        for group in groups:
            found |= {a for a in teammate_access.group_member_actors(c, group) if a.startswith("bot:")}
    found |= {r[0] for r in c.execute("SELECT actor FROM thread_followers WHERE thread_id IN (?,?) AND following=1 "
                                      "AND notify IN ('live','digest') AND actor LIKE 'bot:%'", (root["id"], thread["id"]))}
    for place in {root["id"], thread["id"]}:
        found |= {r[0] for r in c.execute(
            "SELECT DISTINCT from_actor FROM (SELECT from_actor FROM messages WHERE conversation_id=? AND seq IS NOT NULL "
            "AND deleted_at IS NULL ORDER BY seq DESC LIMIT ?) WHERE from_actor LIKE 'bot:%'", (place, POSTERS_WINDOW))}
    muted = {r[0] for r in c.execute("SELECT actor FROM thread_followers WHERE thread_id IN (?,?) AND actor LIKE 'bot:%' "
                                     "AND (following=0 OR notify IN ('muted','mentions'))", (root["id"], thread["id"]))}
    out = []
    for actor in sorted(found - muted - {author}):
        if (H.bot(c, H.actor_id(actor)) or {}).get("state") == "active" and threads.audience_has(c, thread, actor):
            out.append(actor)
    return out[:MAX_CANDIDATES]


def _fleet(c, actors):
    from . import people as P
    from .views import entries, roster
    people, configs = roster(c), entries(c)
    out = []
    for actor in actors:
        slug = H.actor_id(actor)
        config = configs.get(slug) or {}
        out.append({"slug": slug, "name": (H.bot(c, slug) or {}).get("display_name") or slug,
                    "team": P.team_of(slug, configs, people), "description": str(config.get("description") or "")[:400],
                    "reports_to": config.get("reports_to")})
    return out


def questions(fleet):
    """One noul per bot, then the set's fixed questions (questions/slack-route.json), as the Slack gateway asks."""
    out = {}
    for bot in fleet:
        manager = f" It reports to {bot['reports_to']}." if bot.get("reports_to") else ""
        team = f" ({bot['team']} team)" if bot.get("team") else ""
        out["bot:" + bot["slug"]] = {"type": "noul", "instructions":
                                     f"This message should be handled by {bot['name']}{team}: "
                                     f"{bot['description'] or 'no description'}.{manager}"}
    out.update(JUDGE_SET["questions"])
    return out


def _state(c, thread, message, fleet):
    """What the model reads. Only what the thread's readers all read: a restricted message stays out of its context."""
    rows = c.execute("SELECT from_actor, body FROM messages WHERE conversation_id=? AND seq<? AND seq IS NOT NULL "
                     "AND deleted_at IS NULL AND restricted=0 ORDER BY seq DESC LIMIT ?",
                     (thread["id"], message.get("seq") or 0, CONTEXT)).fetchall()
    last = next((r["from_actor"] for r in rows if str(r["from_actor"]).startswith("bot:")), None)
    return {"message": {"text": message.get("body") or "", "from": message.get("from_actor"),
                        "where": thread.get("subject") or ""},
            "thread": [{"from": r["from_actor"], "text": str(r["body"] or "")[:CONTEXT_CHARS]} for r in reversed(rows)],
            "previous_routing": {"last_bot": last}, "roster": fleet}


def chosen(answers, fleet, settings):
    """The bots the answers pick: nobody when it asks for nothing, else those at the bar, best first, three at most."""
    def noul(key):
        try:
            return round(max(0.0, min(1.0, float((answers.get(key) or {}).get("noul") or 0))), 3)
        except (TypeError, ValueError):
            return 0.0
    if noul("asks") < settings.slack_ask_threshold:
        return []
    ranked = sorted(((noul("bot:" + b["slug"]), b["slug"]) for b in fleet), key=lambda s: (-s[0], s[1]))
    picked = [slug for score, slug in ranked if score >= settings.slack_route_threshold]
    return ["bot:" + slug for slug in picked[:min(MAX_RECIPIENTS, settings.slack_max_recipients)]]


def _drop(store, message_id):
    with store.transaction() as c:
        c.execute("DELETE FROM thread_routings WHERE message_id=?", (message_id,))


def _due(c, now):
    return [dict(r) for r in c.execute("SELECT * FROM thread_routings WHERE next_attempt_at<=? ORDER BY next_attempt_at "
                                       "LIMIT ?", (now, BATCH)).fetchall()]


def _plan(store, row, primary):
    """Outside any transaction's lock on writers: what this row needs, or None when it is done with."""
    with store.read() as c:
        message = H.message(c, row["message_id"])
        thread = threads.thread_row(c, row["thread_id"]) if message else None
        # An edit that named someone since was delivered as a mention; a bot already woken for it needs no routing.
        if thread is None or c.execute("SELECT 1 FROM message_mentions WHERE message_id=? AND in_audience=1 LIMIT 1",
                                       (message["id"],)).fetchone():
            return None
        actors = candidates(c, thread, message.get("from_actor"))
        if len(actors) < 2:
            return None
        settings = store.settings
        author = message.get("from_actor")
        role = "owner" if (H.human(c, H.actor_id(author)) or {}).get("email") == settings.owner_email else "human"
        if not row["attempts"] and B.used_today(c, author) >= B.daily_cap(settings, role):
            return None
        engine = B.server_engine(store, c, primary if primary is not None else B.primary_engine(settings, store))
        if engine is None:
            return None
        fleet = _fleet(c, actors)
        return {"message": message, "thread": thread, "fleet": fleet, "engine": engine,
                "state": _state(c, thread, message, fleet)}


def _apply(store, row, plan, result):
    """In one transaction: the message is checked again, the picked bots get their wakes, and the one audit row is written."""
    picked = chosen(result.get("answers") or {}, plan["fleet"], store.settings)
    with store.transaction() as c:
        c.execute("DELETE FROM thread_routings WHERE message_id=?", (row["message_id"],))
        message = H.message(c, row["message_id"])
        thread = threads.thread_row(c, row["thread_id"]) if message else None
        H.event(c, plan["message"].get("from_actor"), "judge.call", JUDGE_LABEL,
                {"via": "thread", "message_id": row["message_id"], "questions": len(plan["fleet"]) + len(JUDGE_SET["questions"]),
                 "calls": 1, "routed": [H.actor_id(a) for a in picked], "answers": J.summary(result.get("answers")),
                 **B.audit_detail(result)})
        if thread is None or c.execute("SELECT 1 FROM message_mentions WHERE message_id=? AND in_audience=1 LIMIT 1",
                                       (message["id"],)).fetchone():
            return []
        root = threads.root_of(c, thread)
        woken = []
        for actor in picked:
            # Routing comes before a digest in the wake rule; a bot with a row of any other kind already has this message.
            c.execute("DELETE FROM message_deliveries WHERE message_id=? AND recipient=? AND mode='digest' "
                      "AND state='pending'", (message["id"], actor))
            if c.execute("SELECT 1 FROM message_deliveries WHERE message_id=? AND recipient=?",
                         (message["id"], actor)).fetchone():
                continue
            sent = threads._deliver_bot(c, thread, root, message, actor, "routing")
            if sent:
                woken.append(actor)
        return woken


def route_one(store, row, primary=None):
    """One queued message. Returns the bots woken."""
    plan = _plan(store, row, primary)
    if plan is None:
        _drop(store, row["message_id"])
        return []
    try:
        result = plan["engine"](plan["state"], questions(plan["fleet"]), JUDGE_LABEL)
    except J.JudgeError as exc:
        if exc.code == "spend_cap" or row["created_at"] <= H.shift(H.now(), seconds=-ROUTE_GIVE_UP_SECONDS):
            LOG.info("A thread message was left unrouted: %s", exc.code)
            _drop(store, row["message_id"])
        else:
            with store.transaction() as c:
                c.execute("UPDATE thread_routings SET attempts=attempts+1, next_attempt_at=?, updated_at=? WHERE message_id=?",
                          (H.shift(H.now(), seconds=RETRY_SECONDS), H.now(), row["message_id"]))
        return []
    return _apply(store, row, plan, result)


def drain(store, primary=None):
    """One run over what is due. One message's failure is logged by its type alone and does not stop the others; its row
    waits a minute, or goes when it has waited too long. Returns the bots woken."""
    with store.read() as c:
        rows = _due(c, H.now())
    woken = []
    for row in rows:
        try:
            woken += route_one(store, row, primary)
        except Exception as exc:  # noqa: BLE001 - one message must not stop the others
            LOG.warning("Routing a thread message failed: %s", type(exc).__name__)
            _drop(store, row["message_id"])
    return woken


def _worker(store):
    try:
        drain(store)
    finally:
        _running.release()


def kick(store):
    """From the scheduler's tick: start the worker when a row is due and none is running. Nothing due reads one index."""
    with store.read() as c:
        if not c.execute("SELECT 1 FROM thread_routings WHERE next_attempt_at<=? LIMIT 1", (H.now(),)).fetchone():
            return False
    if not _running.acquire(blocking=False):
        return False
    threading.Thread(target=_worker, args=(store,), name="thread-routing", daemon=True).start()
    return True
