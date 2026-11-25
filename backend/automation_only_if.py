# pyright: strict
"""Only if (CONTRACT §2): a trigger's live check, read just before its run starts.

An event says something just happened; it cannot say whether something is still true. A trigger's `only_if`
(`{read, at_least: N}` or `{read, none: true}`, checked on save by automation_flow.check_only_if) is one GET of Tico's
own API, `/api/v2/<read>`, made as the automation's owner when a run of it is first claimed: through this application
in-process (as backend/mcp.py and backend/assistant.py `Internal` call it), its middleware and routes, so every access
check the HTTP API applies is applied. Never an outbound request, never a write: the middleware refuses any other
method for a lent identity (`SCOPE_KEY`).

The count is the length of the answer's one top-level list (`tasks`, `prs`). While the rule needs more than the pages
read hold (at_least past them; `none` needs one row), the next page is read at the answer's `next_offset`, up to
MAX_PAGES pages; so `count` is what the pages read hold, which may stop short of every row there is. A miss, or a read
that fails (an error answer, no single list, more than READ_BYTES, a stream, or no answer in TIMEOUT_S) skips the run:
no step runs, nothing is spent, and its run row says why. A run that goes on reads `only_if.count` and `only_if.items`
(at most MAX_ITEMS items, each cut as a find's items are, within ITEMS_BYTES).

It runs outside every transaction (the drain's claim, `Hub.go`), never inside the trigger's write: the read takes its
own read connections, and a write held open across it would stall every other write for as long as it took.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, MutableMapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast
from urllib.parse import parse_qsl

import httpx

from . import automation_engine as _E
from . import automation_flow as _F
from .store import H as _H
from .store import Problem

Json = dict[str, Any]
Message = MutableMapping[str, Any]
H: Any = _H
log = logging.getLogger(__name__)

SCOPE_KEY = "tico.only_if_identity"  # the ASGI scope key a check's identity rides in: no client can set it
TIMEOUT_S = 5.0  # the whole check, every page included
READ_BYTES = 1_048_576  # one answer's body
MAX_PAGES = 10
MAX_ITEMS: int = _F.MAX_FIND
ITEM_TEXT: int = _E.ITEM_TEXT
ITEMS_BYTES = 32_000
# Checks waiting on a slow read hold a thread each; past these, later checks wait and time out (fail closed).
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="only-if")


class Failed(Exception):
    """The check could not be read; its words are the run's reason."""


def needs(only_if: Json) -> int:
    """How many rows the rule must see before it can decide."""
    return 1 if only_if.get("none") else int(only_if["at_least"])


def holds(only_if: Json, count: int) -> bool:
    return count == 0 if only_if.get("none") else count >= int(only_if["at_least"])


def owner_identity(c: Any, auth: Any, owner: str) -> Any:
    """Whom the check reads as: the owner, a group's lead for a group (as its actions act). A person who left or a bot
    that is not active reads as nobody, and the check fails."""
    from . import auth as _auth
    from . import automation_actions as _A

    A: Any = _A
    auth_module: Any = _auth
    actor = A.acts_as(c, {"flow": {"owner": owner}})
    if H.is_bot(actor):
        found = H.resolve_bot(c, actor)
        if not found:
            raise Problem("identity", "Its agent is not on the team", 403)
        return auth_module.bot_identity(c, H.bot_actor(found["id"]))
    if H.is_human(actor):
        return auth.identity_for_actor(c, actor)
    raise Problem("identity", "It has no owner to read as", 403)


def _lent(app: Any, who: Any) -> Any:
    """The app with `who` in every request's scope, and an answer cut off past READ_BYTES or when it is a stream."""

    async def call(
        scope: Message, receive: Callable[[], Awaitable[Message]], send: Callable[[Message], Awaitable[None]]
    ) -> None:
        size = 0

        async def counted(message: Message) -> None:
            nonlocal size
            if message["type"] == "http.response.start":
                headers = cast("list[tuple[bytes, bytes]]", list(message.get("headers") or []))
                kind = b"".join(value for name, value in headers if name.lower() == b"content-type")
                if b"text/event-stream" in kind:
                    raise Failed("the read is a stream")
            elif message["type"] == "http.response.body":
                size += len(message.get("body") or b"")
                if size > READ_BYTES:
                    raise Failed(f"the read answered more than {READ_BYTES // 1024} KB")
            await send(message)

        await app({**scope, SCOPE_KEY: who}, receive, counted)

    return call


