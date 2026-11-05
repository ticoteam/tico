"""The one email send gate and durable undo queue: every send, by a person, a bot, an automation or the calendar.

The server freezes MIME content before approval and dispatch and decides every send here, a computer's mail tool only
queues it (POST /api/v2/email/sends). One sending policy applies, its DEFAULTS when nothing is saved: company, inbox and
bot layers (`effective`), blocked and owner-handled recipients, outside Cc and outside-recipient counts, the lint and
the second reviewer on a bot's own writing, the org rule check (`rule_gate`), bounces and reply loops. The only
approvals are a Tico send approval card bound to the exact frozen content and recipients, or a Tico message after a
verified preview from someone who may approve for that bot (`approved`).

A Workspace mailbox (the server can mint its Gmail token) is sent by the server itself once the undo window and
pacing allow (`drain`). A mailbox only a computer holds a grant for (a person's own Google) is leased to that computer
(`claim`, `/dispatch`, `/result`). A lost provider response is unknown, never an invitation to send a second copy.
Classification and review are triage, not a security boundary: permissions, approvals and limits still apply.
"""

import json
import logging
import re
from email.utils import getaddresses

from fastapi import Request, Response
from urllib.parse import quote
from pydantic import Field

from . import mailbox_access as access
from .models import Contract, Empty
from .email_content import MAX_RAW, attachment, digest, snapshot
from .store import H, Problem

DEFAULTS = {"mode": "ask_first", "what": "replies_only", "to": "anyone", "daily_cap": 50,
            "mailbox_daily_cap": 200, "company_daily_cap": 1000, "max_outside_recipients": 1,
            "pacing_seconds": 10, "undo_seconds": 120,
            "ask_new_address": True, "ask_attachments": True, "ask_many_recipients": True, "ask_outside_cc": True,
            "ask_legal_money": True, "ask_lint": True, "second_review": True,
            "blocked_recipients": [], "owner_handles": [], "who_can_ok": "bot_managers", "approvers": []}
MODES = {"on_their_own": 0, "ask_first": 1, "never": 2}
# A child layer may turn an ask on, never off; lower a cap, never raise it; add to a list, never remove from it.
ASKS = ("ask_new_address", "ask_attachments", "ask_many_recipients", "ask_outside_cc", "ask_legal_money", "ask_lint",
        "second_review")
CAPS = ("daily_cap", "mailbox_daily_cap", "company_daily_cap", "max_outside_recipients")
LISTS = ("blocked_recipients", "owner_handles")
ACTIVE = ("waiting_approval", "pending", "dispatching")
LIST_BYTES = 128 * 1024
LOG = logging.getLogger("tico.email_sending")
LIST_MAX = 200
DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)+$")
SERVER = "server"                    # runner_id of a send the server dispatches itself (`drain`)
# Only a content-bound card lifts these: a person's message grants what it says, and the reviewer judged it with it.
CARD_ONLY = frozenset({"second_review"})
REVIEW = ("Would a careful second reviewer stop this email, written by an automated employee, before it is sent? Stop it "
          "if it makes a commitment (money, a discount, a date, a deliverable, a legal position, a guarantee), confirms "
          "a meeting time, states facts about the recipient or the thread that the thread shown does not support, or "
          "does not read as the company. An approver's instruction, when shown, authorizes only what it says.")
THREAD_CHARS = 6000
INSTRUCTION_CHARS = 2000


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


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
    max_outside_recipients: int | None = Field(default=None, ge=0, le=100)
    pacing_seconds: int | None = Field(default=None, ge=0, le=3600)
    undo_seconds: int | None = Field(default=None, ge=0, le=600)
    ask_new_address: bool | None = None
    ask_attachments: bool | None = None
    ask_many_recipients: bool | None = None
    ask_outside_cc: bool | None = None
    ask_legal_money: bool | None = None
    ask_lint: bool | None = None
    second_review: bool | None = None
    blocked_recipients: list[str] | None = Field(default=None, max_length=LIST_MAX)
    owner_handles: list[str] | None = Field(default=None, max_length=LIST_MAX)
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
    calendar_action_id: str = Field(default="", max_length=100)
    operation_id: str = Field(default="", max_length=200)


class SendApproval(Contract):
    approval_id: str = Field(min_length=1, max_length=200)


class Claim(Contract):
    mailbox: str = Field(min_length=3, max_length=320)
    lease_id: str = Field(default="", max_length=100)


class CalendarPreflight(Contract):
    mailbox: str = Field(min_length=3, max_length=320)
    calendar_action_id: str = Field(min_length=1, max_length=100)


class Receipt(Contract):
    lease_id: str = Field(default="", max_length=100)
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
    recipient_rules, approval_rules = [], []
    for label, settings in layers:
        if settings.get("mode") == "never":
            never = never or label
        for key in DEFAULTS:
            if key not in settings:
                continue
            value, source = settings[key], label
            if key in LISTS:
                value = sorted(set(out[key]) | set(value)) if label != "company" else sorted(set(value))
                source = label if set(value) - set(out[key]) or label == "company" else sources[key]
            elif label != "company":
                if key in ASKS and out[key]:
                    value, source = True, sources[key]
                elif key in CAPS and out[key] <= value:
                    value, source = out[key], sources[key]
                elif key in ("pacing_seconds", "undo_seconds") and out[key] >= value:
                    value, source = out[key], sources[key]
                elif key == "what" and out[key] == "replies_only":
                    value, source = out[key], sources[key]
                elif key in ("to", "who_can_ok", "approvers"):
                    continue  # These permissions intersect below; a child never replaces its parent's restriction.
            out[key], sources[key] = value, source
        recipient = settings.get("to", DEFAULTS["to"] if label == "company" else "anyone")
        if recipient != "anyone":
            recipient_rules.append({"value": recipient, "source": label})
        if "who_can_ok" in settings or label == "company":
            approval_rules.append({"value": settings.get("who_can_ok", DEFAULTS["who_can_ok"]),
                                   "approvers": settings.get("approvers", []), "source": label})
    if never:
        out["mode"], sources["mode"] = "never", never
    if recipient_rules:
        out["to"], sources["to"] = recipient_rules[0]["value"], recipient_rules[0]["source"]
    # The visible rule names the company's ceiling; all additional rules must also be satisfied.
    out["who_can_ok"], out["approvers"] = approval_rules[0]["value"], approval_rules[0]["approvers"]
    out["recipient_rules"], out["approval_rules"] = recipient_rules, approval_rules
    out["sources"] = sources
    out["company_restrictions_set"] = bool(layers[0][1])
    return out


