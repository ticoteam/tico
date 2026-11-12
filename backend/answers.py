# pyright: strict
"""Answers to what a task needs (docs/pipelines-v3.md, "Asks" and "What your answer does").

The one writer of `answers` rows, apart from the `pushed_past` rows `task_engine.move` writes. The owner says done, a
person OKs or sends back, a person answers a question, an ask is given to someone else, or an answer is taken back. The
answer is saved and the task moves on in the same step if that was the last thing it waited for. When the answer hands
the turn back to a bot, the bot is woken once with the person's words.

Thumbs on an ask are `feedbacks` rows, written through `feedback.rate`.

Every function takes the caller's open write transaction and never commits. Each returns the task as
`task_engine.task()` gives it after the write, with `answer_id` where an answers row was written, and refuses through
`hubdb.refuse` with a rule that starts with `task_`, or `not-found`. No answer, give or thumb changes `tasks.version`.
Giving the owner's own "done" to someone else is a reassign, which does.

Names and signatures are in docs/dev/pipelines-v3-build.md.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NoReturn, cast

from . import hubdb_core, pr_conflicts
from . import task_engine as E
from . import task_requirements as R
from . import task_wakes, validate_gate
from . import waiting_on as W

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

# hubdb_core is untyped: its actor helpers get their types here.
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human

DONE, OK, NO, AGAIN, GIVE, PUSHED_PAST = "done", "ok", "no", "again", "give", "pushed_past"
# The kinds a person (or a named bot) answers by hand. A page is answered once Jev has looked (task_requirements._page).
BY_HAND = ("owner_done", "ok", "answer", "page")
# Kinds that are an ask: they can name a person, and take a thumb (C29).
ASKS = ("ok", "answer")

# Thumbs on an ask are feedbacks rows with this subject type (migration 20261110020000) and the subject id
# "<visit id>:<requirement id>".
ASK_SUBJECT = "ask"


def _h() -> Any:
    from . import hubdb

    return hubdb


def _feedback() -> Any:
    from . import feedback

    return feedback


def _tc() -> Any:
    from . import task_calls

    return task_calls


def _refuse(conn: "sqlite3.Connection", by: str, rule: str, detail: str, task_id: str | None = None) -> NoReturn:
    _h().refuse(conn, by, rule, detail, task=task_id)
    raise AssertionError("refuse() always raises")  # pragma: no cover


def _part(row: Row | None, key: str) -> Row:
    """The dict a row carries under `key` (a task's open visit, its pipeline), or an empty one."""
    found: Row | None = (row or {}).get(key)
    return found or {}


def _name(conn: "sqlite3.Connection", actor: str | None) -> str:
    return str(_h().actor_name(conn, actor)) if actor else "nobody"


def ask_subject_id(visit_id: str, requirement_id: str) -> str:
    """The feedbacks subject id of one ask: a requirement, in one visit of one task."""
    return f"{visit_id}:{requirement_id}"


def ask_of(conn: "sqlite3.Connection", subject_id: str) -> Row | None:
    """The ask a feedbacks subject id names: {task_id, visit, requirement, bot}, or None when there is no such ask.
    `bot` is the bot whose ask it is (as in `_ask`), or None when it is no bot's."""
    H = _h()
    visit_id, _, requirement_id = subject_id.partition(":")
    visit: Row | None = H._one(conn, "SELECT * FROM visits WHERE id=?", (visit_id,))
    requirement = R.get(conn, requirement_id)
    if not visit or not requirement or requirement["kind"] not in ASKS:
        return None
    if requirement["kind"] == "answer" and requirement.get("stage_id"):
        return None  # a stage's answer takes no thumb (_ask)
    if requirement.get("visit_id"):
        if requirement["visit_id"] != visit_id:
            return None
        bot = requirement.get("added_by_id")
    else:
        if requirement.get("stage_id") != visit["stage_id"]:
            return None
        owner = conn.execute("SELECT owner_id FROM tasks WHERE id=?", (visit["task_id"],)).fetchone()
        bot = owner[0] if owner else None
    return {
        "task_id": visit["task_id"],
        "visit": visit,
        "requirement": requirement,
        "bot": bot if is_bot(bot) else None,
    }


# ----------------------------------------------------------------------------- checks
def _open_task(conn: "sqlite3.Connection", task_id: str, by: str) -> Row:
    row = E.task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if row["status"] != "open":
        _refuse(conn, by, "task_ended", f"The task is {row['stage']['name']}. Reopen it first.", task_id)
    return row


def _asked(conn: "sqlite3.Connection", row: Row, requirement_id: str, by: str) -> Row:
    """The requirement, when the task's open visit needs it: the stage's, or one added to this visit and not
    withdrawn. A requirement of a stage or visit the task has left is refused as a change since the caller looked."""
    H = _h()
    requirement = R.get(conn, requirement_id)
    if not requirement:
        _refuse(conn, by, "not-found", "No such requirement", row["id"])
    visit = row["visit"]
    if any(r["id"] == requirement_id for r in R.for_visit(conn, visit)):
        return requirement
    left = "The task left that stage. Read it again."
    if requirement.get("visit_id"):
        if requirement["visit_id"] == visit["id"]:
            _refuse(conn, by, "task_requirement", "That was withdrawn", row["id"])
        was = H._one(conn, "SELECT task_id FROM visits WHERE id=?", (requirement["visit_id"],))
        if was and was["task_id"] == row["id"]:
            _refuse(conn, by, "task_changed", left, row["id"])
    elif requirement.get("stage_id") == row["stage_id"]:
        _refuse(conn, by, "task_requirement", "This stage no longer asks for that", row["id"])
    else:
        was_stage = E.stage(conn, requirement["stage_id"])
        if was_stage and was_stage.get("pipeline_id") == row["pipeline_id"]:
            _refuse(conn, by, "task_changed", left, row["id"])
    _refuse(conn, by, "task_requirement", "That is not asked of this task", row["id"])


def _may_answer(
    conn: "sqlite3.Connection",
    by: str,
    row: Row,
    requirement: Row,
    value: str,
    notes: str | None,
    *,
    own_click: bool = False,
) -> None:
    """Refuses an answer that does not fit the requirement or comes from someone it is not asked of. Writes nothing.
    An OK added to one task is a person's: a bot never answers one, and the one who asked it OKs it only as a person,
    by their own click in their own session (`own_click`), never through the Assistant, BotOps or a token."""
    task_id = row["id"]
    kind = requirement["kind"]
    if value == PUSHED_PAST:
        _refuse(conn, by, "task_requirement", "Pushing past is a move, with a reason", task_id)
    if kind not in BY_HAND:
        _refuse(conn, by, "task_requirement", f"{R.name_of(requirement)} is not answered by hand", task_id)
    if kind == "owner_done":
        if value != DONE:
            _refuse(conn, by, "task_requirement", "Say done", task_id)
        manager = _part(row, "pipeline").get("manager_id")
        # The engine itself says it for code that finishes a task for nobody (a routine's own upkeep), never to get
        # past anything else the stage needs.
        if by not in (row.get("owner_id"), manager, E.SYSTEM) and not is_human(by):
            _refuse(conn, by, "task_requirement", "Only the owner, the manager or a person says it is done", task_id)
        return
    if kind == "page":
        _may_answer_page(conn, by, row, requirement, value, notes)
        return
    if R.owners_answer(requirement):
        # A stage's answer that names nobody is the owner's, given as they say done: by the owner, the manager or a
        # person.
        if not value:
            _refuse(conn, by, "task_requirement", "Give an answer", task_id)
        if by not in (row.get("owner_id"), _part(row, "pipeline").get("manager_id")) and not is_human(by):
            _refuse(conn, by, "task_requirement", "Only the owner, the manager or a person answers this", task_id)
        return
    person = R.person_of(conn, requirement, row["visit"]["id"])
    if kind == "ok" and value == AGAIN:
        newest = R.newest_answers(conn, row["visit"]["id"]).get(requirement["id"])
        if not newest or newest["answer"] != NO:
            _refuse(conn, by, "task_requirement", "Look again comes after a send back", task_id)
        if by not in (row.get("owner_id"), W.holder(conn, row)):
            _refuse(conn, by, "task_requirement", "Only the task's owner asks to look again", task_id)
        return
    if kind == "ok" and value not in (OK, NO):
        _refuse(conn, by, "task_requirement", "An OK takes ok or no", task_id)
    if kind == "ok" and requirement.get("visit_id") and not is_human(by):
        _refuse(conn, by, "task_requirement", "An OK is a person's to give", task_id)
    if kind == "ok" and value == OK and requirement.get("visit_id") and by == requirement.get("added_by_id"):
        if not own_click:
            _refuse(conn, by, "task_requirement", "Your own ask is OK'd only by your own click in Tico", task_id)
    if kind == "ok":
        from . import task_asks

        # The Assistant and BotOps never answer OK to an OK, bound or plain, whichever caller brings it here: the value
        # is already stripped (answer), so " ok " is "ok".
        if value == OK and task_asks.acting_for_people(by):
            _refuse(conn, by, "task_requirement", task_asks.NOT_YOURS, task_id)
        # An OK bound to exact content is given only through its preview, with the hash of what was shown
        # (task_asks.give_ok). A send back stays a plain answer.
        refused = task_asks.refuses_plain_ok(conn, requirement["id"], value)
        if refused:
            _refuse(conn, by, "task_requirement", refused, task_id)
    if kind == "answer" and not value:  # a choice or any text: "no" is an answer like any other
        _refuse(conn, by, "task_requirement", "Give an answer", task_id)
    # A requirement that names nobody (a stage's OK), or a person who left, is any person's. A bot answers only when it
    # is named.
    if by != person and not (person is None and is_human(by)):
        _refuse(conn, by, "task_requirement", f"This is asked of {_name(conn, person)}", task_id)
    if R.steers(kind, value) and value == NO and not (notes or "").strip():
        _refuse(conn, by, "task_why", "Say what to change", task_id)


def _may_answer_page(
    conn: "sqlite3.Connection", by: str, row: Row, requirement: Row, value: str, notes: str | None
) -> None:
    """A page takes ok or no, and only once Jev has looked and it waits on someone: the person it names after Jev passed
    it, a person deciding what Jev was unsure of or could not check, or the manager bot deciding what Jev was unsure of
    twice (task_requirements._unsure_to_bots), who says why either way. Never Jev's turn, never the owner's."""
    task_id = row["id"]
    if value not in (OK, NO):
        _refuse(conn, by, "task_requirement", "A page takes ok or no. To change it, attach a new version.", task_id)
    if value == OK:
        from . import task_asks

        if task_asks.acting_for_people(by):
            _refuse(conn, by, "task_requirement", task_asks.NOT_YOURS, task_id)
    newest = R.newest_answers(conn, row["visit"]["id"]).get(requirement["id"])
    standing = R.standing(conn, row, requirement, newest)
    if standing.state != R.WAITING or R.outside(standing.on):
        _refuse(conn, by, "task_requirement", f"{R.name_of(requirement)} is not waiting on anyone", task_id)
    person = standing.on
    if by != person and not (person is None and is_human(by)):
        _refuse(conn, by, "task_requirement", f"This is asked of {_name(conn, person)}", task_id)
    if value == NO and not (notes or "").strip():
        _refuse(conn, by, "task_why", "Say what to change", task_id)
    if value == OK and standing.line.startswith(R.DECIDE) and not (notes or "").strip():
        _refuse(conn, by, "task_why", "Say why it meets the check", task_id)
    if value == OK and R.asked_to_review(standing, by) and not (notes or "").strip():
        _refuse(conn, by, "task_why", "Say which commits you read and what you checked", task_id)


def _page_version(
    conn: "sqlite3.Connection", by: str, row: Row, requirement: Row, page_version: str | None
) -> int | None:
    """The page version an answer on a page is for: the one that counts now (task_requirements.page_for: the one
    attached, or the one a person's OK brought back from Merge). A caller that names the page it was shown
    (`<file id>@<version>`) is refused when another is attached since (`task_changed`): another file at the same
    version number is another page."""
    if requirement["kind"] != "page":
        return None
    page, _ = R.page_for(conn, row, requirement)
    now = int(page["version"]) if page else None
    if page_version not in (None, "") and (not page or str(page_version) != f"{page['file_id']}@{now}"):
        _refuse(conn, by, "task_changed", "The page changed. Look again.", row["id"])
    return now


# ----------------------------------------------------------------------------- writes
def _write(
    conn: "sqlite3.Connection",
    row: Row,
    requirement_id: str,
    by: str,
    value: str,
    notes: str | None,
    page_version: int | None = None,
    typed: Row | None = None,
) -> str:
    """Write one answers row. `typed` is the answer requirement it meets: its name and type go on the row with it. An
    answer on a page keeps the commits it was given at (`_heads`)."""
    H = _h()
    answer_id = H.new_id()
    settings: Row = (typed or {}).get("settings") or {}
    name = str(settings["name"]) if settings.get("name") else None
    value_type = R.answer_type(typed) if typed else None
    heads = _heads(conn, row) if page_version is not None else None
    conn.execute(
        "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, notes, page_version, name, value_type, "
        "heads, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            answer_id,
            row["visit"]["id"],
            requirement_id,
            str(by),
            value,
            notes,
            page_version,
            name,
            value_type,
            heads,
            H.now(),
        ),
    )
    data: Row = {"requirement": requirement_id, "answer_id": answer_id}
    if page_version is not None:
        data["page_version"] = page_version
    # Through the task's history writer: an answer the Assistant or BotOps gave with the person's rights says so (via).
    H._task_event(conn, row["id"], by, "answer", None, value, notes, data=data)
    return answer_id


