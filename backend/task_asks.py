# pyright: strict
"""An OK bound to exactly what was shown, used once (docs/dev/pipelines-v3-cutover.md, "Approvals and questions").

Anything that leaves the company or spends (send this email, mail this letter, publish this edition, spend this, raise
this cap, send this report) waits on an OK from a named person. The OK is a requirement on the task the work belongs to
(`H.task_ask(kind="ok")`), and this module keeps a grant beside it (`ok_grants`): who asked, what will be done
(`subject`, "<kind>:<id>", e.g. "email_send:<id>"), and the content asked about with its hash.

    ask_ok(conn, by, person, line, *, shows, subject, content, task_id=None, title=None) -> {"task_id", "requirement_id"}
    show(conn, person, requirement_id, content, *, own_click) -> shown hash      a preview was put in front of a person
    preview(conn, viewer, requirement_id, *, own_click) -> {...}                 the stored content, shown
    give_ok(conn, person, requirement_id, shown_hash, *, own_click, ctx=None)    the only way a bound OK is given
    standing(conn, requirement_id) -> waiting | ok | sent_back | withdrawn | used
    check(conn, requirement_id, subject, content, by) -> grant                   what use() checks, writing nothing
    use(conn, requirement_id, subject, content, by)                              the one gate before the act
    withdraw(conn, requirement_id, why)                                          the content changed or the act is off
    settle(conn, requirement_id, note)                                           a task ask_ok filed ends with its ask

What an OK has to be, every check the old approval objects made (hubdb.approval_* before the cut-over):
- asked of a person by name, never "anyone", and given by that person (the one it was given to, after a give);
- given by the person's own click in their own session: not a token, an Assistant, BotOps, a run, Tico support;
- for exactly the content they were shown: the preview they opened (`show`, recorded by the server) has the hash of
  the content asked about, and that is the hash they send back;
- used only by whoever asked, for the same subject and exactly the same content, once. A push past is never an OK, a
  content change after the OK needs a new OK, and an OK that was taken back (undo), sent back, withdrawn, or whose task
  was moved back or closed is not one either.
Who may still OK it under the subject's own rules (an inbox's approvers, a bot's managers) is checked by the module
that owns the subject, when it asks, when the OK is given (its preview route) and again before it acts.

A plain `task_answer` of `ok` on a requirement with a grant is refused by the engine (`refuses_plain_ok`): "OK this from
its preview." Even if one got through, `use` would not pass it: only `give_ok` marks a grant OK'd.

There is no OK that expires: Late handles one nobody gives.
"""

import contextvars
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn

from . import hubdb_core

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human
SYSTEM: str = _core.KEEPER

WAITING, OK, SENT_BACK, WITHDRAWN, USED = "waiting", "ok", "sent_back", "withdrawn", "used"
STANDINGS = (WAITING, OK, SENT_BACK, WITHDRAWN, USED)
# How a visit ends that takes its asks with it: the task moved back, closed, or was reopened from that stage.
ENDS_ASKS = ("back", "closed", "reopened")
FROM_PREVIEW = "OK this from its preview in Tico."

# What happens in the same transaction as an OK, by the subject's kind: "<module>:<function>", called as
# fn(conn, grant, ctx). A kind that is not here has nothing to do until whoever asked uses the OK.
ON_OK: dict[str, str] = {
    "board": "backend.company_page.editions:board_ok",
    "email_send": "backend.email_sending:ok_given",
    "letter": "backend.letters:ok_given",
    "proposal": "backend.proposals:ok_given",
}

# Subject kinds whose module asks and uses its OKs itself: a bot's generic ask (POST /api/v2/tasks/{id}/oks) and use
# (POST /api/v2/oks/{id}/use) never name one.
OWNED = ("email_send", "letter", "edition", "board", "proposal", "support", "webhook", "publish")


def generic(subject: str) -> bool:
    """Whether a bot may ask for and use an OK of this subject through the generic routes: any kind but OWNED."""
    return bool(kind_of(subject)) and ":" in str(subject) and kind_of(subject) not in OWNED


# Set while give_ok answers its own requirement, so the engine's refusal of a plain OK lets that one through.
_GIVING: contextvars.ContextVar[str | None] = contextvars.ContextVar("task_asks_giving", default=None)


