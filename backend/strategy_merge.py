"""The Strategy Bot's merged asks (docs/goals-and-kpis.md, "Your Strategy Bot"; spec: Head of Product, Strategy Bot,
test 13).

At level 2 and 3, when several bots ask a person the same thing, the Strategy Bot files one item for all of them: a task
of its own, waiting on the person, that names every original. The originals stay as they were for the bots that asked,
but leave the person's Needs you (`hidden`, read by hubdb.needs_you) while the merged item is open. The Strategy Bot
decides what counts as the same question; the server checks only that each original is really waiting on that person
now.

The person answers the merged item once, as they answer any bot's question (a reply on it, or "answer" in Needs you),
and `on_reply` (from hubdb.task_comment and hubdb.answer) passes the answer to every original: as the answer to a bot's
open question, or as a comment that wakes the bot whose task waits on them or that filed the task for them. The merged
item is then done.

Nothing is dropped: an original whose answer can't be passed on, or every original of a merged item that ends without an
answer, is back in the person's Needs you as it was. A merged item counts in what the Strategy Bot asked today
(backend/strategy_bot.py `asked_today`)."""

from . import strategy_bot
from . import hubdb as H

STRATEGY_BOT = "bot:" + strategy_bot.SLUG
MIN_ORIGINALS, MAX_ORIGINALS = 2, 10


def _name(c, actor):
    if H.is_bot(actor):
        found = H.bot(c, H.actor_id(actor)) or {}
        return found.get("display_name") or found.get("slug") or H.actor_id(actor)
    return (H.human(c, H.actor_id(actor)) or {}).get("name") or H.actor_id(actor or "")


def _open_ask(c, row, person):
    """The open question a bot asked `person` on this task, or None."""
    return next((a for a in H.open_task_asks(c, row) if a["recipient_id"] == person and H.is_bot(a["sender_id"])), None)


def waiting_on(c, person, row):
    """How this task waits on `person` in their Needs you, the bot that asked, and the question when it is one:
    ("asked", bot, ask id) for a bot's open question on it, ("waiting", bot, None) for a bot's task set waiting on
    them, ("owned", bot, None) for a task a bot filed for them; None when it does not wait on them."""
    if not row or row["status"] not in H.ACTIVE_STATUSES:
        return None
    ask = _open_ask(c, row, person)
    if ask:
        return "asked", ask["sender_id"], ask["id"]
    if H.is_bot(row["owner_id"]) and row["status"] == "waiting" and row.get("waiting_on") == person:
        return "waiting", row["owner_id"], None
    if row["owner_id"] == person and H.is_bot(row["requester_id"]):
        return "owned", row["requester_id"], None
    return None


def _current(c, link, row):
    """Whether the original still waits on the person the way it did when merged: the same question still open, or
    the same bot's task still waiting on them or filed for them. A new question since is not what was merged."""
    if not row or row["status"] not in H.ACTIVE_STATUSES:
        return False
    if link["how"] == "asked":
        return any(a["id"] == link["ask_id"] for a in H.open_task_asks(c, row))
    how = waiting_on(c, link["person"], row)
    return bool(how) and how[:2] == (link["how"], link["asker"])


def _asked_again(c, link, row):
    """Whether a bot asked the person something new on the original after their answer was passed on: a message to
    them on it, or the task set waiting on them again."""
    said = c.execute(
        "SELECT 1 FROM messages m JOIN conversations cv ON cv.id=m.conversation_id WHERE m.conversation_id=? AND "
        "m.recipient_id=? AND m.sender_id<>? AND m.sender_id LIKE 'bot:%' AND m.created_at>? "
        f"AND {H.MESSAGE_TASK_SQL}=? LIMIT 1",
        (row["conversation_id"], link["person"], STRATEGY_BOT, link["settled_at"], row["id"])).fetchone()
    return bool(said) or bool(c.execute(
        "SELECT 1 FROM task_events WHERE task_id=? AND field='waiting_on' AND new=? AND created_at>? LIMIT 1",
        (row["id"], link["person"], link["settled_at"])).fetchone())


