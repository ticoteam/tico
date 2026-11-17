# pyright: strict
"""Product's Validate: nothing reaches a person without a passed proof page (spec "The bot brings the proof on a page;
a person never does the QA", step 2).

Validate ends on a Checked in production page a bot makes and Jev checks, with no person: Jev passing it moves the task
to Done. On Nov 12 every item in Validate waited on a person anyway, 8 of 9 as a bot's bare question ("go and check it
on your phone"). So a bot's ask of a person on a task in Validate:

- A ❓ (`check_question`, from question_marks) is a check or a decision: refused with ASK_OK once the page passed,
  else with FIRST and OWN_TASK (a decision, such as which host or who pays, is its own task). The person is never
  asked to test.
- An OK (`check_ask`, from task_engine.add_requirement) is refused too (NOBODY_ASKED): the decision model passing the
  page ends the stage, and no bot asks a person for an OK. An OK a bot asked before that rule waits on Jev until Jev
  passes the page (`held`, from task_requirements._ok), so it reaches the person only with a passed page beside it.
- An OK bound to an action (task_asks.ask_ok: send, spend, publish) is that action's own and is never held here.

A person who sends the OK back with a note (Not validated, `sends_back`) moves the task back to In development with
that note, which files new Engineering work with the note in it (stage_effects._handoff_body).
"""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import hubdb_core

if TYPE_CHECKING:
    import sqlite3

    from .task_requirements import Standing

Row = dict[str, Any]
# hubdb_core is untyped: its actor helpers get their types here.
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human

STAGE = "product-validate"
# Validate on Product, on the Product of the pair it replaced, and on a Product added beside that pair, whose ids taken
# by the pair's get a number (pipeline_settings.add_preset: `product-validate-2`).
STAGES = re.compile(r"product-validate(?:-[0-9]+)?")
BACK_TO = "product-in-development"  # the pair's Product: entering it files new Engineering work
PAGE = "Checked in production page"
FIRST = "Make the Checked in production page first."
OWN_TASK = "Ask it as its own task, or as an OK for that action."
ASK_OK = "The page passed: Validate ends on it, and nobody is asked."
NOBODY_ASKED = "Validate ends when the decision model passes the Checked in production page: nobody is asked for an OK."
HELD = "Waits for the Decision model to pass the Checked in production page"
NOT_VALIDATED = "Not validated"


def applies(row: Row | None, by: object, person: object) -> bool:
    """A bot's ask of a person on a task in Product's Validate."""
    return bool(row) and is_stage((row or {}).get("stage_id")) and is_bot(by) and is_human(person)


def is_stage(stage_id: object) -> bool:
    """Whether a stage is Product's Validate (STAGES)."""
    return isinstance(stage_id, str) and STAGES.fullmatch(stage_id) is not None


def page_of(conn: "sqlite3.Connection", row: Row) -> "tuple[Row | None, Standing | None]":
    """The page attached to the open visit's Checked in production requirement and where that requirement stands
    (None, None when the stage has no page requirement)."""
    from . import task_pages as P
    from . import task_requirements as R

    visit = row.get("visit")
    if not visit:
        return None, None
    for requirement in R.for_visit(conn, visit):
        if requirement["kind"] != "page" or not requirement.get("stage_id"):
            continue
        answer = R.newest_answers(conn, visit["id"]).get(requirement["id"])
        return P.current(conn, visit["id"], requirement["id"]), R.standing(conn, row, requirement, answer)
    return None, None


def ref(page: Row) -> str:
    return f"page:{page['file_id']}@{page['version']}"


def _refuse(conn: "sqlite3.Connection", by: str, detail: str, task_id: str) -> None:
    from . import task_engine as E

    E._refuse(conn, by, "task_requirement", detail, task_id)  # pyright: ignore[reportPrivateUsage]


def check_question(conn: "sqlite3.Connection", row: Row, bot: str, person: object) -> None:
    """Refuses a bot's ❓ to a person on a task in Validate (see the module)."""
    if not applies(row, bot, person):
        return
    _, standing = page_of(conn, row)
    _refuse(conn, bot, ASK_OK if standing and standing.met else f"{FIRST} {OWN_TASK}", str(row["id"]))


def check_ask(conn: "sqlite3.Connection", row: Row, by: str, person: object, shows: object) -> object:
    """Refuses a bot's OK of a person in Validate (see the module): the page's passing ends the stage, so an OK is
    nobody's. Returns what the OK shows, unchanged."""
    if not applies(row, by, person):
        return shows
    task_id = str(row["id"])
    page, _ = page_of(conn, row)
    _refuse(conn, by, NOBODY_ASKED if page else FIRST, task_id)
    return shows


def gated(conn: "sqlite3.Connection", task: Row, requirement: Row) -> bool:
    """A bot's plain OK of a person on a task in Validate: the one this module holds behind the page."""
    from . import task_asks

    return (
        requirement.get("kind") == "ok"
        and bool(requirement.get("by_bot"))
        and bool(requirement.get("visit_id"))
        and applies(task, requirement.get("added_by_id"), requirement.get("person_id"))
        and not task_asks.bound(conn, str(requirement["id"]))
    )


def held(conn: "sqlite3.Connection", task: Row, requirement: Row) -> "Standing | None":
    """Where a bot's unanswered OK stands while its page has not passed: on Jev while Jev checks it, the owner's when
    there is no page or it was sent back. None once Jev passed it: the OK waits on the person, as any OK."""
    if not gated(conn, task, requirement):
        return None
    from . import task_requirements as R

    page, standing = page_of(conn, task)
    if page and standing and standing.met:
        return None
    if page and standing and standing.state == R.WAITING:
        return R.Standing(requirement, R.WAITING, R.JEV, HELD)
    return R.Standing(requirement, R.NOT_MET, task.get("owner_id"), FIRST.rstrip("."))


def sends_back(conn: "sqlite3.Connection", task: Row, requirement: Row, value: str) -> bool:
    """A person's no to a bot's OK of the page: Not validated."""
    return value == "no" and gated(conn, task, requirement)


def back_to(conn: "sqlite3.Connection", task_id: str) -> str:
    """Where Not validated sends a task: In development on the pair's Product (entering it files new Engineering work
    that carries the note), else the first stage of its pipeline that needs a PR open (Product's Build, where its owner
    builds the fix in the same task)."""
    from . import task_engine as E
    from . import task_requirements as R

    row = E.task(conn, task_id) or {}
    work = E.main_work(E.stages(conn, str(row.get("pipeline_id") or "")))
    if any(s["id"] == BACK_TO for s in work):
        return BACK_TO
    for stage in work:
        if any(
            r["kind"] == "item" and r.get("source") == "github" and r.get("condition") == "pr_open"
            for r in R.on_stage(conn, str(stage["id"]))
        ):
            return str(stage["id"])
    return BACK_TO


def not_validated(conn: "sqlite3.Connection", task_id: str, by: str, notes: str | None) -> Row:
    """Back with the person's note (`back_to`)."""
    from . import task_engine as E

    said = " ".join(str(notes or "").split())
    return E.move(conn, task_id, back_to(conn, task_id), by, f"{NOT_VALIDATED}: {said}" if said else NOT_VALIDATED)
