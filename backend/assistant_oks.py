# pyright: strict
"""A person's own Assistant gives an OK for them on their word, when they turned that on (spec "Your assistant gives an
OK when you tell it to", task #3968).

Off by default: a person with no row in `assistant_ok_settings` has it off, and only their own click in Tico
(task_asks.own_click) turns it on or off, never their Assistant's turn, Agent Ops or a personal token.

With it on, an `ok` their Assistant's turn makes as them (H.VIA "assistant") counts when all of this holds; else the
answer is refused before anything is written (`refusal`, read by the answer route and the engine alike):
- the OK is delegable: a plain OK (no content bound to it: a send, money, a file or access stays their own click), not
  on their own ask, or a page only they approve (a page several people approve stays their own click);
- the turn cites their own message in their own Assistant room (backend/assistant.py `instruction`, held for the
  request in H.DELEGATION; a call turn they spoke counts), at most WORDS_TTL_S old, that names the task (`names`);
- that message lent no OK on another task: one message, one task;
- on a page, every version it OKs was attached before they spoke.
Everything else an OK checks still applies (backend/answers.py): asked of them, the decision model passed the page,
the version they were shown. The task's history records the answer via assistant with their words (answers._write).
"""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, Request

from . import models as M
from .api_contract import api_v2, error
from .store import H as _H
from .store import Problem

if TYPE_CHECKING:
    import sqlite3

    from .auth import Identity

H: Any = _H
Row = dict[str, Any]

WORDS_TTL_S = 15 * 60
OFF = "Turn on Give OKs for me in Settings → Assistant"
NEEDS_WORDS = "Tell me in your own words which task to OK"
PAGE_CHANGED = "The page changed after you said OK: look at it again"
OWN_ONLY = "Only you turn Give OKs for me on or off, in Settings → Assistant"
WORDS_KEPT = 500  # of the person's words, on the history line

# Words that name no task on their own: a title's other words name it.
_GENERIC = frozenset(
    "a an and are as at be by can do for from has have i in is it its me my no not of ok oks okay on or our please "
    "so spec page task that the this to up we when with yes you your".split()
)
_WORD = re.compile(r"[a-z0-9]+")


def on(conn: "sqlite3.Connection", person: str) -> bool:
    """Whether `person` lets their Assistant give OKs for them."""
    found = conn.execute("SELECT gives_oks FROM assistant_ok_settings WHERE person_id=?", (person,)).fetchone()
    return bool(found and found[0])


def set_on(conn: "sqlite3.Connection", who: "Identity", gives_oks: bool) -> Row:
    """The person's own click turns it on or off; nobody acting for them does."""
    from . import task_asks

    if not task_asks.own_click(who):
        raise Problem("person_only", OWN_ONLY, 403)
    person = str(who.actor)
    conn.execute(
        "INSERT INTO assistant_ok_settings (person_id, gives_oks, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(person_id) DO UPDATE SET gives_oks=excluded.gives_oks, updated_at=excluded.updated_at",
        (person, int(bool(gives_oks)), H.now()),
    )
    H.event(conn, person, "assistant.gives_oks", person, {"on": bool(gives_oks)})
    return {"gives_oks": bool(gives_oks)}