def hidden(c, person):
    """Ids of the tasks the Strategy Bot merged for this person that stay out of their Needs you: while the merged item
    is open and the original still waits on them as it did when merged, and once its answer was passed on until a bot
    asks them something new there. Anything else on the original is back in their Needs you."""
    return {link["original"] for link in _hidden_links(c, person)}


def hidden_by_item(c, person):
    """{merged item id: [ids of its originals out of the person's Needs you]} while each merged item is open. The
    Tasks page lists Needs you from its own copy of the tasks, so the merged item names what it stands for."""
    out = {}
    for link in _hidden_links(c, person):
        if link["state"] == "merged":
            out.setdefault(link["merged_into"], []).append(link["original"])
    return out


def _hidden_links(c, person):
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    out = []
    for link in H._rows(c.execute(
            "SELECT m.* FROM strategy_merges m JOIN tasks t ON t.id=m.merged_into JOIN tasks o ON o.id=m.original "
            f"WHERE m.person=? AND o.status IN ({marks}) AND "
            f"(m.state='answered' OR (m.state='merged' AND t.status IN ({marks})))",
            (person, *H.ACTIVE_STATUSES, *H.ACTIVE_STATUSES))):
        row = H.task(c, link["original"])
        if link["state"] == "merged" and _current(c, link, row) or \
                link["state"] == "answered" and not _asked_again(c, link, row):
            out.append(link)
    return out


def originals(c, merged_into):
    """[{task_id, title, asker, asker_name, state, note}] for one merged item."""
    out = []
    for r in H._rows(c.execute("SELECT * FROM strategy_merges WHERE merged_into=? ORDER BY rowid",
                               (merged_into,))):
        row = H.task(c, r["original"]) or {}
        out.append({"task_id": r["original"], "title": row.get("title") or "", "asker": r["asker"],
                    "asker_name": _name(c, r["asker"]), "state": r["state"], "note": r["note"]})
    return out


def merge(c, actor, person, title, question, task_ids):
    """File one item for `person` standing for the tasks in `task_ids`, each waiting on them for a bot. Only the
    Strategy Bot, only at level 2 or 3, and only within its 5 a day for that person."""
    from . import strategy_suggest
    if actor != STRATEGY_BOT:
        raise H.Refused("identity", f"Only the {strategy_bot.name(c)} merges the asks waiting on a person")
    if not strategy_suggest.suggests(c):
        raise H.Refused("state", f"The {strategy_bot.name(c)} merges asks at level 2 or 3; it is at level "
                                 f"{strategy_bot.level(c)} here")
    if not H.is_human(person) or not H.human(c, H.actor_id(person)):
        raise H.Refused("reach", "merge asks waiting on a person on the roster")
    ids = list(dict.fromkeys(str(t or "").strip() for t in task_ids or [] if str(t or "").strip()))
    if not MIN_ORIGINALS <= len(ids) <= MAX_ORIGINALS:
        raise H.Refused("lint", f"merge {MIN_ORIGINALS} to {MAX_ORIGINALS} different tasks")
    title, question = " ".join(str(title or "").split())[:300], str(question or "").strip()[:4000]
    if not title or not question:
        raise H.Refused("lint", "say the one question the person answers for all of them: a title and the question")
    taken = hidden(c, person)
    found = []
    for task_id in ids:
        row = H.task(c, task_id)
        if not row or not H.task_private_readable(c, STRATEGY_BOT, row) or not H.task_private_readable(c, person, row):
            raise H.Refused("not-found", f"no task {task_id} you may read")
        how = waiting_on(c, person, row)
        if not how:
            raise H.Refused("state", f'"{row["title"]}" is not waiting on {_name(c, person)} now')
        if how[1] == STRATEGY_BOT:
            raise H.Refused("kind",
                            f'"{row["title"]}" is the {strategy_bot.name(c)}\'s own ask; merge other bots\' asks')
        if task_id in taken:
            raise H.Refused("duplicate", f'"{row["title"]}" is already merged into another item')
        found.append((row, how))
    who = "\n".join(f'- {_name(c, how[1])}: "{row["title"]}"' for row, how in found)
    body = (f"{question}\n\n{len(found)} bots asked you this; it is one item instead of {len(found)}:\n{who}\n"
            f"Answer here once and the {strategy_bot.name(c)} passes your answer to each of them.")
    # Its own task with one open question to the person, as `hub task question` files one: every way they answer a
    # bot (a reply, "answer" in Needs you) closes that question and reaches `on_reply`.
    item = H.task_create(c, STRATEGY_BOT, title, body, STRATEGY_BOT, lane="company", lint=False)
    H.task_comment(c, STRATEGY_BOT, item["id"], question, ask={"who": person})
    H.task_update(c, STRATEGY_BOT, item["id"], status="waiting", waiting_on=person, note=question[:1000])
    at = H.now()
    for row, how in found:
        c.execute("INSERT INTO strategy_merges (merged_into, original, person, asker, how, ask_id, created_at) "
                  "VALUES (?,?,?,?,?,?,?)", (item["id"], row["id"], person, how[1], how[0], how[2], at))
        H.task_comment(c, STRATEGY_BOT, row["id"],
                       f'Merged with {len(found) - 1} similar ask(s) into one item for {_name(c, person)}: "{title}". '
                       "Their answer comes back to this task; nothing to do until then.", wake=False)
    H.event(c, actor, "strategy_bot.merged", item["id"], {"person": person, "originals": [r["id"] for r, _ in found]})
    H.recount(c, [how[1] for _, how in found])
    return {"task": H.task(c, item["id"]), "originals": originals(c, item["id"])}


