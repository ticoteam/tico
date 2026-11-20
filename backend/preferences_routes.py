# pyright: strict
"""A person's or a bot's own preferences: GET and POST /api/v2/preferences/{key}. Each actor reads and writes only its
own. The server checks the values of the keys it acts on: the web's Send way, meetings' auto share and hiding the
Strategy Owner from one's own chart, and the pages whose first-visit intro a person has had."""

import json
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from fastapi import Request

from . import models as M
from . import strategy_bot as strategy_bot_module
from . import task_privacy
from . import views as views_module
from .store import H as hubdb, Problem, encode
from .api_contract import api_v2

if TYPE_CHECKING:
    from fastapi import FastAPI

H: Any = hubdb
strategy_bot: Any = strategy_bot_module
views: Any = views_module
privacy: Any = task_privacy

# A person's own name and look for their assistant (web/src/tico/look.ts): shown only to them, on their own screens.
ASSISTANT_LOOK = "ui.assistant"
DOT_COLORS = frozenset({"jade", "teal", "sky", "indigo", "violet", "rose", "coral", "gold"})
DOT_SPRITES = frozenset({"pixel"})
NAME_MAX = 40

# The defers the web's Send can keep (preference compose.send_way): the durations its menu offers (ui/app/pill.ts).
SEND_WAY_DEFERS = frozenset("defer:" + f for f in ("30m", "1h", "4h", "24h", "2d", "7d", "never"))


# The keys a client keeps here: the web's (ui/app: pill, rails, sidebar, tasks-page, task-list, pipeline-rail), the
# Strategy Owner's chart switch and meetings' auto share. Every other row in `preferences` is the server's own
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
        "ui.tracker_seen_at",
        "intros.seen",
        ASSISTANT_LOOK,
        strategy_bot.HIDE_PREF,
    }
)
MAX_BYTES = 4096
# A page key in web/src/tico/intros.ts INTROS: lower case words joined by dashes.
INTRO_PAGE = re.compile(r"[a-z][a-z-]{0,39}")
INTRO_PAGES = 64


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
    # When the person last opened Tico's tracker (web/src/tico/Tracker.tsx): Back counts what finished since.
    if key == "ui.tracker_seen_at":
        at = H.parse_ts(value) if isinstance(value, str) else None
        if at is None or at.tzinfo is None:
            raise Problem("validation", "ui.tracker_seen_at is an ISO-8601 date/time with a timezone", 422)
    # The pages whose first-visit intro played for this person (web/src/tico/Intro.tsx), each with when: a person's
    # own, as agents get no intros. A write adds its pages to those kept (`seen_union`).
    if key == "intros.seen":
        views.human_only(who)
        if not isinstance(value, dict) or len(cast(dict[str, object], value)) > INTRO_PAGES:
            raise Problem("validation", f"intros.seen is an object of at most {INTRO_PAGES} pages", 422)
        for page, at in cast(dict[str, object], value).items():
            when = H.parse_ts(at) if isinstance(at, str) else None
            if not INTRO_PAGE.fullmatch(page) or when is None or when.tzinfo is None:
                raise Problem("validation", "intros.seen maps a page to an ISO-8601 date/time with a timezone", 422)
    if key == ASSISTANT_LOOK:
        check_look(value)
    # Hide from my chart on the Strategy Owner: a person's own view.
    if key == strategy_bot.HIDE_PREF:
        views.human_only(who)
        if not isinstance(value, bool):
            raise Problem("validation", strategy_bot.HIDE_PREF + " is a boolean", 422)


def seen_union(c: Any, actor: str, value: dict[str, str]) -> dict[str, str]:
    """intros.seen as kept after a write: the pages already kept, each at its first time, and the new ones. Two tabs
    opening two pages each send their own page, and neither loses the other's."""
    row = c.execute("SELECT value FROM preferences WHERE actor=? AND key='intros.seen'", (actor,)).fetchone()
    old = json.loads(row[0]) if row else None
    kept: dict[str, str] = {**value, **(cast(dict[str, str], old) if isinstance(old, dict) else {})}
    if len(kept) > INTRO_PAGES:
        raise Problem("validation", f"intros.seen keeps at most {INTRO_PAGES} pages", 422)
    return kept


def check_look(value: object) -> None:
    """ui.assistant is null, or {name?, color?, sprite?}: a name of 1 to 40 characters, one of the eight Dot colors,
    and the one sprite (or null for none)."""
    if value is None:
        return
    if not isinstance(value, dict):
        raise Problem("validation", ASSISTANT_LOOK + " is an object: name, color, sprite", 422)
    look = cast(dict[str, object], value)
    extra = set(look) - {"name", "color", "sprite"}
    if extra:
        raise Problem("validation", ASSISTANT_LOOK + " keeps only name, color and sprite", 422)
    name = look.get("name")
    if name is not None and not (isinstance(name, str) and 0 < len(name.strip()) <= NAME_MAX and name == name.strip()):
        raise Problem("validation", f"{ASSISTANT_LOOK} name is 1 to {NAME_MAX} characters, trimmed", 422)
    color = look.get("color")
    if color is not None and color not in DOT_COLORS:
        raise Problem("validation", ASSISTANT_LOOK + " color is one of " + ", ".join(sorted(DOT_COLORS)), 422)
    sprite = look.get("sprite")
    if sprite is not None and sprite not in DOT_SPRITES:
        raise Problem(
            "validation", ASSISTANT_LOOK + " sprite is " + " or ".join(sorted(DOT_SPRITES)) + ", or null", 422
        )


def register(app: "FastAPI", auth: Any, mutate: "Callable[..., Any]", store: Any) -> None:
    @app.get(
        "/api/v2/preferences/{key}",
        **api_v2(
            "Team",
            "getPreference",
            "One of your own preferences (the keys clients keep: compose.send_way, ui.rails, org.history, org.mine, tasks.view, tasks.pins, pipeline.rail_hidden, chart.hide_strategy_bot, meetings.auto_share, ui.tracker_seen_at, intros.seen, ui.assistant); null when unset",
            "PreferenceValue",
        ),
    )
    def preference(request: Request, key: str) -> dict[str, Any]:
        """One of the caller's own preferences; null when it has none."""
        who: Any = request.state.identity
        auth.domain(who)
        known(key)
        with store.read() as c:
            row = c.execute("SELECT value FROM preferences WHERE actor=? AND key=?", (who.actor, key)).fetchone()
            return {"key": key, "value": json.loads(row[0]) if row else None}

    @app.post(
        "/api/v2/preferences/{key}",
        **api_v2(
            "Team",
            "setPreference",
            "Keep one of your own preferences; a value is at most 4 KB (intros.seen adds its pages to those kept)",
            "PreferenceValue",
        ),
    )
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
            kept = seen_union(c, who.actor, value) if key == "intros.seen" else value
            c.execute(
                "INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (who.actor, key, encode(kept), at),
            )
            if key == strategy_bot.HIDE_PREF:
                strategy_bot.mirror_hide(c, who.actor, encode(value), at)
            return {"key": key, "value": kept}

        return mutate(request, body, work)