@dataclass
class Context:
    """What a route hands give_ok for the subject's own step after an OK: the caller's auth, settings, identity and
    app. `after` collects what runs once the transaction has committed (a page rebuild, a letter's send)."""

    auth: Any = None
    settings: Any = None
    who: Any = None
    app: Any = None
    after: list[Callable[[], object]] = field(default_factory=lambda: [])


def _h() -> Any:
    from . import hubdb

    return hubdb


def _refuse(conn: "sqlite3.Connection", by: str, rule: str, detail: str, task_id: str | None = None) -> NoReturn:
    _h().refuse(conn, by, rule, detail, task=task_id)
    raise AssertionError("refuse() always raises")  # pragma: no cover


def content_hash(content: object) -> str:
    """The hash of what an OK is for: text as its UTF-8 bytes, anything else as canonical JSON."""
    if isinstance(content, bytes):
        data = content
    elif isinstance(content, str):
        data = content.encode()
    else:
        data = _canonical(content).encode()
    return hashlib.sha256(data).hexdigest()


def _canonical(content: object) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def kind_of(subject: str) -> str:
    """The kind of a subject: "email_send" of "email_send:<id>"."""
    return str(subject or "").partition(":")[0]


def own_click(who: object) -> bool:
    """Whether a request is a person's own click in their own session: a signed-in person, with no personal token, no
    Assistant or BotOps acting for them (confirmed or not), no bot run, external agent, service key, computer or Tico
    support session."""
    role = getattr(who, "role", "")
    if role not in ("owner", "human") or not is_human(getattr(who, "actor", "")):
        return False
    acting = ("via", "via_token", "support", "attempt_id", "agent", "task_actor", "scope", "runner_id")
    return not any(getattr(who, name, None) for name in acting)


# ----------------------------------------------------------------------------- reads
def grant(conn: "sqlite3.Connection", requirement_id: str) -> Row | None:
    """The grant of one OK requirement, with its content loaded, or None."""
    found = conn.execute("SELECT * FROM ok_grants WHERE requirement_id=?", (requirement_id,))
    names = [d[0] for d in found.description]
    row = found.fetchone()
    if not row:
        return None
    out: Row = dict(zip(names, row, strict=True))
    try:
        out["content"] = json.loads(out.get("content") or "null")
    except ValueError:
        out["content"] = None
    return out


def latest(conn: "sqlite3.Connection", subject: str) -> Row | None:
    """The newest grant asked for a subject, or None."""
    found = conn.execute(
        "SELECT requirement_id FROM ok_grants WHERE subject=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (subject,)
    ).fetchone()
    return grant(conn, str(found[0])) if found else None


def _requirement(conn: "sqlite3.Connection", requirement_id: str) -> Row | None:
    from . import task_requirements as R

    return R.get(conn, requirement_id)


def _visit(conn: "sqlite3.Connection", visit_id: object) -> Row | None:
    found = conn.execute("SELECT * FROM visits WHERE id=?", (str(visit_id or ""),))
    names = [d[0] for d in found.description]
    row = found.fetchone()
    return dict(zip(names, row, strict=True)) if row else None


def _newest_answer(conn: "sqlite3.Connection", visit_id: str, requirement_id: str) -> Row | None:
    found = conn.execute(
        "SELECT * FROM answers WHERE visit_id=? AND requirement_id=? AND undone_at IS NULL "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (visit_id, requirement_id),
    )
    names = [d[0] for d in found.description]
    row = found.fetchone()
    return dict(zip(names, row, strict=True)) if row else None


def _task_status(conn: "sqlite3.Connection", task_id: object) -> str | None:
    found = conn.execute("SELECT status FROM tasks WHERE id=?", (str(task_id or ""),)).fetchone()
    return str(found[0]) if found else None


def _state(conn: "sqlite3.Connection", row: Row) -> tuple[str, Row | None, Row | None]:
    """(standing, requirement, visit) of a grant. Writes nothing."""
    if row.get("used_at"):
        return USED, None, None
    requirement = _requirement(conn, row["requirement_id"])
    visit = _visit(conn, requirement.get("visit_id")) if requirement else None
    if not requirement or not visit or requirement.get("withdrawn_at"):
        return WITHDRAWN, requirement, visit
    if _task_status(conn, visit["task_id"]) in (None, "closed") or visit.get("how") in ENDS_ASKS:
        return WITHDRAWN, requirement, visit
    answer = _newest_answer(conn, visit["id"], requirement["id"])
    if row.get("ok_at") and answer and answer["id"] == row.get("answer_id") and answer["answer"] == "ok":
        return OK, requirement, visit
    if answer and answer["answer"] == "no":
        return SENT_BACK, requirement, visit
    if visit.get("left_at"):
        # The task left the stage without this OK: pushed past, or moved on with it taken back.
        return WITHDRAWN, requirement, visit
    return WAITING, requirement, visit


