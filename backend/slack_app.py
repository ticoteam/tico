"""The company's Slack app tokens, encrypted at rest in the vault, and the gateway's health line.

The owner pastes the bot token (xoxb-) and app-level token (xapp-) from a Slack app created with
`connectors/slack-app-manifest.json`. They are two `credentials` rows of kind `service` (SERVICE), sealed with the vault's
data key and backed up with it: never listed, granted or revealed (backend/credentials.py `Vault.row`). The API only seals
them and reports status; the gateway process (`backend/slack_gateway.py`) is the one that opens them. Tokens are never
returned by an endpoint and never written to a log. See docs/slack.md.
"""

import json
import logging
import re
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
from pydantic import BaseModel, ConfigDict

from . import hubdb as H
from .credentials import SERVICE, Vault
from .store import Problem

HEALTH = "slack:gateway"
MANIFEST = Path(__file__).resolve().parent.parent / "connectors" / "slack-app-manifest.json"
BOT_TOKEN = re.compile(r"^xoxb-[A-Za-z0-9-]{10,}$")
APP_TOKEN = re.compile(r"^xapp-[A-Za-z0-9-]{10,}$")
# The two rows: `username` keeps the id pinned from each on first connect (the workspace's, the app's).
BOT_ID, APP_ID = "slack-bot-token", "slack-app-token"
ROWS = f"SELECT * FROM credentials WHERE id IN ('{BOT_ID}','{APP_ID}') AND kind='{SERVICE}'"
# An older server's own table, moved by backend/migrations/20261117075941_slack_tokens_into_vault.py. One it could not
# open then (KMS unreachable) is still here: under KMS it is sealed with the vault's key, and `load` moves it.
OLD_TABLE, OLD_AAD = "slack_credentials", b"tico-slack-app:v1"
log = logging.getLogger("tico.slack_app")


def _put(c, cipher, actor, bot_token, app_token, team_id="", app_id="", at=None):
    at = at or H.now()
    for cid, name, value, pinned in (
        (BOT_ID, "Slack bot token", bot_token, team_id),
        (APP_ID, "Slack app-level token", app_token, app_id),
    ):
        ciphertext, nonce = cipher.encrypt(c, cid, value)
        c.execute(
            "INSERT INTO credentials(id,name,username,kind,ciphertext,nonce,source,created_at,updated_at,updated_by,"
            "created_by) VALUES(?,?,?,?,?,?,'slack',?,?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,"
            "ciphertext=excluded.ciphertext,nonce=excluded.nonce,revision=credentials.revision+1,"
            "updated_at=excluded.updated_at,updated_by=excluded.updated_by",
            (cid, name, pinned, SERVICE, ciphertext, nonce, at, at, actor, actor),
        )


