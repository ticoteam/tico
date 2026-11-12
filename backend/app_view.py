# pyright: strict
"""A bot's read-only view of the app (docs/computers.md, "Let a bot view the app (read only)"): a Product Owner looks at production signed in as
itself, at phone and desktop width, and takes the screenshots for its own proof pages instead of asking a person to.

Off for every bot until an owner or admin turns it on for that bot (`bot_app_view`, one row per bot that has it). A bot
with it on opens a view from inside a run (`POST /api/v2/app-view`): a token (`tico_av_...`) for at most VIEW_MINUTES,
bound to that run, so it ends with the run too. The token is the bot itself and reads only what that bot already reads;
it is never another person's view. `gate` refuses, in Auth.authenticate before any route runs, everything but a read:
any write (POST, PUT, PATCH, DELETE), and the reads that make or show a token, key or credential. Turning the setting
off ends the bot's open views at once (`check_live` reads it on every request).

Each turn on or off and each view opened is a line in the audit log (`app_view:<bot id>`), and the view itself is kept in
`app_views`. `tico bot app-shot` (clients/commands/bots.py, clients/app_shot.mjs) opens a view and takes the screenshot.
"""

import re
import secrets
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from . import audit_log
from . import store as _store_untyped
from .store import Problem

if TYPE_CHECKING:
    from .auth import Identity

_store: Any = _store_untyped
H: Any = _store.H
digest = cast("Callable[[str], str]", _store.digest)
_audit: Any = audit_log

TOKEN_PREFIX = "tico_av_"
VIEW_MINUTES = 15
REFUSED = "Read-only view: nothing can be changed from it."
CODE = "read_only_view"
READS = ("GET", "HEAD", "OPTIONS")

# Reads refused even so: what makes or shows a token, key or credential (as for Tico support,
# backend/support_access.py ALWAYS_REFUSED), a run's own lease and stream, a computer's logins, and the directory.
API_READS_REFUSED = re.compile(
    r"/api/v2/(?:"
    r"app-view|logout|local-signin|oauth/.*|mcp/oauth.*"
    r"|me/tokens(?:/.*)?|access/tokens(?:/.*)?|service-keys(?:/.*)?|enrollments"
    r"|agents/.*|skills/devices(?:/.*)?|directory/.*|github/token|push/key"
    r"|bots/[^/]+/(?:agent-credential(?:/revoke)?|repository-read-token)"
    r"|credentials(?:/.*)?|credential-[a-z-]+(?:/.*)?|runner-[a-z-]*(?:/.*)?"
    r"|attempts/.*|jobs/.*|runners/.*|computers/[^/]+/(?:logins|keys)(?:/.*)?"
    r"|system/storage/export|support/access(?:/.*)?"
    r"|.*/ws"
    r")"
)
# The /api/v2 path forms the gate reads: no empty or dot segment, and no encoded dot, slash, backslash or NUL. Other
# percent-encoding is allowed (the page sends `task%3A<id>`); the gate runs on the address as sent (app.py) and again on
# the decoded one (Auth.authenticate), so an encoded letter cannot carry a refused read past it.
PLAIN_PATH = re.compile(r"/api/v2(?:/(?:[A-Za-z0-9_~@+=:-]|%[0-9A-Fa-f]{2})(?:[A-Za-z0-9._~@+=:-]|%[0-9A-Fa-f]{2})*)+")
ENCODED_SEPARATOR = re.compile(r"%(?:2[eEfF]|5[cC]|00)")
# Outside /api/v2: the page and its files, and the older /api reads the page still makes. Not a bot's session, SCIM
# or a download.
OTHER_REFUSED = re.compile(r"/(?:scim/.*|download/.*|api/employees/[^/]+/session|api/download/.*|api/runs/[^/]+/log)")


def is_on(c: sqlite3.Connection, bot: str) -> bool:
    return bool(c.execute("SELECT 1 FROM bot_app_view WHERE bot=?", (bot,)).fetchone())


def setting(c: sqlite3.Connection, bot: str) -> dict[str, Any]:
    row: Any = c.execute("SELECT turned_on_by, turned_on_at FROM bot_app_view WHERE bot=?", (bot,)).fetchone()
    return {
        "on": bool(row),
        "turned_on_by": row["turned_on_by"] if row else None,
        "turned_on_at": row["turned_on_at"] if row else None,
    }


def turn(c: sqlite3.Connection, who: "Identity", bot: str, on: bool) -> dict[str, Any]:
    """Turn a bot's read-only view on or off; the caller checked that `who` may. Off ends its open views now."""
    before = setting(c, bot)
    if bool(on) == before["on"]:
        return before
    if on:
        c.execute("INSERT INTO bot_app_view(bot, turned_on_by, turned_on_at) VALUES(?,?,?)", (bot, who.actor, H.now()))
    else:
        c.execute("DELETE FROM bot_app_view WHERE bot=?", (bot,))
        c.execute("UPDATE app_views SET expires_at=? WHERE bot=? AND expires_at>?", (H.now(), bot, H.now()))
    after = setting(c, bot)
    _audit.write(c, who.actor, "app_view:" + bot, before, after, who.via or None)
    H.event(c, who.actor, "bot.app_view", bot, {"on": bool(on)})
    return after