def standing(conn: "sqlite3.Connection", requirement_id: str) -> str:
    """How an OK stands: waiting (on its person), ok (given from its preview, not used yet), sent_back, withdrawn (it
    is not asked any more: withdrawn, replaced, its task moved back, closed, deleted or pushed past it), or used."""
    row = grant(conn, requirement_id)
    if not row:
        return WITHDRAWN
    return _state(conn, row)[0]


def task_of(conn: "sqlite3.Connection", requirement_id: str) -> str | None:
    """The task an OK is on, or None when it is gone."""
    found = conn.execute(
        "SELECT v.task_id FROM requirements r JOIN visits v ON v.id = r.visit_id WHERE r.id=?", (requirement_id,)
    ).fetchone()
    return str(found[0]) if found else None


def refuses_plain_ok(conn: "sqlite3.Connection", requirement_id: str, value: object) -> str | None:
    """For the engine's answer check: the refusal's words when `value` is an OK of a requirement that has a grant and
    it is not give_ok answering it, else None. A send back and an undo stay plain answers."""
    if value != "ok" or _GIVING.get() == requirement_id:
        return None
    found = conn.execute("SELECT 1 FROM ok_grants WHERE requirement_id=?", (requirement_id,)).fetchone()
    return FROM_PREVIEW if found else None


# ----------------------------------------------------------------------------- asking
def _never_same(conn: object, task_id: object) -> bool:
    return False


def ask_ok(
    conn: "sqlite3.Connection",
    by: str,
    person: str,
    line: str,
    *,
    shows: object,
    subject: str,
    content: object,
    task_id: str | None = None,
    title: str | None = None,
) -> Row:
    """Ask `person` (always a person by name) to OK `content` before `subject` is done. The OK goes on `task_id`, the
    task the work belongs to; with none, a task is filed in General first, asked for by `by`: owned by `by` when that
    is a bot or a person, by `person` when it is the engine. The same subject asked again with the same content is the
    same ask; with other content, the old ask is withdrawn and a new one made. Returns {task_id, requirement_id}."""
    H = _h()
    subject = str(subject or "").strip()
    if not kind_of(subject) or ":" not in subject:
        _refuse(conn, by, "validation", "Say what the OK is for, as kind:id")
    target = H.resolve_actor(conn, person) or person
    from . import task_requirements as R

    if not is_human(target) or not R.active_person(conn, target):
        _refuse(conn, by, "task_requirement", "An OK to send, spend or publish is asked of a person who is here")
    digest = content_hash(content)
    for old in conn.execute(
        "SELECT requirement_id FROM ok_grants WHERE subject=? AND used_at IS NULL", (subject,)
    ).fetchall():
        before = grant(conn, str(old[0]))
        if not before:
            continue
        state = _state(conn, before)[0]
        if state not in (WAITING, OK):
            continue
        if before["content_hash"] == digest and before["asked_by_id"] == by:
            found = task_of(conn, before["requirement_id"])
            if found and (task_id is None or found == task_id):
                return {"task_id": found, "requirement_id": before["requirement_id"]}
        withdraw(conn, before["requirement_id"], "Asked again")
    filed = task_id is None
    if filed:
        owner = by if (is_bot(by) or is_human(by)) else target
        made: Row = H.task_create(
            conn,
            by,
            (title or line).strip(),
            line.strip(),
            owner,
            lint=False,
            same=_never_same,  # every OK is its own: two emails may share a subject line
        )
        task_id = str(made["id"])
    row: Row | None = H.task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    # Acting for `by` inside this transaction: the version is the one just read.
    asked: Row = H.task_ask(conn, by, task_id, target, line, kind="ok", shows=shows, seen_version=row.get("version"))
    requirement_id = str(asked["requirement_id"])
    conn.execute(
        "INSERT INTO ok_grants (requirement_id, asked_by_id, subject, content_hash, content, filed, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (requirement_id, by, subject, digest, _canonical(content), int(filed), H.now()),
    )
    H.event(conn, by, "ok.asked", requirement_id, {"subject": subject, "to": target, "task": task_id})
    return {"task_id": task_id, "requirement_id": requirement_id}


