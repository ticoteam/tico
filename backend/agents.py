"""External agents: a bot run by a harness Tico does not operate, such as a Hermes or OpenClaw profile.

Such a bot is a full bot record (org tree, chat, tasks, routines) with no computer. Nothing
dispatches to it: a message addressed to it lands in its inbox and stays there until the agent
reads it through Tico's MCP endpoint or the `hub` CLI, with the one bot credential minted
here. Presence is a plain heartbeat the agent's box posts on a timer; that is all Tico knows
about whether the agent is alive, and the pages say exactly that (docs/hermes-agents.md).

One row per bot in `agents`: the credential's hash and the last heartbeat. Rotating the
credential replaces the hash; revoking keeps the row so the page can say who revoked it.
"""

import hmac
import secrets

from .harnesses import is_external, resolve_harness
from .store import H, Problem, digest, encode

# The agent's timer posts every minute; three misses is offline. A Mac runner is offline after
# 60 s because its heartbeat is every 15 s and a lease depends on it; nothing here does.
PRESENCE_GAP = 180

# Pairing: a profile with no credential asks to be connected, and a person (or BotOps for them) approves
# the code it prints. Only hashes of the code and the secret are kept; the minted credential waits in the
# row from approval until the first authenticated heartbeat.
PAIRING_TTL = 600
PAIRING_POLL_EVERY = 3
PAIRINGS_PER_CLIENT_HOUR = 10
PAIRINGS_PENDING_MAX = 20
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"         # no 0/O/1/I/L
# How long after an archived bot's agent last used its credential the issue stays on Health.
ARCHIVED_WINDOW = 3600
# The harnesses whose connector (clients/hermes_agent.py) can pair with a code.
PAIRABLE = ("hermes", "openclaw")


def external_harness(c, bot):
    """The bot's harness id when it is external, else None."""
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    if not row:
        return None
    config = H._json(row["config_json"], {}) or {}
    return resolve_harness(config) if is_external(config) else None


def row(c, bot):
    return c.execute("SELECT * FROM agents WHERE bot=?", (bot,)).fetchone()


def presence(c, bot, harness=None, now=None):
    """What the pages show for an external bot, in the shape `views.machine` gives a runner:
    online/awake/ready are one fact here, whether the agent has reported in lately."""
    harness = harness or external_harness(c, bot)
    if not harness:
        return None
    from . import external_sync
    if harness in external_sync.HARNESSES:
        config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
        return external_sync.presence(c, bot, harness, H._json(config["config_json"], {}) or {}, now)
    now = now or H.now()
    record = row(c, bot)
    credential = bool(record and not record["revoked_at"])
    last_seen = record["last_seen"] if record else None
    online = bool(credential and last_seen and last_seen > H.shift(now, seconds=-PRESENCE_GAP))
    agent = {"harness": harness, "credential": credential, "last_seen": last_seen,
             "revoked_at": record["revoked_at"] if record else None,
             "version": (record["version"] if record else "") or "",
             "platform": (record["platform"] if record else "") or "",
             "model": (record["model"] if record else "") or "",
             "provider": (record["provider"] if record else "") or "",
             "profile": (record["profile"] if record else "") or "",
             "detail": (record["detail"] if record else "") or ""}
    return {"online": online, "awake": online, "ready": online, "machine": None, "agent": agent}


def issue_credential(c, who, bot):
    """Mint (or rotate) the one credential this bot's agent uses. The token is returned once
    and stored only as a hash; the previous token stops working at once."""
    harness = external_harness(c, bot)
    if not harness:
        raise Problem("harness", "Only a bot run by an external agent gets an agent credential; "
                      "this bot runs on a registered computer", 422)
    token = "tico-agent-" + secrets.token_urlsafe(32)
    now = H.now()
    c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) VALUES(?,?,?,?,?) "
              "ON CONFLICT(bot) DO UPDATE SET harness=excluded.harness,token_hash=excluded.token_hash,"
              "created=excluded.created,created_by=excluded.created_by,revoked_at=NULL,revoked_by=NULL,last_seen=NULL",
              (bot, harness, digest(token), now, who.actor))
    H.event(c, who.actor, "agent.credential_issued", bot, {"harness": harness})
    return {"bot": bot, "harness": harness, "token": token, "created": now}


def revoke_credential(c, who, bot):
    record = row(c, bot)
    if not record or record["revoked_at"]:
        raise Problem("not_found", "This bot has no active agent credential", 404)
    c.execute("UPDATE agents SET revoked_at=?,revoked_by=? WHERE bot=?", (H.now(), who.actor, bot))
    H.event(c, who.actor, "agent.credential_revoked", bot)
    return {"bot": bot, "revoked": True}


