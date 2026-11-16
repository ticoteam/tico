"""An automation's draft, send, forward and unsubscribe, carried out by the server for any mailbox it holds a credential
for (backend/automations.py `drain_workspace_mail` claims each one; archive, label and leave stay in `_workspace_mail`).

Each is safe to run again after a lost answer or a lost lease:
  draft            made under the Message-ID `<tico-action-<id>@<domain>>`, so a retry finds it (`mail_compose.ensure_draft`)
  send, forward    the same draft, then its frozen MIME queued with the send gate (`email_sending.enqueue`) under the
                   action's id; a retry finds the queued send, so nothing goes out twice. The action waits
                   (`waiting_send`) until the send settles it (`email_sending.settle_automation`).
  unsubscribe      one RFC 8058 request, claimed before it goes (backend/mail_unsubscribe.py)

A send claimed while the inbox's send switch or What Tico may do's Send mail is off became a draft at the claim
(`automations._claim_workspace_mail`), its result marked `send_off`. Gmail is called under no write lock; the queueing
takes one short write.
"""

import json

from . import gmail_live, mail_compose, mail_unsubscribe
from . import mailbox_access as access
from .store import Problem

ACTIVE_SEND = ("pending", "waiting_approval", "dispatching")


def _original(c, mailbox, msg_id):
    try:
        return gmail_live.message(c, mailbox, msg_id)
    except Problem as missing:
        if missing.code == "not_found":
            raise Problem("not_found", f"there is no message {msg_id} in {mailbox} any more", 404) from missing
        raise


def perform(store, row, actor, app=None):
    """Carry out one claimed draft, send, forward or unsubscribe. Returns {ok, detail, ...}; `waiting_send` true when a
    send is queued and settles the action later. Raises Problem (retryable when Gmail did not answer)."""
    kind, aid, box, mid = row["action"], row["id"], row["mailbox"], row["msg_id"]
    args = json.loads(row["args"] or "{}")
    if kind == "unsubscribe":
        return mail_unsubscribe.unsubscribe(store, box, mid, actor)
    send_off = bool(json.loads(row["result"] or "{}").get("send_off"))
    message_id = mail_compose.message_id("action", aid, box)
    text = str(args.get("body") or args.get("text") or "")
    with store.read() as c:
        original = _original(c, box, mid)
        if kind == "forward":
            raw, thread, _to, _subject = mail_compose.forward(box, original, text, args.get("to"), message_id)
        else:
            if not text.strip():
                raise Problem("body", f"a {'reply' if kind == 'send' else 'draft'} needs a body", 422)
            raw, thread, _to, _subject = mail_compose.reply(box, original, text, message_id, to=args.get("to") or ())
        draft = mail_compose.ensure_draft(c, box, raw, thread, message_id)
    # A reply stays in its original's thread; a forward is a new message, whatever thread Gmail filed its draft in.
    if kind == "draft" or send_off:
        return {"ok": True, "detail": "drafted", "draft": draft["id"], "thread": thread}
    return queue(store, app, row, actor, raw, thread, original["id"] if kind == "send" else "", draft["id"])


def queue(store, app, row, actor, raw, thread, reply_to, draft_id):
    """Queue the action's frozen MIME with the send gate, once: a retry finds the send its first try queued."""
    from . import email_sending as ES
    from .email_content import snapshot

    box, aid = row["mailbox"], row["id"]
    body = ES.Enqueue(
        mailbox=box, raw=raw, draft_id=draft_id, thread_id=thread, reply_to_id=reply_to, automation_action_id=aid
    )
    with store.read() as c:
        prior = c.execute(
            "SELECT * FROM email_sends WHERE automation_action_id=? ORDER BY rowid DESC LIMIT 1", (aid,)
        ).fetchone()
        sent = ES.public(prior) if prior else None
        if sent is None:
            if not access.may(c, actor, box)["send"]:
                raise Problem("forbidden", f"the automation may not send from {box}", 403)
            snap = snapshot(raw, box)
            reply_id, _thread = ES.resolve_reply(c, box, snap, reply_to, thread)
            ES.warm(
                c,
                {
                    "mailbox": box,
                    "bot": actor.partition(":")[2] if actor.startswith("bot:") else "",
                    "reply_to_id": reply_id,
                    "recipients": ES.dump(snap["recipients"]),
                },
            )
    if sent is None:
        if app is None:
            raise Problem("unavailable", "The send gate is not ready; the action is tried again", 503, retryable=True)
        classification = ES.classify(app, store, snap)
        with store.transaction(durable=True) as c:
            sent = ES.enqueue(c, app.state.auth, actor, body, {**classification, "lint": []})
    out = {"draft": draft_id, "thread": thread, "send_queue_id": sent["id"]}
    if sent["state"] in ACTIVE_SEND:
        return {**out, "ok": True, "waiting_send": True, "detail": sent["state"]}
    if sent["state"] == "sent":
        return {**out, "ok": True, "detail": "sent", "message": sent["provider_message_id"] or ""}
    return {**out, "ok": False, "detail": sent["detail"] or sent["state"]}