def show(conn: "sqlite3.Connection", person: str, requirement_id: str, content: object, *, own_click: bool) -> str:
    """A preview of `content` was put in front of `person`: recorded when it is their own session, so their OK can be
    bound to it. The module that owns the subject calls this from its preview, with the content as it is now. Returns
    the hash the person's OK sends back."""
    H = _h()
    digest = content_hash(content)
    if own_click and is_human(person) and grant(conn, requirement_id):
        conn.execute(
            "INSERT OR IGNORE INTO ok_shown (requirement_id, person_id, content_hash, shown_at) VALUES (?,?,?,?)",
            (requirement_id, person, digest, H.now()),
        )
    return digest


def preview(conn: "sqlite3.Connection", viewer: str, requirement_id: str, *, own_click: bool) -> Row:
    """What an OK is for, as it was asked: {requirement_id, subject, content, shown_hash, standing}. For a subject
    whose module has its own preview (an email, an edition), that preview is the one to show; this is the rest. The
    caller has checked that `viewer` may read the task."""
    row = grant(conn, requirement_id)
    if not row:
        _refuse(conn, viewer, "not-found", "No such OK")
    digest = show(conn, viewer, requirement_id, row["content"], own_click=own_click)
    return {
        "requirement_id": requirement_id,
        "subject": row["subject"],
        "content": row["content"],
        "shown_hash": digest,
        "standing": _state(conn, row)[0],
    }


# ----------------------------------------------------------------------------- the OK
def give_ok(
    conn: "sqlite3.Connection",
    person: str,
    requirement_id: str,
    shown_hash: str,
    *,
    own_click: bool,
    ctx: Context | None = None,
) -> None:
    """The only way a bound OK is given. Passes when `person` is the one it waits on now, clicked it themselves in
    their own session, and was shown exactly the content asked about (`shown_hash` is the hash of the preview they
    were shown). Then it answers the requirement `ok` and marks the grant, and the subject's own step runs (ON_OK) in
    the same transaction. Otherwise H.Refused, with nothing written."""
    from . import task_requirements as R

    H = _h()
    row = grant(conn, requirement_id)
    if not row:
        _refuse(conn, person, "not-found", "No such OK")
    if not own_click or not is_human(person):
        _refuse(conn, person, "identity", "Only the person's own click in Tico gives this OK")
    state, requirement, visit = _state(conn, row)
    task_id = str(visit["task_id"]) if visit else None
    if state == USED:
        _refuse(conn, person, "consumed", "This OK was already used", task_id)
    if state == OK:
        return
    if state == WITHDRAWN or not requirement or not visit or visit.get("left_at"):
        _refuse(conn, person, "task_requirement", "This is not asked any more", task_id)
    asked_of = R.person_of(conn, requirement, visit["id"])
    if asked_of != person:
        name = H.actor_name(conn, asked_of) if asked_of else "someone who is here: give it to them"
        _refuse(conn, person, "task_requirement", f"This is asked of {name}", task_id)
    if str(shown_hash or "") != row["content_hash"]:
        _refuse(conn, person, "task_changed", "What you saw is not what is asked now. Open it again.", task_id)
    seen = conn.execute(
        "SELECT 1 FROM ok_shown WHERE requirement_id=? AND person_id=? AND content_hash=?",
        (requirement_id, person, row["content_hash"]),
    ).fetchone()
    if not seen:
        _refuse(conn, person, "task_requirement", "Open its preview in Tico first", task_id)
    token = _GIVING.set(requirement_id)
    try:
        answered: Row = H.task_answer(conn, person, task_id, requirement_id, "ok")
    finally:
        _GIVING.reset(token)
    conn.execute(
        "UPDATE ok_grants SET ok_at=?, ok_by_id=?, answer_id=? WHERE requirement_id=? AND used_at IS NULL",
        (H.now(), person, answered.get("answer_id"), requirement_id),
    )
    H.event(conn, person, "ok.given", requirement_id, {"subject": row["subject"], "task": task_id})
    step = ON_OK.get(kind_of(row["subject"]))
    if step:
        import importlib

        module, _, name = step.partition(":")
        run: Callable[..., object] = getattr(importlib.import_module(module), name)
        run(conn, grant(conn, requirement_id), ctx)


