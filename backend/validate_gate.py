# pyright: strict
"""Product's Validate: nothing reaches a person without a passed proof page (spec "The bot brings the proof on a page;
a person never does the QA", step 2).

Validate ends on a Checked in production page a bot makes and Jev checks, with no person: Jev passing it moves the task
to Done. On Nov 12 every item in Validate waited on a person anyway, 8 of 9 as a bot's bare question ("go and check it
on your phone"). So a bot's ask of a person on a task in Validate (`check_ask`, from task_engine.add_requirement):

- A question whose choices leave out yes and no is a decision, not a check (which host, who pays): refused with
  OWN_TASK.
- Any other question is a check: refused with FIRST, and with ASK_OK once the page passed. The person is never asked
  to test.
- An OK is taken only once the page is attached and Jev has not sent it back. It shows that page, put first, so the
  card opens it (ui/app/decide.ts). It waits on Jev until Jev passes the page (`held`, from task_requirements._ok),
  so it reaches the person only with a passed page beside it: refused at the ask while there is no page, and held
  behind Jev after, since Jev passing the page alone ends the stage.
- An OK bound to an action (task_asks.ask_ok: send, spend, publish) is that action's own and is never held here.

A person who sends the OK back with a note (Not validated, `sends_back`) moves the task back to In development with
that note, which files new Engineering work with the note in it (stage_effects._handoff_body).

`sweep` runs once, at the start of the release that carries this (start_jobs), over tasks already in Validate.
"""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

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
ASK_OK = "Ask their OK of the Checked in production page instead: the card shows the page, with OK and Not validated."
HELD = "Waits for Jev to pass the Checked in production page"
NOT_VALIDATED = "Not validated"
SWEPT = "validate-proof-sweep"


def _h() -> Any:
    from . import hubdb

    return hubdb


def applies(row: Row | None, by: object, person: object) -> bool:
    """A bot's ask of a person on a task in Product's Validate."""
    return bool(row) and is_stage((row or {}).get("stage_id")) and is_bot(by) and is_human(person)


def is_stage(stage_id: object) -> bool:
    """Whether a stage is Product's Validate (STAGES)."""
    return isinstance(stage_id, str) and STAGES.fullmatch(stage_id) is not None


def decision(choices: object) -> bool:
    """A question whose choices leave out yes and no (which host, who pays) is a decision, not a check. A yes/no, with
    or without "Something else", or a question with no choices ("is the row 40 px on your phone?") is a check."""
    found = cast("list[object]", choices) if isinstance(choices, list) else []
    return bool(found) and not {"yes", "no"} <= {str(c).strip().lower() for c in found}


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


def _names(shows: object, page: Row) -> bool:
    refs = cast("list[object]", shows) if isinstance(shows, list) else []
    return any(str(page["file_id"]) in str(one) for one in refs)


def _refuse(conn: "sqlite3.Connection", by: str, detail: str, task_id: str) -> None:
    from . import task_engine as E

    E._refuse(conn, by, "task_requirement", detail, task_id)  # pyright: ignore[reportPrivateUsage]


def check_ask(
    conn: "sqlite3.Connection",
    row: Row,
    by: str,
    person: object,
    kind: str,
    shows: object,
    choices: list[str] | None,
) -> object:
    """Refuses a bot's ask of a person in Validate that is not an OK of its page (see the module). Returns what the
    ask shows: for an OK, the page first, then anything else it named."""
    if not applies(row, by, person):
        return shows
    task_id = str(row["id"])
    if kind == "answer" and decision(choices):
        _refuse(conn, by, OWN_TASK, task_id)
    page, standing = page_of(conn, row)
    if kind == "answer":
        _refuse(conn, by, ASK_OK if standing and standing.met else FIRST, task_id)
    from . import task_requirements as R

    if not page or not standing or standing.state == R.NOT_MET:
        _refuse(conn, by, FIRST, task_id)
    assert page is not None
    refs = cast("list[object]", shows) if isinstance(shows, list) else []
    return [ref(page), *[one for one in refs if str(page["file_id"]) not in str(one)]]


