# pyright: strict
"""Ringing for calls in threads (backend/calls.py, docs/meet.md): who a new call rings, and the hooks that ring them.

A call rings the thread's people (never its bots) other than the caller when the thread has at most RING_MAX people;
in a bigger thread nobody is rung, and its readers see the call on the thread's row and its card. The browser rings
from the call view (`ring`) and `GET /api/v2/calls/ringing`; anything that rings beyond an open page, such as phone
push, registers a hook here.

A hook is `hook(app, call_id, actors, payload)`, called once a call has started, outside any transaction: `actors` are
the rung people, and `payload` is {"title": the caller's name, "body": "# <thread title>", "url": the thread's path with
`?call=<id>`, "tag": "call-<id>"}. A hook that fails is reported and the others still run.
"""

import logging
from collections.abc import Callable, Iterable
from typing import Any

log = logging.getLogger("tico.calls")

RING_MAX = 8  # a thread with more people than this rings nobody
Hook = Callable[[Any, str, list[str], dict[str, str]], None]
HOOKS: list[Hook] = []


def ring(app: Any, call_id: str, actors: Iterable[str], payload: dict[str, str]) -> None:
    """Run every hook for one call's ring. Nothing happens with no hook or nobody to ring."""
    actors = list(actors or ())
    if not actors:
        return
    for hook in list(HOOKS):
        try:
            hook(app, call_id, list(actors), dict(payload))
        except Exception:  # noqa: BLE001 - one hook failing must not stop the call or the other hooks
            log.warning("A call ring hook failed for call %s", call_id, exc_info=True)
