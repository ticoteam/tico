"""Mail actions an automation queued in Tico, carried out on the computer that reads the mailbox.

The runner (runner_support/connectors.py) claims them from the hub and runs `mail.sh mail-action` with one action on stdin.
The hub decided them, its send switch included, so like the owner's own commands they pass no bot policy, lint or
reviewer. Each is safe to run again after a lost answer or a lost lease:
  archive, label, leave       Gmail label changes, the same the second time
  draft                       kept in `drafts` under a key made from the action id: a retry finds it
  send, forward               a draft first, then a row in `sends` before the API call. Gmail deletes a draft
                              when it sends it, so a retry whose draft is gone never sends again; it reports the
                              earlier send (or the person's discard) instead.

Reply drafts keep the thread (In-Reply-To, References, the threadId) and go to the message's sender unless the
automation named `to`.
"""

from datetime import datetime

from . import DEFAULT_TZ, Failure, Refused, compose, db, labels as lb, zone

KINDS = {"archive": "archive", "label": "label", "leave": "label", "draft": "draft", "send": "draft",
         "forward": "draft"}       # action -> the CLI verb it needs on the mailbox
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
    return _send(ctx, aid, draft_id, to, subject, thread)


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


def _send(ctx, aid, draft_id, to, subject, thread):
    key = compose.key("automation-send", ctx.mailbox, aid)
    row = db.get_send(ctx.conn, key)
    if row and row["status"] == "sent":
        return {"ok": True, "detail": "already sent", "message": row["message_id"], "thread": row["thread_id"]}
    if not row:     # the row comes first: a crash after the send leaves it pending, never absent
        day = datetime.now(zone(DEFAULT_TZ)).strftime("%Y-%m-%d")
        db.claim_send(ctx.conn, key, ctx.slug, ctx.mailbox, "automation:" + aid, to, subject, thread, draft_id, day,
                      source="automation")
    try:
        sent = ctx.gmail.send_draft(draft_id)
    except Failure as e:
        if e.status != 404:
            raise                       # pending: the retry finds the draft still there and sends it
        # The draft is gone: an earlier attempt sent it (its answer lost), or a person discarded it.
        db.finish_send(ctx.conn, key, "", "sent")
        return {"ok": True, "detail": "already sent (or the draft was discarded)", "thread": thread}
    db.finish_send(ctx.conn, key, sent.get("id", ""), "sent")
    return {"ok": True, "detail": "sent", "message": sent.get("id", ""),
            "thread": sent.get("threadId") or thread}
