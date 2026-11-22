# pyright: strict
"""An OK bound to exactly what was shown, used once (docs/dev/pipelines-v3-cutover.md, "Approvals and questions").

Anything that leaves the company or spends (send this email, mail this letter, publish this edition, spend this, raise
this cap, send this report) waits on an OK from a named person. The OK is a requirement on the task the work belongs to
(`H.task_ask(kind="ok")`), and this module keeps a grant beside it (`ok_grants`): who asked, what will be done
(`subject`, "<kind>:<id>", e.g. "email_send:<id>"), and the content asked about with its hash.

    ask_ok(conn, by, person, line, *, shows, subject, content, task_id=None, title=None, matter=None, why=..., renew=False)
        -> {"task_id", "requirement_id"}
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
- given by the person's own click in their own session: not a token, an Assistant, Agent Ops, a run, Tico support;
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
from typing import TYPE_CHECKING, Any, NoReturn, cast

from . import hubdb_core

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human
SYSTEM: str = _core.TICO_ACTOR

WAITING, OK, SENT_BACK, WITHDRAWN, USED = "waiting", "ok", "sent_back", "withdrawn", "used"
STANDINGS = (WAITING, OK, SENT_BACK, WITHDRAWN, USED)
# How a visit ends that takes its asks with it: the task moved back, through a side stage, to another pipeline, or was
# closed or reopened.
ENDS_ASKS = ("back", "side", "closed", "reopened", "moved")
FROM_PREVIEW = "OK this from its preview in Tico."

# Each module's step is imported by name inside a small function: lazily, so task_asks imports none of them at load
# (they import task_asks), and by name, so the test selector (scripts/pr_checks.py) can see what depends on what.


def _board_ok(conn: "sqlite3.Connection", grant: Row, ctx: "Context | None") -> object:
    from .company_page import editions

    module: Any = editions  # its functions are typed loosely
    return module.board_ok(conn, grant, ctx)


def _email_ok(conn: "sqlite3.Connection", grant: Row, ctx: "Context | None") -> object:
    from . import email_sending

    module: Any = email_sending  # its functions are typed loosely
    return module.ok_given(conn, grant, ctx)


def _finance_ok(conn: "sqlite3.Connection", grant: Row, ctx: "Context | None") -> object:
    from .finance import actions

    module: Any = actions  # its functions are typed loosely
    return module.ok_given(conn, grant, ctx)


def _support_ok(conn: "sqlite3.Connection", grant: Row, ctx: "Context | None") -> object:
    from . import support

    module: Any = support  # its functions are typed loosely
    return module.ok_given(conn, grant, ctx)


def _letter_ok(conn: "sqlite3.Connection", grant: Row, ctx: "Context | None") -> object:
    from . import letters

    module: Any = letters  # its functions are typed loosely
    return module.ok_given(conn, grant, ctx)


def _proposal_ok(conn: "sqlite3.Connection", grant: Row, ctx: "Context | None") -> object:
    from . import proposals

    module: Any = proposals  # its functions are typed loosely
    return module.ok_given(conn, grant, ctx)


def _growth_ok(conn: "sqlite3.Connection", grant: Row, ctx: "Context | None") -> object:
    from . import growth_proposals

    return growth_proposals.ok_given(conn, grant, ctx)


def _rule_ok(conn: "sqlite3.Connection", grant: Row, ctx: "Context | None") -> object:
    from . import org_rules

    module: Any = org_rules  # its functions are typed loosely
    return module.ok_given(conn, grant, ctx)


def _growth_may_ok(conn: "sqlite3.Connection", person: str, grant: Row) -> str | None:
    from . import growth_proposals

    return growth_proposals.may_ok(conn, person, grant)


def _edition_may_ok(conn: "sqlite3.Connection", person: str, grant: Row) -> str | None:
    from .company_page import editions

    module: Any = editions  # its functions are typed loosely
    return module.may_ok(conn, person, grant)


def _proposal_may_ok(conn: "sqlite3.Connection", person: str, grant: Row) -> str | None:
    from . import proposals

    module: Any = proposals  # its functions are typed loosely
    return module.may_ok(conn, person, grant)


def _file_freeze(conn: "sqlite3.Connection", by: str, subject: str, content: object, shows: object) -> Any:
    from . import file_reviews

    module: Any = file_reviews  # its functions are typed loosely
    return module.freeze(conn, by, subject, content, shows)


def _file_may_ok(conn: "sqlite3.Connection", person: str, grant: Row) -> str | None:
    from . import file_reviews

    module: Any = file_reviews  # its functions are typed loosely
    return module.may_ok(conn, person, grant)


def owner_or_admin(conn: "sqlite3.Connection", person: str) -> bool:
    """Whether `person` is the company's owner or an Admin now."""
    from . import access

    if not is_human(person):
        return False
    found: Row = _h().human(conn, person) or {}
    email = str(found.get("email") or "").strip().lower()
    return bool(email) and email in set(access.owner_and_admin_emails(conn))


def _admin_or_manager(conn: "sqlite3.Connection", person: str, grant: Row) -> bool:
    """Whether `person` is the company's owner or an Admin, or the manager of the bot that asked (its org chart's
    reports_to): who answers for a bot's own work."""
    if owner_or_admin(conn, person):
        return True
    asker = str(grant.get("asked_by_id") or "")
    if not is_bot(asker):
        return False
    from . import ask_ingest

    module: Any = ask_ingest  # its functions are typed loosely
    return str(module.manager_of(conn, asker)[0] or "") == person


