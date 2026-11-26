# pyright: strict
"""Files use counts: how often people and bots list, open, search and review files, for the weekly Files review.

Each answered Files request adds one use to a row per UTC day, person or bot, action and actor (`file_use_daily`), from
any client: the web app, the CLI, an MCP tool (an in-process request to the same route) or the API. A refused request
(not found, no access) raises before it is counted. The routes count, never the helpers they share, so one request is
one use. What counts:

- list: a file list is returned (GET /api/v2/files, GET /api/v2/bots/{bot}/files, its media=1 Made recently included)
- open: a file's info or bytes are served (GET /api/v2/files/{fid}/info, GET /api/v2/files/{fid}, GET
  /api/v2/files/{fid}/versions/{n}), at most once per person or bot and file in OPEN_WINDOW_S: opening a file in the
  app reads its info and then its bytes, a video plays in many Range parts, and a card on screen reads again, and each
  of those is one open. A Range part that does not start at byte 0 and a refused range (416) are no open, nor is a
  thumbnail, poster or Office preview. A card that shows a picture or page in a chat serves its content too and counts:
  the server cannot tell it from an open the reader chose. Which files were opened is kept in memory only, for the
  window, and never written
- search: a file search returns, with or without hits (GET /api/v2/files/search)
- review: a bot's review verdict is saved (POST /api/v2/file-reviews), a person signs a file (POST
  /api/v2/files/{fid}/signings) or gives an OK on a file (task_asks.give_ok)

The rows are read only through `files_use_weekly` (backend/sql.py GRANTED): per week, person or bot and action, how many
distinct people or bots and how many uses. No file, name, content, person or bot leaves them. A failed count never fails
the request.
"""

import logging
import sqlite3
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, cast

from .store import H

LOG = logging.getLogger("tico.file_use")
ACTIONS = ("list", "open", "search", "review")
_is_bot = cast(Callable[[object], bool], H.is_bot)
_is_human = cast(Callable[[object], bool], H.is_human)
OPEN_WINDOW_S = 600.0  # one open per person or bot and file in ten minutes
OPEN_KEPT = 20_000  # files remembered per install; the oldest is forgotten first
clock: Callable[[], float] = time.monotonic
_opened: "weakref.WeakKeyDictionary[Any, OrderedDict[tuple[str, str], float]]" = weakref.WeakKeyDictionary()
_lock = threading.Lock()


def record(c: sqlite3.Connection, actor: str, action: str, at: str | None = None) -> None:
    """Add one use of `action` by `actor` on the UTC day of `at` (now by default), inside the caller's transaction.
    Only a person or a bot counts: a service, support or Tico itself is no team member using Files."""
    if action not in ACTIONS:
        raise ValueError("Unknown Files action " + repr(action))
    kind = "bot" if _is_bot(actor) else "person" if _is_human(actor) else None
    if not kind:
        return
    day = str(at or H.now())[:10]
    c.execute(
        "INSERT INTO file_use_daily(day,kind,action,actor,uses) VALUES(?,?,?,?,1) "
        "ON CONFLICT(day,kind,action,actor) DO UPDATE SET uses=uses+1",
        (day, kind, action, str(actor)),
    )


def count(store: Any, who: Any, action: str) -> None:
    """One use by the caller of an answered request, in its own short write. Never raises."""
    try:
        actor = str(getattr(who, "actor", "") or "")
        if not (_is_bot(actor) or _is_human(actor)):
            return
        with store.transaction() as c:
            record(c, actor, action)
    except Exception:  # noqa: BLE001 - a count never fails the request it counts
        LOG.warning("Files use count failed for %s", action, exc_info=True)


def count_in(c: sqlite3.Connection, actor: str, action: str) -> None:
    """One use inside a write the request already holds (an OK given on a file). Never raises."""
    try:
        c.execute("SAVEPOINT file_use")
        try:
            record(c, actor, action)
        except Exception:
            c.execute("ROLLBACK TO file_use")
            raise
        finally:
            c.execute("RELEASE file_use")
    except Exception:  # noqa: BLE001 - a count never fails the request it counts
        LOG.warning("Files use count failed for %s", action, exc_info=True)


def _served(response: Any) -> bool:
    """A response that gave the file: not a refusal (416 for a range past its end), and a Range part only from byte 0."""
    status = int(getattr(response, "status_code", 200))
    if status >= 400:
        return False
    if status == 206:
        return str(response.headers.get("content-range", "")).startswith("bytes 0-")
    return True


def open_file(store: Any, who: Any, fid: str, response: Any = None) -> None:
    """One open of file `fid` by the caller, unless the caller opened it within OPEN_WINDOW_S or `response` (bytes
    served) gave no file. Never raises."""
    try:
        actor = str(getattr(who, "actor", "") or "")
        if not (_is_bot(actor) or _is_human(actor)) or (response is not None and not _served(response)):
            return
        key, now = (actor, str(fid)), clock()
        with _lock:
            seen = _opened.setdefault(store, OrderedDict())
            last = seen.get(key)
            if last is not None and now - last < OPEN_WINDOW_S:
                return
            seen[key] = now
            seen.move_to_end(key)
            while len(seen) > OPEN_KEPT:
                seen.popitem(last=False)
    except Exception:  # noqa: BLE001 - a count never fails the request it counts
        LOG.warning("Files use count failed for open", exc_info=True)
        return
    count(store, who, "open")
