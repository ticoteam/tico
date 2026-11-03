"""The company's Slack app tokens, encrypted at rest, and the gateway's health line.

The owner pastes the bot token (xoxb-) and app-level token (xapp-) from a Slack app created with
`connectors/slack-app-manifest.json`. The API only seals them and reports status; the gateway
process (`backend/slack_gateway.py`) is the one that opens them. Tokens are never returned by an
endpoint and never written to a log. See docs/slack.md.
"""
import json
import logging
import os
import re
import threading
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
from pydantic import BaseModel, ConfigDict

from . import hubdb as H
from .credentials import open_or_reseal
from .store import Problem

HEALTH = "slack:gateway"
MANIFEST = Path(__file__).resolve().parent.parent / "connectors" / "slack-app-manifest.json"
BOT_TOKEN = re.compile(r"^xoxb-[A-Za-z0-9-]{10,}$")
APP_TOKEN = re.compile(r"^xapp-[A-Za-z0-9-]{10,}$")
AAD = b"tico-slack-app:v1"
KEY_FILE = "slack.key"

SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_credentials(
 id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, team_id TEXT NOT NULL DEFAULT '',
 app_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, created_by TEXT NOT NULL);
"""
_lock = threading.Lock()
log = logging.getLogger("tico.slack_app")


def key_file(settings):
    """The local key file: backed up beside credential.key (backend/replication.py)."""
    return Path(settings.db_path).parent / KEY_FILE


def _key(settings, vault=None, c=None, create=True):
    """The vault's KMS-wrapped key when the deployment has one; otherwise a key file beside the
    database, so a copy of the database alone does not carry the tokens. Only a save makes the file:
    a read with it missing returns None rather than a new key that opens nothing. A save may make one: it replaces
    the only row sealed with slack.key, and the backup keeps the old key's copy aside (backend/replication.py)."""
    if settings.credential_kms_key and vault is not None and c is not None:
        return vault.cipher.key(c)
    path = key_file(settings)
    with _lock:
        if not path.exists():
            if not create:
                return None
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(os.urandom(32))
    return path.read_bytes()


def save(store, settings, actor, bot_token, app_token, vault=None):
    """Seal both tokens. The workspace and app ids are pinned from the tokens on first connect."""
    nonce = os.urandom(12)
    with store.transaction() as c:
        c.executescript(SCHEMA)
        sealed = AESGCM(_key(settings, vault, c)).encrypt(
            nonce, json.dumps({"bot_token": bot_token, "app_token": app_token}).encode(), AAD)
        c.execute("DELETE FROM slack_credentials")
        c.execute("INSERT INTO slack_credentials VALUES('app',?,?,'','',?,?)", (sealed, nonce, H.now(), actor))
        c.execute("DELETE FROM service_health WHERE service=?", (HEALTH,))


def load(store, settings, vault=None):
    """`{"bot_token","app_token","team_id","app_id","created"}` or None. Gateway process only."""
    with store.read() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='slack_credentials'").fetchone():
            return None
        row = c.execute("SELECT * FROM slack_credentials WHERE id='app'").fetchone()
        if not row:
            return None
        try:
            key = _key(settings, vault, c, create=False)
            if key is None:
                return None
            # Under KMS, tokens saved before TICO_CREDENTIAL_KMS_KEY was set open with slack.key and are re-sealed once.
            kms = settings.credential_kms_key and vault is not None
            data = json.loads(open_or_reseal(c, key, key_file(settings) if kms else None, row["nonce"], row["ciphertext"],
                                             AAD, "slack_credentials", "id", "app", log))
        except Exception:
            return None
    return {"bot_token": data["bot_token"], "app_token": data["app_token"], "team_id": row["team_id"],
            "app_id": row["app_id"], "created_at": row["created_at"]}


def stamp(store):
    """What changed last, without opening the tokens: the gateway restarts its connection on a change."""
    with store.read() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='slack_credentials'").fetchone():
            return ""
        row = c.execute("SELECT created_at FROM slack_credentials WHERE id='app'").fetchone()
    return row["created_at"] if row else ""


def pin(store, team_id, app_id):
    with store.transaction() as c:
        c.execute("UPDATE slack_credentials SET team_id=?,app_id=? WHERE id='app'", (team_id, app_id))


def forget(store):
    with store.transaction() as c:
        c.executescript(SCHEMA)
        c.execute("DELETE FROM slack_credentials")
        c.execute("DELETE FROM service_health WHERE service=?", (HEALTH,))


def report(store, state, message="", team=""):
    """The gateway's line on the Health page: `connected`, `waiting` (no tokens yet) or `disconnected`
    with the last error. Written on a change only."""
    now = H.now()
    detail = json.dumps({"state": state, "message": message, "team": team})
    with store.transaction() as c:
        if state == "connected":
            c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,?,NULL,?) "
                      "ON CONFLICT(service) DO UPDATE SET last_succeeded_at=excluded.last_succeeded_at,last_error=NULL,"
                      "detail=excluded.detail", (HEALTH, now, detail))
        else:
            c.execute("INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,NULL,?,?) "
                      "ON CONFLICT(service) DO UPDATE SET last_error=excluded.last_error,detail=excluded.detail",
                      (HEALTH, now, detail))


def status(c):
    """The state the Settings card and the Health page read; never a token."""
    exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='slack_credentials'").fetchone()
    row = c.execute("SELECT team_id,app_id,created_at FROM slack_credentials WHERE id='app'").fetchone() if exists else None
    health = c.execute("SELECT last_succeeded_at,last_error,detail FROM service_health WHERE service=?", (HEALTH,)).fetchone()
    detail = {}
    if health:
        try:
            detail = json.loads(health["detail"] or "{}")
        except ValueError:
            detail = {}
    return {"configured": bool(row), "team_id": row["team_id"] if row else "", "app_id": row["app_id"] if row else "",
            "state": detail.get("state") or ("waiting" if row else "off"), "message": detail.get("message") or "",
            "last_succeeded_at": health["last_succeeded_at"] if health else None,
            "last_error": health["last_error"] if health else None}


class Tokens(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_token: str
    app_token: str


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
        return json.loads(MANIFEST.read_text())

    @app.put("/api/v2/slack/tokens")
    def tokens(request: Request, body: Tokens):
        who = owner(request)
        bot, app_token = body.bot_token.strip(), body.app_token.strip()
        if not BOT_TOKEN.match(bot):
            raise Problem("slack_bot_token", "The bot token starts with xoxb-. Copy it from Install App.", 422)
        if not APP_TOKEN.match(app_token):
            raise Problem("slack_app_token", "The app-level token starts with xapp-. Create it under Basic Information.", 422)
        save(store, settings, who.actor, bot, app_token, getattr(app.state, "vault", None))
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