def _named_and_answerable(what: str) -> Callable[..., str | None]:
    """A rule for a bot's own spend or reply: only the person it was asked of (never whoever the ask was handed to),
    and only while they are the company's owner or an Admin, or the asking bot's manager."""

    def rule(conn: "sqlite3.Connection", person: str, grant: Row) -> str | None:
        named = str(grant.get("asked_of_id") or "")
        if not named or named != person:
            who = _h().actor_name(conn, named) if named else "the person it was asked of"
            return f"Only {who}, who it was asked of, may OK {what}"
        if not _admin_or_manager(conn, person, grant):
            return f"Only the company's owner, an Admin or the bot's manager may OK {what}"
        return None

    return rule


def _file_may_see(conn: "sqlite3.Connection", auth: object, who: object, grant: Row) -> bool:
    from . import file_reviews

    module: Any = file_reviews  # its functions are typed loosely
    return bool(module.may_see(conn, auth, who, grant))


# What happens in the same transaction as an OK, by the subject's kind, called as fn(conn, grant, ctx). A kind that is
# not here has nothing to do until whoever asked uses the OK.
ON_OK: dict[str, Callable[..., object]] = {
    "board": _board_ok,
    "email_send": _email_ok,
    "finance": _finance_ok,
    "growth": _growth_ok,
    "letter": _letter_ok,
    "proposal": _proposal_ok,
    "rule": _rule_ok,
    "support": _support_ok,
}

# Subject kinds whose module asks and uses its OKs itself: a bot's generic ask (POST /api/v2/tasks/{id}/oks) and use
# (POST /api/v2/oks/{id}/use) never name one.
OWNED = (
    "email_send",
    "letter",
    "edition",
    "board",
    "proposal",
    "support",
    "webhook",
    "publish",
    "finance",
    "growth",
    "rule",
)

# Who may OK a subject of this kind under the subject's own rule, called as fn(conn, person, grant) -> the refusal's
# words, or None when `person` may. Checked when the OK is given and again when it is used. An OWNED kind that is not
# here is OK'd only by the person its module asked: giving the ask to someone else never hands on the right to OK it.
# A module whose check needs the caller's sign-in (an inbox's approvers, a letter's) checks it itself, in its ON_OK
# step and again before it acts: CHECKS_ITSELF.
MAY_OK: dict[str, Callable[..., str | None]] = {
    "board": _edition_may_ok,
    "edition": _edition_may_ok,
    "proposal": _proposal_may_ok,
    "growth": _growth_may_ok,
    "file": _file_may_ok,
}
CHECKS_ITSELF = ("email_send", "letter")
# Kinds whose OK anyone its MAY_OK rule lets OK it may give, not only the person it waits on: a bot's Growth proposal is
# asked of the owner, and any admin may OK it (backend/growth_proposals.py). Shown, own click and used once still hold.
ANY_ALLOWED = ("growth",)

# The kinds a bot may ask for and use through the generic routes (POST /api/v2/tasks/{id}/oks, POST
# /api/v2/oks/{id}/use), each with who may OK it. Nothing else: a kind not here is refused when asked, and an OK of a
# kind with no rule is never good (fail closed).
GENERIC: dict[str, Callable[..., str | None]] = {
    # A bot's file, at the version pinned when asked (backend/file_reviews.py).
    "file": _file_may_ok,
    # A raise of a bot's own project budget (templates/catalog/video-owner).
    "budget_raise": _named_and_answerable("a budget raise"),
    # A reply to a Tico HQ support ticket (templates/catalog/support-owner/software/hq-tickets).
    "hq_reply": _named_and_answerable("this reply"),
}
MAY_OK.update({kind: rule for kind, rule in GENERIC.items() if kind not in MAY_OK})

# Subjects whose module pins what is asked about before it is shown, called as fn(conn, by, subject, content, shows)
# -> (subject, content, shows). A file ref that names no version, or a path, becomes the exact version and digest
# there are when it is asked, so an OK never reaches a version nobody saw.
FREEZE: dict[str, Callable[..., Any]] = {
    "file": _file_freeze,
}

# Who of an OK's parties (`party`) may open its preview, by the subject's kind, called as fn(conn, auth, who, grant)
# -> bool. A file's preview names the file, so it also follows the file's own reader check (a page's audience). A kind
# with none here shows its preview to its parties. Anyone else gets "No such OK".
SEES: dict[str, Callable[..., bool]] = {
    "file": _file_may_see,
}

# The title of a task filed for an OK of an OWNED kind: the kind of act, then the matter it is for, the title of the
# task the asker was working (`matter`) when everyone who reads the filed task reads that one too
# ("Mail a letter · Reply to Winberg's demand letter", `_matter_title`). Never anything of the subject (no recipient,
# edition or endpoint name): the filed task's readers (its asker's managers, the person's Assistant) are wider than
# the OK's parties, who alone see the subject. With no matter it is the kind alone. An email's OK is the exception:
# its filed task takes the asker's title (subject and first recipient, email_sending.send_title), under the task the
# email is for, since several sends wait side by side and a person must tell them apart; the task stays private to
# the OK's parties.
FILED_TITLES: dict[str, str] = {
    "letter": "Mail a letter",
    "edition": "Publish an edition",
    "board": "Approve a stats board",
    "proposal": "Approve a change",
    "support": "Send a report to Tico support",
    "webhook": "Make a webhook endpoint",
    "publish": "Publish to the roadmap",
    "finance": "OK a Finance action",
    "growth": "Add to Growth",
}