# ----------------------------------------------------------------------------- the act
def _problem(
    conn: "sqlite3.Connection", row: Row | None, subject: str, content: object, by: str
) -> tuple[str, str, str | None] | None:
    """(rule, words, task id) of why the act may not go ahead on this grant now, or None when it may."""
    if not row:
        return ("not-found", "No such OK", None)
    state, _requirement, visit = _state(conn, row)
    task_id = str(visit["task_id"]) if visit else None
    if by != row["asked_by_id"]:
        return ("identity", "Only whoever asked for this OK may use it", task_id)
    if state == USED:
        return ("consumed", f"This OK was already used at {row['used_at']}", task_id)
    if row["subject"] != subject:
        return ("identity", "This OK is for something else", task_id)
    if content_hash(content) != row["content_hash"]:
        return ("task_ok_changed", "It changed after it was asked about: ask for a new OK", task_id)
    if state != OK:
        words = {WAITING: "It waits for an OK", SENT_BACK: "It was sent back", WITHDRAWN: "It is not asked any more"}
        return ("task_ok_missing", f"{words[state]}: nothing is done without one", task_id)
    return None


def passes(conn: "sqlite3.Connection", requirement_id: str, subject: str, content: object, by: str) -> bool:
    """Whether `use` would pass now. A read for code that decides on every pass (the email queue): it records no
    refusal."""
    return _problem(conn, grant(conn, requirement_id), subject, content, by) is None


def check(conn: "sqlite3.Connection", requirement_id: str, subject: str, content: object, by: str) -> Row:
    """What `use` checks, writing nothing but the refusal: the grant, when the act may go ahead now. Otherwise
    H.Refused."""
    row = grant(conn, requirement_id)
    problem = _problem(conn, row, subject, content, by)
    if problem or not row:
        rule, words, task_id = problem or ("not-found", "No such OK", None)
        _refuse(conn, by, rule, words, task_id)
    return row


def use(conn: "sqlite3.Connection", requirement_id: str, subject: str, content: object, by: str) -> None:
    """The one gate before the act. Passes once, and only when the OK was given through give_ok, `by` is who asked,
    and the subject and the content are exactly what was OK'd. A push past never passes it. Marks the grant used in
    the caller's transaction, the one that writes the act. Otherwise H.Refused."""
    H = _h()
    check(conn, requirement_id, subject, content, by)
    spent = conn.execute(
        "UPDATE ok_grants SET used_at=?, used_by_id=? WHERE requirement_id=? AND used_at IS NULL AND ok_at IS NOT NULL",
        (H.now(), by, requirement_id),
    ).rowcount
    if not spent:
        _refuse(conn, by, "consumed", "This OK was already used")
    H.event(conn, by, "ok.used", requirement_id, {"subject": subject})


def withdraw(conn: "sqlite3.Connection", requirement_id: str, why: str) -> None:
    """Take an OK back that nobody needs any more: the content changed, or the act will not happen. A grant that was
    used stays as it is. A task ask_ok filed is closed with `why`."""
    H = _h()
    row = grant(conn, requirement_id)
    if not row or row.get("used_at"):
        return
    requirement = _requirement(conn, requirement_id)
    if requirement and not requirement.get("withdrawn_at"):
        H.attempt(conn, H.task_withdraw_ask, SYSTEM, requirement_id, why)
    settle(conn, requirement_id, why)


def settle(conn: "sqlite3.Connection", requirement_id: str, note: str) -> None:
    """A task ask_ok filed ends with its ask: done with `note` (what was done) once the OK was used, closed with it as
    the why once the OK was sent back or is not asked any more. Any other task, and a task that already ended, is left
    as it is."""
    H = _h()
    row = grant(conn, requirement_id)
    task_id = task_of(conn, requirement_id)
    if not row or not row.get("filed") or not task_id or _task_status(conn, task_id) != "open":
        return
    state = _state(conn, row)[0]
    if state == USED:
        H.attempt(conn, H.task_done, SYSTEM, task_id, note)
    elif state in (SENT_BACK, WITHDRAWN):
        H.attempt(conn, H.task_close, SYSTEM, task_id, note)