def _heads(conn: "sqlite3.Connection", row: Row) -> str | None:
    """The newest commits of the task's linked PRs, as an answer on a page keeps them: a person's OK counts only while
    they are still the newest, and a bot's review of the commits after the reviews names the ones it read
    (task_requirements._new_commits). None when the task links no PR Tico has read."""
    import json

    heads = R.heads_now(conn, row)
    return json.dumps(heads, sort_keys=True) if heads else None


def _wake_key(row: Row, answer_id: str) -> str:
    return f"{row['id']}:{row['visit']['id']}:answer:{answer_id}"


def _wake(
    conn: "sqlite3.Connection", row: Row, target: str | None, lead: str, wake_key: str, shows: object = None
) -> str | None:
    """Wake a bot about an open task once per key. A snoozed task wakes nobody. A bot that cannot run gets it when it
    can. `shows`, the pages the ask showed, go with the words as their provenance (refs.body_sources), so a reader who
    may no longer open one of them reads none of the words (files.readable_page_refs)."""
    if not is_bot(target) or row["status"] != "open" or row.get("snooze"):
        return None
    sources = [str(ref) for ref in cast("list[object]", shows)] if isinstance(shows, list) else []
    _, refs = _h().sourced_text(lead, sources)
    return task_wakes.add(
        conn,
        row,
        target,
        row["visit"],
        task_wakes.body(conn, row, lead, to=target),
        wake_key=wake_key,
        refs=refs or None,
    )


