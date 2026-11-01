"""Bot digests (contract §4.3 rule 5): a bot that follows a thread with `notify=digest` is not woken per message. Its
deliveries wait (`mode='digest'`), and this sweep starts one run for all of them, once an hour.

The sweep is plain code: no model is called unless a batch has something in it. Each delivery is checked again when the
batch is made, as a carrier is when it is claimed: gone or no longer readable by the bot, it goes moot. The wake is one
keeper notice in the bot's own conversation, naming the threads and how many messages each has, never their words, since
that conversation has other readers. A run started by it reads those threads, granted at the claim while the bot is
still in their audience (`grant`). A bot that is paused or quarantined keeps its deliveries until it is back, and a batch
waits while the last one's run has not started, so one bot never has two.
"""
from . import hubdb as H
from . import threads

WAKE = "thread_digest"
MAX_BOTS = 200            # bots one run looks at
MAX_DELIVERIES = 200      # deliveries one batch covers; the rest wait for the next run
MAX_THREADS = 20          # threads one notice names, so its grant stays small; the rest wait for the next run


def _waiting(c):
    return [r[0] for r in c.execute(
        "SELECT DISTINCT recipient FROM message_deliveries WHERE mode='digest' AND state='pending' "
        "AND recipient LIKE 'bot:%' LIMIT ?", (MAX_BOTS,)).fetchall()]


def _batch_open(c, slug):
    return c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot=? "
                     "AND j.state IN ('queued','leased') AND json_extract(m.refs_json,'$.wake')=? LIMIT 1",
                     (slug, WAKE)).fetchone() is not None


def wake_bot(c, bot):
    """One batch for one bot, in the caller's transaction. Returns the notice's id, or None when nothing is due."""
    slug = H.actor_id(bot)
    if (H.bot(c, slug) or {}).get("state") != "active" or _batch_open(c, slug):
        return None
    covered, counts = [], {}
    for d in c.execute("SELECT id, message_id, thread_id FROM message_deliveries WHERE recipient=? AND mode='digest' "
                       "AND state='pending' ORDER BY created_at LIMIT ?", (bot, MAX_DELIVERIES)).fetchall():
        message = H.message(c, d["message_id"])
        thread = threads.thread_row(c, d["thread_id"]) if message else None
        if thread is None or not threads._source_readable(c, thread, message, bot):
            c.execute("UPDATE message_deliveries SET state='moot', updated_at=? WHERE id=?", (H.now(), d["id"]))
            continue
        if d["thread_id"] not in counts and len(counts) >= MAX_THREADS:
            continue
        counts[d["thread_id"]] = counts.get(d["thread_id"], 0) + 1
        covered.append(d["id"])
    if not covered:
        return None
    lines = "\n".join(f"- {thread_id}: {n}" for thread_id, n in counts.items())
    notice = H.say(c, H.KEEPER, bot, f"New messages in threads you follow on digest (thread: messages):\n{lines}\n"
                   "Catch up with hub_thread_catchup if they need you.", kind="notice",
                   refs={"wake": WAKE, "wake_digest": len(covered), "trigger": "other", "threads": sorted(counts)})
    now = H.now()
    marks = ",".join("?" * len(covered))
    c.execute(f"UPDATE message_deliveries SET state='delivered', carrier_id=?, delivered_at=?, updated_at=? "
              f"WHERE id IN ({marks})", (notice["id"], now, now, *covered))
    return notice["id"]


def sweep(store):
    """One run: one transaction per bot, so one bot's failure leaves the others' batches. Returns how many bots woke."""
    with store.read() as c:
        bots = _waiting(c)
    woken = 0
    for bot in bots:
        with store.transaction() as c:
            woken += 1 if wake_bot(c, bot) else 0
    return woken


def grant(c, attempt_id, message):
    """A run a digest notice started, or took in, reads the threads it names: each one the bot is still in the audience
    of, so a thread it lost since is not granted. Every other message is left alone at the cost of a dict look."""
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else H._json(message.get("refs_json"), {})
    if (refs or {}).get("wake") != WAKE or message.get("from_actor") != H.KEEPER:
        return
    bot = message.get("to_actor")
    for thread_id in (refs.get("threads") or [])[:MAX_THREADS]:
        thread = threads.thread_row(c, thread_id)
        if thread is not None and threads.audience_has(c, thread, bot):
            c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (attempt_id, thread_id))
