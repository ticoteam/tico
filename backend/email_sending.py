"""The shared email send gate and durable undo queue.

The server freezes MIME content before approval and dispatch. All provider sends, including automation and Assistant
sends, are leased from this queue. A lost provider response is unknown, never an invitation to send a second copy.
Classification is triage, not a security boundary: deterministic permissions, approvals and limits still apply.
"""

import base64
import hashlib
import json
import re
from email import policy as email_policy
from email.parser import BytesParser
from email.utils import getaddresses

from fastapi import Request
from pydantic import Field

from . import mailbox_access as access
from .models import Contract, Empty
from .store import H, Problem

DEFAULTS = {"mode": "ask_first", "what": "replies_only", "to": "anyone", "daily_cap": 50,
            "mailbox_daily_cap": 200, "company_daily_cap": 1000, "pacing_seconds": 10, "undo_seconds": 120,
            "ask_new_address": True, "ask_attachments": True, "ask_many_recipients": True,
            "ask_legal_money": True, "who_can_ok": "bot_managers", "approvers": []}
MODES = {"on_their_own": 0, "ask_first": 1, "never": 2}
ASKS = ("ask_new_address", "ask_attachments", "ask_many_recipients", "ask_legal_money")
CAPS = ("daily_cap", "mailbox_daily_cap", "company_daily_cap")
ACTIVE = ("waiting_approval", "pending", "dispatching")
MAX_RAW = 24 * 1024 * 1024
LIST_BYTES = 128 * 1024


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else str(value).encode()).hexdigest()


def record_bounces(c, mailbox, sender, body):
    """Delivery-status reports suppress only recipients that this inbox actually tried to reach."""
    if not sender.lower().startswith(("mailer-daemon@", "postmaster@")):
        return
    for address in set(re.findall(r"(?:Final|Original)-Recipient:\s*rfc822;\s*([^\s<>;]+@[^\s<>;]+)", body, re.I)):
        address = address.lower()
        if c.execute("SELECT 1 FROM email_sends WHERE mailbox=? AND state IN ('sent','unknown') "
                     "AND EXISTS (SELECT 1 FROM json_each(recipients) WHERE value=?) LIMIT 1", (mailbox, address)).fetchone():
            c.execute("INSERT OR IGNORE INTO email_suppressed_recipients VALUES(?,?,?,?)",
                      (mailbox, address, "delivery_status", H.now()))


class PolicyUpdate(Contract):
    mode: str | None = None
    what: str | None = None
    to: str | None = None
    daily_cap: int | None = Field(default=None, ge=0, le=100000)
    mailbox_daily_cap: int | None = Field(default=None, ge=0, le=100000)
    company_daily_cap: int | None = Field(default=None, ge=0, le=1000000)
    pacing_seconds: int | None = Field(default=None, ge=0, le=3600)
    undo_seconds: int | None = Field(default=None, ge=0, le=600)
    ask_new_address: bool | None = None
    ask_attachments: bool | None = None
    ask_many_recipients: bool | None = None
    ask_legal_money: bool | None = None
    who_can_ok: str | None = None
    approvers: list[str] | None = Field(default=None, max_length=50)
    inherit: bool = False