def _whose(conn: "sqlite3.Connection", row: Row, bot: str | None) -> str | None:
    """Who gets news meant for `bot`: the bot itself while it has a turn on the task, else the one bot whose turn it is
    now (`waiting_on.turn`), so only whoever has the turn is woken (R2). None when it is nobody's turn, or a
    person's."""
    if is_bot(bot) and bot in W.turns(conn, row):
        return bot
    return W.turn(conn, row, paused=True)


def _tell_bot(
    conn: "sqlite3.Connection", row: Row, requirement: Row, by: str, value: str, notes: str | None, key: str
) -> None:
    """After an answer that left the task where it is: a send back is the bot's turn, with the notes word for word; an
    answer to something that was added to this task (a question or an OK) goes to the bot that asked, with the ask and
    the answer. A question's answer is never a send back, whatever it says. A bot that has no turn on the task now is
    not woken: whoever has the turn gets the words."""
    steered = R.steers(requirement["kind"], value)
    if steered and value == NO:
        _wake(
            conn,
            row,
            _whose(conn, row, W.holder(conn, row)),
            f"{_name(conn, by)} sent this back: {notes}",
            key,
            requirement.get("shows"),
        )
    elif requirement.get("visit_id") and not steered:
        asker = requirement.get("added_by_id")
        target = _whose(conn, row, asker if is_bot(asker) else W.holder(conn, row))
        lead = f'{_name(conn, by)} answered "{R.name_of(requirement)}": {value}'
        if is_bot(asker) and target != asker:
            lead = f'{_name(conn, by)} answered "{R.name_of(requirement)}", asked by {_name(conn, asker)}: {value}'
        _wake(conn, row, target, lead, key, requirement.get("shows"))