def _old(c):
    """The older server's row the move left, or None."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (OLD_TABLE,)).fetchone():
        return None
    return c.execute(f"SELECT * FROM {OLD_TABLE} WHERE id='app'").fetchone()


def save(store, actor, bot_token, app_token, vault=None):
    """Seal both tokens. The workspace and app ids are pinned from the tokens on first connect."""
    vault = vault or Vault(store)
    with store.transaction() as c:
        _put(c, vault.cipher, actor, bot_token, app_token)
        c.execute("DELETE FROM service_health WHERE service=?", (HEALTH,))


def _move_old(store, vault):
    """The older server's row, opened with the vault's key and moved into the vault; None when it does not open."""
    try:
        with store.transaction() as c:
            row = _old(c)
            if not row:
                return None
            data = json.loads(AESGCM(vault.cipher.key(c)).decrypt(row["nonce"], row["ciphertext"], OLD_AAD))
            _put(
                c,
                vault.cipher,
                row["created_by"],
                data["bot_token"],
                data["app_token"],
                row["team_id"],
                row["app_id"],
                row["created_at"],
            )
            c.execute(f"DELETE FROM {OLD_TABLE}")
    except Exception as exc:
        log.warning(
            "slack tokens not moved into the vault: category=%s id=%s:app detail=%s",
            "vault_unavailable" if isinstance(exc, Problem) else "cannot_open",
            OLD_TABLE,
            type(exc).__name__,
        )
        return None
    log.info("Moved the Slack tokens from %s into the vault", OLD_TABLE)
    return True


def load(store, vault=None):
    """`{"bot_token","app_token","team_id","app_id","created_at"}` or None. Gateway process only."""
    vault = vault or Vault(store)
    with store.read() as c:
        rows = {r["id"]: r for r in c.execute(ROWS)}
        old = len(rows) < 2 and _old(c)
    if old and _move_old(store, vault):
        return load(store, vault)
    if len(rows) < 2:
        return None
    try:
        with store.read() as c:
            bot, app = (vault.cipher.decrypt(c, rows[cid]) for cid in (BOT_ID, APP_ID))
    except Problem:
        return None
    return {
        "bot_token": bot,
        "app_token": app,
        "team_id": rows[BOT_ID]["username"],
        "app_id": rows[APP_ID]["username"],
        "created_at": rows[BOT_ID]["updated_at"],
    }


def stamp(store):
    """What changed last, without opening the tokens: the gateway restarts its connection on a change."""
    with store.read() as c:
        row = c.execute("SELECT updated_at FROM credentials WHERE id=? AND kind=?", (BOT_ID, SERVICE)).fetchone()
    return row["updated_at"] if row else ""


def pin(store, team_id, app_id):
    with store.transaction() as c:
        for cid, pinned in ((BOT_ID, team_id), (APP_ID, app_id)):
            c.execute("UPDATE credentials SET username=? WHERE id=? AND kind=?", (pinned, cid, SERVICE))


def forget(store):
    with store.transaction() as c:
        c.execute(f"DELETE FROM credentials WHERE id IN ('{BOT_ID}','{APP_ID}') AND kind='{SERVICE}'")
        if _old(c):
            c.execute(f"DELETE FROM {OLD_TABLE}")
        c.execute("DELETE FROM service_health WHERE service=?", (HEALTH,))


def report(store, state, message="", team=""):
    """The gateway's line on the Health page: `connected`, `waiting` (no tokens yet) or `disconnected`
    with the last error. Written on a change only."""
    now = H.now()
    detail = json.dumps({"state": state, "message": message, "team": team})
    with store.transaction() as c:
        if state == "connected":
            c.execute(
                "INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,?,NULL,?) "
                "ON CONFLICT(service) DO UPDATE SET last_succeeded_at=excluded.last_succeeded_at,last_error=NULL,"
                "detail=excluded.detail",
                (HEALTH, now, detail),
            )
        else:
            c.execute(
                "INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,NULL,?,?) "
                "ON CONFLICT(service) DO UPDATE SET last_error=excluded.last_error,detail=excluded.detail",
                (HEALTH, now, detail),
            )


def status(c):
    """The state the Settings card and the Health page read; never a token."""
    rows = {r["id"]: r for r in c.execute(ROWS)}
    old = _old(c) if len(rows) < 2 else None
    row = {"team_id": rows[BOT_ID]["username"], "app_id": rows[APP_ID]["username"]} if len(rows) == 2 else old
    health = c.execute(
        "SELECT last_succeeded_at,last_error,detail FROM service_health WHERE service=?", (HEALTH,)
    ).fetchone()
    detail = {}
    if health:
        try:
            detail = json.loads(health["detail"] or "{}")
        except ValueError:
            detail = {}
    return {
        "configured": bool(row),
        "team_id": row["team_id"] if row else "",
        "app_id": row["app_id"] if row else "",
        "state": detail.get("state") or ("waiting" if row else "off"),
        "message": detail.get("message") or "",
        "last_succeeded_at": health["last_succeeded_at"] if health else None,
        "last_error": health["last_error"] if health else None,
    }


class Tokens(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_token: str
    app_token: str


def with_hosts(manifest, settings):
    """The manifest with this server's link-preview hosts (backend/slack_unfurl.py) for the example one; with
    none Slack can register (a loopback install), no previews are asked for."""
    from .slack_unfurl import hosts

    names = sorted(hosts(settings))
    if names:
        manifest["features"]["unfurl_domains"] = names
        return manifest
    manifest["features"].pop("unfurl_domains", None)
    manifest["oauth_config"]["scopes"]["bot"] = [
        s for s in manifest["oauth_config"]["scopes"]["bot"] if s not in ("links:read", "links:write")
    ]
    events = manifest["settings"]["event_subscriptions"]["bot_events"]
    manifest["settings"]["event_subscriptions"]["bot_events"] = [e for e in events if e != "link_shared"]
    return manifest


def install_slack_app(app, settings, store):
    def owner(request):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner connects Slack", 403)
        return who

    @app.get("/api/v2/slack/app")
    def read(request: Request):
        owner(request)
        with store.read() as c:
            return status(c)

    @app.get("/api/v2/slack/manifest")
    def manifest(request: Request):
        owner(request)
        return with_hosts(json.loads(MANIFEST.read_text()), settings)

    @app.put("/api/v2/slack/tokens")
    def tokens(request: Request, body: Tokens):
        who = owner(request)
        bot, app_token = body.bot_token.strip(), body.app_token.strip()
        if not BOT_TOKEN.match(bot):
            raise Problem("slack_bot_token", "The bot token starts with xoxb-. Copy it from Install App.", 422)
        if not APP_TOKEN.match(app_token):
            raise Problem(
                "slack_app_token", "The app-level token starts with xapp-. Create it under Basic Information.", 422
            )
        save(store, who.actor, bot, app_token, getattr(app.state, "vault", None))
        with store.transaction() as c:
            H.event(c, who.actor, "slack.tokens_saved", "", {})
        return {"ok": True}

    @app.post("/api/v2/slack/disconnect")
    def disconnect(request: Request):
        who = owner(request)
        forget(store)
        with store.transaction() as c:
            H.event(c, who.actor, "slack.disconnected", "", {})
        return {"ok": True, "note": "Forgotten here. Delete or reinstall the app on Slack to revoke its tokens there."}
