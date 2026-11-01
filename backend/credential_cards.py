"""Credentials asked for in chat, and set for a bot (docs/credential-vault.md).

A bot that needs a secret opens a card in the conversation where it asked (`POST /api/v2/credential-requests`): what it is
for, the format it takes and where to get one. The person types the value into the card. It goes from their browser to
`POST /api/v2/credential-requests/{id}/save`, is checked here, stored in the vault under the variable's name and
granted to that one bot. The bot is told it is saved; the value never enters a message, an event, a log or the model.

`hub credential set` takes a value a person gave BotOps in chat instead: the same store, as the person, and the
words they pasted are taken out of the conversation (`redact_value`). What the vault allows is unchanged: only a
credential administrator, or (team rule "Members store credentials") a person who manages the bot, stores a credential, and
only for a bot they may manage.

`hub credential import` moves a secret a bot already has in its own file on its computer (`secrets/<bot>.env`) into the vault,
granted to that bot (`POST /api/v2/credential-imports`): the computer reads the one variable and hands it over its own
authenticated channel (`runner-credential-imports`). Only a credential administrator asks, and the value is in no request
the person or BotOps makes, no message, event, receipt or log.
"""
import json
import re
import time
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Request
from pydantic import Field, SecretStr

from .auth import validate_identity
from .credentials import CredentialWrite, administrator, admin_names as _admin_names, ask_admin_detail, owned
from .models import Contract, ID
from . import reconnects
from .store import H, Problem

ENV = r"^[A-Z_][A-Z0-9_]{0,99}$"
MASK = "••••"
MAX_VALUE = 4096
REMEMBER_S = 3600
KINDS = ("api_key", "password", "token", "connection")

SCHEMA = """
CREATE TABLE IF NOT EXISTS credential_requests(
 id TEXT PRIMARY KEY, requester TEXT NOT NULL, conversation_id TEXT NOT NULL, bot TEXT NOT NULL, asker TEXT NOT NULL,
 env TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', format TEXT NOT NULL DEFAULT '', help_url TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL DEFAULT 'api_key', status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, credential_id TEXT, message_id TEXT);
CREATE INDEX IF NOT EXISTS credential_requests_pending ON credential_requests(requester, status);
CREATE TABLE IF NOT EXISTS credential_imports(
 id TEXT PRIMARY KEY, requester TEXT NOT NULL, bot TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL DEFAULT 'api_key', state TEXT NOT NULL DEFAULT 'requested', message TEXT NOT NULL DEFAULT '',
 credential_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS credential_imports_state ON credential_imports(state, bot);
"""
# A variable the run never takes from the vault, or takes from the computer itself (runner-rs/src/env.rs).
# The one reserved list (clients/access_entry.py), plus two an import never moves: the computer's OpenRouter sign-in
# and its 1Password token, which a bot's secrets file may echo but which belong to the computer.
from clients.access_entry import RESERVED_PREFIXES as RESERVED_ENV_PREFIXES, RESERVED_ENV as _RESERVED  # noqa: E402
RESERVED_ENV = _RESERVED | {"OPENROUTER_API_KEY", "OP_SERVICE_ACCOUNT_TOKEN"}
IMPORT_EXPIRES_S = 600
ONLINE_S = 120


class CredentialRequest(Contract):
    env: str = Field(pattern=ENV)
    for_bot: ID | None = None
    label: str = Field(default="", max_length=120)
    format: str = Field(default="", max_length=120)
    help_url: str = Field(default="", max_length=300)
    kind: Literal["api_key", "password", "token", "connection"] = "api_key"
    on_behalf_of: ID | None = None


class CredentialSave(Contract):
    value: SecretStr = Field(max_length=MAX_VALUE)


class CredentialSet(Contract):
    env: str = Field(pattern=ENV)
    for_bot: ID
    value: SecretStr = Field(max_length=MAX_VALUE)
    name: str = Field(default="", max_length=150)
    kind: Literal["api_key", "password", "token", "connection"] = "api_key"
    username: str = Field(default="", max_length=250)
    redact: bool = True
    on_behalf_of: ID | None = None