def _bound(conn: "sqlite3.Connection", requirement_id: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM ok_grants WHERE requirement_id=?", (requirement_id,)).fetchone())


def gated(conn: "sqlite3.Connection", task: Row, requirement: Row) -> bool:
    """A bot's plain OK of a person on a task in Validate: the one this module holds behind the page."""
    return (
        requirement.get("kind") == "ok"
        and bool(requirement.get("by_bot"))
        and bool(requirement.get("visit_id"))
        and applies(task, requirement.get("added_by_id"), requirement.get("person_id"))
        and not _bound(conn, str(requirement["id"]))
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
    work = [s for s in E.stages(conn, str(row.get("pipeline_id") or "")) if s["kind"] == "work"]
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


# ----------------------------------------------------------------------------- the one-time sweep at the release
def _withdrawn_line(conn: "sqlite3.Connection", requirement: Row, why: str) -> str:
    name = _h().actor_name(conn, requirement.get("person_id"))
    what = "OK" if requirement["kind"] == "ok" else "question"
    return f"Withdrew the {what} to {name}, “{requirement.get('line') or ''}”: {why}"


def sweep(conn: "sqlite3.Connection") -> list[str]:
    """Once (no schema change; a registry mark): every open task in Validate. A bot's unanswered ask of a person that
    shows no passed page is withdrawn with a comment saying what and why (FIRST, or OWN_TASK for a decision); the task
    then moves to Done when the passed page was all else it waited for, or its owner has the turn and is woken once.
    A person's own ask, an OK bound to an action, an answered ask, and an OK that shows a passed page are untouched.
    Returns the tasks it changed."""
    from . import task_engine as E
    from . import task_requirements as R
    from . import task_wakes

    H = _h()
    if conn.execute("SELECT 1 FROM registry_metadata WHERE key=?", (SWEPT,)).fetchone():
        return []
    conn.execute("INSERT INTO registry_metadata VALUES(?,?)", (SWEPT, H.now()))
    changed: list[str] = []
    rows = conn.execute(
        "SELECT id FROM tasks WHERE status='open' AND stage_id=? ORDER BY created_at, id", (STAGE,)
    ).fetchall()
    for (task_id,) in rows:
        row = E.task(conn, str(task_id))
        if not row or not row.get("visit"):
            continue
        page, standing = page_of(conn, row)
        passed = bool(page and standing and standing.met)
        answered = R.newest_answers(conn, row["visit"]["id"])
        lines: list[str] = []
        for requirement in R.for_visit(conn, row["visit"]):
            if requirement["kind"] not in ("ok", "answer") or requirement.get("stage_id"):
                continue
            if not requirement.get("by_bot") or not is_human(requirement.get("person_id")):
                continue
            if requirement["id"] in answered or _bound(conn, str(requirement["id"])):
                continue
            if requirement["kind"] == "ok" and passed and page and _names(requirement.get("shows"), page):
                continue
            why = OWN_TASK if requirement["kind"] == "answer" and decision(requirement.get("choices")) else FIRST
            E.withdraw_requirement(conn, str(requirement["id"]), E.SYSTEM, why)
            lines.append(_withdrawn_line(conn, requirement, why))
        if not lines:
            continue
        changed.append(str(task_id))
        H.task_comment(conn, E.SYSTEM, str(task_id), "\n".join(lines), wake=False)
        out = E.task(conn, str(task_id))
        if not out or out["status"] != "open":
            continue
        target, _ = E._turn_of(conn, out)  # pyright: ignore[reportPrivateUsage]
        if target:
            lead = " ".join(lines)
            task_wakes.add(
                conn,
                out,
                target,
                out["visit"],
                task_wakes.body(conn, out, lead, to=target),
                wake_key=f"{task_id}:{out['visit']['id']}:{SWEPT}",
            )
    return changed
