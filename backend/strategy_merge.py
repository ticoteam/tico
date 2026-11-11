"""The Strategy Owner's merged asks (docs/goals-and-kpis.md, "Your Strategy Owner"; spec: Head of Product, Strategy Owner,
test 13).

At level 2 and 3, when several bots ask a person the same question, the Strategy Owner files one item for all of them: a
task for the person in General, with one question on it (an answer from them, `H.task_ask`), that names every
original. An original is a bot's open question to the person on its own task (a requirement of kind `answer` that
names them; never an OK, which is the person's own click on what it lets go). The originals stay as they were for the
bots that asked, but leave the person's Needs you (`hidden`) while the merged item is open. The Strategy Owner decides
what counts as the same question; the server checks only that each original is still asked of that person now.

The person answers the merged item once, and `on_answer` passes the answer to every original that is still asked: as
their answer to it (`H.task_answer`, as them), which wakes the bot that asked. Then the merged item is done.

Nothing is dropped: an original whose answer can't be passed on, or every original of a merged item that ends without an
answer, is back in the person's Needs you as it was. A merged item counts in what the Strategy Owner asked today
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
    """File one item for `person` standing for the bots' questions to them on the tasks in `task_ids`. Only the
    Strategy Owner, only at level 2 or 3, and only within its 5 a day for that person."""
    from . import strategy_suggest

    if actor != STRATEGY_BOT:
        raise H.Refused("identity", f"Only the {strategy_bot.name(c)} merges the asks waiting on a person")
    if not strategy_suggest.suggests(c):
        raise H.Refused(
            "state",
            f"The {strategy_bot.name(c)} merges asks at level 2 or 3; it is at level {strategy_bot.level(c)} here",
        )
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
        asks = [(r, a) for r, a in _asks(c, person, task_id) if a != STRATEGY_BOT and r["id"] not in taken]
        if not asks:
            raise H.Refused("state", f'"{row["title"]}" has no question from a bot to {_name(c, person)} now')
        found.append((row, *asks[0]))
    who = "\n".join(f'- {_name(c, asker)}: "{row["title"]}"' for row, _, asker in found)
    body = (
        f"{question}\n\n{len(found)} bots asked you this; it is one item instead of {len(found)}:\n{who}\n"
        f"Answer it once and the {strategy_bot.name(c)} passes your answer to each of them."
    )
    item = H.task_create(c, STRATEGY_BOT, title, body, person, lane="company", lint=False)
    H.task_ask(c, STRATEGY_BOT, item["id"], person, question, seen_version=item["version"])
    at = H.now()
    for row, requirement, asker in found:
        c.execute(
            "INSERT INTO strategy_merges (merged_into, original, person, asker, how, ask_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (item["id"], row["id"], person, asker, "asked", requirement["id"], at),
        )
        H.task_comment(
            c,
            STRATEGY_BOT,
            row["id"],
            f'Merged with {len(found) - 1} similar ask(s) into one item for {_name(c, person)}: "{title}". '
            "Their answer comes back to this task; nothing to do until then.",
            wake=False,
        )
    H.event(
        c, actor, "strategy_bot.merged", item["id"], {"person": person, "originals": [r["id"] for r, _, _ in found]}
    )
    return {"task": H.task(c, item["id"]), "originals": originals(c, item["id"])}


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