class CredentialImport(Contract):
    env: str = Field(pattern=ENV)
    bot: ID
    name: str = Field(default="", max_length=150)
    kind: Literal["api_key", "password", "token", "connection"] = "api_key"
    on_behalf_of: ID | None = None


class CredentialImportReport(Contract):
    value: SecretStr | None = Field(default=None, max_length=MAX_VALUE)
    error: str = Field(default="", max_length=200)


class MessageRedact(Contract):
    span: SecretStr = Field(max_length=MAX_VALUE)
    label: str = Field(default="", max_length=100)
    on_behalf_of: ID | None = None


# ------------------------------------------------------------------ what a value looks like, and where it may still be
_recent = {}          # attempt id -> (until, [values]): memory only, so what comes in late is scrubbed too


def remember(attempt_id, value):
    """Keep a value in memory for an hour so an event or the final reply that arrives after it was set is scrubbed."""
    now = time.monotonic()
    for key in [k for k, (until, _) in _recent.items() if until < now]:
        _recent.pop(key, None)
    if attempt_id:
        _recent.setdefault(attempt_id, (now + REMEMBER_S, []))[1].append(value)


def scrub(text, values, label=MASK):
    for value in sorted(values or (), key=len, reverse=True):
        for form in {value, json.dumps(value)[1:-1]}:
            text = text.replace(form, label)
    return text


def scrub_attempt(attempt_id, body):
    """`body` (an events batch or a completion) with any value set in this attempt taken out; the same object when none."""
    entry = _recent.get(attempt_id)
    if not entry or entry[0] < time.monotonic():
        return body
    return type(body).model_validate(json.loads(scrub(json.dumps(body.model_dump(mode="json")), entry[1])))


def check_format(hint, value):
    """The value as it will be stored, or a refusal that says what it should look like and never repeats it."""
    value = str(value or "").strip()
    shape = ("It should look like " + hint) if hint else "Check it and paste it again"
    if not value or re.search(r"[\x00-\x1f\x7f]", value) or len(value) > MAX_VALUE:
        raise Problem("format", "That is empty or has a line break in it. " + shape, 422)
    if ":" in hint:
        left, _, right = value.partition(":")
        if not left.strip() or not right.strip():
            raise Problem("format", "That is missing the part before or after the colon. " + shape, 422)
    return value