def policy_view(c, mailbox="", bot=""):
    def visible(settings):
        return {key: value for key, value in settings.items() if key in DEFAULTS or key in ("updated_at", "updated_by")}
    return {"company": visible(saved(c, "company")), "inbox": visible(saved(c, "inbox:" + mailbox)) if mailbox else {},
            "bot": visible(saved(c, "bot:" + bot)) if bot else {}, "effective": effective(c, mailbox, bot)}


def entries(values):
    """A recipient list's entries: addresses, or domains (which cover their subdomains), lowercased, once each."""
    from .mail_settings import ADDRESS
    out = []
    for item in values:
        value = str(item or "").strip().lower().removeprefix("@")
        if not value:
            continue
        if not (ADDRESS.fullmatch(value) if "@" in value else DOMAIN.fullmatch(value)):
            raise Problem("policy", "Not an email address or domain: " + value[:80], 422)
        if value not in out:
            out.append(value)
    return out


def listed(values, address):
    domain = address.rpartition("@")[2]
    return any(address == value if "@" in value else domain == value or domain.endswith("." + value) for value in values)


def update_policy(c, who, scope, body):
    values = body.model_dump(exclude_none=True)
    inherit = values.pop("inherit")
    for key, allowed in (("mode", MODES), ("what", ("replies_only", "replies_and_new")),
                         ("to", ("anyone", "known_addresses", "internal")),
                         ("who_can_ok", ("bot_managers", "inbox_admins", "named_people"))):
        if key in values and values[key] not in allowed:
            raise Problem("policy", "Invalid " + key, 422)
    for key in LISTS:
        if key in values:
            values[key] = entries(values[key])
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


def sends_on_own(c, bot):
    """Whether the bot's own layer lets it send without asking first: what older readers call `outbound_send`."""
    return effective(c, bot=bot)["mode"] == "on_their_own"


def can_ok(c, auth, actor, mailbox, bot, policy):
    try:
        who = auth.identity_for_actor(c, actor)
    except Problem:
        return False
    def permits(rule):
        if rule["value"] == "named_people":
            return H.actor_id(actor) in rule["approvers"]
        if rule["value"] == "bot_managers" and bot:
            return auth.bot_manager(c, who, bot)
        try:
            row = access._row(c, mailbox)
            if row:
                access._manager(c, who, row, "approve sends")
                return True
            return who.email.lower() == mailbox and access.may(c, who, mailbox)["send"]
        except Problem:
            return False
    return all(permits(rule) for rule in policy["approval_rules"])


def approver(c, auth, row, policy):
    """Route the approval card to a person who may actually decide it under the effective rule."""
    facts = access._Facts(c)
    candidates = list(facts.people)
    return next(("human:" + pid for pid in candidates if can_ok(c, auth, "human:" + pid, row["mailbox"], row["bot"], policy)), None)


def approved(c, auth, row, policy):
    """'card' for a decided send approval bound to this exact frozen send, 'message' for a person's instruction after a
    verified preview, else ''. Nothing else approves a send: no GitHub Issue, no standing allowance."""
    aid = row["approval_id"]
    if not aid:
        return ""
    ap = H.approval(c, aid)
    if ap:
        payload = ap.get("payload") or {}
        if payload.get("email_send_id") != row["id"] or payload.get("content_sha256") != row["content_sha256"]:
            return ""
        if (ap.get("kind") != "send" or ap.get("decision") != "approved" or ap.get("consumed_at")
                or ap.get("requested_by_id") != row["actor"]
                or not can_ok(c, auth, ap.get("decided_by_id"), row["mailbox"], row["bot"], policy)):
            return ""
        if not c.execute("SELECT 1 FROM email_send_previews WHERE send_id=? AND actor=? AND content_sha256=?",
                         (row["id"], ap["decided_by_id"], row["content_sha256"])).fetchone():
            return ""
        def addresses(value):
            return {a.lower() for _, a in getaddresses(value if isinstance(value, list) else [str(value or "")]) if a}
        recipients = addresses(payload.get("to")) | addresses(payload.get("cc")) | addresses(payload.get("bcc"))
        if payload.get("mailbox", "").lower() != row["mailbox"] or recipients != set(json.loads(row["recipients"])):
            return ""
        if payload.get("subject") != row["subject"] or payload.get("body_sha256") != row["body_sha256"]:
            return ""
        approved_files = [{"name": f.get("name"), "sha256": f.get("sha256")} for f in payload.get("attachments") or [] if isinstance(f, dict)]
        actual_files = [{"name": f["name"], "sha256": f["sha256"]} for f in json.loads(row["attachments"])]
        if sorted(approved_files, key=dump) != sorted(actual_files, key=dump):
            return ""
        return "card"
    # A server-recorded preview precedes the person's instruction and binds it to this exact immutable send.
    message = H.message(c, aid)
    if not message or message.get("recipient_id") != "bot:" + row["bot"] or not can_ok(
            c, auth, message.get("sender_id"), row["mailbox"], row["bot"], policy):
        return ""
    preview = c.execute("SELECT * FROM email_send_previews WHERE send_id=? AND actor=? AND content_sha256=?",
                        (row["id"], message["sender_id"], row["content_sha256"])).fetchone()
    if not preview or message["created_at"] <= preview["shown_at"]:
        return ""
    text = (message.get("body") or "").strip()
    # Deliberately accept only unambiguous affirmative forms; arbitrary prose needs the approval card.
    if re.search(r"\b(?:not|no|never|unless|except|hold|stop|cancel|wait|decline|reject)\b|n['’]t", text, re.I):
        return ""
    card = c.execute("SELECT message_id FROM approvals WHERE json_extract(payload,'$.email_send_id')=? "
                     "ORDER BY rowid DESC LIMIT 1", (row["id"],)).fetchone()
    named = re.search(r"(?<![\w-])" + re.escape(row["id"]) + r"(?![\w-])", text)
    reply = card and message.get("in_reply_to") == card["message_id"]
    refs = message.get("refs") or {}
    if not (named or reply or refs.get("email_send_id") == row["id"]):
        return ""
    words = re.sub(re.escape(row["id"]), "", text).strip(" #.,!?:;").lower()
    if not re.fullmatch(r"(?:yes[,! ]+)?(?:please )?(?:send(?: it| this(?: email)?)?|approve(?: this(?: email)?)?|go ahead(?: and send it)?)(?: please)?", words):
        return ""
    if c.execute("SELECT 1 FROM email_sends WHERE approval_id=? AND id<>? LIMIT 1", (aid, row["id"])).fetchone():
        return ""
    return "message"