def _done_ends_own_snooze(conn: "sqlite3.Connection", task_id: str, by: str) -> None:
    """Saying done on a snoozed task: the snooze was a wait (for a repository, for an answer) that the done says is
    over. When nothing else is missing, the snooze ends and the task moves now, not when the snooze runs out. Only the
    one who set it ends it this way, and a person's done ends a bot's; a bot never ends a snooze a person set."""
    row = E.task(conn, task_id)
    snoozed = (row or {}).get("snooze")
    if not row or not snoozed or R.unmet(conn, row):
        return
    setter = str(snoozed["by_id"])
    if setter == str(by) or (is_human(by) and is_bot(setter)):
        E.unsnooze(conn, task_id, by, "Done")


def _answer_ends_bots_snooze(conn: "sqlite3.Connection", task_id: str, by: str) -> None:
    """A person answering an ask on a task a bot snoozed: a bot's snooze leaves the ask in their list (waiting_on.needs),
    and the answer is what that snooze waited for, so it ends now and the task goes on, not when the date comes. A
    person's own snooze is theirs to end."""
    row = E.task(conn, task_id)
    snoozed = (row or {}).get("snooze")
    if row and snoozed and is_human(by) and is_bot(snoozed["by_id"]):
        E.unsnooze(conn, task_id, by, "Answered")