def open_view(c: sqlite3.Connection, who: "Identity") -> dict[str, Any]:
    """A view for the bot `who` in the run it is in: the token, once. Refused unless the setting is on."""
    if who.role != "bot" or not who.attempt_id or who.view or who.via or who.agent:
        raise Problem("forbidden", "Only a bot, from inside one of its runs, opens a view of the app", 403)
    bot: str = H.actor_id(who.actor)
    if not is_on(c, bot):
        raise Problem(
            "app_view_off",
            "This bot cannot view the app. An owner or admin turns on “Can view the app (read only)” for it.",
            403,
        )
    vid: str = H.new_id()
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    opened: str = H.now()
    expires: str = H.shift(H.now(), seconds=VIEW_MINUTES * 60)
    c.execute(
        "INSERT INTO app_views(id, bot, attempt_id, opened_at, expires_at, token_hash) VALUES(?,?,?,?,?,?)",
        (vid, bot, who.attempt_id, opened, expires, digest(token)),
    )
    _audit.write(
        c, who.actor, "app_view:" + bot, None, {"view": vid, "attempt_id": who.attempt_id, "expires_at": expires}
    )
    return {"id": vid, "token": token, "expires_at": expires, "read_only": True}


def identity(c: sqlite3.Connection, token: str) -> "Identity":
    """The bot a view token belongs to, in the run that opened it. Else 401."""
    from .auth import Identity

    row: Any = c.execute(
        "SELECT v.*, a.runner_id FROM app_views v JOIN attempts a ON a.id=v.attempt_id WHERE v.token_hash=?",
        (digest(token),),
    ).fetchone()
    if not row or row["expires_at"] <= H.now() or not is_on(c, row["bot"]):
        raise Problem("identity", "Invalid credential", 401)
    return Identity(
        "bot:" + row["bot"], "bot", runner_id=row["runner_id"], attempt_id=row["attempt_id"], view=row["id"]
    )


def check_live(c: sqlite3.Connection, who: "Identity") -> None:
    """validate_identity for a view: not ended, not expired, and the setting still on, under a write's lock too."""
    row: Any = c.execute("SELECT bot, expires_at FROM app_views WHERE id=?", (who.view,)).fetchone()
    if not row or row["expires_at"] <= H.now() or not is_on(c, row["bot"]) or "bot:" + row["bot"] != who.actor:
        raise Problem("identity", "Invalid credential", 401)


def gate(path: str, method: str | None) -> None:
    """What a view reaches: reads only. Refused (403) before any route runs. `path` is the address as sent."""
    method = (method or "").upper()
    if method not in READS:
        raise Problem(CODE, REFUSED, 403)
    path = path[:-1] if path.endswith("/") and len(path) > 1 else path
    if path == "/api/v2" or path.startswith("/api/v2/"):
        if not PLAIN_PATH.fullmatch(path) or ENCODED_SEPARATOR.search(path) or API_READS_REFUSED.fullmatch(path):
            raise Problem(CODE, "Read-only view: this is not shown in it.", 403)
    elif "%" in path or "/." in path or OTHER_REFUSED.fullmatch(path):
        raise Problem(CODE, "Read-only view: this is not shown in it.", 403)


def install(app: Any, store: Any, auth: Any) -> None:
    from fastapi import Request

    from . import models as M
    from . import views as _views_untyped

    views: Any = _views_untyped

    def bot_id(c: sqlite3.Connection, who: "Identity", slug: str) -> str:
        auth.require_see(c, who, slug)
        row: Any = H.resolve_bot(c, slug)
        if not row:
            raise Problem("not_found", "Bot not found", 404)
        return row["id"]

    @app.get("/api/v2/bots/{bot}/app-view")
    def app_view_setting(request: Request, bot: str) -> dict[str, Any]:
        """Whether this bot may view the app, read only, and who turned it on."""
        who: Identity = request.state.identity
        with store.read() as c:
            return setting(c, bot_id(c, who, bot))

    @app.post("/api/v2/bots/{bot}/app-view")
    def app_view_turn(request: Request, bot: str, body: M.BotAppView) -> dict[str, Any]:
        """Turn “Can view the app (read only)” on or off for a bot. An owner or admin only, as themselves."""
        who: Identity = request.state.identity
        views.human_only(who)
        if not auth.bot_admin(who) or who.support or (who.via and not who.confirmed):
            raise Problem("forbidden", "Only an owner or an admin lets a bot view the app", 403)
        with store.transaction() as c:
            return turn(c, who, bot_id(c, who, bot), body.on)

    @app.post("/api/v2/app-view")
    def app_view_open(request: Request) -> dict[str, Any]:
        """Open a read-only view of the app for this bot, from inside its run. The token is shown once."""
        who: Identity = request.state.identity
        from . import auth as _auth_untyped

        auth_module: Any = _auth_untyped

        with store.transaction() as c:
            auth_module.validate_identity(c, who)  # the run is still this bot's, under the write's lock
            return open_view(c, who)
