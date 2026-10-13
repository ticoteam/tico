"""Whether a bot sends mail without a per-message approval, and to which forward targets: held here, set by a person.

The mail connector (connectors/mail/policy.py `switch`) reads this setting in every send and ignores `outbound_send`
and `forward_to` in the bot's bot.yaml, which only ask. Anything that can push to the bot's repository writes
bot.yaml, BotOps' own runs included, so the file alone must never let a bot write to outside parties unapproved.

Only a person sets the value: the owner, or a human who manages the bot (`Auth.bot_manager`), signed in or with
their own personal token. A bot, BotOps (in its own run or acting for a person) and the Assistant are refused.

No row means no person has decided yet: sending without approval is off (docs/mail.md, "Turning sending on").
Each bot's computer reports what its bot.yaml asks (`mail_request` in the readiness report,
runner/declared_access.py); Health raises an alert when it asks for more than a person approved, and a person may
take exactly that request with `from_request` once they have read it.
"""

import json
import re

from fastapi import Request

from . import models as M
from .store import H, Problem, readiness_document

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_mail_settings(
 bot TEXT PRIMARY KEY, outbound_send INTEGER NOT NULL DEFAULT 0, forward_to_json TEXT NOT NULL DEFAULT '[]',
 updated TEXT NOT NULL, updated_by TEXT NOT NULL);
"""
ADDRESS = re.compile(r"^[^\s@<>\",;()\[\]\\]{1,64}@[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\.[a-z]{2,24}$")
MAX_TARGETS = 20


def stored(c, bot):
    row = c.execute("SELECT * FROM bot_mail_settings WHERE bot=?", (bot,)).fetchone()
    if not row:
        return None
    return {"outbound_send": bool(row["outbound_send"]), "forward_to": json.loads(row["forward_to_json"] or "[]"),
            "updated": row["updated"], "updated_by": row["updated_by"]}


def _addresses(values):
    out = []
    for item in values or []:
        addr = str(item or "").strip().lower()
        if addr and addr not in out:
            out.append(addr)
    return out


def requested(c, bot):
    """What bot.yaml asks, from the computer the bot is assigned to; None when it asks nothing or says nothing."""
    row = c.execute("SELECT r.readiness_json FROM assignments a JOIN runners r ON r.id=a.runner_id "
                    "WHERE a.bot=? AND r.revoked_at IS NULL", (bot,)).fetchone()
    if not row:
        return None
    report = ((readiness_document(row["readiness_json"]).get("bots") or {}).get(bot) or {}).get("mail_request")
    if not isinstance(report, dict):
        return None
    return {"outbound_send": report.get("outbound_send") is True, "forward_to": _addresses(report.get("forward_to"))}


def unapproved(setting, request):
    """What `request` asks beyond `setting`: {outbound_send, forward_to}, or None when nothing."""
    if not request:
        return None
    on = bool(setting and setting["outbound_send"])
    allowed = set((setting or {}).get("forward_to") or [])
    extra = {"outbound_send": request["outbound_send"] and not on,
             "forward_to": [a for a in request["forward_to"] if a not in allowed]}
    return extra if extra["outbound_send"] or extra["forward_to"] else None


def view(c, bot):
    setting, request = stored(c, bot), requested(c, bot)
    return {"bot": bot, "set": setting is not None,
            "outbound_send": bool(setting and setting["outbound_send"]),
            "forward_to": (setting or {}).get("forward_to") or [],
            "updated": (setting or {}).get("updated"), "updated_by": (setting or {}).get("updated_by"),
            "requested": request, "unapproved": unapproved(setting, request)}


def require_person(who, bot=""):
    """A person acting as themselves: never a bot or a computer, never BotOps or the Assistant acting for them."""
    if who.role not in ("owner", "human") or who.via or who.task_actor:
        from .botops_act import mail_settings_hint
        raise Problem("forbidden", "Only a person who manages this bot turns its mail sending on or off, in Tico or "
                                   "with their own token" + (". " + mail_settings_hint(bot) if bot else ""), 403)


def update(c, auth, who, bot, body):
    require_person(who, bot)
    if not H.bot(c, bot):
        raise Problem("not_found", "Bot not found", 404)
    if not auth.bot_manager(c, who, bot):
        raise Problem("forbidden", "Only the owner or a person who manages " + bot + " changes its mail sending", 403)
    current = stored(c, bot) or {"outbound_send": False, "forward_to": []}
    on, targets = current["outbound_send"], list(current["forward_to"])
    if body.from_request:
        request = requested(c, bot)
        if request is None:
            raise Problem("no_request", bot + "'s computer reports no mail request from its bot.yaml", 409)
        on, targets = request["outbound_send"], request["forward_to"]
    if body.outbound_send is not None:
        on = body.outbound_send
    if body.forward_to is not None:
        targets = _addresses(body.forward_to)
    bad = [a for a in targets if not ADDRESS.match(a)]
    if bad:
        raise Problem("forward_to", "Not an email address: " + ", ".join(bad[:3]), 422)
    if len(targets) > MAX_TARGETS:
        raise Problem("forward_to", f"At most {MAX_TARGETS} forward addresses", 422)
    c.execute("INSERT INTO bot_mail_settings(bot,outbound_send,forward_to_json,updated,updated_by) VALUES(?,?,?,?,?) "
              "ON CONFLICT(bot) DO UPDATE SET outbound_send=excluded.outbound_send, "
              "forward_to_json=excluded.forward_to_json, updated=excluded.updated, updated_by=excluded.updated_by",
              (bot, int(bool(on)), json.dumps(targets), H.now(), who.actor))
    H.event(c, who.actor, "bot.mail_settings", bot, {"outbound_send": bool(on), "forward_to": targets})
    return view(c, bot)


def alerts(c):
    """[(bot, what it asks beyond the server value)] for every bot whose bot.yaml asks for more than a person approved."""
    out = []
    for row in c.execute("SELECT a.bot FROM assignments a JOIN bots b ON b.slug=a.bot "
                         "WHERE b.state<>'archived' ORDER BY a.bot"):
        extra = unapproved(stored(c, row["bot"]), requested(c, row["bot"]))
        if extra:
            out.append((row["bot"], extra))
    return out


def alert_text(bot, extra):
    asks = []
    if extra["outbound_send"]:
        asks.append("to send mail without approval")
    if extra["forward_to"]:
        asks.append("to forward mail to " + ", ".join(extra["forward_to"][:3])
                    + (f" and {len(extra['forward_to']) - 3} more" if len(extra["forward_to"]) > 3 else ""))
    return f"{bot} asks {' and '.join(asks)}; a person must turn this on"


def install(app, store, auth, mutate):
    @app.get("/api/v2/bots/{bot}/mail-settings")
    def read(request: Request, bot: str):
        """The bot's own mail sending setting, for the bot itself (its mail connector) or a person who may read it."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if who.actor != "bot:" + bot:
                if who.role not in ("owner", "human"):
                    raise Problem("forbidden", "Only the bot itself or a person reads its mail setting", 403)
                auth.require_read(c, who, bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            return view(c, bot)

    @app.post("/api/v2/bots/{bot}/mail-settings")
    def write(request: Request, bot: str, body: M.MailSettingsUpdate):
        """Turn a bot's sending without approval on or off, or change its forward targets: a person only."""
        who = request.state.identity
        require_person(who, bot)
        return mutate(request, body, lambda c: update(c, auth, who, bot, body))