def _original(c, row):
    if not row["reply_to_id"]:
        return None
    found = c.execute("SELECT * FROM mail_messages WHERE mailbox=? AND msg_id=? AND deleted_at IS NULL",
                      (row["mailbox"], row["reply_to_id"])).fetchone()
    return dict(found) if found else None


def resolve_reply(c, mailbox, snap, supplied_reply="", supplied_thread=""):
    ids = re.findall(r"<[^<>\s]+>", snap["in_reply_to"] or snap["references"])
    original = None
    for reference in reversed(ids):
        found = c.execute("SELECT msg_id,thread_id FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL "
                          "AND json_extract(send_facts,'$.message-id')=? LIMIT 1", (mailbox, reference)).fetchone()
        if found:
            original = found
            break
    if (supplied_reply and (not original or supplied_reply != original["msg_id"])) or (
            supplied_thread and (not original or supplied_thread != original["thread_id"])):
        raise Problem("reply", "Reply identifiers must match the frozen MIME headers and synced message", 422)
    if (snap["in_reply_to"] or snap["references"]) and not original:
        raise Problem("reply", "Sync the original message's headers before sending this reply", 422)
    return (original["msg_id"], original["thread_id"]) if original else ("", "")


def require_preview(c, actor, row):
    if not row or row["state"] not in ACTIVE:
        raise Problem("state", "This email is no longer waiting to send", 409)
    preview = c.execute("SELECT 1 FROM email_send_previews WHERE send_id=? AND actor=? AND content_sha256=?",
                        (row["id"], actor, row["content_sha256"])).fetchone()
    if not preview:
        raise Problem("preview", "Read the frozen email and attachments before approving", 409)


def known(c, mailbox, address):
    return bool(c.execute("SELECT 1 FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL "
                          "AND (lower(from_addr)=? OR (lower(from_addr)=? "
                          "AND EXISTS (SELECT 1 FROM json_each(labels) WHERE value='SENT') "
                          "AND (EXISTS (SELECT 1 FROM json_each(to_addresses) WHERE lower(value)=?) "
                          "OR EXISTS (SELECT 1 FROM json_each(cc) WHERE lower(value)=?)))) LIMIT 1",
                          (mailbox, address, mailbox, address, address)).fetchone() or c.execute(
        "SELECT 1 FROM email_sends WHERE mailbox=? AND state='sent' "
        "AND EXISTS (SELECT 1 FROM json_each(recipients) WHERE value=?) LIMIT 1", (mailbox, address)).fetchone())


def own_writing(row):
    """A bot's own email, not one Tico composed for an automation or the calendar: the lint and the reviewer read it."""
    return bool(row["bot"]) and not row["automation_action_id"] and not row["calendar_action_id"]


def why(found):
    """A decision's reasons as its detail, naming the setting behind a refusal: `never` says which layer set Never.
    The reasons themselves stay codes."""
    layer = (found.get("policy") or {}).get("sources", {}).get("mode", "company")
    return ", ".join(reason + " (" + layer + " setting)" if reason == "never" else reason for reason in found["reasons"])


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
        if (H.bot(c, row["bot"]) or {}).get("state") != "active":
            blocked.append("bot_inactive")
        if row["via"].startswith("agent:"):
            credential = c.execute("SELECT revoked_at FROM agents WHERE bot=?", (row["bot"],)).fetchone()
            if not credential or credential["revoked_at"]:
                blocked.append("agent_revoked")
    recipients = json.loads(row["recipients"])
    checks = json.loads(row["classification"])
    domains = access.company_domains(c)
    outside = [a for a in recipients if not access.inside(a, domains)]
    if any(listed(policy["blocked_recipients"], a) for a in recipients):
        blocked.append("blocked_recipient")
    if row["bot"] and any(listed(policy["owner_handles"], a) for a in recipients):
        blocked.append("owner_handles_personally")
    original = _original(c, row)
    is_reply = bool(original and row["thread_id"] == original["thread_id"]
                    and original["from_addr"].lower() in recipients and original["from_addr"].lower() != row["mailbox"])
    legality = checks.get("legal_or_money")
    if not isinstance(legality, bool):
        reasons.append("legal_or_money_or_unclassified")
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
    if policy["ask_many_recipients"] and len(outside) > policy["max_outside_recipients"]:
        reasons.append("outside_recipients")
    if policy["ask_outside_cc"] and set(checks.get("copied") or ()) & set(outside):
        reasons.append("outside_cc")
    if policy["ask_legal_money"] and legality is True:
        reasons.append("legal_or_money_or_unclassified")
    if own_writing(row) and policy["ask_lint"] and checks.get("lint"):
        reasons.append("lint")
    if own_writing(row) and policy["second_review"] and checks.get("review") is not True:
        reasons.append("second_review")
    if any(rule["value"] == "known_addresses" for rule in policy["recipient_rules"]) and any(not known(c, row["mailbox"], a) for a in recipients):
        blocked.append("known_addresses_only")
    if any(rule["value"] == "internal" for rule in policy["recipient_rules"]) and outside:
        blocked.append("internal_only")
    if row["automation_action_id"]:
        found = c.execute("SELECT a.*,m.owner_id,m.enabled,m.deleted_at,m.flow FROM automation_mail_actions a "
                          "JOIN automations m ON m.id=a.automation_id WHERE a.id=?", (row["automation_action_id"],)).fetchone()
        from .automation_actions import acts_as, send_on
        if (not found or found["deleted_at"] or not found["enabled"]
                or found["state"] not in ("claimed", "waiting_send")
                or acts_as(c, {"flow": json.loads(found["flow"])}) != row["actor"]
                or not send_on(c, row["mailbox"])):
            blocked.append("automation_revoked")
    if row["calendar_action_id"]:
        action = c.execute("SELECT * FROM calendar_actions WHERE id=?", (row["calendar_action_id"],)).fetchone()
        if not action or action["requested_by"] != row["actor"] or action["status"] not in ("running", "succeeded"):
            blocked.append("calendar_revoked")
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
    kind = approved(c, auth, row, policy)
    lifted = kind == "card" or (kind == "message" and not CARD_ONLY & set(reasons))
    if reasons and not lifted:
        return {"outcome": "blocked" if blocked else "ask", "reasons": blocked + reasons,
                "policy": policy, "approval_id": row["approval_id"], "approved": False}
    if dispatch:
        day = H.now()[:10]
        for field, clause, params in (("daily_cap", "actor=?", (row["actor"],)),
                                      ("mailbox_daily_cap", "mailbox=?", (row["mailbox"],)),
                                      ("company_daily_cap", "1=1", ())):
            count = c.execute("SELECT count(*) FROM email_sends WHERE " + clause + " AND id<>? "
                              "AND state IN ('sent','dispatching','unknown') AND substr(coalesce(provider_attempted_at,claimed_at,created_at),1,10)=?",
                              (*params, row["id"], day)).fetchone()[0]
            if count >= policy[field]:
                blocked.append(field)
        last = c.execute("SELECT max(coalesce(provider_attempted_at,claimed_at)) FROM email_sends WHERE mailbox=? AND id<>? "
                         "AND state IN ('sent','dispatching','unknown')", (row["mailbox"], row["id"])).fetchone()[0]
        if last and last > H.shift(H.now(), seconds=-policy["pacing_seconds"]):
            blocked.append("pacing")
    return {"outcome": "blocked" if blocked else "allow", "reasons": blocked, "policy": policy,
            "approval_id": row["approval_id"], "approved": bool(kind)}