# Titles a filed task had for its kind alone: FILED_TITLES' and what an email's filed task was titled before it took
# the email's own (views.needs_items says what such a task's OK is about beside it).
KIND_TITLES: frozenset[str] = frozenset({*FILED_TITLES.values(), "Send an email"})


def kind_titled(title: str) -> bool:
    """Whether a filed task's title is its kind's, alone or with its matter ("Mail a letter · <matter>"): either way
    it says nothing of what the OK binds, which its party's Needs you row says beside it (views.needs_items)."""
    return title.split(" · ", 1)[0] in KIND_TITLES


def _matter_title(conn: "sqlite3.Connection", by: str, person: str, task_id: str | None) -> str:
    """The title of task `task_id` for a filed OK's title, "" when it may not go there. A company-visible task's title
    is everyone's. A private one's goes only where every reader of the filed task reads it: the person asked, and
    the asker as its owner or requester, which is how the asker's managers read it too."""
    H = _h()
    row: Row | None = H.task(conn, task_id) if task_id else None
    if not row:
        return ""
    if H.task_private(conn, row) and not (
        by in (row["owner_id"], row["requester_id"]) and H.task_private_readable(conn, person, row)
    ):
        return ""
    return _clip_words(row["title"], 120)


def _clip_words(text: object, most: int = 120) -> str:
    said = " ".join(str(text or "").split())
    return said if len(said) <= most else said[: most - 1].rstrip() + "…"


def _named(content: object, *path: str) -> str:
    """The text at `path` in an OK's content, or ""."""
    at: object = content
    for key in path:
        at = cast("dict[str, object]", at).get(key) if isinstance(at, dict) else None
    return _clip_words(at) if isinstance(at, str) else ""


# What an OK of each OWNED kind is about, in its subject's own words, from the content it binds: the line a party's
# Needs you row shows instead of the filed task's FILED_TITLES (task #389: "Send an email" twice on one phone). Read
# from the content, not stored, so an OK asked before this shows its subject too. Only a party to the OK ever sees it
# (views.needs_items gives it with the ask entry their own read of the task gives).
ABOUT: dict[str, Callable[[object], str]] = {
    "email_send": lambda c: f"Send email: {s}" if (s := _named(c, "subject")) else "",
    "letter": lambda c: f"Mail a letter to {n}" if (n := _named(c, "recipient", "name")) else "",
    "edition": lambda c: f"Publish: {t}" if (t := _named(c, "title")) else "",
    "webhook": lambda c: f"Make webhook endpoint {n}" if (n := _named(c, "name")) else "",
    "publish": lambda c: f"Publish to the roadmap: {t}" if (t := _published_title(c)) else "",
    "finance": lambda c: _finance_summary(c),
    "rule": lambda c: f"Add rule: {t}" if (t := _named(c, "text")) else "",
}


def _finance_summary(content: object) -> str:
    """What a Finance OK does, from the action it binds (finance/actions.py `_approval_payload`): its plan's text, which
    names a payment's payee and amount first ("Pay 540.00 USD to Figma by ach; <why it needs an OK>"), whole (a payee's
    name may hold a "; "), without its own action id, then shortened. The payload names a payee only by its id."""
    bound = cast("dict[str, object]", content) if isinstance(content, dict) else {}
    what = bound.get("what")
    if not isinstance(what, str):
        return ""
    return _clip_words(what.removesuffix(f" (Finance action {bound.get('action_id')})"), FINANCE_ABOUT_CHARS)


FINANCE_ABOUT_CHARS = 200  # room for a full payee and amount before the reasons it needs an OK


def _published_title(content: object) -> str:
    """The public title a roadmap OK publishes: its content's text is the JSON of the published values
    (roadmaps.publication_payload). "" when the change does not set one (a summary only); about() then reads the task's
    own public title."""
    text = cast("dict[str, object]", content).get("text") if isinstance(content, dict) else None
    try:
        values: object = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return ""
    return _named(values, "public_title")


# Kinds whose OK says what it is about on any task, not only one titled by its kind (`about`).
ALWAYS_ABOUT = frozenset({"finance"})


def about(conn: "sqlite3.Connection", requirement_id: str, line: str = "", *, titled: bool = True) -> str:
    """What one OK is about for its parties' Needs you rows: its subject's words (ABOUT); else the ask's own line when it
    says more than a question of the kind does; else "". Only on a task titled by its kind (`titled`, kind_titled),
    except for an ALWAYS_ABOUT kind, whose bound grant says what it is (a Finance OK's payee and amount)."""
    found = grant(conn, requirement_id)
    if not found:
        return ""
    kind = kind_of(found["subject"])
    if not titled and kind not in ALWAYS_ABOUT:
        return ""
    said = ABOUT.get(kind, lambda _c: "")(found["content"])
    if said:
        return said
    if kind == "publish":
        # A change that leaves the public title as it is: the task's public title is what the post is about (public text,
        # never the internal title).
        row = conn.execute(
            "SELECT public_title FROM tasks WHERE id=?", (str(found["subject"]).partition(":")[2],)
        ).fetchone()
        if row and (title := _clip_words(row[0])):
            return f"Publish to the roadmap: {title}"
    line = _clip_words(line)
    return line if line and not line.endswith("?") else ""