def answer(
    conn: "sqlite3.Connection",
    task_id: str,
    requirement_id: str,
    by: str,
    value: str,
    notes: str | None = None,
    *,
    seen_version: int | str | None = None,
    own_click: bool = False,
    page_version: str | None = None,
) -> Row:
    """Answer a requirement of the task's open visit: `done` (owner says done), `ok`, `no` with notes or `again` (an
    OK), `ok` or `no` on a page (for the version attached now; `page_version`, when given, is the `<file id>@<version>`
    the person was shown), or the choice or text (a question). If that was the last thing missing, the task moves in the same step. The
    same answer given twice is one answer. `own_click`: the person's own click in their own session (_may_answer)."""
    row = _open_task(conn, task_id, by)
    E.check_version(conn, by, row, seen_version)
    requirement = _asked(conn, row, requirement_id, by)
    value = str(value or "").strip()
    pinned = _page_version(conn, by, row, requirement, page_version)
    _may_answer(conn, by, row, requirement, value, notes, own_click=own_click)
    if requirement["kind"] == "answer":
        # One exact form: "080" is 80, a choice as the choice is spelled. What does not fit is refused in one line.
        stored, why = R.typed(requirement, value)
        if stored is None:
            _refuse(conn, by, "task_requirement", why, task_id)
        value = stored
    newest = R.newest_answers(conn, row["visit"]["id"]).get(requirement_id)
    same = (str(by), value, notes, pinned, _heads(conn, row) if pinned is not None else None)
    kept = (newest or {}).get("heads")
    if newest and (newest["by_id"], newest["answer"], newest.get("notes"), newest.get("page_version"), kept) == same:
        out = E.task(conn, task_id) or row
        out["answer_id"] = newest["id"]
        return out
    typed = requirement if requirement["kind"] == "answer" else None
    answer_id = _write(conn, row, requirement_id, by, value, notes, pinned, typed)
    if requirement["kind"] == "ok" and value != OK:
        from . import task_asks

        task_asks.revoke(conn, requirement_id)  # a send back ends a bound OK for good (no grant: nothing)
    if validate_gate.sends_back(conn, row, requirement, value):
        # Not validated: back to In development with the note, which files new Engineering work.
        out = validate_gate.not_validated(conn, task_id, by, notes)
        out["answer_id"] = answer_id
        return out
    if requirement["kind"] == "owner_done":
        _done_ends_own_snooze(conn, task_id, by)
    else:
        _answer_ends_bots_snooze(conn, task_id, by)
    out = E.advance(conn, task_id)
    if out is None:
        # The task changed and stays: the never-stuck ladder starts again, before any wake this answer sends.
        E.changed(conn, task_id, by)
        out = E.task(conn, task_id) or row
        _tell_bot(conn, out, requirement, by, value, notes, _wake_key(out, answer_id))
        # A merge conflict held for this answer is the Builder's now, before the task moves on (pr_conflicts).
        pr_conflicts.after_answer(conn, task_id)
    if requirement["kind"] == "answer" and requirement.get("visit_id"):
        from . import strategy_merge

        merged: Any = strategy_merge
        # A person's answer on an item the Strategy Owner merged is their answer to every original still asked of them,
        # and the item is done. Any other task is left alone.
        if merged.on_answer(conn, by, task_id, value):
            out = E.task(conn, task_id) or out
    out["answer_id"] = answer_id
    return out