def heartbeat(c, who, body):
    """The agent reporting in. Identity is the credential; the body is only what the page shows."""
    if who.role != "bot" or not who.agent:
        raise Problem("identity", "An agent credential is required", 403)
    bot = H.actor_id(who.actor)
    now = H.now()
    fields = {key: getattr(body, key) for key in ("version", "platform", "model", "provider", "profile", "detail")
              if key in body.model_fields_set}
    fields["last_seen"] = now
    c.execute("UPDATE agents SET " + ",".join(key + "=?" for key in fields) + " WHERE bot=?",
              (*fields.values(), bot))
    if body.tools is not None:
        from . import bot_tools
        bot_tools.agent_report(c, bot, body.tools)
    c.execute("UPDATE agent_pairings SET state='claimed',token=NULL WHERE bot=? AND state='approved'", (bot,))
    inbox = H.inbox(c, who.actor, at=now)
    from . import task_privacy as privacy
    inbox["messages"] = [m for m in inbox["messages"] if privacy.message_readable(c, privacy.actor(who), m)]
    inbox["tasks"] = [t for t in inbox["tasks"] if privacy.task_readable(c, who, t)]
    return {"server_time": now, "bot": bot, "presence_gap_s": PRESENCE_GAP,
            "waiting": {"messages": len(inbox["messages"]), "tasks": len(inbox["tasks"])}}


def _code(value):
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def create_pairing(c, body, client):
    """A profile asks to be paired. Returns what only the profile keeps: the code to read out and the secret."""
    now = H.now()
    c.execute("DELETE FROM agent_pairings WHERE expires_at<?", (H.shift(now, hours=-24),))
    # Whatever is unclaimed and past its time stops holding a credential.
    c.execute("UPDATE agent_pairings SET state='expired',token=NULL WHERE expires_at<=? AND state IN ('pending','approved')",
              (now,))
    client_hash = digest("pairing-client:" + client)
    if c.execute("SELECT count(*) FROM agent_pairings WHERE client_hash=? AND created>?",
                 (client_hash, H.shift(now, hours=-1))).fetchone()[0] >= PAIRINGS_PER_CLIENT_HOUR:
        raise Problem("rate_limited", "Too many pairing requests from this address; try again in an hour", 429, retryable=True)
    if c.execute("SELECT count(*) FROM agent_pairings WHERE state='pending'").fetchone()[0] >= PAIRINGS_PENDING_MAX:
        raise Problem("rate_limited", "Too many pairings are waiting; try again in a few minutes", 429, retryable=True)
    secret = secrets.token_urlsafe(32)
    pairing_id = H.new_id()
    for _ in range(20):
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not c.execute("SELECT 1 FROM agent_pairings WHERE code_hash=?", (digest(raw),)).fetchone():
            break
    else:
        raise Problem("unavailable", "Could not make a pairing code; try again", 503, retryable=True)
    c.execute("INSERT INTO agent_pairings(id,code_hash,secret_hash,profile,host,version,harness,client_hash,created,expires_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)",
              (pairing_id, digest(raw), digest(secret), body.profile, body.host, body.version, body.harness,
               client_hash, now, H.shift(now, seconds=PAIRING_TTL)))
    return {"pairing_id": pairing_id, "code": raw[:4] + "-" + raw[4:], "secret": secret,
            "expires_in": PAIRING_TTL, "poll_every": PAIRING_POLL_EVERY}


def poll_pairing(c, pairing_id, secret):
    """What the profile sees. The credential is handed over exactly once; a wrong secret is a 404."""
    record = c.execute("SELECT * FROM agent_pairings WHERE id=?", (pairing_id,)).fetchone()
    if not record or not hmac.compare_digest(record["secret_hash"], digest(secret or "")):
        raise Problem("not_found", "No such pairing", 404)
    state = record["state"]
    if state in ("pending", "approved") and record["expires_at"] <= H.now():
        c.execute("UPDATE agent_pairings SET state='expired',token=NULL WHERE id=?", (pairing_id,))
        return {"state": "expired"}
    if state == "approved":
        return {"state": "approved", "bot": record["bot"], "token": record["token"]}
    return {"state": state}


def _pending(c, code):
    record = c.execute("SELECT * FROM agent_pairings WHERE code_hash=? AND state='pending' AND expires_at>?",
                       (digest(_code(code)), H.now())).fetchone() if len(_code(code)) == 8 else None
    if not record:
        raise Problem("not_found", "That code is not valid or has expired. Run the pair command again for a new one", 404)
    return record


