# pyright: strict
"""Setup handoff: how the owner's coding agent gets into a new server without a person pressing anything.

Our team provisions a hosted server for a customer whose coding agent then finishes setting it up over HTTPS (the MCP
server, docs/connect-an-agent.md). A personal token (backend/personal_tokens.py) is how an outside agent reaches a team,
and only a signed-in person can make one, so nothing would let the agent in. The installer makes a one-time secret on
the operator's own computer (TICO_SETUP_HANDOFF, read from its environment, never a command line argument) and gives the
server only its hash (TICO_SETUP_HANDOFF_HASH), as it does the owner's sign-in link (backend/owner_link.py). Whoever holds
the secret, which HQ passes to the customer's agent, trades it once for a personal token of the owner:

    POST /api/v2/setup/handoff  {"secret": "..."}   (no sign-in; the secret is the credential)

The server places the hash once, on its first start, and it lasts HANDOFF_DAYS from then. The trade works once. It is
refused after that, after the week, and after the owner turned it off (`POST /api/v2/setup/handoff/off`, from a signed-in
browser). Every refusal reads the same, so a wrong secret learns nothing a spent one does not. Wrong tries per address
are limited. The token is the owner's own, named "Setup agent · <date>", good for TOKEN_DAYS, listed with the other API
tokens (Settings, or `GET /api/v2/me/tokens`) and revoked there like any of them. It has the owner's rights and the
limit every personal token has: it cannot make or revoke tokens.

The owner's person row is made when the server starts (the installer's seed), so the trade works before the owner has
ever signed in. A server that somehow has none gets it here, the way the first sign-in would make it.
"""

import re
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, Request, Response

from . import store as store_module
from .store import H, Problem

if TYPE_CHECKING:
    from .config import Settings

PATH = "/api/v2/setup/handoff"
OFF_PATH = PATH + "/off"
HANDOFF_DAYS = 7
TOKEN_DAYS = 90
LABEL = "Setup agent"
HASH_RE = re.compile(r"[0-9a-f]{64}")
SECRET_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
GONE = "This setup handoff was used, turned off or has expired. Sign in to the team, or ask its owner for a new token."

# The store's helpers are not annotated; these are the shapes this module relies on.
_now = H.now
_shift = cast(Callable[..., str], H.shift)
_event = cast(Callable[[sqlite3.Connection, str, str, str, dict[str, Any]], Any], H.event)
_digest = cast(Callable[[str], str], cast(Any, store_module).digest)


def check(settings: "Settings") -> None:
    """A hash the server cannot use, or one on an address no certificate covers, stops the start."""
    if not HASH_RE.fullmatch(settings.setup_handoff_hash):
        raise RuntimeError("TICO_SETUP_HANDOFF_HASH must be the 64-character sha256 of the handoff secret")
    if not settings.public_url.startswith("https://") or settings.loopback:
        raise RuntimeError(
            "The setup handoff (TICO_SETUP_HANDOFF_HASH) needs an https address (TICO_DOMAIN): its token would "
            "otherwise cross the network in the clear"
        )
    if not settings.owner_email:
        raise RuntimeError("The setup handoff (TICO_SETUP_HANDOFF_HASH) needs TICO_OWNER_EMAIL")


def place_first(settings: "Settings", store: Any) -> None:
    """On every start, after the tables exist: the hash the installer gave becomes the handoff, once. A server that has
    ever had one keeps its own (a used or turned-off handoff does not come back with a restart)."""
    if settings.setup_handoff_hash:
        with store.transaction() as c:
            place(c, settings.setup_handoff_hash, only_if_none=True)


def place(c: sqlite3.Connection, secret_hash: str, *, only_if_none: bool = False) -> bool:
    if only_if_none and c.execute("SELECT 1 FROM setup_handoffs LIMIT 1").fetchone():
        return False
    now = _now()
    c.execute(
        "INSERT OR IGNORE INTO setup_handoffs(token_hash,created_at,expires_at) VALUES(?,?,?)",
        (secret_hash, now, _shift(now, days=HANDOFF_DAYS)),
    )
    return True


def owner_person(c: sqlite3.Connection, auth: Any) -> str:
    """The owner's roster id: the person row the seed made, or, on a server that has none yet, the one the owner's first
    sign-in would make (auth.join)."""
    email: str = auth.owner_email
    row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone()
    if row:
        return str(row["id"])
    from . import access

    if email in cast(Callable[[sqlite3.Connection], list[str]], access.retired_emails)(c):
        raise auth.retired()
    return cast(Callable[[sqlite3.Connection, str], str], access.join_on_sign_in)(c, email)