def public(row):
    keys = ("id", "mailbox", "actor", "bot", "via", "automation_action_id", "calendar_action_id", "content_sha256", "body_sha256", "subject", "approval_id", "state",
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


def request_approval(c, auth, row, policy, metadata=None):
    groups = snapshot(row["raw"], row["mailbox"])["recipient_headers"]
    payload = {**(metadata or {}), **groups, "mailbox": row["mailbox"], "subject": row["subject"],
               "body_sha256": row["body_sha256"], "content_sha256": row["content_sha256"],
               "attachments": json.loads(row["attachments"]), "email_send_id": row["id"],
               "preview_url": "/api/v2/email/sends/" + row["id"]}
    approval = H.approval_request(c, row["actor"], "send", payload, to=approver(c, auth, row, policy))
    c.execute("UPDATE email_sends SET approval_id=? WHERE id=?", (approval["id"], row["id"]))


def enqueue(c, auth, actor, body, classification, via="", operation_key="", approval_metadata=None, rules=None):
    """Freeze, decide and queue one send. `rules` is the org rule check's in-write half (`rule_gate`): a held email is
    queued blocked with the hold's text, and a warned one carries the warning line in its frozen copy."""
    mailbox = body.mailbox.strip().lower()
    bot = H.actor_id(actor) if actor.startswith("bot:") else ""
    if not access.may(c, actor, mailbox)["send"]:
        raise Problem("forbidden", "The sender cannot send from this inbox", 403)
    raw = body.raw
    snap = snapshot(raw, mailbox)
    reply_id, thread_id = resolve_reply(c, mailbox, snap, body.reply_to_id, body.thread_id)
    operation_key = operation_key or body.operation_id
    if operation_key:
        prior = c.execute("SELECT s.* FROM email_send_operations o JOIN email_sends s ON s.id=o.send_id "
                          "WHERE o.actor=? AND o.operation_key=?", (actor, operation_key)).fetchone()
        if prior:
            if snap["content_sha256"] not in (prior["content_sha256"], json.loads(prior["classification"]).get(
                    "source_sha256")) or prior["mailbox"] != mailbox:
                raise Problem("operation", "This operation already froze different email content", 409)
            return public(prior)
    key = digest(dump([mailbox, snap["content_sha256"], snap["recipients"]]))
    old = c.execute("SELECT * FROM email_sends WHERE idempotency_key=?", (key,)).fetchone()
    if old and old["state"] in (*ACTIVE, "sent", "unknown"):
        if old["actor"] != actor:
            raise Problem("duplicate", "This email is already queued or sent", 409)
        if operation_key:
            c.execute("INSERT INTO email_send_operations VALUES(?,?,?)", (actor, operation_key, old["id"]))
        return public(old)
    if old:
        c.execute("UPDATE email_sends SET idempotency_key=? WHERE id=?", ("retired:" + old["id"], old["id"]))
    if c.execute("SELECT count(*) FROM email_sends WHERE state IN ('pending','waiting_approval','dispatching')").fetchone()[0] >= 1000:
        raise Problem("queue_full", "The email queue is full", 429)
    if c.execute("SELECT coalesce(sum(length(raw)),0) FROM email_sends").fetchone()[0] + len(raw) > 128 * 1024 * 1024:
        raise Problem("queue_full", "The email queue content limit is reached", 429)
    groups = snap["recipient_headers"]
    checks = {**classification, "copied": sorted(set(groups["cc"]) | set(groups["bcc"]))}
    gate = rules(c) if rules else None
    if gate:
        checks["rules"] = gate["outcome"]
        if gate.get("warning_line"):
            # A Warn rule: the recipient's copy carries the line, and that copy is what is frozen and approved.
            from connectors.mail.compose import with_warning
            raw = with_warning(raw, gate["warning_line"])
            checks["source_sha256"] = snap["content_sha256"]
            snap = snapshot(raw, mailbox)
    now, ident = H.now(), H.new_id()
    row = {"id": ident, "mailbox": mailbox, "actor": actor, "bot": bot,
           "via": "automation:" + body.automation_action_id if body.automation_action_id else "calendar:" + body.calendar_action_id if body.calendar_action_id else via,
           "automation_action_id": body.automation_action_id or None, "calendar_action_id": body.calendar_action_id or None, "content_sha256": snap["content_sha256"],
           "idempotency_key": key, "operation_key": operation_key, "recipients": dump(snap["recipients"]), "attachments": dump(snap["attachments"]),
           "subject": snap["subject"], "body_sha256": snap["body_sha256"], "raw": raw,
           "draft_id": body.draft_id, "thread_id": thread_id, "reply_to_id": reply_id, "approval_id": body.approval_id,
           "classification": dump(checks), "decision": "{}", "state": "pending", "created_at": now,
           "send_after": H.shift(now, seconds=effective(c, mailbox, bot)["undo_seconds"])}
    found = decision(c, auth, row)
    if gate and gate["outcome"] == "held":
        found = {**found, "outcome": "blocked", "reasons": ["org_rule", *found["reasons"]], "held": gate.get("held")}
    row["decision"] = dump(found)
    row["state"] = {"allow": "pending", "ask": "waiting_approval", "blocked": "blocked"}[found["outcome"]]
    if row["state"] == "blocked":
        text = (gate or {}).get("held", {}).get("text") if found.get("held") else ""
        row.update(raw="", finished_at=now, detail=text or why(found))
    c.execute("INSERT INTO email_sends(" + ",".join(row) + ") VALUES(" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    if operation_key:
        c.execute("INSERT INTO email_send_operations VALUES(?,?,?)", (actor, operation_key, ident))
    if row["state"] == "waiting_approval" and not found["approved"]:
        # Only a content-bound card or an instruction after a verified preview can release this snapshot.
        request_approval(c, auth, row, found["policy"], approval_metadata)
    if classification.get("status") == "classified":
        H.event(c, actor, "judge.call", "email.sending", {"label": "email.sending", "questions": classification.get(
            "questions", 1), **{k: v for k, v in classification.items() if k not in ("lint", "questions")}})
    H.event(c, actor, "email.queued", ident, {"mailbox": mailbox, "state": row["state"]})
    return public(c.execute("SELECT * FROM email_sends WHERE id=?", (ident,)).fetchone())


def lint_errors(c, actor, mailbox, snap, reply_id):
    """The deterministic lint's error ids on a bot's own email (connectors/mail/lint.py), read on the frozen copy."""
    from connectors.mail import lint
    bot = H.bot(c, H.actor_id(actor)) or {}
    groups = snap["recipient_headers"]
    domains = tuple(access.company_domains(c)) or (mailbox.rpartition("@")[2],)
    found = lint.run(snap["body"], snap["subject"], groups["to"], [*groups["cc"], *groups["bcc"]],
                     slug=bot.get("slug", ""), reply_to=reply_id, is_reply=bool(reply_id), internal_domains=domains)
    return sorted({item["id"] for item in found["errors"]})


def review_context(c, auth, actor, body, mailbox, snap):
    """What the second reviewer reads beside the email: the synced thread it answers and the approver's instruction,
    when the send names a person's message that may approve it. Both bounded."""
    _reply, thread_id = resolve_reply(c, mailbox, snap, body.reply_to_id, body.thread_id)
    rows = c.execute("SELECT from_addr,subject,body,snippet FROM mail_messages WHERE mailbox=? AND thread_id=? "
                     "AND deleted_at IS NULL AND ?<>'' ORDER BY epoch DESC LIMIT 5", (mailbox, thread_id, thread_id)).fetchall()
    thread = "\n\n----\n\n".join("From: " + r["from_addr"] + "\nSubject: " + r["subject"] + "\n\n" + (r["body"] or r["snippet"])
                                 for r in reversed(rows))[:THREAD_CHARS]
    instruction = ""
    message = H.message(c, body.approval_id) if body.approval_id else None
    bot = H.actor_id(actor)
    if message and message.get("recipient_id") == actor and can_ok(
            c, auth, message.get("sender_id"), mailbox, bot, effective(c, mailbox, bot)):
        instruction = str(message.get("body") or "")[:INSTRUCTION_CHARS]
    return {"thread": thread, "approver_instruction": instruction}


def classify(app, store, snap, review=None):
    """One decision-model call: whether the email concerns legal or money matters and, for a bot's own email
    (`review`, its thread and approver's instruction), the second reviewer's verdict. Unknown asks a person: this is
    triage, not a defense against malicious mail."""
    from .judge import ask, audit_detail, server_engine
    from clients.judge import JudgeError
    with store.read() as c:
        engine = server_engine(store, c, app.state.judge)
    visible = snap["visible_bodies"]
    if not snap["classification_complete"] or not visible or sum(len(part["text"]) for part in visible) > 16000:
        return {"legal_or_money": None, "review": None, "status": "incomplete_content"}
    if engine is None:
        return {"legal_or_money": None, "review": None, "status": "unavailable"}
    questions = {"legal_or_money": {"type": "choice", "instructions": "Does this email concern legal or money matters?",
                                    "criteria": {"yes": "Legal, contracts, payments, finance or money",
                                                 "no": "None of these matters"}}}
    state = {"subject": snap["subject"], "body": "\n\n".join(part["type"] + ":\n" + part["text"] for part in visible)}
    if review is not None:
        questions["review"] = {"type": "choice", "instructions": REVIEW,
                               "criteria": {"send": "Nothing a careful reviewer would stop",
                                            "stop": "A commitment, an unsupported claim or a confirmed time"}}
        state.update(thread=review["thread"] or "(no incoming thread: a first contact)",
                     **({"approver_instruction": review["approver_instruction"]} if review["approver_instruction"] else {}))
    try:
        result = ask(engine, state, questions, "email.sending", "low")
        answers = result.get("answers") or {}
        value = (answers.get("legal_or_money") or {}).get("value")
        verdict = (answers.get("review") or {}).get("value")
        return {"legal_or_money": value == "yes" if value in ("yes", "no") else None,
                "review": verdict == "send" if verdict in ("send", "stop") else None,
                "questions": len(questions), "status": "classified", **audit_detail(result)}
    except (JudgeError, ValueError, TypeError):
        return {"legal_or_money": None, "review": None, "status": "unavailable"}


def _lease(c, auth, mailbox, runner_id, *, permit=False, lease=True):
    """One pass over an inbox's queue inside a write: abandoned leases recovered, approvals reconciled, and the first
    due send leased to `runner_id`. `permit` (the server's own dispatch) also issues the dispatch permit in the same
    write. Returns the leased row with its lease id, or None."""
    now = H.now()
    # Only an issued permit can have reached a provider; an abandoned claim is safe to lease again.
    for stale in c.execute("SELECT * FROM email_sends WHERE mailbox=? AND state='dispatching' AND claimed_at<? LIMIT 100",
                           (mailbox, H.shift(now, minutes=-5))).fetchall():
        if stale["provider_attempted_at"]:
            c.execute("UPDATE email_sends SET state='unknown',detail='The connector did not confirm delivery',"
                      "finished_at=?,raw='' WHERE id=?", (now, stale["id"]))
            settle_automation(c, stale, "unknown")
        else:
            c.execute("UPDATE email_sends SET state='pending',runner_id=NULL,claimed_at=NULL,lease_id='' WHERE id=?",
                      (stale["id"],))
    # Rotate approval reconciliation independently of due sends; a waiting prefix cannot starve delivery.
    waiting = c.execute("SELECT * FROM email_sends WHERE mailbox=? AND state='waiting_approval' "
                        "ORDER BY checked_at,rowid LIMIT 100", (mailbox,)).fetchall()
    due = c.execute("SELECT * FROM email_sends WHERE mailbox=? AND state='pending' AND send_after<=? "
                    "ORDER BY created_at,rowid LIMIT 1000", (mailbox, now)).fetchall() if lease else []
    for row in [*waiting, *due]:
        c.execute("UPDATE email_sends SET checked_at=? WHERE id=?", (now, row["id"]))
        found = decision(c, auth, row)
        approval = H.approval(c, row["approval_id"]) if row["approval_id"] else None
        if approval and approval.get("decision") in ("declined", "withdrawn"):
            found["outcome"], found["reasons"] = "blocked", ["approval_" + approval["decision"]]
        if found["outcome"] == "blocked":
            c.execute("UPDATE email_sends SET state='blocked',decision=?,detail=?,finished_at=?,raw='' WHERE id=?",
                      (dump(found), why(found), now, row["id"]))
            settle_automation(c, row, "blocked", why(found))
            continue
        if found["outcome"] == "ask":
            c.execute("UPDATE email_sends SET state='waiting_approval',decision=? WHERE id=?", (dump(found), row["id"]))
            previous_recipient = (H.message(c, approval["message_id"]) or {}).get("recipient_id") if approval else None
            stale_approval = approval and (approval.get("decision") == "approved" or not can_ok(
                c, auth, previous_recipient, row["mailbox"], row["bot"], found["policy"]))
            if stale_approval and (approval.get("payload") or {}).get("email_send_id") == row["id"] and not approval.get("decision"):
                H.approval_withdraw(c, approval["id"], "The email's approval rule changed")
            if not row["approval_id"] or stale_approval:
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
                      (dump(found), why(found), row["id"]))
            continue
        token = H.new_id()
        c.execute("UPDATE email_sends SET state='dispatching',decision=?,runner_id=?,claimed_at=?,lease_id=? WHERE id=?",
                  (dump(found), runner_id, now, token, row["id"]))
        if permit:
            _permit(c, row, found)
        return {**dict(row), "lease_id": token}
    return None


def _permit(c, row, found):
    """The one write after which a provider may have the email: a lost answer from here on is unknown."""
    c.execute("UPDATE email_sends SET provider_attempted_at=?,decision=? WHERE id=?", (H.now(), dump(found), row["id"]))
    if found["approved"] and H.approval(c, row["approval_id"]):
        H.approval_consume(c, row["actor"], row["approval_id"])
    H.event(c, row["actor"], "email.dispatch", row["id"], {"mailbox": row["mailbox"]})


def _finish(c, row, status, message="", detail=""):
    c.execute("UPDATE email_sends SET state=?,provider_message_id=?,detail=?,finished_at=?,raw='' WHERE id=?",
              (status, message, detail, H.now(), row["id"]))
    H.event(c, row["actor"], "email." + status, row["id"], {"mailbox": row["mailbox"], "provider_message_id": message})
    settle_automation(c, row, status, detail, message)


def server_sends(c, mailbox):
    """Whether the server sends this inbox's mail itself: the Workspace key covers it and no person's own grant does."""
    from . import google_workspace
    return google_workspace.authority(c, mailbox) == "workspace"


def drain(store, auth, limit=20):
    """The server's own dispatch, on scheduler ticks: each Workspace inbox with queued mail is reconciled, and its due
    sends go to Gmail one at a time (pacing usually allows one a pass). A token is minted before any permit, so a
    credential that fails leaves the email pending, never unknown. Gmail is called under no write lock."""
    from . import gmail_live, google_workspace
    if store.settings.rehearsal or store.settings.demo:
        return 0
    with store.read() as c:
        boxes = [r[0] for r in c.execute("SELECT DISTINCT mailbox FROM email_sends WHERE state IN "
                                         "('waiting_approval','pending','dispatching')").fetchall()]
        boxes = [box for box in boxes if server_sends(c, box)]
    sent = 0
    for mailbox in boxes:
        token = None
        with store.read() as c:
            if c.execute("SELECT 1 FROM email_sends WHERE mailbox=? AND state='pending' AND send_after<=? LIMIT 1",
                         (mailbox, H.now())).fetchone():
                try:
                    token = google_workspace.mint(c, mailbox, "gmail",
                                                  scopes=google_workspace.WORKSPACE_SCOPES["gmail"])["access_token"]
                except Problem:
                    token = None                 # the key is failing: the email waits; Settings > Inboxes says why
        for _ in range(limit):
            with store.transaction(durable=True) as c:
                row = _lease(c, auth, mailbox, SERVER, permit=True, lease=token is not None)
            if not row:
                break
            status, message, detail = gmail_live.send(mailbox, token, row["raw"], row["thread_id"])
            with store.transaction(durable=True) as c:
                current = c.execute("SELECT * FROM email_sends WHERE id=?", (row["id"],)).fetchone()
                if current and current["state"] == "dispatching" and current["lease_id"] == row["lease_id"]:
                    _finish(c, current, status, message, detail)
            sent += status == "sent"
    return sent


def rule_gate(app, store, who, snap):
    """The org rule check of a bot's own email, before its write (backend/org_rule_check.py): the in-write half for
    `enqueue`, or None when the sender is not a bot writing (a person's own mail is not checked). Like every rule
    check it fails open: an error is a `rules.unchecked` outcome, never a lost send."""
    from . import org_rule_check as rules
    found = rules.checked_as(who, store.settings.assistant_bot)
    if not found or not H.is_bot(found[1]):
        return None
    groups = snap["recipient_headers"]
    try:
        prep = rules.mail_prepare(store, app.state.judge, who, found[0], groups["to"], [*groups["cc"], *groups["bcc"]],
                                  snap["subject"], snap["body"])
    except Exception as exc:   # noqa: BLE001  (fails open, as every rule check does)
        LOG.warning("The org rule check of an email failed: %s", type(exc).__name__)
        return lambda c: {"outcome": "unchecked"}
    return lambda c: rules.mail_decide(c, prep)


IMPORTED = "email_sending.registry_imported"


def import_registry(store):
    """Once: a retired registry/mail-policy.yaml's safeguards become company settings, so retiring the file widens
    nothing. Its blocklist and owner-handles-personally lists, sending off, paused inboxes, the daily cap and the outside
    recipient cap carry over; the per-recipient cooldown and standing allowances do not (they only ever lifted asks).
    The file is not read again, and a person may change the result like any setting."""
    path = store.settings.registry_dir / "mail-policy.yaml"
    if not path.is_file():
        return
    import yaml
    with store.transaction() as c:
        if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (IMPORTED,)).fetchone():
            return
        c.execute("INSERT INTO registry_metadata VALUES(?,?)", (IMPORTED, H.now()))
        try:
            pol = yaml.safe_load(path.read_text()) or {}
            assert isinstance(pol, dict)
        except (OSError, ValueError, AssertionError, yaml.YAMLError):
            LOG.warning("registry/mail-policy.yaml could not be read; nothing was carried over")
            return
        def mapping(value):
            return value if isinstance(value, dict) else {}
        def wanted(block):
            block = mapping(block)
            found = [*(block.get("addresses") or []), *(block.get("domains") or [])]
            return entries(item for item in found if isinstance(item, str))
        company = {k: v for k, v in saved(c, "company").items() if k in DEFAULTS}
        changes = {}
        for key, block in (("blocked_recipients", pol.get("blocklist")), ("owner_handles", pol.get("owner_handles_personally"))):
            extra = sorted(set(wanted(block)) - set(company.get(key, [])))
            if extra:
                changes[key] = sorted(set(company.get(key, [])) | set(extra))
        if mapping(pol.get("global")).get("send_enabled") is not True:
            changes["mode"] = "never"     # the file turned sending off unless it said otherwise
        caps = mapping(pol.get("defaults"))
        for key, source in (("daily_cap", "max_sends_per_day"), ("max_outside_recipients", "max_external_recipients")):
            value = caps.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < company.get(key, DEFAULTS[key]):
                changes[key] = value
        if changes:
            c.execute("INSERT INTO email_sending_policies VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                      "settings=excluded.settings,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                      ("company", dump({**company, **changes}), H.now(), "registry"))
        for mailbox, settings in mapping(pol.get("mailboxes")).items():
            if mapping(settings).get("paused") is True:
                scope = "inbox:" + str(mailbox).strip().lower()
                inbox = {k: v for k, v in saved(c, scope).items() if k in DEFAULTS}
                c.execute("INSERT INTO email_sending_policies VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                          "settings=excluded.settings,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                          (scope, dump({**inbox, "mode": "never"}), H.now(), "registry"))
        H.event(c, "system", "email.policy", "company", {"fields": sorted(changes), "source": "registry"})


