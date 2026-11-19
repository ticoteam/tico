# pyright: strict
"""Answers to what a task needs (docs/dev/pipelines-v3.md, "Asks" and "What your answer does").

The one writer of `answers` rows, apart from the `pushed_past` rows `task_engine.move` writes. The owner says done, a
person OKs or sends back, an ask is given to someone else, or an answer is taken back. The answer is saved and the task
moves on in the same step if that was the last thing it waited for. When a send back hands the turn back to a bot, the
bot is woken once with the person's words. A question to a person is a message marked ❓ (backend/question_marks.py),
answered by a reply in the chat, never here; a person's answer to a stage's page approval clears their open ❓ on the
task (question_marks.approval_answered).

Every function takes the caller's open write transaction and never commits. Each returns the task as
`task_engine.task()` gives it after the write, with `answer_id` where an answers row was written, and refuses through
`hubdb.refuse` with a rule that starts with `task_`, or `not-found`. No answer or give changes `tasks.version`.
Giving the owner's own "done" to someone else is a reassign, which does.

Names and signatures are in docs/dev/pipelines-v3-build.md.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NoReturn, cast

from . import hubdb_core, pr_conflicts, question_marks
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

DONE, OK, NO, GIVE, PUSHED_PAST = "done", "ok", "no", "give", "pushed_past"
# What the owner saying done again writes on each stage OK that was sent back (reopen_sent_back): it waits on its
# approver again. Nobody answers it by hand: there is no Look again any more.
AGAIN = "again"
# The kinds a person (or a named bot) answers by hand. A page is answered once Jev has looked (task_requirements._page).
BY_HAND = ("owner_done", "ok", "page")


def _h() -> Any:
    from . import hubdb

    return hubdb


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
    person = R.person_of(conn, requirement, row["visit"]["id"])
    if value not in (OK, NO):
        _refuse(conn, by, "task_requirement", "An OK takes ok or no", task_id)
    if validate_gate.held(conn, row, requirement):
        # A bot's OK in Validate is the person's only once Jev passed its page: not from the task page before then.
        _refuse(conn, by, "task_requirement", f"{validate_gate.HELD}.", task_id)
    if requirement.get("visit_id") and not is_human(by):
        _refuse(conn, by, "task_requirement", "An OK is a person's to give", task_id)
    if value == OK and requirement.get("visit_id") and by == requirement.get("added_by_id"):
        if not own_click:
            _refuse(conn, by, "task_requirement", "Your own ask is OK'd only by your own click in Tico", task_id)
    from . import task_asks

    # The Assistant and BotOps never answer OK to an OK, bound or plain, whichever caller brings it here: the value is
    # already stripped (answer), so " ok " is "ok".
    if value == OK and task_asks.acting_for_people(by):
        _refuse(conn, by, "task_requirement", task_asks.NOT_YOURS, task_id)
    # An OK bound to exact content is given only through its preview, with the hash of what was shown
    # (task_asks.give_ok). A send back stays a plain answer.
    refused = task_asks.refuses_plain_ok(conn, requirement["id"], value)
    if refused:
        _refuse(conn, by, "task_requirement", refused, task_id)
    # A requirement that names nobody (a stage's OK), or a person who left, is any person's. A bot answers only when it
    # is named. An OK of a kind any allowed person may give (task_asks.ANY_ALLOWED) passed its rule in give_ok.
    if (
        by != person
        and not (person is None and is_human(by))
        and not task_asks.given_by_another_allowed(conn, requirement["id"], value)
    ):
        _refuse(conn, by, "task_requirement", f"This is asked of {_name(conn, person)}", task_id)
    if value == NO and not (notes or "").strip():
        _refuse(conn, by, "task_why", "Say what to change", task_id)


def _may_answer_page(
    conn: "sqlite3.Connection",
    by: str,
    row: Row,
    requirement: Row,
    value: str,
    notes: str | None,
    *,
    stale: bool = False,
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
    person = standing.on
    if stale and person == R.JEV and (requirement.get("person_id") or R.people_only(requirement)):
        # The approver can refresh a stale key while the Decision model checks the new submission.
        visit = row["visit"]["id"]
        person = R.person_of(conn, requirement, visit)
        if not requirement.get("person_id") and (back := R.last_sent_back(conn, requirement["id"], visit_id=visit)):
            sender = str(back["by_id"] or "")
            if is_human(sender) and R.active_person(conn, sender):
                person = person or sender
    elif standing.state != R.WAITING or R.outside(person):
        _refuse(conn, by, "task_requirement", f"{R.name_of(requirement)} is not waiting on anyone", task_id)
    if by != person and not (person is None and is_human(by)):
        _refuse(conn, by, "task_requirement", f"This is asked of {_name(conn, person)}", task_id)
    if value == NO and not (notes or "").strip():
        _refuse(conn, by, "task_why", "Say what to change", task_id)
    if value == OK and standing.line.startswith(R.DECIDE) and not (notes or "").strip():
        _refuse(conn, by, "task_why", "Say why it meets the check", task_id)
    if value == OK and R.asked_to_review(standing, by) and not (notes or "").strip():
        _refuse(conn, by, "task_why", "Say which commits you read and what you checked", task_id)


# A stale approval key: what the approver is told, beside the version that is submitted now (`extra.approval`).
STALE_APPROVAL = "A newer version is submitted. Look again."
APPROVE_SHOWN = "Approve the version you were shown"


def _deciding_bot(conn: "sqlite3.Connection", by: str, row: Row, requirement: Row) -> bool:
    """Whether `by` is the pipeline's manager bot the page waits on to decide what Jev was unsure of
    (task_requirements._unsure_to_bots). It answers a document page it may not open as it answers a plain page (which
    has no read check): else a bot-held stage's document, such as a Validate's Checked in production, stays stuck. A
    person approving a document is still refused a page they may not read."""
    if not is_bot(by):
        return False
    newest = R.newest_answers(conn, row["visit"]["id"]).get(requirement["id"])
    standing = R.standing(conn, row, requirement, newest)
    return standing.state == R.WAITING and standing.on == by and standing.line.startswith(R.DECIDE)


def _page_version(
    conn: "sqlite3.Connection",
    by: str,
    row: Row,
    requirement: Row,
    page_version: str | None,
    value: str = "",
    visit_id: str | None = None,
    keyed: bool = False,
    page_reader: Callable[[list[str]], bool] | None = None,
    notes: str | None = None,
) -> int | None:
    """The page version an answer on a page is for: the one that counts now (task_requirements.page_for: the one
    attached, or the one a person's OK brought back from Merge). A caller that names the page it was shown
    (`<file id>@<version>`) is refused when another is attached since (`task_changed`): another file at the same
    version number is another page. A document approval's OK carries its whole key, the visit and the version shown:
    a person's from a client (`keyed`, the answer route) is refused without the version (a bot's OK there is a review
    of new commits, never the approval), and a stale key is refused with the version submitted now
    (`extra.approval`), which the approver is shown instead."""
    if requirement["kind"] != "page":
        return None
    page, _ = R.page_for(conn, row, requirement)
    now = int(page["version"]) if page else None
    document = R.document_of(requirement)
    from . import task_pages as P

    pages = P.current_pages(conn, row["visit"]["id"], requirement["id"]) if document else ([page] if page else [])
    if document and not pages and page:
        pages = P.current_pages(conn, str(page["visit_id"]), requirement["id"]) or [page]
    if document and pages:
        refs = [f"page:{p['file_id']}@{p['version']}" for p in pages]
        if page_reader is None:
            from . import files, teammate_access
            from .auth import Auth

            viewer: Any = cast(Callable[..., Any], teammate_access._viewer_for)(conn, by)  # pyright: ignore[reportPrivateUsage]
            policy: Any = cast(Callable[..., Any], Auth.read_policy)(conn)
            readable = viewer is not None and cast(Callable[..., bool], files.page_refs_readable)(
                conn, policy, viewer, refs
            )
        else:
            readable = page_reader(refs)
        if not readable and not _deciding_bot(conn, by, row, requirement):
            _refuse(conn, by, "task_requirement", "This approval is unavailable", row["id"])
    expected = P.version_key(pages)
    if keyed and is_human(by) and document and value == OK and page_version in (None, ""):
        _refuse(conn, by, "task_requirement", f"{APPROVE_SHOWN}: send its page_version", row["id"])
    stale_visit = bool(visit_id) and visit_id != _part(row, "visit").get("id")
    if stale_visit or (page_version not in (None, "") and (not page or str(page_version) != expected)):
        if not document:
            _refuse(conn, by, "task_changed", "The page changed. Look again.", row["id"])
        _may_answer_page(conn, by, row, requirement, value, notes, stale=True)
        approval = {
            "document": document,
            "visit_id": _part(row, "visit").get("id"),
            "file_id": page["file_id"] if page else None,
            "version": now,
            **(
                {"pages": [{"file_id": p["file_id"], "version": p["version"]} for p in pages], "page_version": expected}
                if len(pages) > 1
                else {}
            ),
        }
        _h().refuse(
            conn,
            by,
            "task_changed",
            STALE_APPROVAL,
            task=row["id"],
            extra={"approval": approval},
            stored={"detail": "The approval changed. Read the task again.", "extra": {}},
        )
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
) -> str:
    """Write one answers row. An answer on a page keeps the commits it was given at (`_heads`)."""
    H = _h()
    answer_id = H.new_id()
    heads = _heads(conn, row) if page_version is not None else None
    conn.execute(
        "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, notes, page_version, heads, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (answer_id, row["visit"]["id"], requirement_id, str(by), value, notes, page_version, heads, H.now()),
    )
    data: Row = {"requirement": requirement_id, "answer_id": answer_id}
    if page_version is not None:
        from . import task_pages as P

        data["page_version"] = page_version
        pages = P.current_pages(conn, row["visit"]["id"], requirement_id)
        if not pages and (requirement := R.get(conn, requirement_id)):
            page, _ = R.page_for(conn, row, requirement)
            if page:
                pages = P.current_pages(conn, str(page["visit_id"]), requirement_id)
        data["pages"] = [{"file_id": p["file_id"], "version": p["version"]} for p in pages]
    # Through the task's history writer: an answer the Assistant or BotOps gave with the person's rights says so (via).
    H._task_event(conn, row["id"], by, "answer", None, value, notes, data=data)
    return answer_id


def reopen_sent_back(conn: "sqlite3.Connection", row: Row, by: str, note: str | None = None) -> list[str]:
    """The owner says done again after a send back (task_calls.task_done, or `done` here): each of the stage's OKs that
    was sent back on this visit waits on its approver again, back in their Needs you. An `again` answer by whoever said
    done, with their note, so the send back stays in the history. The answers written."""
    visit: Row = row.get("visit") or {}
    if row.get("status") != "open" or not visit.get("id"):
        return []
    newest = R.newest_answers(conn, visit["id"])
    written: list[str] = []
    for requirement in R.on_stage(conn, visit["stage_id"]):
        if requirement["kind"] == "ok" and (newest.get(requirement["id"]) or {}).get("answer") == NO:
            written.append(_write(conn, row, requirement["id"], by, AGAIN, note))
    return written


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
    OK on something that was added to this task goes to the bot that asked, with the ask and the answer. A bot that
    has no turn on the task now is not woken: whoever has the turn gets the words."""
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
    visit_id: str | None = None,
    keyed: bool = False,
    page_reader: Callable[[list[str]], bool] | None = None,
) -> Row:
    """Answer a requirement of the task's open visit: `done` (owner says done), `ok` or `no` with notes (an OK), or `ok`
    or `no` on a page (for the version attached now; `page_version`, when given, is the `<file id>@<version>` the
    person was shown; on a document approval `visit_id` too, the approval's key, which `keyed` requires). If that was
    the last thing missing, the task moves in the same step. The same answer given twice is one answer. `own_click`:
    the person's own click in their own session (_may_answer)."""
    row = _open_task(conn, task_id, by)
    E.check_version(conn, by, row, seen_version)
    requirement = _asked(conn, row, requirement_id, by)
    value = str(value or "").strip()
    pinned = _page_version(conn, by, row, requirement, page_version, value, visit_id, keyed, page_reader, notes)
    _may_answer(conn, by, row, requirement, value, notes, own_click=own_click)
    newest = R.newest_answers(conn, row["visit"]["id"]).get(requirement_id)
    same = (str(by), value, notes, pinned, _heads(conn, row) if pinned is not None else None)
    kept = (newest or {}).get("heads")
    from . import task_pages as P

    pages_same = (
        not R.document_of(requirement)
        or (
            P.answer_pages(conn, task_id, newest)
            == {str(p["file_id"]): int(p["version"]) for p in P.current_pages(conn, row["visit"]["id"], requirement_id)}
        )
        if newest
        else False
    )
    if (
        newest
        and pages_same
        and (newest["by_id"], newest["answer"], newest.get("notes"), newest.get("page_version"), kept) == same
    ):
        out = E.task(conn, task_id) or row
        out["answer_id"] = newest["id"]
        return out
    answer_id = _write(conn, row, requirement_id, by, value, notes, pinned)
    if requirement["kind"] == "page" and requirement.get("stage_id") and value in (OK, NO):
        # The approval was the person's ask on this task: a bot's ❓ to them here clears with it (one card per person).
        question_marks.approval_answered(conn, task_id, str(by), answer_id)
    if requirement["kind"] == "owner_done":
        reopen_sent_back(conn, row, by, notes)
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
    if kind != "ok":
        _refuse(conn, by, "task_requirement", f"{R.name_of(requirement)} can't name a person", task_id)
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
    # Only an answer that meets a requirement can move a task: not a send back or a give on an OK.
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