def generic(subject: str) -> bool:
    """Whether a bot may ask for and use an OK of this subject through the generic routes: a GENERIC kind only."""
    return ":" in str(subject) and kind_of(subject) in GENERIC


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
    Assistant or Agent Ops acting for them (confirmed or not), no bot run, external agent, service key, computer or Tico
    support session."""
    role = getattr(who, "role", "")
    if role not in ("owner", "human") or not is_human(getattr(who, "actor", "")):
        return False
    acting = ("via", "via_token", "support", "attempt_id", "agent", "task_actor", "scope", "runner_id")
    return not any(getattr(who, name, None) for name in acting)


# ----------------------------------------------------------------------------- reads
GRANT_SQL = "SELECT * FROM ok_grants WHERE requirement_id=?"


def grant(conn: "sqlite3.Connection", requirement_id: str) -> Row | None:
    """The grant of one OK requirement, with its content loaded, or None. Read through the snapshot's cache: a list of
    waits primes it for every OK it holds (waiting_on.prime)."""
    from . import read_cache

    row = read_cache.one(conn, GRANT_SQL, (requirement_id,))
    if not row:
        return None
    if not hasattr(row, "keys"):  # a connection without named rows
        found = conn.execute(GRANT_SQL, (requirement_id,))
        row = dict(zip([d[0] for d in found.description], found.fetchone() or (), strict=True))
    out: Row = dict(row)
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


def _taken_back(conn: "sqlite3.Connection", visit: Row) -> bool:
    """Whether a move since this visit ended took back the asks the task moved on with."""
    found = conn.execute(
        f"SELECT 1 FROM visits WHERE task_id=? AND left_at>=? AND how IN ({','.join('?' for _ in ENDS_ASKS)}) LIMIT 1",
        (visit["task_id"], visit["left_at"], *ENDS_ASKS),
    ).fetchone()
    return found is not None


def _state(conn: "sqlite3.Connection", row: Row) -> tuple[str, Row | None, Row | None]:
    """(standing, requirement, visit) of a grant. Writes nothing."""
    if row.get("used_at"):
        return USED, None, None
    requirement = _requirement(conn, row["requirement_id"])
    visit = _visit(conn, requirement.get("visit_id")) if requirement else None
    if not requirement or not visit:
        return WITHDRAWN, requirement, visit
    if row.get("revoked_at"):
        # Sent back or taken back: dead for good, whatever the answers say since (an undone send back included).
        newest = _newest_answer(conn, visit["id"], requirement["id"]) or {}
        return (SENT_BACK if newest.get("answer") == "no" else WITHDRAWN), requirement, visit
    # Withdrawn means taken back: a visit that ends met leaves its asks as they were (task_engine._close_visit).
    if requirement.get("withdrawn_at"):
        return WITHDRAWN, requirement, visit
    if _task_status(conn, visit["task_id"]) in (None, "closed") or visit.get("how") in ENDS_ASKS:
        return WITHDRAWN, requirement, visit
    if visit.get("left_at") and _taken_back(conn, visit):
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
    is not asked any more: withdrawn, replaced, its task diverted, moved back, closed, deleted or pushed past it),
    or used."""
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


# The tasks filed for an OK asked of a person (`?`): their own Assistant's turn reads these as she does, so it can send
# one back on her word. Only a task filed for the OK: it holds nothing but the ask. Twin of `filed_for`.
FILED_FOR_SQL = (
    "SELECT v.task_id FROM ok_grants g JOIN requirements r ON r.id = g.requirement_id "
    "JOIN visits v ON v.id = r.visit_id WHERE g.filed = 1 AND g.asked_of_id = ?"
)


def filed_for(conn: "sqlite3.Connection", person: str, task_id: object) -> bool:
    """Whether `task_id` is a task filed for an OK asked of `person`."""
    return bool(
        person and conn.execute(FILED_FOR_SQL + " AND v.task_id = ? LIMIT 1", (person, str(task_id))).fetchone()
    )


def assistant_for(conn: "sqlite3.Connection", person: object, carried: object) -> bool:
    """Whether a read as `person` carried by `carried` is that person's own Assistant turn: her rights, the Assistant
    carrying them (backend/auth.py chat_person, tico_principal)."""
    return is_human(person) and carried == _h().assistant_actor(conn)


def assistant_reads_own_ok(conn: "sqlite3.Connection", person: object, carried: object, task_id: object) -> bool:
    """The person's own Assistant reads the task filed for an OK asked of her, as she does, so it can send it back on
    her word (it never gives one: refuses_ok_by). Another person's Assistant, or the Assistant on its own, does not."""
    return assistant_for(conn, person, carried) and filed_for(conn, str(person), task_id)


def acting_for_people(by: str) -> bool:
    """Whether this write is the Assistant's or Agent Ops': as itself, or acting for a person in this request (H.VIA,
    set by backend/app.py for the whole request). The engine's answer refuses them an OK (backend/answers.py)."""
    H = _h()
    return by in ACTS_FOR_PEOPLE or str(H.VIA.get() or "") in ("assistant", "botops")


