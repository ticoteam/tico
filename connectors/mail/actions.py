"""Mail actions an automation queued in Tico, carried out on the computer that reads the mailbox.

The runner (runner_support/connectors.py) claims them from the hub and runs `mail.sh mail-action` with one action on stdin.
The hub decided them, its send switch included, so like the owner's own commands they pass no bot policy, lint or
reviewer. Each is safe to run again after a lost answer or a lost lease:
  archive, label, leave       Gmail label changes, the same the second time
  draft                       kept in `drafts` under a key made from the action id: a retry finds it
  send, forward               a draft first, then a row in `sends` before the API call. Gmail deletes a draft
                              when it sends it, so a retry whose draft is gone never sends again; it reports the
                              earlier send (or the person's discard) instead.
  unsubscribe                 one RFC 8058 request to the message's own link, claimed before its POST
                              (unsubscribe.submit): a retry answers already_attempted and sends nothing

Reply drafts keep the thread (In-Reply-To, References, the threadId) and go to the message's sender unless the
automation named `to`.
"""

import base64
from datetime import datetime

from . import DEFAULT_TZ, Failure, Refused, access, compose, db, gmail as gm, labels as lb, zone
from . import unsubscribe as unsub
from .rules import in_domain

KINDS = {"archive": "archive", "label": "label", "leave": "label", "draft": "draft", "send": "draft",
         "forward": "draft", "unsubscribe": "unsubscribe", "dispatch_send": "draft"}       # action -> the CLI verb it needs on the mailbox
FORWARDED = "\n\n---------- Forwarded message ---------\nFrom: {from_header}\nDate: {date}\nSubject: {subject}\n" \
            "To: {to}\n\n{body}"


def verb(kind):
    if kind not in KINDS:
        raise Refused(f"unknown mail action {kind!r}.", "Tico queues " + ", ".join(KINDS) + ".")
    return KINDS[kind]


def perform(ctx, action):
    """Carry out one claimed action ({id, mailbox, msg_id, action, args}) with ctx.gmail and ctx.conn. Returns
    {ok, detail, ...}; raises Refused for an action that can never succeed and Failure for one worth a retry."""
    kind, aid = action["action"], str(action["id"])
    mid, args = str(action.get("msg_id") or ""), action.get("args") or {}
    verb(kind)
    if kind == "dispatch_send":
        return _dispatch(ctx, aid)
    if not mid:
        raise Refused(f"the {kind} action names no message.")
    if kind == "archive":
        _modify(ctx, mid, remove=["INBOX"])
        return {"ok": True, "detail": "archived"}
    if kind == "label":
        name = label_name(args.get("label") or args.get("tag") or args.get("name"))
        _modify(ctx, mid, add=[name])
        return {"ok": True, "detail": f"labelled {name}"}
    if kind == "leave":
        _modify(ctx, mid, add=[lb.NEEDS_OWNER])
        return {"ok": True, "detail": "left for you"}
    if kind == "unsubscribe":
        return _unsubscribe(ctx, mid)
    original = _original(ctx, mid)
    text = str(args.get("body") or args.get("text") or "")
    if kind == "forward":
        to = addresses(args.get("to"))
        if not to:
            raise Refused("forward needs `to`.")
        subject = "Fwd: " + compose.RE_PREFIX.sub("", original["subject"] or "").strip()
        body = text + FORWARDED.format(from_header=original["from_header"], date=original["date"],
                                       subject=original["subject"], to=", ".join(original["to"]),
                                       body=original["body"])
        raw, thread = compose.build(to, subject, body, from_addr=ctx.mailbox), ""
    else:
        if not text.strip():
            raise Refused(f"a {'reply' if kind == 'send' else 'draft'} needs a body.")
        to = addresses(args.get("to")) or [original["from"]]
        headers = original.get("headers") or {}
        mid_header = str(headers.get("message-id") or "").strip()
        refs = " ".join(x for x in (str(headers.get("references") or "").strip(), mid_header) if x)
        subject = compose.reply_subject(original["subject"]) or "Re:"
        raw = compose.build(to, subject, text, from_addr=ctx.mailbox, in_reply_to=mid_header, references=refs)
        thread = original["thread_id"]
    draft_id = _draft(ctx, aid, raw, thread, to, subject)
    if kind == "draft" or args.get("draft_only"):     # draft_only: the inbox's send switch was off at claim
        return {"ok": True, "detail": "drafted", "draft": draft_id, "thread": thread}
    from . import policy
    queued = policy.queue_send(ctx.mailbox, raw, thread, original["id"], automation_action_id=aid)
    return {"ok": queued["state"] in ("pending", "waiting_approval", "sent"), "retry": False,
            "detail": queued["state"], "send_queue_id": queued["id"], "draft": draft_id, "thread": thread}


def label_name(name):
    """A hub label: `x` is hub/x, a Gmail system label (INBOX, STARRED) stays itself."""
    name = str(name or "").strip()
    if not name:
        raise Refused("label needs a label name.")
    return lb.check_name(name if name.startswith(lb.PREFIX) or name.upper() == name else lb.PREFIX + name)


def addresses(value):
    items = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    return [str(a).strip().lower() for a in items if "@" in str(a)]


def _modify(ctx, mid, add=(), remove=()):
    ctx.gmail.modify(mid, [lb.resolve(ctx.gmail, n) for n in add], [lb.resolve(ctx.gmail, n) for n in remove])


def _original(ctx, mid):
    try:
        return ctx.normalize(ctx.gmail.get(mid))
    except Failure as e:
        if e.status == 404:
            raise Refused(f"there is no message {mid} in {ctx.mailbox} any more.") from e
        raise