def give_to(
    conn: "sqlite3.Connection",
    task_id: str,
    requirement_id: str,
    by: str,
    person: str,
    *,
    seen_version: int | str | None = None,
) -> Row:
    """Give an ask to another person: it gets their name and moves to their list. A requirement added to one task gets
    the new name. A stage's OK gets a `give` answer naming them. The owner's own "done" is a reassign (C29: only a
    person is named, and one who is still here). A bot says which version of the task it saw."""
    H = _h()
    row = _open_task(conn, task_id, by)
    E.check_version(conn, by, row, seen_version)
    if not is_human(person):
        _refuse(conn, by, "task_requirement", "Give it to a person", task_id)
    if not R.active_person(conn, person):
        _refuse(
            conn, by, "task_requirement", "That person has left or is not here. Give it to someone who is.", task_id
        )
    requirement = _asked(conn, row, requirement_id, by)
    kind = requirement["kind"]
    if kind == "owner_done":
        if by != row.get("owner_id") and not is_human(by):
            _refuse(conn, by, "task_requirement", "Only the owner or a person gives this task to someone", task_id)
        return E.reassign(conn, task_id, person, by, seen_version=row.get("version"))
    if kind not in ASKS:
        _refuse(conn, by, "task_requirement", f"{R.name_of(requirement)} can't name a person", task_id)
    if kind == "answer" and requirement.get("stage_id"):
        # A give is kept as an answer, and on a stage's answer any value meets it: the stage names who answers.
        _refuse(conn, by, "task_requirement", f"{R.name_of(requirement)} is answered by who the stage names", task_id)
    visit = row["visit"]
    standing = R.standing(conn, row, requirement, R.newest_answers(conn, visit["id"]).get(requirement_id))
    if standing.state != R.WAITING:
        _refuse(conn, by, "task_requirement", "This is not waiting on anyone", task_id)
    current = standing.on
    if not _tc().may_handle_ask(conn, by, row, requirement, recipient=current):
        _refuse(conn, by, "identity", "Only who it waits on, whoever asked it, or who works the task gives it", task_id)
    if person == current:
        return row
    if requirement.get("visit_id"):
        conn.execute("UPDATE requirements SET person_id=? WHERE id=?", (person, requirement_id))
        H._task_event(conn, task_id, by, "give", current, person, None, data={"requirement": requirement_id})
        E.changed(conn, task_id, by)
        return E.task(conn, task_id) or row
    answer_id = _write(conn, row, requirement_id, by, GIVE, person)
    E.changed(conn, task_id, by)
    out = E.task(conn, task_id) or row
    out["answer_id"] = answer_id
    return out