def refuses_plain_ok(conn: "sqlite3.Connection", requirement_id: str, value: object) -> str | None:
    """For the engine's answer check: the refusal's words when `value` is an OK of a requirement that has a grant and
    it is not give_ok answering it, else None. A send back and an undo stay plain answers."""
    if value != "ok" or _GIVING.get() == requirement_id:
        return None
    return FROM_PREVIEW if bound(conn, requirement_id) else None


def given_by_another_allowed(conn: "sqlite3.Connection", requirement_id: str, value: object) -> bool:
    """For the engine's answer check: whether give_ok is answering `ok` to an OK of an ANY_ALLOWED kind, whose giver
    it already checked against the kind's rule (MAY_OK), so someone other than the person it waits on may answer."""
    if value != "ok" or _GIVING.get() != requirement_id:
        return False
    found = grant(conn, requirement_id)
    return bool(found) and kind_of(found["subject"]) in ANY_ALLOWED


# The bots that act for a person (backend/assistant.py, backend/agent_ops_act.py): they never give an OK of any kind.
ACTS_FOR_PEOPLE = ("bot:assistant", "bot:botops")
NOT_YOURS = "An OK is the person's own: the Assistant and Agent Ops never give one. Send back is fine."
OWN_IN_TICO = "An OK is the person's own, in Tico. Send back is yours to say"
SHARED_OWN = "A page several people approve is answered only by your own click in Tico"
SHARED_NOT_HELD = "A page several people approve takes its answer now, never sent later from a hold"


def refuses_ok_by(conn: "sqlite3.Connection", who: object, requirement_id: str, value: object) -> str | None:
    """For every answer path before the engine (the answer route, a batch decision): the refusal's words when `ok` to
    an OK requirement, bound or plain, on anyone's ask, does not come from the person's own session: the Assistant or
    Agent Ops (acting for a person, confirmed or not, or as itself), a personal token, or anything else acting for them.
    Else None. A send back stays theirs to give, except on a page several people approve: its first answer, OK or send
    back, settles it for all of them, so either is only an approver's own click."""
    said = str(value or "").strip().casefold()
    if said not in ("ok", "no"):
        return None
    via = str(getattr(who, "via", "") or "")
    actors = {str(getattr(who, name, "") or "") for name in ("actor", "task_actor")}
    if via in ("assistant", "botops") or actors & set(ACTS_FOR_PEOPLE):
        words = NOT_YOURS
    elif via or getattr(who, "via_token", False):
        words = OWN_IN_TICO
    else:
        return None
    if shared_page(conn, requirement_id):
        return SHARED_OWN
    found = conn.execute("SELECT kind FROM requirements WHERE id=?", (requirement_id,)).fetchone()
    return words if found and found[0] == "ok" and said == "ok" else None