def the_list(body: Any) -> list[Any]:
    """The answer's one top-level list."""
    values: list[Any] = list(cast(Json, body).values()) if isinstance(body, dict) else []
    lists = [cast("list[Any]", v) for v in values if isinstance(v, list)]
    if len(lists) != 1:
        raise Failed("the read does not answer one list")
    return lists[0]


async def _pages(app: Any, who: Any, base: str, only_if: Json) -> list[Any]:
    path, _, query = str(only_if["read"]).partition("?")
    params = dict(parse_qsl(query, keep_blank_values=True))
    want = needs(only_if)
    rows: list[Any] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_lent(app, who)), base_url=base) as client:
        for _ in range(MAX_PAGES):
            answer = await client.get("/api/v2/" + path, params=params, headers={"Accept": "application/json"})
            if answer.status_code != 200:
                try:
                    said: Any = answer.json()
                    error: Any = said.get("error")
                    detail: Any = (
                        cast(Json, error).get("detail") if isinstance(error, dict) else said.get("detail") or error
                    )
                except (ValueError, AttributeError):
                    detail = None
                raise Failed(f"the read answered {answer.status_code}" + (f": {detail}" if detail else ""))
            try:
                body: Any = answer.json()
            except ValueError:
                raise Failed("the read is not JSON") from None
            rows += the_list(body)
            more: Any = body.get("next_offset")
            if len(rows) >= want or isinstance(more, bool) or not isinstance(more, int):
                break
            params["offset"] = str(more)
    return rows


def _read(app: Any, who: Any, base: str, only_if: Json, deadline: float) -> list[Any]:
    """The read, within what is left of the check's TIMEOUT_S: time spent waiting for a thread counts too."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError
    return asyncio.run(asyncio.wait_for(_pages(app, who, base, only_if), left))


def _items(rows: list[Any]) -> list[Any]:
    """What the run reads as `only_if.items`: the first rows, each cut as a find's items are, within ITEMS_BYTES."""
    kept: list[Any] = []
    used = 2
    for row in rows[:MAX_ITEMS]:
        item: Any = (
            {k: (v[:ITEM_TEXT] if isinstance(v, str) else v) for k, v in cast(Json, row).items()}
            if isinstance(row, dict)
            else row
        )
        size = len(json.dumps(item, default=str)) + 1
        if used + size > ITEMS_BYTES:
            break
        kept.append(item)
        used += size
    return kept


def evaluate(app: Any, owner: str, only_if: Json) -> Json:
    """The check, as the run records it: {read, count, items, ok} and, when the run is skipped, `reason`."""
    read = str(only_if["read"])
    out: Json = {"read": read, "count": 0, "items": [], "ok": False}
    try:
        if app is None:
            raise Failed("the server is stopping")
        store: Any = app.state.store
        with store.read() as c:
            who = owner_identity(c, app.state.auth, owner)
        base = str(store.settings.public_url or "http://127.0.0.1")
        deadline = time.monotonic() + TIMEOUT_S
        pending = _POOL.submit(_read, app, who, base, only_if, deadline)
        try:
            rows = pending.result(TIMEOUT_S)
        except TimeoutError:
            pending.cancel()  # one still queued never starts; one running ends at its deadline
            raise
    except TimeoutError:  # concurrent.futures' timeout is this one
        out["reason"] = f"Only if check failed: {read} did not answer in {TIMEOUT_S:g} s"
        return out
    except Problem as e:
        out["reason"] = f"Only if check failed: {getattr(e, 'detail', e)}"
        return out
    except Exception as e:  # noqa: BLE001 - any failure to read fails the check closed, with its reason
        if not isinstance(e, Failed):
            log.warning("only_if read %s failed", read, exc_info=True)
        out["reason"] = f"Only if check failed: {str(e) or type(e).__name__}"[:300]
        return out
    out.update(count=len(rows), items=_items(rows), ok=holds(only_if, len(rows)))
    if not out["ok"]:
        out["reason"] = (
            f"Only if: {read} is not empty"
            if only_if.get("none")
            else f"Only if: {read} has {len(rows)}, needs at least {only_if['at_least']}"
        )[:300]
    return out
