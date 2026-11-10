# pyright: strict
"""A person's or a bot's own preferences: GET and POST /api/v2/preferences/{key}. Each actor reads and writes only its
own. The server checks the values of the keys it acts on: the web's Send way, meetings' auto share and hiding the
Strategy Bot from one's own chart."""

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from fastapi import Request

from . import models as M
from . import strategy_bot as strategy_bot_module
from . import task_privacy
from . import views as views_module
from .store import H as hubdb, Problem, encode

if TYPE_CHECKING:
    from fastapi import FastAPI

H: Any = hubdb
strategy_bot: Any = strategy_bot_module
views: Any = views_module
privacy: Any = task_privacy

# The defers the web's Send can keep (preference compose.send_way): the durations its menu offers (ui/app/pill.ts).
SEND_WAY_DEFERS = frozenset("defer:" + f for f in ("30m", "1h", "4h", "24h", "2d", "7d", "never"))


# The keys a client keeps here: the web's (ui/app: pill, rails, sidebar, tasks-page, task-list, pipeline-rail), the
# Strategy Bot's chart switch and meetings' auto share. Every other row in `preferences` is the server's own
# (getting started, the changelog, the Librarian's setup, ...) and is written only by its module.
KEYS = frozenset(
    {
        "compose.send_way",
        "ui.rails",
        "org.history",
        "org.mine",
        "tasks.view",
        "tasks.pins",
        "pipeline.rail_hidden",
        "meetings.auto_share",
        strategy_bot.HIDE_PREF,
    }
)
MAX_BYTES = 4096


def known(key: str) -> None:
    if key.startswith("assistant."):
        raise Problem("forbidden", "Your Assistant's settings are set in Settings > General", 403)
    if key not in KEYS:
        raise Problem("key", "No such preference", 422)


def check(who: Any, key: str, value: object) -> None:
    """Refuse a key or a value the server does not keep."""
    known(key)
    if len(encode(value).encode()) > MAX_BYTES:
        raise Problem("validation", f"A preference is at most {MAX_BYTES} bytes", 422)
    if key == "meetings.auto_share":
        views.human_only(who)
        if not isinstance(value, bool):
            raise Problem("validation", "meetings.auto_share is a boolean", 422)
    # The way the web's Send goes (ui/app/pill.ts): read by any client; sends that name no delivery ignore it.
    if key == "compose.send_way" and not (
        value in ("steer", "queue") or isinstance(value, str) and value in SEND_WAY_DEFERS
    ):
        raise Problem("validation", "compose.send_way is steer, queue or defer:<30m|1h|4h|24h|2d|7d|never>", 422)
    # Hide from my chart on the Strategy Bot: a person's own view.
    if key == strategy_bot.HIDE_PREF:
        views.human_only(who)
        if not isinstance(value, bool):
            raise Problem("validation", strategy_bot.HIDE_PREF + " is a boolean", 422)


def register(app: "FastAPI", auth: Any, mutate: "Callable[..., Any]", store: Any) -> None:
    @app.get("/api/v2/preferences/{key}")
    def preference(request: Request, key: str) -> dict[str, Any]:
        """One of the caller's own preferences; null when it has none."""
        who: Any = request.state.identity
        auth.domain(who)
        known(key)
        with store.read() as c:
            row = c.execute("SELECT value FROM preferences WHERE actor=? AND key=?", (who.actor, key)).fetchone()
            return {"key": key, "value": json.loads(row[0]) if row else None}

    @app.post("/api/v2/preferences/{key}")
    def set_preference(request: Request, key: str, body: M.Preference) -> Any:
        """Keep one of the caller's own preferences."""
        who: Any = request.state.identity
        # The model's bare dict | list (its OpenAPI shape) reads as partially unknown: any JSON value is kept.
        value: Any = cast(Any, body).value
        auth.domain(who)
        check(who, key, value)
        if not request.headers.get("idempotency-key"):
            # An unchanged value (a page re-saving what it read) answers from a read: no writer wait.
            with store.read() as c:
                same = c.execute(
                    "SELECT 1 FROM preferences WHERE actor=? AND key=? AND value=?",
                    (who.actor, key, encode(value)),
                ).fetchone()
                if same:
                    privacy.guard_write(c, getattr(request.state, "privacy_source", who), request.url.path, auth)
            if same:
                return {"key": key, "value": value}

        def work(c: Any) -> dict[str, Any]:
            at = H.now()
            c.execute(
                "INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (who.actor, key, encode(value), at),
            )
            if key == strategy_bot.HIDE_PREF:
                strategy_bot.mirror_hide(c, who.actor, encode(value), at)
            return {"key": key, "value": value}

        return mutate(request, body, work)