def _settle(c, merged_into, original, state, note):
    c.execute("UPDATE strategy_merges SET state=?, settled_at=?, note=? WHERE merged_into=? AND original=?",
              (state, H.now(), note[:500], merged_into, original))


def _pass_on(c, person, link, item, text):
    """The person's answer to one original, as the bot that asked reads it. Returns why it couldn't be, or None."""
    row = H.task(c, link["original"])
    if not _current(c, link, row):
        # Answered there already, or asked something else since: this answer was for what was merged, not that.
        return "it no longer waits on them for what was merged"
    said = f'{_name(c, person)} answered this in the {strategy_bot.name(c)}\'s merged item "{item["title"]}": "{text}"'
    if link["how"] == "asked":
        H.task_ask_resolve(c, STRATEGY_BOT, row["id"], link["ask_id"], quote=text, relay=True)
        return None
    H.task_comment(c, STRATEGY_BOT, row["id"], said)
    if link["how"] == "waiting":
        # The person has answered: the next move is the bot's, as when they reply on its task themselves.
        c.execute("UPDATE tasks SET waiting_on=NULL WHERE id=?", (row["id"],))
        H._task_event(c, row["id"], STRATEGY_BOT, "waiting_on", person, None, "")
    return None


def on_reply(c, actor, row, text):
    """A person's reply on a merged item they were asked (hubdb.task_comment, hubdb.answer): passed to every original,
    then the item is done; called again for the same reply, it finds nothing left to pass on. An original it can't
    reach is back in their Needs you, with the reason kept."""
    if not row or row["owner_id"] != STRATEGY_BOT or not H.is_human(actor):
        return
    links = H._rows(c.execute("SELECT * FROM strategy_merges WHERE merged_into=? AND person=? AND state='merged'",
                              (row["id"], actor)))
    if not links:
        return
    passed, back = 0, []
    for link in links:
        c.execute("SAVEPOINT strategy_merge_answer")
        try:
            why = _pass_on(c, actor, link, row, text)
        except H.Refused as exc:
            c.execute("ROLLBACK TO strategy_merge_answer")
            why = exc.detail or str(exc.rule)
        c.execute("RELEASE strategy_merge_answer")
        if why:
            _settle(c, row["id"], link["original"], "released", f"Not passed on: {why}")
            back.append(why)
        else:
            _settle(c, row["id"], link["original"], "answered", "")
            passed += 1
    note = f"Answered; passed to {passed} bot(s)." + (f" {len(back)} could not be: {'; '.join(back)}." if back else "")
    H.task_update(c, STRATEGY_BOT, row["id"], status="done", note=note[:1000], quiet=True)
    H.recount(c, [link["asker"] for link in links])
