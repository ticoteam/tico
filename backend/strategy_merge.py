"""The Strategy Owner's merged asks (docs/goals-and-kpis.md, "Your Strategy Owner"; spec: Head of Product, Strategy Owner,
test 13), from before a bot's question to a person was a message marked ❓ (backend/question_marks.py).

A merged item was a task for the person in General, with one question on it, that named every original: a bot's open
question to the person on its own task (a requirement of kind `answer`). The originals left the person's Needs you
(`hidden`) while the merged item was open. Questions are ❓ now, never requirements, so nothing new is merged: `merge`
refuses (`RETIRED`).

A merged item from before still works: the person answers it once, and `on_answer` passes the answer to every original
that is still asked, as their answer to it (`H.task_answer`, as them), which wakes the bot that asked. Then the merged
item is done. An original whose answer can't be passed on, or every original of a merged item that ends without an
answer, is back in the person's Needs you as it was. A merged item counts in what the Strategy Owner asked today
(backend/strategy_bot.py `asked_today`)."""

from . import strategy_bot
from . import hubdb as H

STRATEGY_BOT = "bot:" + strategy_bot.SLUG
RETIRED = (
    "Nothing to merge: a bot's question to a person is a ❓ message on its own task now, and their reply clears it"
)


def _name(c, actor):
    if H.is_bot(actor):
        found = H.bot(c, H.actor_id(actor)) or {}
        return found.get("display_name") or found.get("slug") or H.actor_id(actor)
    return (H.human(c, H.actor_id(actor)) or {}).get("name") or H.actor_id(actor or "")


def _asks(c, person, task_id):
    """The open questions a bot asked `person` on this task's open visit, oldest first: [(requirement, asker)]."""
    from . import task_requirements as R

    row = H.task(c, task_id)
    if not row or row["status"] != "open":
        return []
    visit = c.execute("SELECT id FROM visits WHERE task_id=? AND left_at IS NULL", (task_id,)).fetchone()
    if not visit:
        return []
    answered = R.newest_answers(c, visit[0])
    out = []
    for requirement in R.on_visit(c, visit[0]):
        asker = requirement.get("added_by_id")
        if (
            requirement["kind"] == "answer"
            and H.is_bot(asker)
            and requirement["id"] not in answered
            and R.person_of(c, requirement, visit[0]) == person
        ):
            out.append((requirement, asker))
    return out


def _current(c, link):
    """Whether the original is still asked of the person as it was when merged: the same question, open, theirs."""
    return any(r["id"] == link["ask_id"] for r, _ in _asks(c, link["person"], link["original"]))


def hidden(c, person):
    """The questions (requirement ids) the Strategy Owner merged for this person that stay out of their Needs you: while
    the merged item is open and the original is still asked as it was when merged."""
    return {link["ask_id"] for link in _hidden_links(c, person)}


def hidden_by_item(c, person):
    """{merged item id: [requirement ids of its originals out of the person's Needs you]} while each merged item is
    open, so the merged item names what it stands for."""
    out = {}
    for link in _hidden_links(c, person):
        out.setdefault(link["merged_into"], []).append(link["ask_id"])
    return out


def _hidden_links(c, person):
    return [
        link
        for link in H._rows(
            c.execute(
                "SELECT m.* FROM strategy_merges m JOIN tasks t ON t.id=m.merged_into "
                "WHERE m.person=? AND m.state='merged' AND t.status='open'",
                (person,),
            )
        )
        if _current(c, link)
    ]


def originals(c, merged_into):
    """[{task_id, requirement_id, title, asker, asker_name, state, note}] for one merged item."""
    out = []
    for r in H._rows(c.execute("SELECT * FROM strategy_merges WHERE merged_into=? ORDER BY rowid", (merged_into,))):
        row = H.task(c, r["original"]) or {}
        out.append(
            {
                "task_id": r["original"],
                "requirement_id": r["ask_id"],
                "title": row.get("title") or "",
                "asker": r["asker"],
                "asker_name": _name(c, r["asker"]),
                "state": r["state"],
                "note": r["note"],
            }
        )
    return out


def merge(c, actor, person, title, question, task_ids):
    """Refused: a bot's question to a person is a ❓ now (`RETIRED`), so no bot's question waits as a requirement to
    merge. Only the Strategy Owner was ever let."""
    if actor != STRATEGY_BOT:
        raise H.Refused("identity", f"Only the {strategy_bot.name(c)} merges the asks waiting on a person")
    raise H.Refused("state", RETIRED)


def _settle(c, merged_into, original, state, note):
    c.execute(
        "UPDATE strategy_merges SET state=?, settled_at=?, note=? WHERE merged_into=? AND original=?",
        (state, H.now(), note[:500], merged_into, original),
    )


def on_answer(c, actor, task_id, value):
    """The person answered the question on a merged item (after `H.task_answer` on a task the Strategy Owner filed):
    their answer goes to every original still asked of them, as their own answer there, and the merged item is done.
    One that is not asked any more is back to how it was, with the reason kept. Anything else is left alone."""
    row = H.task(c, task_id)
    if not row or row.get("requester_id") != STRATEGY_BOT or row["owner_id"] != actor or not H.is_human(actor):
        return None
    links = H._rows(
        c.execute("SELECT * FROM strategy_merges WHERE merged_into=? AND person=? AND state='merged'", (task_id, actor))
    )
    if not links:
        return None
    text = str(value or "").strip()
    passed, back = 0, []
    for link in links:
        why = None
        if not _current(c, link):
            why = "it is not asked of them any more"
        else:
            c.execute("SAVEPOINT strategy_merge_answer")
            try:
                H.task_answer(c, actor, link["original"], link["ask_id"], text[:1000])
            except H.Refused as exc:
                c.execute("ROLLBACK TO strategy_merge_answer")
                why = exc.detail or str(exc.rule)
            c.execute("RELEASE strategy_merge_answer")
        if why:
            _settle(c, task_id, link["original"], "released", f"Not passed on: {why}")
            back.append(why)
        else:
            _settle(c, task_id, link["original"], "answered", "")
            passed += 1
    note = f"Answered; passed to {passed} bot(s)." + (f" {len(back)} could not be: {'; '.join(back)}." if back else "")
    H.attempt(c, H.task_done, H.KEEPER, task_id, note)
    return note