def clean_help_url(url):
    url = str(url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or len(url) > 300:
        raise Problem("help_url", "A help link is an https address", 422)
    return url


# ------------------------------------------------------------------ the store
def admin_names(c, vault):
    return _admin_names(c, vault.admins)


def store_for_bot(c, vault, who, env, bot, value, name="", kind="api_key", username=""):
    """The credential for `env` on `bot`, created or replaced, and granted to that bot alone. `who` is the person."""
    validate_identity(c, who)
    row = H.bot(c, bot)
    admin = administrator(c, who, vault.admins)
    if not admin and not (row and vault.member_stores(c, who, bot)):
        names = admin_names(c, vault)
        raise Problem("forbidden", "Only a credential admin, or someone who manages this bot, can store credentials for it"
                      + (". Ask " + ", ".join(names[:3]) if names else ""), 403)
    if not row:
        raise Problem("not_found", "Bot not found", 404)
    subject = "bot:" + bot
    grants = {}
    for g in c.execute("SELECT credential_id,subject FROM credential_grants WHERE revoked_at IS NULL"):
        grants.setdefault(g["credential_id"], set()).add(g["subject"])
    # A member replaces only a credential they stored themselves; anyone else's stays, and theirs is a new one.
    mine = set() if admin else owned(c, who)
    rows = [r for r in c.execute("SELECT * FROM credentials WHERE env=? ORDER BY created_at", (env,)) if admin or r["id"] in mine]
    own = next((r for r in rows if grants.get(r["id"], set()) <= {subject} and r["id"] in grants), None)
    unused = next((r for r in rows if not grants.get(r["id"])), None)
    existing = own or unused
    label = (name or "").strip() or env
    if not existing and c.execute("SELECT 1 FROM credentials WHERE name=?", (label,)).fetchone():
        label = f"{env} ({row['display_name'] or row['slug']})"
    write = CredentialWrite(name=existing["name"] if existing else label, username=username or (existing["username"] if existing else ""),
                            kind=kind, env=env, secret=SecretStr(value), source="",
                            expected_revision=existing["revision"] if existing else None)
    saved = vault.write(c, who, write, existing["id"] if existing else None, for_bot=bot)
    vault.grant(c, who, saved["id"], subject)
    # A shared credential that already carried this variable to the bot is replaced by its own.
    for r in rows:
        if r["id"] != saved["id"] and subject in grants.get(r["id"], set()):
            c.execute("UPDATE credential_grants SET revoked_at=?,revoked_by=? WHERE credential_id=? AND subject=? AND revoked_at IS NULL",
                      (H.now(), who.actor, r["id"], subject))
    H.event(c, who.actor, "credential.set_for_bot", saved["id"], {"env": env, "bot": bot, "replaced": bool(existing)})
    # A replaced value keeps its grant, so the grant's own hook did not run: the waiting tasks go on here.
    from . import reconnects
    reconnects.secret_stored(c, bot, env)
    return {"id": saved["id"], "name": saved["name"], "env": env, "bot": bot, "replaced": bool(existing)}


def redact_value(c, person, value, env, conversation_id="", attempt_id="", message_id=""):
    """Take a pasted secret out of what was stored: the person's own messages that hold it (in this conversation, or the one
    named), and what the current run recorded. Later events of the run are scrubbed as they arrive."""
    label = f"{MASK} saved as {env}"
    forms = {value, json.dumps(value)[1:-1]}
    count = 0
    ids = set()
    if message_id:
        ids.add(message_id)
    if conversation_id:
        ids.update(r["id"] for r in c.execute("SELECT id FROM messages WHERE conversation_id=? AND sender_id=?",
                                              (conversation_id, person.actor)))
    for mid in ids:
        row = c.execute("SELECT body,sender_id FROM messages WHERE id=?", (mid,)).fetchone()
        if row and row["sender_id"] == person.actor and value in (row["body"] or ""):
            c.execute("UPDATE messages SET body=? WHERE id=?", ((row["body"] or "").replace(value, label), mid))
            count += 1
    if attempt_id:
        for row in c.execute("SELECT id,payload FROM attempt_events WHERE attempt_id=?", (attempt_id,)).fetchall():
            if any(f in row["payload"] for f in forms):
                c.execute("UPDATE attempt_events SET payload=? WHERE id=?", (scrub(row["payload"], [value], label), row["id"]))
        row = c.execute("SELECT final_text FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row and row["final_text"] and value in row["final_text"]:
            c.execute("UPDATE attempts SET final_text=? WHERE id=?", (row["final_text"].replace(value, label), attempt_id))
        remember(attempt_id, value)
    from .stream_capture import scrub_value
    scrub_value(c, value, label, attempt_id, conversation_id)
    # The answers kept for retries (the chat send that carried it, the claim that delivered it to the run).
    for form in forms:
        for row in c.execute("SELECT actor,operation,key,response FROM idempotency WHERE instr(response, ?) > 0", (form,)).fetchall():
            c.execute("UPDATE idempotency SET response=? WHERE actor=? AND operation=? AND key=?",
                      (row["response"].replace(form, label), row["actor"], row["operation"], row["key"]))
    if count:
        H.event(c, person.actor, "message.redacted", conversation_id or message_id, {"messages": count, "env": env})
    return count


# ------------------------------------------------------------------ routes
def install_credential_cards(app, store, vault, auth, botops, delegate, manager):
    """`botops` is the built-in bot's slug, `delegate(c, who, ref)` BotOps' acting-as-the-requester check, and
    `manager(c, person, bot)` the rule for who may manage a bot."""
    def view(c, row, viewer):
        bot = H.bot(c, row["bot"]) or {}
        name = bot.get("display_name") or bot.get("slug") or row["bot"]
        allowed = administrator(c, viewer, vault.admins) or vault.member_stores(c, viewer, row["bot"])
        note = None
        if row["status"] == "pending" and not allowed:
            names = admin_names(c, vault)
            note = "Only a credential admin, or someone who manages this bot, can store this" + (". Ask " + ", ".join(names[:3]) if names else "") + "."
        return {"id": row["id"], "status": row["status"], "env": row["env"], "bot": row["bot"], "bot_name": name,
                "label": row["label"], "title": f"{name} needs {row['label'] or row['env']}", "format": row["format"],
                "help_url": row["help_url"], "kind": row["kind"], "can_save": allowed and row["status"] == "pending",
                "note": note, "credential_id": row["credential_id"]}

    def turn_of(c, who):
        row = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                        (who.attempt_id,)).fetchone() if who.attempt_id else None
        return H.message(c, row["message_id"]) if row else None

    def requester(c, who, ref="turn"):
        """(the person a bot's turn is for, the message whose conversation the card goes in, the task or None): the
        one whose own chat message started it, or on a task the person who fixes the bot's accounts."""
        if (who.role != "bot" and who.via != "botops") or not who.attempt_id:
            raise Problem("forbidden", "Only a bot in a turn asks for a credential", 403)
        turn = turn_of(c, who)
        if who.actor == "bot:" + botops or who.via == "botops":
            person = delegate(c, who, ref or "turn")
            if person.role not in ("owner", "human"):
                raise Problem("no_person", "A credential card needs a human requester", 409)
            msg = H.message(c, ref) if ref and ref not in ("turn", "default") else turn
            # On a task BotOps works, the card goes in that task's thread and the task waits on it, as any bot's does;
            # a person who cannot read the task gets it where they asked, as before.
            task = task_of(c, "bot:" + botops, turn, strict=False)
            if task and msg and H.task_private_readable(c, person.actor, task):
                return person, {**msg, "conversation_id": task["conversation_id"]}, task
            return person, msg, None
        msg = turn
        refs = (msg or {}).get("refs") or {}
        plain = bool(msg and str(msg["sender_id"]).startswith("human:") and msg["recipient_id"] == who.actor
                     and not (refs.get("via") or refs.get("assistant") or refs.get("slack")))
        task = task_of(c, who.actor, msg)
        if task:
            # A turn on a task: the card goes in the task's thread, for the person who wrote to the bot there, else
            # whoever fixes the bot's accounts and can read the task (backend/reconnects.py), and the task waits on it.
            person = msg["sender_id"] if plain else reconnects.person_for(c, store.settings, H.actor_id(who.actor), task)
            if not person:
                raise Problem("no_person", "Nobody can store a credential for this bot on this task: neither its operator "
                              "nor the company owner can read it", 409)
            return auth.identity_for_actor(c, person), {**msg, "conversation_id": task["conversation_id"]}, task
        if not plain:
            raise Problem("no_person", "A credential card needs a person's own chat message to answer", 409)
        return auth.identity_for_actor(c, msg["sender_id"]), msg, None

    def task_of(c, owner, msg, strict=True):
        """The live task `owner` (the bot in the turn) works on, from the turn's message (its thread, or its `task`
        reference). `strict` refuses a turn on a task it cannot card."""
        if not msg:
            return None
        conversation = H.conversation(c, msg["conversation_id"]) or {}
        task_id = conversation.get("task_id") or ((msg.get("refs") or {}).get("task") if msg["recipient_id"] == owner else None)
        task = H.task(c, task_id) if task_id else None
        if task and task["owner_id"] == owner and task["status"] in H.ACTIVE_STATUSES:
            return task
        if strict and (conversation.get("task_id") or conversation.get("scope") == "task" or (msg.get("refs") or {}).get("task")):
            raise Problem("no_person", "A credential card in a task needs a live task the bot owns", 409)
        return None

    def person_only(request):
        who = request.state.identity
        if who.role not in ("owner", "human") or who.via or who.via_token:
            raise Problem("forbidden", "Enter it in Tico yourself, signed in", 403)
        return who

    def load(c, rid, viewer):
        row = c.execute("SELECT * FROM credential_requests WHERE id=?", (rid,)).fetchone()
        if not row or (row["requester"] != viewer.actor and not administrator(c, viewer, vault.admins)):
            raise Problem("not_found", "No such request", 404)
        # A card in a task's thread is for someone who can read the task now: one made private since asks them nothing.
        task_id = (H.conversation(c, row["conversation_id"]) or {}).get("task_id")
        if task_id and not H.task_private_readable(c, viewer.actor, H.task(c, task_id)):
            raise Problem("not_found", "No such request", 404)
        return row

    @app.post("/api/v2/credential-requests")
    def open_card(request: Request, body: CredentialRequest):
        who = request.state.identity
        with store.transaction() as c:
            validate_identity(c, who)
            person, msg, task = requester(c, who, body.on_behalf_of or "turn")
            asker = botops if who.via == "botops" and who.role in ("owner", "human") else H.actor_id(who.actor)
            target = (H.resolve_bot(c, body.for_bot) or {}).get("id", body.for_bot) if body.for_bot else asker
            if target != asker and asker != botops:
                raise Problem("forbidden", "A bot asks for its own credential", 403)
            if not H.bot(c, target):
                raise Problem("not_found", "Bot not found", 404)
            manager(c, person, target)
            url = clean_help_url(body.help_url)
            key = reconnects.secret_key(target, body.env)
            again = reconnects.request_of(c, task["id"], key) if task else None
            if again and again["requester"] != person.actor:
                # Someone else is to save it now: their card replaces the old one (reconnects.open retires it).
                c.execute("UPDATE credential_requests SET status='cancelled',updated_at=? WHERE id=?", (H.now(), again["id"]))
                again = None
            if again:
                # Asked again on the same task: its card is still open, so that card is the answer, not a second one.
                shown = view(c, again, person)
                return {"id": again["id"], "status": "pending", "message_id": again["message_id"], "title": shown["title"],
                        "detail": "The card is still open in the task. You are told when it is saved: do not ask for the value in words."}
            # A newer card replaces the person's older one for the same variable, except one another task waits on.
            waited = reconnects.waited_requests(c, target, body.env)
            for (old,) in c.execute("SELECT id FROM credential_requests WHERE requester=? AND bot=? AND env=? AND status='pending'",
                                    (person.actor, target, body.env)).fetchall():
                if old not in waited:
                    c.execute("UPDATE credential_requests SET status='cancelled',updated_at=? WHERE id=?", (H.now(), old))
            rid = H.new_id()
            row = {"id": rid, "requester": person.actor, "conversation_id": msg["conversation_id"], "bot": target, "asker": asker,
                   "env": body.env, "label": body.label.strip(), "format": body.format.strip(), "help_url": url,
                   "kind": body.kind, "status": "pending", "created_at": H.now(), "updated_at": H.now(), "credential_id": None, "message_id": None}
            c.execute("INSERT INTO credential_requests VALUES(:id,:requester,:conversation_id,:bot,:asker,:env,:label,:format,:help_url,"
                      ":kind,:status,:created_at,:updated_at,:credential_id,:message_id)", row)
            shown = view(c, row, person)
            card = H._write_message(c, "bot:" + asker, person.actor, shown["title"], H.conversation(c, msg["conversation_id"]), "say",
                                    {"credential_request": rid, **({"task": task["id"]} if task else {})}, None, None,
                                    delivered_at=H.now())
            c.execute("UPDATE credential_requests SET message_id=? WHERE id=?", (card["id"], rid))
            if task and not reconnects.open(c, task["id"], target, key, "secret", body.label.strip() or body.env,
                                            person.actor, {"kind": "secret", "request": rid}, message_id=card["id"],
                                            owner=task["owner_id"]):
                # The task could not wait on it: no card is better than one nobody is told was not made.
                raise Problem("no_card", "This task cannot wait on a credential card now", 409)
            H.event(c, who.actor, "credential.requested", rid, {"env": body.env, "bot": target, "for": person.actor})
            return {"id": rid, "status": "pending", "message_id": card["id"], "title": shown["title"],
                    "detail": "A card is open in the chat. You are told when it is saved: do not ask for the value in words."}

    @app.get("/api/v2/credential-requests/{rid}")
    def show_card(request: Request, rid: str):
        who = person_only(request)
        with store.read() as c:
            return view(c, load(c, rid, who), who)

    @app.post("/api/v2/credential-requests/{rid}/cancel")
    def cancel_card(request: Request, rid: str):
        who = person_only(request)
        with store.transaction() as c:
            row = load(c, rid, who)
            if row["status"] == "pending":
                c.execute("UPDATE credential_requests SET status='cancelled',updated_at=? WHERE id=?", (H.now(), rid))
                row = c.execute("SELECT * FROM credential_requests WHERE id=?", (rid,)).fetchone()
                reconnects.dropped(c, rid)     # a task waiting on this card goes on without it
            return view(c, row, who)

    @app.post("/api/v2/credential-requests/{rid}/save")
    def save_card(request: Request, rid: str, body: CredentialSave):
        who = person_only(request)
        with store.transaction() as c:
            validate_identity(c, who)
            row = load(c, rid, who)
            if row["status"] != "pending":
                raise Problem("state", "This card is already " + row["status"], 409)
            value = check_format(row["format"], body.value.get_secret_value())
            manager(c, auth.identity_for_actor(c, row["requester"]), row["bot"])
            stored = store_for_bot(c, vault, who, row["env"], row["bot"], value, kind=row["kind"])
            c.execute("UPDATE credential_requests SET status='saved',updated_at=?,credential_id=? WHERE id=?", (H.now(), stored["id"], rid))
            # The asking bot hears it as the person's own next message: saved, go on. The value is not in it. A card in
            # a task's thread already woke the task once (backend/reconnects.py).
            conversation = H.conversation(c, row["conversation_id"])
            if conversation and H.bot(c, row["asker"]) and not reconnects.card_of(c, row["message_id"]):
                H._write_message(c, row["requester"], "bot:" + row["asker"],
                                 f"Saved {row['label'] or row['env']}. Test the connection now.", conversation, "say",
                                 {"credential_saved": rid}, None, None)
            return view(c, c.execute("SELECT * FROM credential_requests WHERE id=?", (rid,)).fetchone(), who)

    def acting(c, who, ref, metadata_only=False):
        """The person: the caller, or the one BotOps works for."""
        if ref:
            return delegate(c, who, ref)
        if who.role not in ("owner", "human") or who.via not in ("", "botops"):
            raise Problem("forbidden", "A person stores a credential, or BotOps for the person who asked it", 403)
        return who

    @app.post("/api/v2/credential-set")
    def set_credential(request: Request, body: CredentialSet):
        caller = request.state.identity
        with store.transaction() as c:
            validate_identity(c, caller)
            person = acting(c, caller, body.on_behalf_of)
            bot = (H.resolve_bot(c, body.for_bot) or {}).get("id", body.for_bot)
            manager(c, person, bot)
            value = check_format("", body.value.get_secret_value())
            stored = store_for_bot(c, vault, person, body.env, bot, value, body.name, body.kind, body.username)
            if (body.on_behalf_of and caller.role == "bot") or (caller.via == "botops" and caller.attempt_id):
                turn = turn_of(c, caller)
                stored["redacted"] = redact_value(c, person, value, body.env, (turn or {}).get("conversation_id", ""),
                                                  caller.attempt_id, (turn or {}).get("id", "")) if body.redact else 0
            return {**stored, "granted": True}

    @app.post("/api/v2/messages/{mid}/redact")
    def redact_message(request: Request, mid: str, body: MessageRedact):
        caller = request.state.identity
        with store.transaction() as c:
            validate_identity(c, caller)
            person = acting(c, caller, body.on_behalf_of)
            msg = H.message(c, mid)
            if not msg:
                raise Problem("not_found", "Message not found", 404)
            auth.conversation(c, person, msg["conversation_id"])
            if msg["sender_id"] != person.actor and not administrator(c, person, vault.admins):
                raise Problem("forbidden", "Only the person who wrote it, or a credential admin, takes a secret out of a message", 403)
            span, label = body.span.get_secret_value(), body.label.strip() or "saved"
            if not span or span not in (msg["body"] or ""):
                return {"redacted": 0}
            c.execute("UPDATE messages SET body=? WHERE id=?", ((msg["body"] or "").replace(span, f"{MASK} {label}"), mid))
            from .stream_capture import scrub_value
            scrub_value(c, span, f"{MASK} {label}", conversation_id=msg["conversation_id"])
            H.event(c, person.actor, "message.redacted", mid, {"messages": 1, "label": label})
            return {"redacted": 1}

    # ------------------------------------------------------------ a secret a bot already keeps in its own file
    def import_view(row):
        return {"id": row["id"], "bot": row["bot"], "env": row["env"], "state": row["state"], "message": row["message"],
                "credential_id": row["credential_id"], "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def imports_due(c):
        now = H.now()
        return c.execute("SELECT 1 FROM credential_imports WHERE (state='requested' AND created_at<?) OR created_at<? LIMIT 1",
                         (H.shift(now, seconds=-IMPORT_EXPIRES_S), H.shift(now, days=-2))).fetchone()

    def sweep_imports(c):
        now = H.now()
        c.execute("UPDATE credential_imports SET state='failed',message='The computer did not answer in time',updated_at=? "
                  "WHERE state='requested' AND created_at<?", (now, H.shift(now, seconds=-IMPORT_EXPIRES_S)))
        c.execute("DELETE FROM credential_imports WHERE created_at<?", (H.shift(now, days=-2),))

    @app.post("/api/v2/credential-imports")
    def request_import(request: Request, body: CredentialImport):
        caller = request.state.identity
        with store.transaction() as c:
            validate_identity(c, caller)
            person = acting(c, caller, body.on_behalf_of, metadata_only=True)
            if not administrator(c, person, vault.admins):
                raise Problem("forbidden", ask_admin_detail(c, vault.admins, "move a bot's secret into Credentials"), 403)
            bot = (H.resolve_bot(c, body.bot) or {}).get("id", body.bot)
            manager(c, person, bot)
            if body.env in RESERVED_ENV or body.env.startswith(RESERVED_ENV_PREFIXES):
                raise Problem("env", f"{body.env} is a setting of the computer or of Tico, not a credential a bot can be given", 422)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            place = c.execute("SELECT r.last_seen_at,r.label FROM assignments a JOIN runners r ON r.id=a.runner_id "
                              "WHERE a.bot=? AND r.revoked_at IS NULL", (bot,)).fetchone()
            if not place:
                raise Problem("no_computer", "That bot is not on a computer, so there is no secrets file to read", 409)
            if not place["last_seen_at"] or place["last_seen_at"] < H.shift(H.now(), seconds=-ONLINE_S):
                raise Problem("offline", f"{place['label']}, where that bot runs, is offline; try again when it is back", 409)
            sweep_imports(c)
            same = c.execute("SELECT * FROM credential_imports WHERE bot=? AND env=? AND state='requested'",
                             (bot, body.env)).fetchone()
            if same:
                return import_view(same)
            iid, now = H.new_id(), H.now()
            c.execute("INSERT INTO credential_imports(id,requester,bot,env,name,kind,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (iid, person.actor, bot, body.env, body.name.strip(), body.kind, "requested", now, now))
            H.event(c, person.actor, "credential.import_requested", iid, {"env": body.env, "bot": bot})
            return import_view(c.execute("SELECT * FROM credential_imports WHERE id=?", (iid,)).fetchone())

    @app.get("/api/v2/credential-imports/{iid}")
    def show_import(request: Request, iid: str, on_behalf_of: str = ""):
        caller = request.state.identity
        with store.transaction() as c:
            validate_identity(c, caller)
            person = acting(c, caller, on_behalf_of, metadata_only=True)
            sweep_imports(c)
            row = c.execute("SELECT * FROM credential_imports WHERE id=?", (iid,)).fetchone()
            if not row or (row["requester"] != person.actor and not administrator(c, person, vault.admins)):
                raise Problem("not_found", "No such import", 404)
            return import_view(row)

    @app.get("/api/v2/runner-credential-imports")
    def runner_imports(request: Request):
        who = request.state.identity
        # A computer's poll: the write lock only when an import has run out.
        with store.read() as c:
            due = imports_due(c)
        with (store.transaction() if due else store.read()) as c:
            if who.role != "runner" or not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL", (who.runner_id,)).fetchone():
                raise Problem("forbidden", "Only a registered computer may ask for this", 403)
            if due:
                sweep_imports(c)
            rows = c.execute("SELECT i.id,i.bot,i.env FROM credential_imports i JOIN assignments a ON a.bot=i.bot "
                             "WHERE a.runner_id=? AND i.state='requested' ORDER BY i.created_at", (who.runner_id,)).fetchall()
            return {"imports": [dict(r) for r in rows]}

    @app.post("/api/v2/runner-credential-imports/{iid}/report")
    def report_import(request: Request, iid: str, body: CredentialImportReport):
        """The computer's answer: the one variable from that bot's own file, or why there is none. Only the computer the bot
        is on may answer, and the person who asked must still be a credential administrator."""
        who = request.state.identity
        with store.transaction() as c:
            if who.role != "runner" or not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL", (who.runner_id,)).fetchone():
                raise Problem("forbidden", "Only a registered computer may answer this", 403)
            row = c.execute("SELECT i.* FROM credential_imports i JOIN assignments a ON a.bot=i.bot "
                            "WHERE i.id=? AND a.runner_id=?", (iid, who.runner_id)).fetchone()
            if not row:
                raise Problem("not_found", "No such import", 404)
            if row["state"] != "requested":
                return import_view(row)

            def finish(state, message="", credential_id=None):
                c.execute("UPDATE credential_imports SET state=?,message=?,credential_id=?,updated_at=? WHERE id=?",
                          (state, message, credential_id, H.now(), iid))
                return import_view(c.execute("SELECT * FROM credential_imports WHERE id=?", (iid,)).fetchone())

            value = body.value.get_secret_value() if body.value is not None else ""
            if not value:
                return finish("failed", (body.error or f"{row['env']} is not in that bot's secrets file").strip()[:200])
            try:
                person = auth.identity_for_actor(c, row["requester"])
            except Problem:
                return finish("failed", "The person who asked is no longer on the team")
            if not administrator(c, person, vault.admins):
                return finish("failed", "The person who asked is no longer a credential administrator")
            try:
                value = check_format("", value)
            except Problem:
                return finish("failed", f"{row['env']} in that bot's secrets file is empty or has a line break in it")
            stored = store_for_bot(c, vault, person, row["env"], row["bot"], value, row["name"], row["kind"])
            H.event(c, who.actor, "credential.imported", stored["id"], {"env": row["env"], "bot": row["bot"], "by": person.actor})
            return finish("done", "", stored["id"])