def undo(conn: "sqlite3.Connection", answer_id: str, by: str) -> Row:
    """Take an answer back: only whoever gave it (task_calls.may_handle_ask). While the task is still in that visit the answer is marked
    undone. If the task moved on because of it, and has not moved since, it goes back to that stage: an ordinary move
    back."""
    H = _h()
    found = H._one(conn, "SELECT * FROM answers WHERE id=?", (answer_id,))
    visit = H._one(conn, "SELECT * FROM visits WHERE id=?", (found["visit_id"],)) if found else None
    row = E.task(conn, visit["task_id"]) if visit else None
    if not found or not visit or not row:
        _refuse(conn, by, "not-found", "No such answer")
    task_id = row["id"]
    if found["answer"] == PUSHED_PAST:
        _refuse(conn, by, "task_requirement", "A push past is a move. Move the task back instead.", task_id)
    if not _tc().may_handle_ask(conn, by, row, R.get(conn, found["requirement_id"]) or {}, answered_by=found["by_id"]):
        _refuse(conn, by, "identity", "Only who gave the answer takes it back", task_id)
    if found.get("undone_at"):
        return row
    now = _part(row, "visit")
    stays = visit.get("left_at") is None
    # Only an answer that meets a requirement can move a task: not a send back, a look again or a give on an OK. On a
    # question every answer meets it, "no" too.
    answered = R.get(conn, found["requirement_id"])
    moved_by_it = (
        not R.steers(answered["kind"] if answered else "ok", found["answer"])
        and visit.get("how") == "met"
        and now.get("entered_how") == "met"
        and now.get("entered_at") == visit.get("left_at")
    )
    if not stays and not moved_by_it:
        _refuse(conn, by, "task_changed", "The task moved since. Move it back by hand.", task_id)
    conn.execute("UPDATE answers SET undone_at=? WHERE id=?", (H.now(), answer_id))
    if answered and answered["kind"] == "ok" and found["answer"] in (OK, NO):
        from . import task_asks

        # An OK taken back is revoked for good; a send back taken back leaves its revocation where it is.
        task_asks.revoke(conn, found["requirement_id"])
    # A wake this answer caused and no run took yet is moot.
    task_wakes.cancel(conn, f"{task_id}:{visit['id']}:answer:{answer_id}")
    H._task_event(conn, task_id, by, "answer_undone", found["answer"], None, None, data={"answer_id": answer_id})
    if stays:
        moved = E.advance(conn, task_id)
        if moved is None:
            E.changed(conn, task_id, by)  # whoever has it now is woken by the never-stuck check
        return moved or E.task(conn, task_id) or row
    return E.move(conn, task_id, visit["stage_id"], by, "Answer taken back", seen_version=row.get("version"))


# ----------------------------------------------------------------------------- thumbs
def _ask(conn: "sqlite3.Connection", task_id: str, requirement_id: str, by: str) -> tuple[Row, Row, Row, str]:
    """The task, the requirement, the visit it was asked in and the bot whose ask it is. A requirement added to one
    task is the ask of the bot that added it. A stage's OK is asked in the task's latest visit to that stage, about the
    work of the bot that owns the task."""
    H = _h()
    if not is_human(by):
        _refuse(conn, by, "task_requirement", "Only a person gives a thumb", task_id)
    row = E.task(conn, task_id)
    requirement = R.get(conn, requirement_id)
    if not row or not requirement:
        _refuse(conn, by, "not-found", "No such ask", task_id)
    if requirement.get("visit_id"):
        visit = H._one(conn, "SELECT * FROM visits WHERE id=? AND task_id=?", (requirement["visit_id"], task_id))
        bot = requirement.get("added_by_id")
    else:
        visit = H._one(
            conn,
            "SELECT * FROM visits WHERE task_id=? AND stage_id=? ORDER BY entered_at DESC, id DESC LIMIT 1",
            (task_id, requirement["stage_id"]),
        )
        bot = row.get("owner_id")
    if not visit or requirement["kind"] not in ASKS:
        _refuse(conn, by, "task_requirement", "That is not an ask on this task", task_id)
    if requirement["kind"] == "answer" and requirement.get("stage_id"):
        # A thumb rates a bot's ask, and a send back would answer the stage's question with the reason.
        _refuse(conn, by, "task_requirement", "A stage's answer takes no thumb", task_id)
    if not is_bot(bot):
        _refuse(conn, by, "task_requirement", "Only a bot's ask takes a thumb", task_id)
    return row, requirement, visit, str(bot)