class Enqueue(Contract):
    mailbox: str = Field(min_length=3, max_length=320)
    raw: str = Field(min_length=1, max_length=MAX_RAW * 4 // 3 + 8)
    draft_id: str = Field(default="", max_length=200)
    thread_id: str = Field(default="", max_length=200)
    reply_to_id: str = Field(default="", max_length=200)
    approval_id: str = Field(default="", max_length=200)
    automation_action_id: str = Field(default="", max_length=100)


class SendApproval(Contract):
    approval_id: str = Field(min_length=1, max_length=200)


class Claim(Contract):
    mailbox: str = Field(min_length=3, max_length=320)


class Receipt(Contract):
    status: str
    message: str = Field(default="", max_length=200)
    detail: str = Field(default="", max_length=1000)


def saved(c, scope):
    row = c.execute("SELECT * FROM email_sending_policies WHERE scope=?", (scope,)).fetchone()
    return {**json.loads(row["settings"]), "updated_at": row["updated_at"], "updated_by": row["updated_by"]} if row else {}


def effective(c, mailbox="", bot=""):
    """Company Ask first is a default. Never and company safeguards are ceilings on every override."""
    layers = [("company", saved(c, "company"))]
    if mailbox:
        layers.append(("inbox", saved(c, "inbox:" + mailbox)))
    if bot:
        layers.append(("bot", saved(c, "bot:" + bot)))
    out, sources, never = dict(DEFAULTS), {key: "company" for key in DEFAULTS}, None
    company = {**DEFAULTS, **layers[0][1]}
    for label, settings in layers:
        if settings.get("mode") == "never":
            never = never or label
        for key in DEFAULTS:
            if key in settings:
                value, source = settings[key], label
                if label != "company":
                    if key in ASKS:
                        if out[key]:
                            value, source = True, sources[key]
                    elif key in CAPS:
                        if out[key] <= value:
                            value, source = out[key], sources[key]
                    elif key in ("pacing_seconds", "undo_seconds"):
                        if out[key] >= value:
                            value, source = out[key], sources[key]
                out[key], sources[key] = value, source
    if never:
        out["mode"], sources["mode"] = "never", never
    # Replies only and recipient restrictions on an inbox cannot be undone by its bot.
    inbox = layers[1][1] if mailbox else {}
    if inbox.get("what") == "replies_only":
        out["what"], sources["what"] = "replies_only", "inbox"
    if inbox.get("to") in ("known_addresses", "internal"):
        out["to"], sources["to"] = inbox["to"], "inbox"
    if "who_can_ok" in inbox:
        out["who_can_ok"], out["approvers"] = inbox["who_can_ok"], inbox.get("approvers", [])
        sources["who_can_ok"] = "inbox"
    if layers[0][1].get("who_can_ok") == "named_people":
        out["who_can_ok"], out["approvers"] = company["who_can_ok"], company["approvers"]
        sources["who_can_ok"] = "company"
    out["sources"] = sources
    out["legacy"] = bool(layers[-1][1].get("legacy"))
    out["company_restrictions_set"] = bool(layers[0][1])
    return out


def policy_view(c, mailbox="", bot=""):
    return {"company": saved(c, "company"), "inbox": saved(c, "inbox:" + mailbox) if mailbox else {},
            "bot": saved(c, "bot:" + bot) if bot else {}, "effective": effective(c, mailbox, bot)}


def update_policy(c, who, scope, body):
    values = body.model_dump(exclude_none=True)
    inherit = values.pop("inherit")
    for key, allowed in (("mode", MODES), ("what", ("replies_only", "replies_and_new")),
                         ("to", ("anyone", "known_addresses", "internal")),
                         ("who_can_ok", ("bot_managers", "inbox_admins", "named_people"))):
        if key in values and values[key] not in allowed:
            raise Problem("policy", "Invalid " + key, 422)
    if any(not access._Facts(c).person(pid) for pid in values.get("approvers", [])):
        raise Problem("policy", "Approvers must be people on the team", 422)
    if inherit:
        c.execute("DELETE FROM email_sending_policies WHERE scope=?", (scope,))
    else:
        current = {k: v for k, v in saved(c, scope).items() if k in DEFAULTS}
        for key in body.model_fields_set:
            if key in DEFAULTS and getattr(body, key) is None:
                current.pop(key, None)
        c.execute("INSERT INTO email_sending_policies VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                  "settings=excluded.settings,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                  (scope, dump({**current, **values}), H.now(), who.actor))
    H.event(c, who.actor, "email.policy", scope, {"fields": sorted(values), "inherit": inherit})


def snapshot(raw, mailbox):
    try:
        data = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
        if len(data) > MAX_RAW:
            raise ValueError()
        msg = BytesParser(policy=email_policy.default).parsebytes(data)
        to = sorted({a.lower() for _, a in getaddresses(msg.get_all("to", []) + msg.get_all("cc", [])
                                                      + msg.get_all("bcc", [])) if a})
        sender = [a.lower() for _, a in getaddresses(msg.get_all("from", []))]
        if not to or len(to) > 100 or sender != [mailbox]:
            raise ValueError()
        from .mail_settings import ADDRESS
        if any(not ADDRESS.fullmatch(a) for a in to):
            raise ValueError()
        files, texts = [], []
        for part in msg.walk():
            if part.is_multipart():
                continue
            payload = part.get_payload(decode=True) or b""
            if part.get_filename() or part.get_content_disposition() == "attachment":
                files.append({"name": str(part.get_filename() or "attachment")[:255], "sha256": digest(payload)})
            elif part.get_content_type() == "text/plain":
                texts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
        if len(files) > 50:
            raise ValueError()
    except (ValueError, TypeError, LookupError) as exc:
        raise Problem("email", "Invalid email: From must be the inbox, with 1–100 valid recipients and at most 50 files", 422) from exc
    body = "\n".join(texts)
    # Stable across MIME boundaries, Date, Message-ID and recipient ordering; includes HTML and attachment bytes.
    parts = [{"type": p.get_content_type(), "name": p.get_filename(), "content_id": p.get("Content-ID"),
              "disposition": p.get_content_disposition(),
              "sha256": digest(p.get_payload(decode=True) or b"")} for p in msg.walk() if not p.is_multipart()]
    material_headers = {key: str(msg.get(key) or "") for key in ("from", "sender", "reply-to", "in-reply-to", "references")}
    groups = {key: sorted(a.lower() for _, a in getaddresses(msg.get_all(key, []))) for key in ("to", "cc", "bcc")}
    content_hash = digest(dump({"subject": str(msg.get("subject", "")), "headers": material_headers,
                                "recipient_headers": groups, "parts": parts}))
    return {"recipients": to, "attachments": files, "subject": str(msg.get("subject", ""))[:1000],
            "body_sha256": digest(body), "body": body, "content_sha256": content_hash,
            "in_reply_to": str(msg.get("in-reply-to", "")), "raw": raw}


def can_ok(c, auth, actor, mailbox, bot, policy):
    try:
        who = auth.identity_for_actor(c, actor)
    except Problem:
        return False
    if policy["who_can_ok"] == "named_people":
        return H.actor_id(actor) in policy["approvers"]
    if policy["who_can_ok"] == "bot_managers" and bot:
        return auth.bot_manager(c, who, bot)
    try:
        row = access._row(c, mailbox)
        if row:
            access._manager(c, who, row, "approve sends")
            return True
        if who.email.lower() == mailbox and access.may(c, who, mailbox)["send"]:
            return True
    except Problem:
        pass
    return False


def approver(c, auth, row, policy):
    """Route the approval card to a person who may actually decide it under the effective rule."""
    facts = access._Facts(c)
    candidates = list(policy["approvers"]) if policy["who_can_ok"] == "named_people" else list(facts.people)
    return next(("human:" + pid for pid in candidates if can_ok(c, auth, "human:" + pid, row["mailbox"], row["bot"], policy)), None)


def approved(c, auth, row, policy):
    aid = row["approval_id"]
    if not aid:
        return False
    ap = H.approval(c, aid)
    if ap:
        payload = ap.get("payload") or {}
        if (ap.get("kind") != "send" or ap.get("decision") != "approved"
                or ap.get("requested_by_id") != row["actor"]
                or not can_ok(c, auth, ap.get("decided_by_id"), row["mailbox"], row["bot"], policy)):
            return False
        def addresses(value):
            return {a.lower() for _, a in getaddresses(value if isinstance(value, list) else [str(value or "")])}
        recipients = addresses(payload.get("to")) | addresses(payload.get("cc")) | addresses(payload.get("bcc"))
        if recipients != set(json.loads(row["recipients"])) or payload.get("mailbox", "").lower() != row["mailbox"]:
            return False
        if payload.get("subject") != row["subject"]:
            return False
        if payload.get("body_sha256") != row["body_sha256"]:
            from connectors.mail.policy import body_digests
            if not row["raw"] or payload.get("body_sha256") not in body_digests(snapshot(row["raw"], row["mailbox"])["body"]):
                return False
        approved_files = [{"name": f.get("name"), "sha256": f.get("sha256")} for f in payload.get("attachments") or [] if isinstance(f, dict)]
        if sorted(approved_files, key=dump) != sorted(json.loads(row["attachments"]), key=dump):
            return False
        # A queue approval binds the full MIME content too; existing text-bound approvals retain their original scope.
        if payload.get("content_sha256") and payload["content_sha256"] != row["content_sha256"]:
            return False
        return True
    # In-chat "send it" is rechecked against the current approver rule. The connector's existing reviewer checks the
    # instruction before enqueue; the binding below prevents reusing that instruction for a changed queued message.
    message = H.message(c, aid)
    if not message or message.get("recipient_id") != "bot:" + row["bot"]:
        return False
    from connectors.mail.policy import SEND_INTENT
    if not SEND_INTENT.search(message.get("body") or "") or not can_ok(
            c, auth, message.get("sender_id"), row["mailbox"], row["bot"], policy):
        return False
    used = c.execute("SELECT id,content_sha256,recipients FROM email_sends WHERE approval_id=? AND id<>? LIMIT 1",
                     (aid, row["id"])).fetchone()
    return not used or (used["content_sha256"] == row["content_sha256"] and used["recipients"] == row["recipients"])


def _original(c, row):
    if not row["reply_to_id"]:
        return None
    found = c.execute("SELECT * FROM mail_messages WHERE mailbox=? AND msg_id=? AND deleted_at IS NULL",
                      (row["mailbox"], row["reply_to_id"])).fetchone()
    return dict(found) if found else None


def known(c, mailbox, address):
    return bool(c.execute("SELECT 1 FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL "
                          "AND (lower(from_addr)=? OR (lower(from_addr)=? "
                          "AND EXISTS (SELECT 1 FROM json_each(labels) WHERE value='SENT') "
                          "AND (EXISTS (SELECT 1 FROM json_each(to_addresses) WHERE lower(value)=?) "
                          "OR EXISTS (SELECT 1 FROM json_each(cc) WHERE lower(value)=?)))) LIMIT 1",
                          (mailbox, address, mailbox, address, address)).fetchone() or c.execute(
        "SELECT 1 FROM email_sends WHERE mailbox=? AND state='sent' "
        "AND EXISTS (SELECT 1 FROM json_each(recipients) WHERE value=?) LIMIT 1", (mailbox, address)).fetchone())


def legacy_authorized(c, row):
    """Only pre-cutover paths keep their original connector checks; new automations never inherit the exception."""
    if row["automation_action_id"]:
        return bool(c.execute("SELECT 1 FROM automation_mail_actions a JOIN automations m ON m.id=a.automation_id "
                              "JOIN email_legacy_automations l ON l.automation_id=m.id AND l.owner_id=m.owner_id "
                              "AND l.version=m.version WHERE a.id=?", (row["automation_action_id"],)).fetchone())
    setting = saved(c, "bot:" + row["bot"]) if row["bot"] else {}
    if not setting.get("legacy") or setting.get("mode") != "on_their_own":
        return False
    from .mail_settings import stored
    bot_setting = stored(c, row["bot"]) or {}
    if not bot_setting.get("outbound_send"):
        return False
    domains = access.company_domains(c)
    targets = set(bot_setting.get("forward_to") or [])
    original = _original(c, row)
    if original and original["thread_id"] == row["thread_id"]:
        targets.add(original["from_addr"].lower())
    targets.update((setting.get("legacy_allowances") or {}).get(row["mailbox"], []))
    return all(a in targets or (domains and access.inside(a, domains)) for a in json.loads(row["recipients"]))


def decision(c, auth, row, *, dispatch=False):
    policy = effective(c, row["mailbox"], row["bot"])
    reasons, blocked = [], []
    if policy["mode"] == "never":
        blocked.append("never")
    if not access.may(c, row["actor"], row["mailbox"])["send"]:
        blocked.append("send_access_revoked")
    if row["bot"]:
        from . import lifecycle
        if lifecycle.is_onboarding(c, row["bot"]):
            blocked.append("onboarding")
    legacy = legacy_authorized(c, row)
    # A changed company policy takes effect immediately, including for grandfathered authorizations.
    grandfathered = (legacy and not policy["company_restrictions_set"]
                     and not saved(c, "inbox:" + row["mailbox"]))
    recipients = json.loads(row["recipients"])
    original = _original(c, row)
    is_reply = bool(original and row["thread_id"] == original["thread_id"]
                    and original["from_addr"].lower() in recipients and original["from_addr"].lower() != row["mailbox"])
    if not grandfathered:
        if policy["mode"] == "ask_first":
            reasons.append("ask_first")
        if policy["what"] == "replies_only" and not is_reply:
            reasons.append("new_message")
        if policy["ask_new_address"] and any(not known(c, row["mailbox"], a) for a in recipients):
            reasons.append("new_address")
        if policy["ask_attachments"] and json.loads(row["attachments"]):
            reasons.append("attachments")
        if policy["ask_many_recipients"] and len(recipients) > 10:
            reasons.append("more_than_10_recipients")
        if policy["ask_legal_money"] and json.loads(row["classification"]).get("legal_or_money") is not False:
            reasons.append("legal_or_money_or_unclassified")
    if policy["to"] == "known_addresses" and any(not known(c, row["mailbox"], a) for a in recipients):
        blocked.append("known_addresses_only")
    if policy["to"] == "internal" and any(not access.inside(a, access.company_domains(c)) for a in recipients):
        blocked.append("internal_only")
    if row["automation_action_id"]:
        found = c.execute("SELECT a.*,m.owner_id,m.enabled,m.deleted_at,m.flow FROM automation_mail_actions a "
                          "JOIN automations m ON m.id=a.automation_id WHERE a.id=?", (row["automation_action_id"],)).fetchone()
        from .automation_actions import acts_as, send_on
        if (not found or found["deleted_at"] or acts_as(c, {"flow": json.loads(found["flow"])}) != row["actor"]
                or not send_on(c, row["mailbox"])):
            blocked.append("automation_revoked")
    if c.execute("SELECT 1 FROM email_suppressed_recipients WHERE mailbox=? AND address IN ("
                 + ",".join("?" for _ in recipients) + ") LIMIT 1", (row["mailbox"], *recipients)).fetchone():
        blocked.append("bounce_suppression")
    if original:
        headers = json.loads(original.get("send_facts") or "{}")
        auto = str(headers.get("auto-submitted") or "").lower()
        sender = original["from_addr"].lower()
        # Header facts are populated by sync; sender and subject checks also catch old synced bounces.
        if (auto and auto != "no") or sender.startswith(("mailer-daemon@", "postmaster@")) or \
                str(headers.get("precedence") or "").lower() in ("bulk", "list", "junk"):
            blocked.append("reply_loop")
        if c.execute("SELECT 1 FROM email_sends WHERE mailbox=? AND reply_to_id=? AND id<>? "
                     "AND state IN ('pending','dispatching','sent','unknown') LIMIT 1",
                     (row["mailbox"], row["reply_to_id"], row["id"])).fetchone():
            blocked.append("already_replied")
    approval_ok = approved(c, auth, row, policy)
    if reasons and not approval_ok:
        return {"outcome": "blocked" if blocked else "ask", "reasons": blocked or reasons,
                "policy": policy, "approval_id": row["approval_id"], "approved": False, "legacy": grandfathered}
    if dispatch:
        day = H.now()[:10]
        for field, clause, params in (("daily_cap", "actor=?", (row["actor"],)),
                                      ("mailbox_daily_cap", "mailbox=?", (row["mailbox"],)),
                                      ("company_daily_cap", "1=1", ())):
            count = c.execute("SELECT count(*) FROM email_sends WHERE " + clause + " AND id<>? "
                              "AND state IN ('sent','dispatching','unknown') AND substr(coalesce(claimed_at,created_at),1,10)=?",
                              (*params, row["id"], day)).fetchone()[0]
            if count >= policy[field]:
                blocked.append(field)
        last = c.execute("SELECT max(claimed_at) FROM email_sends WHERE mailbox=? AND id<>? "
                         "AND state IN ('sent','dispatching','unknown')", (row["mailbox"], row["id"])).fetchone()[0]
        if last and last > H.shift(H.now(), seconds=-policy["pacing_seconds"]):
            blocked.append("pacing")
    return {"outcome": "blocked" if blocked else "allow", "reasons": blocked, "policy": policy,
            "approval_id": row["approval_id"], "approved": approval_ok, "legacy": grandfathered}


def public(row):
    keys = ("id", "mailbox", "actor", "bot", "via", "automation_action_id", "content_sha256", "body_sha256", "subject", "approval_id", "state",
            "created_at", "send_after", "claimed_at", "finished_at", "provider_message_id", "detail")
    return {**{key: row[key] for key in keys}, **{key: json.loads(row[key]) for key in
                                               ("recipients", "attachments", "decision", "classification")}}


def settle_automation(c, row, state, detail="", message=""):
    if not row["automation_action_id"]:
        return
    from .automations import _mail_report
    action = c.execute("SELECT * FROM automation_mail_actions WHERE id=?", (row["automation_action_id"],)).fetchone()
    if action and action["state"] not in ("done", "failed", "canceled"):
        _mail_report(c, action, "done" if state == "sent" else "failed",
                     {"ok": state == "sent", "message": message, "detail": detail or state})


def request_approval(c, auth, row, policy):
    payload = {"to": json.loads(row["recipients"]), "cc": [], "mailbox": row["mailbox"], "subject": row["subject"],
               "body_sha256": row["body_sha256"], "content_sha256": row["content_sha256"],
               "attachments": json.loads(row["attachments"]), "email_send_id": row["id"]}
    approval = H.approval_request(c, row["actor"], "send", payload, to=approver(c, auth, row, policy))
    c.execute("UPDATE email_sends SET approval_id=? WHERE id=?", (approval["id"], row["id"]))


def enqueue(c, auth, actor, body, classification, via=""):
    mailbox = body.mailbox.strip().lower()
    bot = H.actor_id(actor) if actor.startswith("bot:") else ""
    if not access.may(c, actor, mailbox)["send"]:
        raise Problem("forbidden", "The sender cannot send from this inbox", 403)
    snap = snapshot(body.raw, mailbox)
    # Reply facts come from synced mail, never from the caller's description of the recipients.
    key = digest(dump([mailbox, snap["content_sha256"], snap["recipients"]]))
    old = c.execute("SELECT * FROM email_sends WHERE idempotency_key=?", (key,)).fetchone()
    if old:
        if old["actor"] != actor:
            raise Problem("duplicate", "This email is already queued or sent", 409)
        return public(old)
    if c.execute("SELECT count(*) FROM email_sends WHERE state IN ('pending','waiting_approval','dispatching')").fetchone()[0] >= 1000:
        raise Problem("queue_full", "The email queue is full", 429)
    if c.execute("SELECT coalesce(sum(length(raw)),0) FROM email_sends").fetchone()[0] + len(body.raw) > 128 * 1024 * 1024:
        raise Problem("queue_full", "The email queue content limit is reached", 429)
    now, ident = H.now(), H.new_id()
    row = {"id": ident, "mailbox": mailbox, "actor": actor, "bot": bot,
           "via": "automation:" + body.automation_action_id if body.automation_action_id else via,
           "automation_action_id": body.automation_action_id or None, "content_sha256": snap["content_sha256"],
           "idempotency_key": key, "recipients": dump(snap["recipients"]), "attachments": dump(snap["attachments"]),
           "subject": snap["subject"], "body_sha256": snap["body_sha256"], "raw": body.raw,
           "draft_id": body.draft_id, "thread_id": body.thread_id, "reply_to_id": body.reply_to_id, "approval_id": body.approval_id,
           "classification": dump(classification), "decision": "{}", "state": "pending", "created_at": now,
           "send_after": H.shift(now, seconds=effective(c, mailbox, bot)["undo_seconds"])}
    found = decision(c, auth, row)
    row["decision"] = dump(found)
    row["state"] = {"allow": "pending", "ask": "waiting_approval", "blocked": "blocked"}[found["outcome"]]
    c.execute("INSERT INTO email_sends(" + ",".join(row) + ") VALUES(" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    if row["state"] == "waiting_approval" and not row["approval_id"]:
        # Existing approval cards expose only the proof metadata, never the MIME payload.
        request_approval(c, auth, row, found["policy"])
    if classification.get("status") == "classified":
        H.event(c, actor, "judge.call", "email.sending", {"label": "email.sending", "questions": 1,
                                                          **classification})
    H.event(c, actor, "email.queued", ident, {"mailbox": mailbox, "state": row["state"]})
    return public(c.execute("SELECT * FROM email_sends WHERE id=?", (ident,)).fetchone())


def classify(app, store, snap):
    """Unknown classification asks a person. The classifier is triage, not a defense against malicious mail."""
    from .judge import ask, audit_detail, server_engine
    from clients.judge import JudgeError
    with store.read() as c:
        engine = server_engine(store, c, app.state.judge)
    if engine is None:
        return {"legal_or_money": None, "status": "unavailable"}
    try:
        result = ask(engine, {"subject": snap["subject"], "body": snap["body"][:16000]},
                     {"legal_or_money": {"type": "choice", "instructions": "Does this email concern legal or money matters?",
                                         "criteria": {"yes": "Legal, contracts, payments, finance or money",
                                                      "no": "None of these matters"}}},
                     "email.sending", "low")
        answer = (result.get("answers") or {}).get("legal_or_money") or {}
        value = answer.get("value")
        return {"legal_or_money": value == "yes" if value in ("yes", "no") else None,
                "status": "classified", **audit_detail(result)}
    except (JudgeError, ValueError, TypeError):
        return {"legal_or_money": None, "status": "unavailable"}


def install(app, store, auth, mutate):
    # The server snapshots registry allowances; callers cannot claim a grandfathered recipient of their own.
    path = store.settings.registry_dir / "mail-policy.yaml"
    if path.is_file():
        from connectors.mail import policy as local_policy
        pol = local_policy.load(path)
        with store.transaction() as c:
            for item in c.execute("SELECT * FROM email_sending_policies WHERE scope LIKE 'bot:%'").fetchall():
                settings = json.loads(item["settings"])
                if not settings.get("legacy"):
                    continue
                bot = H.bot(c, item["scope"].partition(":")[2]) or {}
                allowances = {}
                for allowance in pol["allowances"]:
                    if allowance["employee"] == bot.get("slug"):
                        addresses = local_policy.allowed_recipients(allowance, store.settings.registry_dir.parent)
                        allowances[allowance["mailbox"]] = sorted(addresses)
                settings["legacy_allowances"] = allowances
                settings["daily_cap"] = pol["defaults"]["max_sends_per_day"]
                c.execute("UPDATE email_sending_policies SET settings=? WHERE scope=?", (dump(settings), item["scope"]))

    def inbox(c, address, who, *, manage=False):
        address = address.strip().lower()
        row = access._row(c, address)
        if not row:
            raise Problem("not_found", "Inbox not found", 404)
        if manage:
            from .mail_settings import require_person
            require_person(who)
            access._manager(c, who, row, "change sending policy")
        elif not access.may(c, who, address)["read"]:
            raise Problem("forbidden", "This inbox is private", 403)
        return address

    def company(who, *, write=False):
        if write:
            from .mail_settings import require_person
            require_person(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only an admin manages company sending policy", 403)

    def bot_ref(c, who, bot, *, write=False):
        bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        if not H.bot(c, bot):
            raise Problem("not_found", "Bot not found", 404)
        if write:
            from .mail_settings import require_person
            require_person(who, bot)
            if not auth.bot_manager(c, who, bot):
                raise Problem("forbidden", "Only a bot manager changes its sending policy", 403)
        elif who.actor != "bot:" + bot:
            auth.require_read(c, who, bot)
        return bot

    def reader(c, who, mailbox):
        if store.settings.rehearsal or store.settings.demo:
            raise Problem("rehearsal", "This install does not dispatch email", 403)
        from . import google_rights
        runner = google_rights.runner_of(app.state.execution, c, who)
        google_rights.check(c, runner["id"], google_rights.publishes(store, app.state.execution, c, runner), mailbox)
        published = c.execute("SELECT runner_id FROM mail_mailboxes WHERE address=?", (mailbox,)).fetchone()
        if published and published["runner_id"] and published["runner_id"] != runner["id"]:
            raise Problem("forbidden", "Another computer reads this inbox", 403)
        return runner

    def can_cancel(c, who, row):
        if who.actor == row["actor"] or (row["bot"] and who.role in ("owner", "human")
                                         and auth.bot_manager(c, who, row["bot"])):
            return True
        try:
            access._manager(c, who, access._require_row(c, access._row(c, row["mailbox"])["id"]), "cancel sends")
            return True
        except (Problem, TypeError):
            return False

    @app.get("/api/v2/email/sending-policy")
    def read_company(request: Request):
        company(request.state.identity)
        with store.read() as c:
            return policy_view(c)

    @app.put("/api/v2/email/sending-policy")
    def write_company(request: Request, body: PolicyUpdate):
        company(request.state.identity, write=True)
        return mutate(request, body, lambda c: (update_policy(c, request.state.identity, "company", body), policy_view(c))[1])

    @app.get("/api/v2/email/inboxes/{mailbox}/sending-policy")
    def read_inbox(request: Request, mailbox: str):
        with store.read() as c:
            return policy_view(c, inbox(c, mailbox, request.state.identity))

    @app.put("/api/v2/email/inboxes/{mailbox}/sending-policy")
    def write_inbox(request: Request, mailbox: str, body: PolicyUpdate):
        def work(c):
            address = inbox(c, mailbox, request.state.identity, manage=True)
            update_policy(c, request.state.identity, "inbox:" + address, body)
            return policy_view(c, address)
        return mutate(request, body, work)

    @app.get("/api/v2/bots/{bot}/email-sending-policy")
    def read_bot(request: Request, bot: str, mailbox: str = ""):
        with store.read() as c:
            ref = bot_ref(c, request.state.identity, bot)
            if mailbox and not access.may(c, "bot:" + ref, mailbox)["read"]:
                raise Problem("forbidden", "The bot cannot use this inbox", 403)
            return policy_view(c, mailbox.strip().lower(), ref)

    @app.put("/api/v2/bots/{bot}/email-sending-policy")
    def write_bot(request: Request, bot: str, body: PolicyUpdate):
        def work(c):
            ref = bot_ref(c, request.state.identity, bot, write=True)
            update_policy(c, request.state.identity, "bot:" + ref, body)
            return policy_view(c, bot=ref)
        return mutate(request, body, work)

    @app.post("/api/v2/email/sends")
    def queue(request: Request, body: Enqueue):
        who = request.state.identity
        with store.read() as c:
            if body.automation_action_id:
                runner = reader(c, who, body.mailbox.strip().lower())
                action = c.execute("SELECT a.*,m.owner_id,m.flow FROM automation_mail_actions a JOIN automations m "
                                   "ON m.id=a.automation_id WHERE a.id=?", (body.automation_action_id,)).fetchone()
                if (not action or action["claimed_by"] != runner["id"] or action["state"] != "claimed"
                        or action["mailbox"] != body.mailbox.strip().lower() or action["action"] not in ("send", "forward")):
                    raise Problem("forbidden", "No send action leased to this computer", 403)
                from .automation_actions import acts_as
                actor = acts_as(c, {"flow": json.loads(action["flow"])})
            else:
                actor = who.actor
            if not access.may(c, actor, body.mailbox.strip().lower())["send"]:
                raise Problem("forbidden", "The sender cannot send from this inbox", 403)
            policy = effective(c, body.mailbox.strip().lower(), H.actor_id(actor) if actor.startswith("bot:") else "")
        snap = snapshot(body.raw, body.mailbox.strip().lower())
        classification = classify(app, store, snap) if policy["ask_legal_money"] else {"legal_or_money": False, "status": "off"}
        return mutate(request, body, lambda c: {"send": enqueue(c, auth, actor, body, classification, via=who.via)})

    @app.get("/api/v2/email/sends")
    def sends(request: Request, mailbox: str, pending: bool = False, cursor: str = "", limit: int = 50):
        with store.read() as c:
            address = inbox(c, mailbox, request.state.identity)
            sql, args = "SELECT * FROM email_sends WHERE mailbox=?", [address]
            if pending:
                sql += " AND state IN ('pending','waiting_approval')"
            if cursor:
                sql += " AND rowid<(SELECT rowid FROM email_sends WHERE id=?)"
                args.append(cursor)
            cap = min(100, max(1, limit))
            rows = c.execute(sql + " ORDER BY rowid DESC LIMIT ?", (*args, cap + 1)).fetchall()
            out, spent = [], 0
            for row in rows[:cap]:
                item = public(row)
                item["can_cancel"] = row["state"] in ("pending", "waiting_approval") and can_cancel(c, request.state.identity, row)
                size = len(dump(item).encode())
                if out and spent + size > LIST_BYTES:
                    break
                out.append(item)
                spent += size
            more = len(out) < len(rows)
            return {"sends": out, "next_cursor": out[-1]["id"] if more and out else None}

    @app.get("/api/v2/email/sends/{sid}")
    def send_detail(request: Request, sid: str):
        with store.read() as c:
            row = c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()
            if not row:
                raise Problem("not_found", "Send not found", 404)
            if not can_cancel(c, request.state.identity, row) and not can_ok(
                    c, auth, request.state.identity.actor, row["mailbox"], row["bot"], effective(c, row["mailbox"], row["bot"])):
                inbox(c, row["mailbox"], request.state.identity)
            snap = snapshot(row["raw"], row["mailbox"]) if row["raw"] else None
            return {"send": public(row), "body": (snap or {}).get("body", "")[:32768]}

    @app.get("/api/v2/email/inboxes/{mailbox}/suppressed-recipients")
    def suppressed(request: Request, mailbox: str, cursor: str = "", limit: int = 50):
        with store.read() as c:
            address = inbox(c, mailbox, request.state.identity, manage=True)
            rows = c.execute("SELECT address,reason,created_at FROM email_suppressed_recipients WHERE mailbox=? "
                             "AND address>? ORDER BY address LIMIT ?", (address, cursor, min(100, max(1, limit)) + 1)).fetchall()
            cap = min(100, max(1, limit))
            return {"recipients": [dict(row) for row in rows[:cap]],
                    "next_cursor": rows[cap - 1]["address"] if len(rows) > cap else None}

    @app.delete("/api/v2/email/inboxes/{mailbox}/suppressed-recipients/{address}")
    def unsuppress(request: Request, mailbox: str, address: str):
        def work(c):
            box = inbox(c, mailbox, request.state.identity, manage=True)
            c.execute("DELETE FROM email_suppressed_recipients WHERE mailbox=? AND address=?", (box, address.lower()))
            H.event(c, request.state.identity.actor, "email.unsuppressed", box, {"address": address.lower()})
            return {"ok": True}
        return mutate(request, Empty(), work)

    @app.post("/api/v2/email/sends/{sid}/approve")
    def bind_approval(request: Request, sid: str, body: SendApproval):
        def work(c):
            row = c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()
            if not row or not can_cancel(c, request.state.identity, row):
                raise Problem("forbidden", "Only the sender or a send manager attaches an approval", 403)
            if row["state"] != "waiting_approval":
                raise Problem("state", "This email is not waiting for approval", 409)
            c.execute("UPDATE email_sends SET approval_id=? WHERE id=?", (body.approval_id, sid))
            return {"send": public(c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone())}
        return mutate(request, body, work)

    @app.post("/api/v2/email/sends/{sid}/cancel")
    def cancel(request: Request, sid: str):
        def work(c):
            row = c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()
            if not row or not can_cancel(c, request.state.identity, row):
                raise Problem("forbidden", "Only the sender, its manager or an inbox admin cancels a send", 403)
            if row["state"] == "canceled":
                return {"send": public(row)}
            if row["state"] not in ("pending", "waiting_approval"):
                raise Problem("state", "This email can no longer be canceled; delivered email cannot be recalled", 409)
            c.execute("UPDATE email_sends SET state='canceled',raw='',finished_at=? WHERE id=?", (H.now(), sid))
            settle_automation(c, row, "canceled")
            H.event(c, request.state.identity.actor, "email.canceled", sid, {})
            return {"send": public(c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone())}
        return mutate(request, Empty(), work)

    @app.post("/api/v2/connectors/email/sends/claim")
    def claim(request: Request, body: Claim):
        def work(c):
            mailbox, now = body.mailbox.strip().lower(), H.now()
            runner = reader(c, request.state.identity, mailbox)
            # A provider may have accepted an unreported send. Preserve its receipt as unknown; never resend it.
            for stale in c.execute("SELECT * FROM email_sends WHERE state='dispatching' AND claimed_at<? LIMIT 100",
                                   (H.shift(now, minutes=-5),)).fetchall():
                c.execute("UPDATE email_sends SET state='unknown',detail='The connector did not confirm delivery',"
                          "finished_at=?,raw='' WHERE id=?", (now, stale["id"]))
                settle_automation(c, stale, "unknown")
            rows = c.execute("SELECT * FROM email_sends WHERE mailbox=? AND state IN ('pending','waiting_approval') "
                             "ORDER BY created_at,rowid LIMIT 100", (mailbox,)).fetchall()
            for row in rows:
                found = decision(c, auth, row)
                approval = H.approval(c, row["approval_id"]) if row["approval_id"] else None
                if approval and approval.get("decision") in ("declined", "withdrawn"):
                    found["outcome"], found["reasons"] = "blocked", ["approval_" + approval["decision"]]
                if found["outcome"] == "blocked":
                    c.execute("UPDATE email_sends SET state='blocked',decision=?,detail=?,finished_at=?,raw='' WHERE id=?",
                              (dump(found), ", ".join(found["reasons"]), now, row["id"]))
                    settle_automation(c, row, "blocked", ", ".join(found["reasons"]))
                    continue
                if found["outcome"] == "ask":
                    c.execute("UPDATE email_sends SET state='waiting_approval',decision=? WHERE id=?", (dump(found), row["id"]))
                    if not row["approval_id"]:
                        request_approval(c, auth, row, found["policy"])
                    continue
                if row["state"] == "waiting_approval":
                    c.execute("UPDATE email_sends SET state='pending',send_after=?,decision=? WHERE id=?",
                              (H.shift(now, seconds=found["policy"]["undo_seconds"]), dump(found), row["id"]))
                    continue
                if max(row["send_after"], H.shift(row["created_at"], seconds=found["policy"]["undo_seconds"])) > now:
                    continue
                found = decision(c, auth, row, dispatch=True)
                if found["outcome"] != "allow":
                    c.execute("UPDATE email_sends SET decision=?,detail=? WHERE id=?",
                              (dump(found), ", ".join(found["reasons"]), row["id"]))
                    continue
                c.execute("UPDATE email_sends SET state='dispatching',decision=?,runner_id=?,claimed_at=? WHERE id=?",
                          (dump(found), runner["id"], now, row["id"]))
                return {"send": {"id": row["id"], "mailbox": mailbox, "raw": row["raw"], "thread_id": row["thread_id"]}}
            return {"send": None}
        return mutate(request, body, work)

    @app.post("/api/v2/connectors/email/sends/{sid}/dispatch")
    def dispatch(request: Request, sid: str, body: Claim):
        def work(c):
            runner = reader(c, request.state.identity, body.mailbox.strip().lower())
            row = c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()
            if not row or row["runner_id"] != runner["id"] or row["mailbox"] != body.mailbox.strip().lower():
                raise Problem("forbidden", "This send belongs to another connector", 403)
            if row["state"] != "dispatching" or row["provider_attempted_at"]:
                return {"send": None}
            found = decision(c, auth, row, dispatch=True)
            if found["outcome"] != "allow":
                c.execute("UPDATE email_sends SET state='blocked',decision=?,detail=?,finished_at=?,raw='' WHERE id=?",
                          (dump(found), ", ".join(found["reasons"]), H.now(), sid))
                settle_automation(c, row, "blocked", ", ".join(found["reasons"]))
                return {"send": None}
            c.execute("UPDATE email_sends SET provider_attempted_at=? WHERE id=?", (H.now(), sid))
            return {"send": {"id": sid, "raw": row["raw"], "thread_id": row["thread_id"], "actor": row["actor"],
                             "recipients": json.loads(row["recipients"]), "subject": row["subject"], "draft_id": row["draft_id"]}}
        return mutate(request, body, work)

    @app.post("/api/v2/connectors/email/sends/{sid}/result")
    def receipt(request: Request, sid: str, body: Receipt):
        if body.status not in ("sent", "failed", "unknown"):
            raise Problem("receipt", "Receipt status must be sent, failed or unknown", 422)
        if body.status == "sent" and not body.message:
            raise Problem("receipt", "Confirmed delivery needs the provider's message id", 422)
        def work(c):
            from . import google_rights
            runner = google_rights.runner_of(app.state.execution, c, request.state.identity)
            row = c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()
            if not row or row["runner_id"] != runner["id"]:
                raise Problem("forbidden", "Only the dispatching computer reports this receipt", 403)
            if row["state"] not in ("dispatching", "unknown"):
                return {"send": public(row)}
            c.execute("UPDATE email_sends SET state=?,provider_message_id=?,detail=?,finished_at=?,raw='' WHERE id=?",
                      (body.status, body.message, body.detail, H.now(), sid))
            H.event(c, row["actor"], "email." + body.status, sid, {"mailbox": row["mailbox"], "provider_message_id": body.message})
            settle_automation(c, row, body.status, body.detail, body.message)
            return {"send": public(c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone())}
        return mutate(request, body, work)