def show_pairing(c, who, code):
    if who.role not in ("owner", "human"):
        raise Problem("forbidden", "Only a human previews a pairing", 403)
    record = _pending(c, code)
    return {key: record[key] for key in ("profile", "host", "harness", "version", "expires_at")}


def approve_pairing(c, who, manager, code, bot):
    """A person who may manage the bot approves a profile's code: the bot's standing credential is minted (the one
    Create credential makes, replacing any earlier one) and held for the profile to collect."""
    if who.role not in ("owner", "human"):
        raise Problem("forbidden", "Only a person approves a pairing", 403)
    record = _pending(c, code)
    if not H.bot(c, bot):
        raise Problem("not_found", "Bot not found", 404)
    manager(c, who, bot)
    state = H.bot(c, bot)["state"]
    harness = external_harness(c, bot)
    if harness not in PAIRABLE or harness != record["harness"]:
        name = "OpenClaw" if record["harness"] == "openclaw" else "Hermes"
        raise Problem("harness", f"Only a bot run by a {name} profile can be paired with this code; register "
                      f"the bot with the model {record['harness']} first", 422)
    if state not in ("planned", "active"):
        raise Problem("bot_state", "This bot is " + state + "; make it planned or active first", 409)
    if state == "planned":
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (bot,))
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.status','active'),revision=revision+1 WHERE bot=?", (bot,))
        H.event(c, who.actor, "bot.activated", bot)
    issued = issue_credential(c, who, bot)
    c.execute("UPDATE agent_pairings SET state='approved',bot=?,token=?,decided_by=?,decided_at=? WHERE id=?",
              (bot, issued["token"], who.actor, H.now(), record["id"]))
    H.event(c, who.actor, "agent.pairing_approved", bot, {"profile": record["profile"], "host": record["host"]})
    return {"bot": bot, "profile": record["profile"], "host": record["host"]}


def decline_pairing(c, who, code):
    if who.role not in ("owner", "human"):
        raise Problem("forbidden", "Only a person declines a pairing", 403)
    record = _pending(c, code)
    c.execute("UPDATE agent_pairings SET state='declined',decided_by=?,decided_at=? WHERE id=?",
              (who.actor, H.now(), record["id"]))
    H.event(c, who.actor, "agent.pairing_declined", record["profile"] or record["id"], {"host": record["host"]})
    return {"declined": True, "profile": record["profile"], "host": record["host"]}


def note_archived(store, bot):
    """An archived bot's agent used its still-valid credential: Health says so until it stops or is revoked."""
    with store.transaction() as c:
        c.execute("UPDATE agents SET archived_seen=? WHERE bot=? AND revoked_at IS NULL", (H.now(), bot))


def still_reporting(c, bot):
    """The agent record of an archived bot whose credential still works and was used lately, else None."""
    record = row(c, bot)
    if (record and not record["revoked_at"] and record["archived_seen"]
            and record["archived_seen"] > H.shift(H.now(), seconds=-ARCHIVED_WINDOW)):
        return record
    return None


def listing(c, who, auth):
    """Every external agent this person may see, for Settings."""
    out = []
    for record in c.execute("SELECT a.*,b.display_name FROM agents a JOIN bots b ON b.slug=a.bot ORDER BY a.bot"):
        if not auth.bot_access(c, who, record["bot"])["read"]:
            continue
        config = c.execute("SELECT operator FROM bot_config WHERE bot=?", (record["bot"],)).fetchone()
        if who.role != "owner" and not (config and who.actor == "human:" + config["operator"]):
            continue
        value = {k: record[k] for k in ("bot", "display_name", "harness", "created", "created_by",
                                        "last_seen", "version", "platform", "model", "provider",
                                        "profile", "detail", "revoked_at", "revoked_by")}
        value["online"] = bool(not record["revoked_at"] and record["last_seen"]
                               and record["last_seen"] > H.shift(H.now(), seconds=-PRESENCE_GAP))
        out.append(value)
    return out


def setup_snippet(url, bot, token, harness="hermes"):
    """What the person pastes on the agent's box. Kept here so the API and the docs agree."""
    return {"url": url, "bot": bot, "harness": harness, "token": token,
            "mcp_servers": {"tico": {"url": url + "/api/v2/mcp",
                                     "headers": {"Authorization": "Bearer " + token}}},
            "heartbeat": {"method": "POST", "path": "/api/v2/agents/heartbeat",
                          "every_seconds": 60, "offline_after_seconds": PRESENCE_GAP}}


__all__ = ["PRESENCE_GAP", "external_harness", "presence", "issue_credential", "revoke_credential",
           "heartbeat", "listing", "setup_snippet", "encode", "create_pairing", "poll_pairing",
           "approve_pairing", "decline_pairing", "note_archived", "still_reporting"]