def shared_page(conn: "sqlite3.Connection", requirement_id: str) -> bool:
    """Whether a requirement is a page several people approve (task_requirements.shared)."""
    from . import task_requirements as R

    found = conn.execute("SELECT kind, person_ids FROM requirements WHERE id=?", (requirement_id,)).fetchone()
    return bool(found) and R.shared({"kind": found[0], "person_ids": found[1]})


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
    matter: str | None = None,
    why: str = "Asked again",
    renew: bool = False,
    opening: str | None = None,
) -> Row:
    """Ask `person` (always a person by name) to OK `content` before `subject` is done. The OK goes on `task_id`, the
    task the work belongs to; with none, a task is filed in General first, asked for by `by`: owned by `by` when that is
    a bot or a person, by `person` when it is the engine, and titled by FILED_TITLES with `matter`, the task the asker
    was working, when its title may go there. The same subject asked again with the same content is the same ask; with
    other content, a new one is made and then the old one withdrawn with `why` (for a GENERIC subject, only the same
    asker's on the same task), so the task never moves on between the two; `renew` asks anew even for the same content
    (its old ask is no longer good: whom it waits on may not approve it now). `opening` is a filed task's first message
    when it must say less than `line`: the task's chat reaches its readers, who may be more than the OK's parties, and
    only the ask's own line is kept to its parties. Returns {task_id, requirement_id}."""
    H = _h()
    subject = str(subject or "").strip()
    if not kind_of(subject) or ":" not in subject:
        _refuse(conn, by, "validation", "Say what the OK is for, as kind:id")
    if kind_of(subject) not in OWNED and kind_of(subject) not in GENERIC:
        _refuse(conn, by, "validation", f"Tico has no OK of the kind {kind_of(subject)!r}")
    pin = FREEZE.get(kind_of(subject))
    if pin:
        subject, content, shows = pin(conn, by, subject, content, shows)
    target = H.resolve_actor(conn, person) or person
    from . import task_requirements as R

    if not is_human(target) or not R.active_person(conn, target):
        _refuse(conn, by, "task_requirement", "An OK to send, spend or publish is asked of a person who is here")
    digest = content_hash(content)
    # A subject a module owns names one object (an email, a letter, an edition): the module checked the asker's rights
    # on it, and one live OK per object is what it reads back (`latest`), so asking again replaces anyone's. A GENERIC
    # subject is the asker's own words: only the same asker's ask on the same task is replaced; another's is never
    # touched (they stand side by side, each used by whoever asked it).
    own_words = kind_of(subject) in GENERIC and kind_of(subject) not in OWNED
    replaced: list[str] = []
    for old in conn.execute(
        "SELECT requirement_id FROM ok_grants WHERE subject=? AND used_at IS NULL", (subject,)
    ).fetchall():
        before = grant(conn, str(old[0]))
        if not before:
            continue
        if own_words and (
            before["asked_by_id"] != by or (task_id is not None and task_of(conn, before["requirement_id"]) != task_id)
        ):
            continue
        state = _state(conn, before)[0]
        if state == SENT_BACK and (before["asked_by_id"] == by or not own_words):
            # Asked again after a send back: the new ask replaces the old one, which would otherwise hold its task in
            # the stage (C15), once the new one is written. The send back stays in the task's history.
            replaced.append(before["requirement_id"])
            continue
        if state not in (WAITING, OK):
            continue
        if before["content_hash"] == digest and before["asked_by_id"] == by and not renew:
            found = task_of(conn, before["requirement_id"])
            if found and (task_id is None or found == task_id):
                return {"task_id": found, "requirement_id": before["requirement_id"]}
        replaced.append(before["requirement_id"])
    filed = task_id is None
    # One Assistant serves everyone and never holds a private task (hubdb.refuse_shared_assistant): an OK it asks with no
    # task of its own is filed and asked as the engine's, on a task the person it is for owns, as the Health Monitor's
    # are. The grant still names the Assistant as the asker, which is who may use the OK.
    filer = H.TICO_ACTOR if by == H.assistant_actor(conn) else by
    if filed:
        owner = filer if (is_bot(filer) or is_human(filer)) else target
        kind = kind_of(subject)
        named = FILED_TITLES.get(kind)
        about = _matter_title(conn, by, target, matter) if named else ""
        made: Row = H.task_create(
            conn,
            filer,
            (f"{named} · {about}"[:300] if about else named) or (title or line).strip(),
            (opening or line).strip(),
            owner,
            lint=False,
            # A task filed for an OK is private to its parties: the subject it previews is never wider than they are.
            private=True,
            same=_never_same,  # every OK is its own: two emails may share a subject line
        )
        task_id = str(made["id"])
    row: Row | None = H.task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if filed and not H.task_private_readable(conn, target, row):
        H.task_reach(conn, filer, row, target)
        reached: Row | None = H.task(conn, task_id)
        if reached:
            row = reached
    # Acting for `by` inside this transaction: the version is the one just read.
    asked: Row = H.task_ask(
        conn,
        filer if filed else by,
        task_id,
        target,
        line,
        kind="ok",
        shows=shows,
        seen_version=row.get("version"),
        for_action=True,
    )
    requirement_id = str(asked["requirement_id"])
    conn.execute(
        "INSERT INTO ok_grants (requirement_id, asked_by_id, asked_of_id, subject, content_hash, content, filed, "
        "created_at) VALUES (?,?,?,?,?,?,?,?)",
        (requirement_id, by, target, subject, digest, _canonical(content), int(filed), H.now()),
    )
    H.event(conn, by, "ok.asked", requirement_id, {"subject": subject, "to": target, "task": task_id})
    for old_id in replaced:
        withdraw(conn, old_id, why)
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


# What anyone but an OK's parties reads where the OK's own line would be: on the task, in what it waits on, in lists
# and briefs. That the task waits on a person is all they learn: never what for, nor what it shows.
PRIVATE_LINE = "Waits on a person's OK"


def party(conn: "sqlite3.Connection", actor: object, row: Row) -> bool:
    """Whether `actor` is one of the OK's parties: who asked, who it was asked of (and gave it), or the company's owner
    or an Admin. Nobody else, not whoever the ask was handed to, nor anyone who reads its task."""
    # A run of the shared Assistant sees an OK as the person whose own chat it runs in (hubdb.read_as).
    who = str(_h().read_as(conn, actor) or "")
    if not who:
        return False
    if who in {row["asked_by_id"], row.get("asked_of_id"), row.get("ok_by_id")} - {None, ""}:
        return True
    return owner_or_admin(conn, who)


def parties_to_all(conn: "sqlite3.Connection", actor: object, requirement_ids: list[str]) -> bool:
    """Whether `actor` is a party (`party`) to the bound OK of every one of these requirements, read in one statement.
    False with no actor, no requirement, or one that names no bound OK: nothing then vouches for the reader."""
    who, wanted = str(_h().read_as(conn, actor) or ""), set(requirement_ids)  # as the run sees (hubdb.read_as)
    if not who or not wanted:
        return False
    rows = conn.execute(
        "SELECT asked_by_id, asked_of_id, ok_by_id FROM ok_grants WHERE requirement_id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(wanted)),),
    ).fetchall()
    if len(rows) != len(wanted):  # one is no bound OK (any more): nothing vouches for it
        return False
    return all(who in (row[0], row[1], row[2]) for row in rows) or owner_or_admin(conn, who)


def hidden_from(conn: "sqlite3.Connection", viewer: object, requirement_id: str) -> bool:
    """Whether the requirement is a bound OK whose line, shows and answer note `viewer` may not read. A None viewer is
    the engine's own code, which reads everything."""
    if viewer is None:
        return False
    row = grant(conn, requirement_id)
    return bool(row) and not party(conn, viewer, row)