def _rate(
    conn: "sqlite3.Connection", by: str, row: Row, visit: Row, requirement: Row, bot: str, rating: str, reason: str
) -> str:
    subj: Row = {
        "bot_id": bot,
        "conversation_id": row.get("conversation_id"),
        "attempt": (None, None, None),
        "revision": row.get("version"),
    }
    subject_id = ask_subject_id(visit["id"], requirement["id"])
    saved: Row = _feedback().rate(conn, by, ASK_SUBJECT, subject_id, subj, rating, reason)
    return str(saved["id"])


def thumbs_up(conn: "sqlite3.Connection", task_id: str, requirement_id: str, by: str) -> Row:
    """👍 on a bot's ask: one tap, on the bot's record. Rating again replaces it."""
    row, requirement, visit, bot = _ask(conn, task_id, requirement_id, by)
    out = dict(row)
    out["feedback_id"] = _rate(conn, by, row, visit, requirement, bot, "up", "")
    return out


def thumbs_down(
    conn: "sqlite3.Connection", task_id: str, requirement_id: str, by: str, reason: str, *, send_back: bool = True
) -> Row:
    """👎 on a bot's ask, with what was wrong. With `send_back` an open ask also leaves the person's list: one a bot
    added to the task is withdrawn and the bot is woken once with the person's words; a stage's OK is sent back
    (`answer(..., "no", notes=reason)`)."""
    row, requirement, visit, bot = _ask(conn, task_id, requirement_id, by)
    reason = str(reason or "")
    if send_back and not reason.strip():
        _refuse(conn, by, "task_why", "Say what was wrong", task_id)
    still_asked = row["status"] == "open" and _part(row, "visit").get("id") == visit["id"]
    still_asked = still_asked and any(r["id"] == requirement_id for r in R.for_visit(conn, visit))
    asked = R.person_of(conn, requirement, visit["id"])
    if send_back and still_asked and not _tc().may_handle_ask(conn, by, row, requirement, recipient=asked):
        _refuse(conn, by, "identity", "Only who was asked, whoever asked, or who works the task sends it back", task_id)
    out: Row = dict(row)
    if send_back and still_asked and not requirement.get("visit_id"):
        out = answer(conn, task_id, requirement_id, by, NO, reason)
    feedback_id = _rate(conn, by, row, visit, requirement, bot, "down", reason)
    if send_back and still_asked and requirement.get("visit_id"):
        out = E.withdraw_requirement(conn, requirement_id, by, reason) or E.task(conn, task_id) or row
        target = _whose(conn, out, bot) if out["status"] == "open" else None
        lead = f'{_name(conn, by)} sent back your ask "{R.name_of(requirement)}": {reason}'
        if target != bot:
            lead = f'{_name(conn, by)} sent back {_name(conn, bot)}\'s ask "{R.name_of(requirement)}": {reason}'
        _wake(conn, out, target, lead, f"{task_id}:{visit['id']}:thumbs_down:{feedback_id}")
    out["feedback_id"] = feedback_id
    return out


def thumbs_down_count(conn: "sqlite3.Connection", bot: str, days: int = 30) -> int:
    """How many 👎 people gave one bot's asks in the last `days` days. On the third in 30 days the 👎 sheet opens with
    a rule drafted (C21)."""
    H = _h()
    since = H.shift(H.now(), days=-days)
    found = conn.execute(
        "SELECT COUNT(*) FROM feedbacks WHERE bot_id=? AND rating='down' AND subject_type=? AND updated_at>=?",
        (bot, ASK_SUBJECT, since),
    ).fetchone()
    return int(found[0]) if found else 0
