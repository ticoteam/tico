"""A bot's "send without approval" switch, as older readers name it: a view of the bot's layer of the one sending
policy (backend/email_sending.py), set only by a person.

`outbound_send` is whether the bot's effective mode is On their own. Writing it sets the bot layer's mode (On their own
or Ask first). There are no forward targets any more: a forward is a send, decided and approved like any other, and a
bot's bot.yaml asks for nothing (its `outbound_send` and `forward_to` keys are ignored).

Only a person sets the value: the owner, or a human who manages the bot (`Auth.bot_manager`), signed in or with their
own personal token. A bot, BotOps (in its own run or acting for a person) and the Assistant are refused.
"""

import re

from fastapi import Request

from . import models as M
from .store import H, Problem

ADDRESS = re.compile(r"^[^\s@<>\",;()\[\]\\]{1,64}@[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\.[a-z]{2,24}$")
RETIRED_FORWARDS = "Forward addresses are retired: a forward is a send, approved like any other"


def view(c, bot):
    from .email_sending import effective, saved
    layer = saved(c, "bot:" + bot)
    return {"bot": bot, "set": "mode" in layer, "outbound_send": effective(c, bot=bot)["mode"] == "on_their_own",
            "forward_to": [], "requested": None, "unapproved": None,
            "updated_at": layer.get("updated_at"), "updated_by": layer.get("updated_by")}


def require_person(who, bot=""):
    """A person acting as themselves: never a bot or a computer, never BotOps or the Assistant acting for them."""
    if who.role not in ("owner", "human") or who.via or who.task_actor:
        from .botops_act import mail_settings_hint
        raise Problem("forbidden", "Only a person who manages this bot turns its mail sending on or off, in Tico or "
                                   "with their own token" + (". " + mail_settings_hint(bot) if bot else ""), 403)


def message_sender_can_approve_send(c, auth, row):
    """A message's human sender may approve mail for its bot addressee when they may manage its sending."""
    sender, recipient = str(row.get("sender_id") or ""), str(row.get("recipient_id") or "")
    if not sender.startswith("human:") or not recipient.startswith("bot:"):
        return False
    from .email_sending import can_ok, effective
    bot = H.actor_id(recipient)
    from . import mailbox_access
    boxes = mailbox_access.mailboxes_for(c, recipient)
    if boxes:
        return any(can_ok(c, auth, sender, box["address"], bot, effective(c, box["address"], bot)) for box in boxes)
    return can_ok(c, auth, sender, "", bot, effective(c, bot=bot))


def update(c, auth, who, bot, body):
    require_person(who, bot)
    if not H.bot(c, bot):
        raise Problem("not_found", "Bot not found", 404)
    if not auth.bot_manager(c, who, bot):
        raise Problem("forbidden", "Only the owner or a person who manages " + bot + " changes its mail sending", 403)
    if body.forward_to:
        raise Problem("forward_to", RETIRED_FORWARDS, 422)
    if body.outbound_send is not None:
        from .email_sending import PolicyUpdate, update_policy
        update_policy(c, who, "bot:" + bot, PolicyUpdate(mode="on_their_own" if body.outbound_send else "ask_first"))
        H.event(c, who.actor, "bot.mail_settings", bot, {"outbound_send": body.outbound_send})
    return view(c, bot)


def install(app, store, auth, mutate):
    @app.get("/api/v2/bots/{bot}/mail-settings")
    def read(request: Request, bot: str):
        """The bot's own mail sending setting, for the bot itself (its mail connector) or a person who may read it."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if who.actor != "bot:" + bot:
                if who.role not in ("owner", "human"):
                    raise Problem("forbidden", "Only the bot itself or a person reads its mail setting", 403)
                auth.require_read(c, who, bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            found = view(c, bot)
            from .email_sending import effective
            found["sending_policy"] = effective(c, bot=bot)
            # What Tico may do in Tools is the company's own switch over every bot's: with Send mail off
            # every send is refused (backend/mailbox_access.py caps `send`), and the bot reads sending as off.
            # Organize mail and Change calendar gate the connector's other writes the same way, whichever credential it
            # holds (connectors/mail/oauth.py company_allows).
            from .google_workspace import permissions
            may = permissions(c)
            found.update(company_send=may["send_mail"], company_organize=may["organize_mail"],
                         company_calendar=may["change_calendar"])
            if who.actor == "bot:" + bot and not found["company_send"]:
                found["outbound_send"] = False
            from . import lifecycle
            if who.actor == "bot:" + bot and lifecycle.is_onboarding(c, bot):
                # An onboarding bot sends nothing (backend/lifecycle.py): the send queue refuses every message it
                # queues, and its connector reads sending as off and `onboarding`.
                found.update({"set": True, "outbound_send": False, "forward_to": [], "onboarding": True,
                              "updated_by": "onboarding, until a person above it marks onboarding complete"})
            return found

    @app.post("/api/v2/bots/{bot}/mail-settings")
    def write(request: Request, bot: str, body: M.MailSettingsUpdate):
        """Turn a bot's sending without approval on or off: a person only. It sets the bot's sending policy mode."""
        who = request.state.identity
        require_person(who, bot)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        return mutate(request, body, lambda c: update(c, auth, who, bot, body))