def event_hidden(conn: "sqlite3.Connection", principals: object, target: object) -> bool:
    """Whether an audit event about a bound OK (its target is the OK's requirement: ok.asked, ok.given, ok.used,
    ok.not_done) is hidden from the reader: every one of `principals` (one actor, or a person and whom they act as)
    must be a party of the OK. Reading its task is not enough, as for the OK's line (party)."""
    if not isinstance(target, str) or not target:
        return False
    row = grant(conn, target)
    if not row:
        return False
    many = isinstance(principals, (tuple, list, set))
    viewers = list(cast("tuple[object, ...]", principals)) if many else [principals]
    return any(not party(conn, viewer, row) for viewer in viewers)


BOUND_SQL = "SELECT 1 FROM ok_grants WHERE requirement_id=?"


def bound(conn: "sqlite3.Connection", requirement_id: str) -> bool:
    """Whether a requirement is a bound OK (has a grant). Read through the snapshot's cache: a list of waits primes it
    for every OK it holds (waiting_on.prime)."""
    from . import read_cache

    return read_cache.one(conn, BOUND_SQL, (requirement_id,)) is not None


def private_history(conn: "sqlite3.Connection", viewer: object, events: list[Row]) -> list[Row]:
    """A task's history as `viewer` reads it: an event about a bound OK they are no party of (its ask, an answer to it,
    a give) keeps what happened and loses its words: the ask's line becomes PRIVATE_LINE, a note goes. An event about
    an ask that showed pages carries them as its words' provenance (`body_sources`), for files.readable_page_refs."""
    shows_of: dict[str, list[str]] = {}
    for event in events:
        data: Any = event.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data or "null")
            except ValueError:
                data = None
        found: Row = data if isinstance(data, dict) else {}  # pyright: ignore[reportUnknownVariableType]
        rid = str(found.get("requirement") or "")
        if not rid and event.get("field") == "requirement":
            rid = str(event.get("new") or "")
        if not rid:
            continue
        if hidden_from(conn, viewer, rid):
            event["note"] = PRIVATE_LINE if event.get("field") == "requirement" else ""
        if rid not in shows_of:
            requirement = _requirement(conn, rid) or {}
            shown: Any = requirement.get("shows")
            if isinstance(shown, str):
                try:
                    shown = json.loads(shown or "null")
                except ValueError:
                    shown = None
            shows_of[rid] = [str(ref) for ref in cast("list[object]", shown)] if isinstance(shown, list) else []
        if shows_of[rid]:
            event["body_sources"] = shows_of[rid]
    return events


def may_see(conn: "sqlite3.Connection", auth: object, who: object, requirement_id: str) -> bool:
    """Whether `who`, who reads the OK's task, may also open its preview: only its parties (`party`), and of those
    only whom the subject's own reader check lets read it (SEES: a file's audience). Anyone else gets "No such OK"."""
    from . import task_privacy as _task_privacy

    reads_as: Any = cast(Any, _task_privacy).actor  # task_privacy is untyped: as the run reads (hubdb.read_as)
    principals: Any = reads_as(who) if who is not None else ""
    reader: Any = cast(Any, principals)[0] if isinstance(principals, tuple) else principals
    row = grant(conn, requirement_id)
    if not row or not party(conn, reader, row):
        return False
    check = SEES.get(kind_of(row["subject"]))
    return bool(check(conn, auth, who, row)) if check else True


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
) -> str | None:
    """The only way a bound OK is given. Passes when `person` is the one it waits on now, clicked it themselves in
    their own session, and was shown exactly the content asked about (`shown_hash` is the hash of the preview they
    were shown). Then it answers the requirement `ok` and marks the grant, and the subject's own step runs (ON_OK) in
    the same transaction. Returns the answer's id, which takes it back (POST /api/v2/answers/{id}/undo); an OK already
    given returns its own. Otherwise H.Refused, with nothing written."""
    from . import task_requirements as R

    H = _h()
    row = grant(conn, requirement_id)
    if not row:
        _refuse(conn, person, "not-found", "No such OK")
    if not own_click or not is_human(person):
        _refuse(conn, person, "identity", "Only the person's own click in Tico gives this OK")
    step = ON_OK.get(kind_of(row["subject"]))
    if step and ctx is None:
        # Its own step acts as the person who OK'd it (a page rebuilt, a letter sent): without them, nothing is given.
        _refuse(conn, person, "identity", "Only the person's own click in Tico gives this OK")
    state, requirement, visit = _state(conn, row)
    task_id = str(visit["task_id"]) if visit else None
    if state == USED:
        _refuse(conn, person, "consumed", "This OK was already used", task_id)
    if state == OK:
        return row.get("answer_id")
    if row.get("revoked_at"):
        _refuse(conn, person, "task_requirement", "This was sent back or taken back: it needs a new ask", task_id)
    if state == WITHDRAWN or not requirement or not visit or visit.get("left_at"):
        _refuse(conn, person, "task_requirement", "This is not asked any more", task_id)
    asked_of = R.person_of(conn, requirement, visit["id"])
    if asked_of != person and kind_of(row["subject"]) not in ANY_ALLOWED:
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
    refused = _authority(conn, person, row)
    if refused:
        _refuse(conn, person, "task_requirement", refused, task_id)
    token = _GIVING.set(requirement_id)
    try:
        answered: Row = H.task_answer(conn, person, task_id, requirement_id, "ok", own_click=True)
    finally:
        _GIVING.reset(token)
    conn.execute(
        "UPDATE ok_grants SET ok_at=?, ok_by_id=?, answer_id=? WHERE requirement_id=? AND used_at IS NULL",
        (H.now(), person, answered.get("answer_id"), requirement_id),
    )
    H.event(conn, person, "ok.given", requirement_id, {"subject": row["subject"], "task": task_id})
    if step:
        step(conn, grant(conn, requirement_id), ctx)
    return answered.get("answer_id")


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
    refused = _authority(conn, str(row.get("ok_by_id") or ""), row)
    if refused:
        return ("task_ok_missing", f"{refused}: ask for a new OK", task_id)
    return None