def _words(text: object) -> list[str]:
    """Lower-case words, a plural's `s` dropped, so "card" and "cards" match."""
    out: list[str] = []
    for w in _WORD.findall(str(text or "").casefold()):
        out.append(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def names(text: object, task: Row) -> bool:
    """Whether the person's words name this task: its number (`#3555`, `E-3555`), its id, or at least two of its title's
    own words and half of them ("the suggestion cards task" for "Hide suggestion cards when typing"); a title of one own
    word, that word. A bare number or one word of a longer title names nothing for sure."""
    said = str(text or "")
    number = task.get("number")
    if number:
        prefix = re.escape(str(task.get("prefix") or ""))
        pattern = rf"(?<![\w-])(#|{prefix}-){number}\b" if prefix else rf"(?<![\w-])#{number}\b"
        if re.search(pattern, said, re.IGNORECASE):
            return True
    if task.get("id") and str(task["id"]) in said:
        return True
    own = {w for w in _words(task.get("title")) if w not in _GENERIC}
    if not own:
        return False
    found = own & set(_words(said))
    return len(found) >= min(2, len(own)) and 2 * len(found) >= len(own)


def _delegable(conn: "sqlite3.Connection", by: str, requirement: Row) -> bool:
    from . import task_asks
    from . import task_requirements as R

    if requirement.get("visit_id") and requirement.get("added_by_id") == by:
        return False  # their own ask stays their own click
    if requirement["kind"] == "ok":
        return not task_asks.bound(conn, str(requirement["id"]))
    return requirement["kind"] == "page" and not R.shared(requirement)


def _lent_elsewhere(conn: "sqlite3.Connection", cited: str, task_id: str) -> bool:
    found = conn.execute(
        "SELECT 1 FROM history_events WHERE subject_type='task' AND kind='answer' AND new='ok' AND via='assistant' "
        "AND subject_id<>? AND json_extract(data,'$.delegation.cited.message_id')=? LIMIT 1",
        (task_id, cited),
    ).fetchone()
    return found is not None


def _page_since(conn: "sqlite3.Connection", row: Row, requirement: Row, spoken_at: str) -> bool:
    """Whether a page this OK would approve was attached after the person spoke."""
    from . import task_pages as P
    from . import task_requirements as R

    pages = P.current_pages(conn, row["visit"]["id"], str(requirement["id"]))
    if not pages:
        page, _ = R.page_for(conn, row, requirement)
        if page:
            pages = P.current_pages(conn, str(page["visit_id"]), str(requirement["id"]))
    return any(str(p.get("created_at") or "") > spoken_at for p in pages)


def _record() -> Row | None:
    """The delegation record this request holds (H.DELEGATION), when it is the person's Assistant's (H.VIA)."""
    record: Any = H.DELEGATION.get()
    if str(H.VIA.get() or "") != "assistant" or not isinstance(record, dict):
        return None
    return cast(Row, record)


def _said(conn: "sqlite3.Connection", cited: str) -> Row | None:
    """The person's words a delegation cites: their message, or the call turn they spoke (assistant.cited_message)."""
    from . import assistant

    module: Any = assistant  # its functions are typed loosely
    found: Any = module.cited_message(conn, cited) if cited else None
    return cast(Row, dict(found)) if found else None


def refusal(conn: "sqlite3.Connection", by: str, row: Row, requirement: Row) -> str | None:
    """For an `ok` that the Assistant or Agent Ops makes (task_asks.acting_for_people): None when it is the person's
    Assistant giving a delegable OK on their word with the setting on, else the words it is refused with."""
    from . import task_asks

    record = _record()
    if record is None or not H.is_human(by) or not _delegable(conn, by, requirement):
        return task_asks.NOT_YOURS
    if not on(conn, by):
        return OFF
    cited = str(record.get("cited") or "") if record.get("for") == by else ""
    said = _said(conn, cited)
    if (
        not said
        or said.get("sender_id") != by
        or str(said.get("created_at") or "") < H.shift(H.now(), seconds=-WORDS_TTL_S)
        or not names(said.get("body"), row)
        or _lent_elsewhere(conn, cited, str(row["id"]))
    ):
        return NEEDS_WORDS
    if requirement["kind"] == "page" and _page_since(conn, row, requirement, str(said["created_at"])):
        return PAGE_CHANGED
    return None


def delegation(conn: "sqlite3.Connection") -> Row | None:
    """For the history line of an answer the person's Assistant gave on their word: their words, and the message they
    came from as a link (`cited`: message id and the chat it is in; a spoken call turn's chat is its Assistant room)."""
    record = _record()
    cited = str((record or {}).get("cited") or "")
    if not cited:
        return None
    said = _said(conn, cited) or {}
    words = re.sub(r"\s+", " ", str(said.get("body") or "")).strip()[:WORDS_KEPT]
    room = str(said.get("conversation_id") or "") or None
    return {"cited": {"message_id": cited, "conversation_id": room}, "words": words}


RECEIPT = "assistant_ok"  # the receipt's refs key: {answer_id, task_id, title, stage}


def receipt(conn: "sqlite3.Connection", row: Row, answer_id: str, lent: Row) -> None:
    """The Assistant's receipt in the chat the person spoke in: "OK'd: <title>, <stage>", a card whose refs carry the
    answer to undo (POST /api/v2/answers/{answer_id}/undo) and the task. Whether Undo still applies is the task's own
    read: the answer's requirement lists `undo` in `may` while it does."""
    room = H.conversation(conn, lent["cited"].get("conversation_id")) if lent["cited"].get("conversation_id") else None
    person = str((_record() or {}).get("for") or "")
    if not room or not person:
        return
    at: Row = row.get("stage") or {}
    stage = str(at.get("name") or "")
    title = str(row.get("title") or "")
    refs = {RECEIPT: {"answer_id": answer_id, "task_id": row["id"], "title": title, "stage": stage}, "task": row["id"]}
    via = H.VIA.set("")  # the Assistant's own line, not one written for the person
    try:
        H._write_message(
            conn, H.assistant_actor(conn), person, f"OK'd: {title}, {stage}", room, "say", refs, None, None
        )
    finally:
        H.VIA.reset(via)


def _shown(conn: "sqlite3.Connection", who: "Identity") -> Row:
    from . import task_asks

    return {"gives_oks": on(conn, str(who.actor)), "may_change": task_asks.own_click(who)}


def install(app: FastAPI, store: Any, mutate: Callable[..., Any]) -> None:
    @app.get(
        "/api/v2/assistant/settings",
        **api_v2(
            "Assistant",
            "getAssistantOkSettings",
            "Whether your Assistant gives OKs for you when you tell it to, and whether this caller may change it (only "
            "your own click in Tico: never your Assistant, Agent Ops or a personal token)",
            "AssistantOkSettingsResult",
        ),
    )
    def assistant_settings_show(request: Request):
        """Whether your Assistant gives OKs for you when you tell it to."""
        who = request.state.identity
        if not H.is_human(who.actor):
            raise Problem("forbidden", "Only a person has this setting", 403)
        with store.read() as c:
            return _shown(c, who)

    @app.post(
        "/api/v2/assistant/settings",
        **api_v2(
            "Assistant",
            "setAssistantOkSettings",
            "Let your Assistant give OKs for you when you tell it to, or stop it: your own click only",
            "AssistantOkSettingsResult",
            responses={"403": error("Not your own click (person_only)")},
        ),
    )
    def assistant_settings_set(request: Request, body: M.AssistantOkSettings):
        """Let your Assistant give OKs for you when you tell it to, or stop it. Only your own click."""
        who = request.state.identity

        def work(c: "sqlite3.Connection") -> Row:
            set_on(c, who, body.gives_oks)
            return _shown(c, who)

        return mutate(request, body, work)