def install(app, store, auth, mutate):
    import_registry(store)

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
        return mutate(request, body, lambda c: (update_policy(c, request.state.identity, "company", body), policy_view(c))[1], durable=True)

    @app.get("/api/v2/email/inboxes/{mailbox}/sending-policy")
    def read_inbox(request: Request, mailbox: str):
        with store.read() as c:
            return policy_view(c, inbox(c, mailbox, request.state.identity),
                               H.actor_id(request.state.identity.actor) if request.state.identity.actor.startswith("bot:") else "")

    @app.put("/api/v2/email/inboxes/{mailbox}/sending-policy")
    def write_inbox(request: Request, mailbox: str, body: PolicyUpdate):
        def work(c):
            address = inbox(c, mailbox, request.state.identity, manage=True)
            update_policy(c, request.state.identity, "inbox:" + address, body)
            return policy_view(c, address)
        return mutate(request, body, work, durable=True)

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
        return mutate(request, body, work, durable=True)

    def calendar_actor(c, who, mailbox, action_id):
        runner = reader(c, who, mailbox)
        action = c.execute("SELECT * FROM calendar_actions WHERE id=?", (action_id,)).fetchone()
        if not action or action["runner_id"] != runner["id"] or action["status"] != "running" or action["calendar_email"] != mailbox:
            raise Problem("forbidden", "No calendar action leased to this computer", 403)
        return action["requested_by"]

    @app.post("/api/v2/connectors/email/sends/preflight")
    def calendar_preflight(request: Request, body: CalendarPreflight):
        with store.read() as c:
            mailbox = body.mailbox.strip().lower()
            actor = calendar_actor(c, request.state.identity, mailbox, body.calendar_action_id)
            policy = effective(c, mailbox, H.actor_id(actor) if actor.startswith("bot:") else "")
            return {"effective": policy}

    @app.post("/api/v2/email/sends")
    def queue(request: Request, body: Enqueue):
        who = request.state.identity
        with store.read() as c:
            if body.calendar_action_id:
                actor = calendar_actor(c, who, body.mailbox.strip().lower(), body.calendar_action_id)
            elif body.automation_action_id:
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
            mailbox = body.mailbox.strip().lower()
            snap = snapshot(body.raw, mailbox)
            # A bot's own email (not one Tico composed for an automation or the calendar) is linted and reviewed.
            writing = actor.startswith("bot:") and not body.automation_action_id and not body.calendar_action_id
            review = review_context(c, auth, actor, body, mailbox, snap) if writing else None
            lint = lint_errors(c, actor, mailbox, snap, resolve_reply(c, mailbox, snap, body.reply_to_id,
                                                                      body.thread_id)[0]) if writing else []
        classification = {**classify(app, store, snap, review), "lint": lint}
        # Every bot's writing, the Assistant's for its person included, meets the org rules (`rule_gate` answers None
        # for a person's own mail); Tico's own automation and calendar email does not.
        composed = body.automation_action_id or body.calendar_action_id
        rules = None if composed else rule_gate(app, store, who, snap)
        via = who.via or ("agent:" + who.agent if who.agent else "")
        return mutate(request, body, lambda c: {"send": enqueue(
            c, auth, actor, body, classification, via=via, rules=rules,
            operation_key=body.operation_id or request.headers.get("Idempotency-Key", ""))}, durable=True)

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

    def preview_row(c, request, sid):
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()
        if not row:
            raise Problem("not_found", "Send not found", 404)
        if request.state.identity.actor != row["actor"] and not can_ok(
                c, auth, request.state.identity.actor, row["mailbox"], row["bot"], effective(c, row["mailbox"], row["bot"])):
            inbox(c, row["mailbox"], request.state.identity)
        return row

    @app.get("/api/v2/email/sends/{sid}")
    def send_detail(request: Request, sid: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        with store.transaction(durable=True) as c:
            row = preview_row(c, request, sid)
            snap = snapshot(row["raw"], row["mailbox"]) if row["raw"] else None
            if snap and request.state.identity.role in ("owner", "human") and not request.state.identity.via and not request.state.identity.task_actor:
                c.execute("INSERT INTO email_send_previews VALUES(?,?,?,?) ON CONFLICT(send_id,actor) DO UPDATE SET "
                          "shown_at=CASE WHEN content_sha256=excluded.content_sha256 THEN shown_at ELSE excluded.shown_at END,"
                          "content_sha256=excluded.content_sha256",
                          (sid, request.state.identity.actor, row["content_sha256"], H.now()))
            return {"send": public(row), "body": (snap or {}).get("body", ""),
                    "bodies": (snap or {}).get("visible_bodies", []), "preview_complete": bool(snap)}

    @app.get("/api/v2/email/sends/{sid}/attachments/{part}")
    def send_attachment(request: Request, sid: str, part: int):
        with store.read() as c:
            row = preview_row(c, request, sid)
            file = next((file for file in json.loads(row["attachments"]) if file["part"] == part), None)
            if not row["raw"] or not file:
                raise Problem("not_found", "The frozen file is no longer available", 404)
            data = attachment(row["raw"], part)
            return Response(data, media_type="application/octet-stream", headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file["name"], safe=""),
                "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "no-store"})

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
        return mutate(request, Empty(), work, durable=True)

    @app.post("/api/v2/email/sends/{sid}/approve")
    def bind_approval(request: Request, sid: str, body: SendApproval):
        def work(c):
            row = c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()
            if not row or not can_cancel(c, request.state.identity, row):
                raise Problem("forbidden", "Only the sender or a send manager attaches an approval", 403)
            if row["state"] != "waiting_approval":
                raise Problem("state", "This email is not waiting for approval", 409)
            asks = CARD_ONLY & set(json.loads(row["decision"] or "{}").get("reasons") or [])
            if asks and not H.approval(c, body.approval_id):
                # The card stays: only a person approving the frozen email in Tico lifts the second reviewer's ask.
                raise Problem("card_only", "The second reviewer stopped this email: a person approves it in Tico, "
                                           "not by message", 409)
            c.execute("UPDATE email_sends SET approval_id=? WHERE id=?", (body.approval_id, sid))
            return {"send": public(c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone())}
        return mutate(request, body, work, durable=True)

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
        return mutate(request, Empty(), work, durable=True)

    @app.post("/api/v2/connectors/email/sends/claim")
    def claim(request: Request, body: Claim):
        """A computer leases the next due send of an inbox only it can send from. A Workspace inbox's mail is the
        server's own (`drain`): its computer gets nothing, so no email can go out twice."""
        def work(c):
            mailbox = body.mailbox.strip().lower()
            runner = reader(c, request.state.identity, mailbox)
            if server_sends(c, mailbox):
                return {"send": None}
            row = _lease(c, auth, mailbox, runner["id"])
            return {"send": {"id": row["id"], "mailbox": mailbox, "thread_id": row["thread_id"],
                             "lease_id": row["lease_id"]} if row else None}
        return mutate(request, body, work, durable=True)

    @app.post("/api/v2/connectors/email/sends/{sid}/dispatch")
    def dispatch(request: Request, sid: str, body: Claim):
        def work(c):
            runner = reader(c, request.state.identity, body.mailbox.strip().lower())
            row = c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()
            if (not row or row["runner_id"] != runner["id"] or row["mailbox"] != body.mailbox.strip().lower()
                    or not body.lease_id or row["lease_id"] != body.lease_id):
                raise Problem("forbidden", "This send belongs to another connector", 403)
            if row["state"] != "dispatching" or row["provider_attempted_at"]:
                return {"send": None}
            found = decision(c, auth, row, dispatch=True)
            if found["outcome"] != "allow":
                c.execute("UPDATE email_sends SET state='blocked',decision=?,detail=?,finished_at=?,raw='' WHERE id=?",
                          (dump(found), why(found), H.now(), sid))
                settle_automation(c, row, "blocked", why(found))
                return {"send": None}
            _permit(c, row, found)
            return {"send": {"id": sid, "raw": row["raw"], "thread_id": row["thread_id"], "actor": row["actor"],
                             "recipients": json.loads(row["recipients"]), "subject": row["subject"], "draft_id": row["draft_id"]}}
        # A permit is never cached as an idempotency response: even the same request key cannot replay it.
        with store.transaction(durable=True) as c:
            return work(c)

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
            if not row or row["runner_id"] != runner["id"] or not body.lease_id or row["lease_id"] != body.lease_id:
                raise Problem("forbidden", "Only the current dispatch lease reports this receipt", 403)
            if row["state"] not in ("dispatching", "unknown"):
                return {"send": public(row)}
            if not row["provider_attempted_at"]:
                raise Problem("state", "This lease has not received a dispatch permit", 409)
            _finish(c, row, body.status, body.message, body.detail)
            return {"send": public(c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone())}
        return mutate(request, body, work, durable=True)