def _authority(conn: "sqlite3.Connection", person: str, row: Row) -> str | None:
    """Why `person` may not OK this grant now, or None when they may: a person who is still here and may sign in, and
    whom the subject's own rule lets OK it (MAY_OK; for an OWNED kind with no rule, the person its module asked)."""
    from . import task_requirements as R

    H = _h()
    if not R.active_person(conn, person):
        return f"{H.actor_name(conn, person) if person else 'Whoever gave it'} can no longer OK this"
    kind = kind_of(row["subject"])
    check = MAY_OK.get(kind)
    if check:
        return check(conn, person, row)
    if kind not in OWNED:
        return "Nobody may OK this kind of act"  # no rule says who may: fail closed
    named = str(row.get("asked_of_id") or "")
    if kind not in CHECKS_ITSELF and named and named != person:
        return f"Only {H.actor_name(conn, named)}, who it was asked of, may OK this"
    return None


def claimed_problem(conn: "sqlite3.Connection", requirement_id: str, *, authority: bool = True) -> str | None:
    """Why an OK its click claimed (used) may not be delivered now, or None when it may: it was not given up on
    (not_done), and, with `authority` (rule C, rechecked before the first delivery of an act queued on the click, such
    as a report to Tico support), the person who gave it is still here and still entitled to give it."""
    row = grant(conn, requirement_id)
    if not row or not row.get("used_at") or not row.get("ok_by_id"):
        return "No OK claimed this"
    if conn.execute("SELECT 1 FROM events WHERE action='ok.not_done' AND target=?", (requirement_id,)).fetchone():
        return "It was given up on"
    return _authority(conn, str(row["ok_by_id"]), row) if authority else None


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
    H.event(conn, by, "ok.used", requirement_id, {"subject": subject, "task": task_of(conn, requirement_id)})


def revoke(conn: "sqlite3.Connection", requirement_id: str) -> None:
    """An OK sent back, or its OK taken back (backend/answers.py): marked once, for good. Undoing the send back never
    brings it back, and no later click on this ask gives it again; the bot asks anew. A used grant is left as it is."""
    H = _h()
    conn.execute(
        "UPDATE ok_grants SET revoked_at=? WHERE requirement_id=? AND used_at IS NULL AND revoked_at IS NULL",
        (H.now(), requirement_id),
    )


def withdraw(conn: "sqlite3.Connection", requirement_id: str, why: str, *, advance: bool = True) -> None:
    """Take an OK back that nobody needs any more: the content changed, or the act will not happen. A grant that was
    used stays as it is. One that was not is revoked for good (`revoke`), even when its visit already ended met and
    its requirement can no longer be withdrawn: asking about the same content again needs a new OK. Its answer stays
    on record. A task ask_ok filed is closed with `why`. `advance` False: its task does not move on for it (a revised
    draft's ask is about to take its place)."""
    H = _h()
    row = grant(conn, requirement_id)
    if not row or row.get("used_at"):
        return
    revoke(conn, requirement_id)
    requirement = _requirement(conn, requirement_id)
    if requirement and not requirement.get("withdrawn_at"):
        H.attempt(conn, H.task_withdraw_ask, SYSTEM, requirement_id, why, advance=advance)
    settle(conn, requirement_id, why)


def not_done(conn: "sqlite3.Connection", requirement_id: str, why: str) -> None:
    """The act an OK was used for did not happen after all (a provider refused the email, or never said it took it):
    the OK stays used, so it lets nothing else go, and a task ask_ok filed for it ends closed with `why`, never done
    as if the act happened. Any other task, and a task that already ended, is left as it is."""
    H = _h()
    row = grant(conn, requirement_id)
    if not row or not row.get("used_at"):
        return
    task_id = task_of(conn, requirement_id)
    H.event(conn, SYSTEM, "ok.not_done", requirement_id, {"subject": row["subject"], "why": why[:300], "task": task_id})
    if row.get("filed") and task_id and _task_status(conn, task_id) == "open":
        H.attempt(conn, H.task_close, SYSTEM, task_id, why)
    elif task_id:
        # Its task goes on: its history says why the act did not happen, to the OK's parties only (the event names the
        # requirement: private_history drops the words for anyone else). Nobody is sent a message about it.
        H._task_event(conn, task_id, SYSTEM, "ok_not_done", None, None, why[:300], data={"requirement": requirement_id})


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