def _unsubscribe(ctx, mid):
    """One RFC 8058 request to the message's own unsubscribe link, for mail the owner may unsubscribe from. Refused, with
    a reason and nothing sent, for starred mail, internal mail, mail from someone on the people roster and mail from
    someone the mailbox already writes to. Only an accepted request is ok, so an archive chained after it never runs
    for a sender who did not confirm. Nothing here is retried: a repeat would only answer already_attempted."""
    original = _original(ctx, mid)
    why = _keep_reason(ctx, original)
    if why:
        return {"ok": False, "retry": False, "detail": f"not unsubscribing: {why}"}
    try:
        url = _one_click_url(ctx, mid)
    except unsub.Unsupported as e:
        return {"ok": False, "retry": False, "detail": str(e)}
    result = unsub.submit(ctx.conn, ctx.mailbox, url)
    ctx.audit("unsubscribe", mid, result)
    if result["accepted"]:
        return {"ok": True, "detail": "already unsubscribed" if result.get("already_attempted") else "unsubscribed"}
    return {"ok": False, "retry": False, "detail": f"not confirmed by the sender: {result['status']}"}


def _keep_reason(ctx, original):
    """Why the owner keeps this mail (no unsubscribe), or '' when it may be unsubscribed from. With no internal domains
    known on this computer (a Docker install with no registry), nothing can be told apart from a coworker: refused."""
    sender = original["from"]
    if not ctx.policy.get("internal_domains"):
        return "internal domains unknown on this computer"
    if "STARRED" in original["labels"]:
        return "the message is starred"
    if in_domain(sender, ctx.policy["internal_domains"]) or sender == ctx.mailbox:
        return "the mail is internal"
    if sender in {a.lower() for a in access.roster_mailboxes()}:
        return "the sender is on the people roster"
    if _known_sender(ctx, original):
        return "the mailbox already writes to the sender"
    return ""


def _known_sender(ctx, original):
    """Whether the mailbox already writes to this sender: its sends or its synced sent mail have them as a recipient,
    in any thread (db.has_sent_to), or the message's thread holds a message the mailbox sent to them (_answered_in_thread)."""
    if db.has_sent_to(ctx.conn, ctx.mailbox, original["from"]):
        return True
    if not original["thread_id"]:
        return False
    return _answered_in_thread(ctx, original)


def _answered_in_thread(ctx, original):
    """Whether the thread holds a message the mailbox sent to this sender: SENT, from the owner's address, with the sender
    among its To or Cc. A draft, or inbound mail that spoofs the owner's From, does not count."""
    sender = original["from"].lower()
    for m in ctx.gmail.thread(original["thread_id"]):
        sent = gm.normalize(m)
        if ("SENT" in sent["labels"] and sent["from"].lower() == ctx.mailbox.lower()
                and sender in {a.lower() for a in sent["to"] + sent["cc"]}):
            return True
    return False


def _one_click_url(ctx, mid):
    """The message's List-Unsubscribe URL, found as the unsubscribe command finds it (__main__.py `unsubscribe_message`):
    the raw message, whose DKIM signature is checked before any link is used."""
    raw = ctx.gmail.get(mid, fmt="raw")
    encoded = raw.get("raw", "")
    if not encoded:
        raise unsub.Unsupported("Raw message unavailable; manual unsubscribe needed.")
    return unsub.endpoint(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def _draft(ctx, aid, raw, thread, to, subject):
    """The action's draft: made once, found again by the action id on a retry."""
    key = compose.key("automation", ctx.mailbox, aid)
    row = db.get_draft(ctx.conn, key)
    if row and row["draft_id"]:
        return row["draft_id"]
    made = ctx.gmail.create_draft(raw, thread)
    db.put_draft(ctx.conn, key, ctx.slug, ctx.mailbox, "automation:" + aid, to, subject, thread,
                 made.get("id", ""), (made.get("message") or {}).get("id", ""))
    return made.get("id", "")


def _dispatch(ctx, ident):
    """Only the server's frozen payload can leave; uncertain results are never retried into duplicates."""
    from . import oauth
    key = compose.key("email-queue", ctx.mailbox, ident)
    prior = db.get_send(ctx.conn, key)
    if prior:
        return {"ok": prior["status"] == "sent", "retry": False,
                "status": "sent" if prior["status"] == "sent" else "unknown",
                "message": prior.get("message_id", ""), "detail": "This dispatch was already attempted"}
    # This final server round trip rechecks permissions, policy and caps immediately before the provider call.
    permit = oauth.broker(retries=0).post(f"connectors/email/sends/{ident}/dispatch", {"mailbox": ctx.mailbox})
    action = permit.get("send")
    if not action:
        return {"ok": False, "retry": False, "status": "failed", "detail": "Sending permission was revoked"}
    raw, thread = action["raw"], action.get("thread_id", "")
    made = ctx.gmail.create_draft(raw, thread)
    draft_id = made.get("id", "")
    db.claim_send(ctx.conn, key, "connector", ctx.mailbox, ident, [], "", thread, draft_id,
                  datetime.now(zone(DEFAULT_TZ)).strftime("%Y-%m-%d"), source="email-queue")
    try:
        sent = ctx.gmail.send_draft(draft_id)
    except Exception:
        return {"ok": False, "retry": False, "status": "unknown", "detail": "The provider result is unknown"}
    db.finish_send(ctx.conn, key, sent.get("id", ""), "sent")
    return {"ok": True, "retry": False, "status": "sent", "message": sent.get("id", ""), "detail": "sent"}