def redeem(c: sqlite3.Connection, auth: Any, secret: str | None) -> dict[str, Any] | None:
    """Trade the secret for a token, or None when it is wrong, used, turned off or a week old. The handoff is marked used
    in the caller's transaction before the token is made, so two requests with one secret cannot both get one."""
    if not SECRET_RE.fullmatch(secret or ""):
        return None
    key = _digest(secret or "")
    now = _now()
    claimed = c.execute(
        "UPDATE setup_handoffs SET used_at=? WHERE token_hash=? AND used_at IS NULL AND revoked_at IS NULL "
        "AND expires_at>?",
        (now, key, now),
    ).rowcount
    if claimed != 1:
        return None
    from . import personal_tokens

    human = owner_person(c, auth)
    mint = cast(Callable[[sqlite3.Connection, str, str, int, str], dict[str, Any]], personal_tokens.mint)
    made = mint(c, human, f"{LABEL} · {now[:10]}", TOKEN_DAYS, "human:" + human)
    c.execute("UPDATE setup_handoffs SET human_token_id=? WHERE token_hash=?", (made["id"], key))
    _event(c, "human:" + human, "setup.handoff", made["id"], {"label": made["label"]})
    return made


def view(c: sqlite3.Connection) -> dict[str, Any]:
    """Where the handoff stands: none (this server was not given one), waiting, used, expired or off."""
    row = c.execute(
        "SELECT created_at,expires_at,used_at,revoked_at,human_token_id FROM setup_handoffs "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"state": "none"}
    if row["revoked_at"]:
        state = "off"
    elif row["used_at"]:
        state = "used"
    elif row["expires_at"] <= _now():
        state = "expired"
    else:
        state = "waiting"
    return {
        "state": state,
        "expires_at": row["expires_at"],
        "used_at": row["used_at"],
        "token_id": row["human_token_id"],
    }


def turn_off(c: sqlite3.Connection, who: Any) -> dict[str, Any]:
    """End the handoff: a secret not yet traded stops working, and the token it made, if it made one, is revoked."""
    rows = c.execute(
        "SELECT token_hash,human_token_id FROM setup_handoffs WHERE revoked_at IS NULL ORDER BY created_at DESC"
    ).fetchall()
    now = _now()
    revoked = False
    for item in rows:
        c.execute("UPDATE setup_handoffs SET revoked_at=? WHERE token_hash=?", (now, item["token_hash"]))
        if item["human_token_id"]:
            ended = c.execute(
                "UPDATE human_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (now, item["human_token_id"]),
            ).rowcount
            revoked = ended > 0 or revoked
    if rows:
        _event(c, who.actor, "setup.handoff_off", "", {"token_revoked": revoked})
    return {**view(c), "token_revoked": revoked}


def register(app: FastAPI, auth: Any, store: Any, mutate: Callable[..., Any]) -> None:
    """The trade is open to anyone holding the secret; the status and the switch are the owner's."""
    from . import models as M, personal_tokens
    from . import client_ip as client_ips
    from .mcp import PATH as MCP_PATH
    from .owner_link import Failures

    settings: "Settings" = auth.settings
    failures: Any = Failures()
    address = cast(Callable[[Request, "Settings"], str], cast(Any, client_ips).client_ip)
    no_minting = cast(Callable[[Any, str], None], personal_tokens.no_minting)

    def owner(request: Request) -> Any:
        who: Any = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner sees or turns off the setup handoff", 403)
        return who

    @app.post(PATH)
    def setup_handoff(request: Request, response: Response, body: M.SetupHandoff) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Trade the one-time setup secret for the owner's personal token. No sign-in: the secret is the credential."""
        if not settings.setup_handoff_hash:
            raise Problem("not_found", "Not found", 404)
        ip = address(request, settings)
        if failures.blocked(ip):
            raise Problem("rate_limited", "Too many tries from this address. Wait a few minutes.", 429)
        with store.transaction() as c:
            made = redeem(c, auth, body.secret)
        if not made:
            failures.note(ip)
            raise Problem("gone", GONE, 410)
        response.headers["Cache-Control"] = "no-store"
        return {
            "token": made["token"],
            "token_id": made["id"],
            "label": made["label"],
            "expires_at": made["expires_at"],
            "mcp_url": settings.runner_url + MCP_PATH,
            "team_url": settings.public_url,
        }

    @app.get(PATH)
    def setup_handoff_status(request: Request) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Whether the handoff is waiting, used, expired or off, and which token it made."""
        owner(request)
        with store.read() as c:
            return view(c)

    @app.post(OFF_PATH)
    def setup_handoff_off(request: Request, body: M.Empty) -> Any:  # pyright: ignore[reportUnusedFunction]
        """Turn the handoff off: an unused secret stops working and the token it made is revoked. From a signed-in
        browser, like every change to API tokens."""
        who = owner(request)
        no_minting(who, "Turn off the setup handoff")

        def off(c: sqlite3.Connection) -> dict[str, Any]:
            return turn_off(c, who)

        return mutate(request, body, off)
