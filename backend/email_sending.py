"""The email send gate and durable undo window on the outbound queue: every send, by a person, a bot, an automation or the calendar.

The server freezes MIME content before approval and dispatch and decides every send here, a computer's mail tool only
queues it (POST /api/v2/email/sends). One sending policy applies, its DEFAULTS when nothing is saved: company, inbox and
bot layers (`effective`), blocked and owner-handled recipients, outside Cc and outside-recipient counts, the lint and
the second reviewer on a bot's own writing, the org rule check (`rule_gate`), bounces and reply loops. The only thing
that lets a held send go is an OK bound to this exact frozen send (backend/task_asks.py, subject `email_send:<id>`):
asked of someone who may approve it under the inbox's rule (`can_ok`), given by their own click after the server showed
them this exact email, still theirs to give when it is sent, and used once (`approved`).

Every email is sent by the server itself once the undo window and pacing allow (`drain`), with the Workspace key or the
person's own Connect Google grant (backend/google_tokens.py); one from a mailbox neither covers fails with why. A
computer of an older release still asks to lease sends (`claim`) and gets none. A lost provider response is unknown,
never an invitation to send a second copy.
Classification and review are triage, not a security boundary: permissions, OKs and limits still apply.
"""

import json
import logging
import re
from datetime import datetime, timezone

from fastapi import Request, Response
from urllib.parse import quote
from pydantic import Field

from . import mailbox_access as access
from . import outbound_email as OE
from . import rule_links
from . import task_asks
from .models import Contract, Empty
from .email_content import MAX_RAW, attachment, digest, snapshot
from .store import H, Problem
from .api_contract import api_v2

DEFAULTS = {
    "mode": "ask_first",
    "what": "replies_only",
    "to": "anyone",
    "daily_cap": 50,
    "mailbox_daily_cap": 200,
    "company_daily_cap": 1000,
    "max_outside_recipients": 1,
    "pacing_seconds": 10,
    "undo_seconds": 120,
    "ask_new_address": True,
    "ask_attachments": True,
    "ask_many_recipients": True,
    "ask_outside_cc": True,
    "ask_legal_money": True,
    "ask_lint": True,
    "second_review": True,
    "blocked_recipients": [],
    "owner_handles": [],
    "who_can_ok": "bot_managers",
    "approvers": [],
}
MODES = {"on_their_own": 0, "ask_first": 1, "never": 2}
# A child layer may turn an ask on, never off; lower a cap, never raise it; add to a list, never remove from it.
ASKS = (
    "ask_new_address",
    "ask_attachments",
    "ask_many_recipients",
    "ask_outside_cc",
    "ask_legal_money",
    "ask_lint",
    "second_review",
)
CAPS = ("daily_cap", "mailbox_daily_cap", "company_daily_cap", "max_outside_recipients")
LISTS = ("blocked_recipients", "owner_handles")
ACTIVE = ("waiting_approval", "pending", "dispatching")
LIST_BYTES = 128 * 1024
LOG = logging.getLogger("tico.email_sending")
LIST_MAX = 200
DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)+$")
SERVER = "server"  # runner_id of a send the server dispatches itself (`drain`)
SUBJECT = "email_send:"  # an OK's subject for one frozen send: SUBJECT + its id
REVIEW = (
    "Would a careful second reviewer stop this email, written by an automated employee, before it is sent? Stop it "
    "if it makes a commitment (money, a discount, a date, a deliverable, a legal position, a guarantee), confirms "
    "a meeting time, states facts about the recipient or the thread that the thread shown does not support, or "
    "does not read as the company. An approver's instruction, when shown, authorizes only what it says."
)
# Why the second reviewer stopped a bot's own email. The decision model writes no prose (clients/judge.py), so the reason
# is a second choice asked in the same call, and each option is the sentence the bot and the person read.
STOPPED = {
    "commitment": "It makes a commitment: money, a discount, a date, a deliverable, a legal position or a guarantee.",
    "confirmed_time": "It confirms a meeting time.",
    "unsupported_claim": "It states something about the recipient or the thread that the thread does not support.",
    "missing_file": "It says files are attached, but the email has no attachments. Attach them, or take the line out.",
    "voice": "It does not read as the company.",
}
REVIEW_WHY = "If a careful reviewer stopped this email, which is the main reason?"
# A stop that names none of the reasons above, and a review that did not answer: still said in words, with what to do.
NO_REASON = (
    "The second reviewer stopped it without naming a reason. Check it for a commitment, a confirmed time, a claim the "
    "thread does not support, a file it mentions but lacks, or wording that is not the company's; rewrite and draft it "
    "again to review it again. Queued as it is, it waits for a person's OK."
)
UNANSWERED = {
    "incomplete_content": "The second reviewer could not read the whole email (a part that is not plain text or HTML, "
    "or over 16,000 characters), so it gave no verdict. Queued as it is, it waits for a person's OK.",
    "unavailable": "The second reviewer did not answer (the decision model is off, at its spend cap or did not reply), "
    "so it gave no verdict. Draft it again with the same words to review it again; queued as it is, it waits for a "
    "person's OK.",
}
# A sentence of the email's own (not a quoted line) that says something is attached or enclosed.
SAYS_ATTACHED = re.compile(r"\b(?:attached|attaching|attachments?|enclosed|enclosing|enclosures?)\b", re.I)
NOT_ATTACHED = re.compile(r"\b(?:not|no|without|never)\s+(?:\w+\s+){0,2}(?:attached|attachments?|enclosed)\b", re.I)
QUOTE_START = re.compile(r"^\s*(?:On .{0,300}wrote:|-{2,} ?Original Message ?-{2,}|From: .+)\s*$", re.I | re.M)
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
        if OE.reached(c, mailbox, address, unconfirmed=True):
            c.execute(
                "INSERT OR IGNORE INTO email_suppressed_recipients VALUES(?,?,?,?)",
                (mailbox, address, "delivery_status", H.now()),
            )


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
    # The frozen MIME (base64url), or none with `draft_id`: the server freezes the draft as Gmail holds it now.
    raw: str = Field(default="", max_length=MAX_RAW * 4 // 3 + 8)
    draft_id: str = Field(default="", max_length=200)
    # With draft_id: the draft's message id the caller last saw; a draft changed since is refused (409).
    draft_message: str = Field(default="", max_length=200)
    thread_id: str = Field(default="", max_length=200)
    reply_to_id: str = Field(default="", max_length=200)
    approval_id: str = Field(default="", max_length=200)
    automation_action_id: str = Field(default="", max_length=100)
    calendar_action_id: str = Field(default="", max_length=100)
    operation_id: str = Field(default="", max_length=200)
    # The task this email is for: its OK, when it needs one, is asked on it. A bot's turn on a task names it when this is
    # left out (`turn_task`).
    task_id: str = Field(default="", max_length=200)


def saved(c, scope):
    row = c.execute("SELECT * FROM email_sending_policies WHERE scope=?", (scope,)).fetchone()
    return (
        {**json.loads(row["settings"]), "updated_at": row["updated_at"], "updated_by": row["updated_by"]} if row else {}
    )


def effective(c, mailbox="", bot="", read=None):
    """Company Ask first is a default. Never and company safeguards are ceilings on every override. `read`: how a
    layer's saved settings are read (`saved`), memoized by a caller that resolves many."""
    read = read or (lambda scope: saved(c, scope))
    layers = [("company", read("company"))]
    if mailbox:
        layers.append(("inbox", read("inbox:" + mailbox)))
    if bot:
        layers.append(("bot", read("bot:" + bot)))
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
            approval_rules.append(
                {
                    "value": settings.get("who_can_ok", DEFAULTS["who_can_ok"]),
                    "approvers": settings.get("approvers", []),
                    "source": label,
                }
            )
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

    return {
        "company": visible(saved(c, "company")),
        "inbox": visible(saved(c, "inbox:" + mailbox)) if mailbox else {},
        "bot": visible(saved(c, "bot:" + bot)) if bot else {},
        "effective": effective(c, mailbox, bot),
    }


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
    return any(
        address == value if "@" in value else domain == value or domain.endswith("." + value) for value in values
    )


def update_policy(c, who, scope, body):
    values = body.model_dump(exclude_none=True)
    inherit = values.pop("inherit")
    for key, allowed in (
        ("mode", MODES),
        ("what", ("replies_only", "replies_and_new")),
        ("to", ("anyone", "known_addresses", "internal")),
        ("who_can_ok", ("bot_managers", "inbox_admins", "named_people")),
    ):
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
        c.execute(
            "INSERT INTO email_sending_policies VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
            "settings=excluded.settings,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
            (scope, dump({**current, **values}), H.now(), who.actor),
        )
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
    """The person an OK of this send is asked of: the first who may approve it under the effective rule."""
    facts = access._Facts(c)
    candidates = list(facts.people)
    return next(
        ("human:" + pid for pid in candidates if can_ok(c, auth, "human:" + pid, row["mailbox"], row["bot"], policy)),
        None,
    )


def content(row):
    """What an OK of a frozen send is for, exactly: the inbox, every recipient, the subject, the body's hash, the whole
    MIME's hash and each file with its hash. The preview shows it and the send checks it."""
    return {
        "mailbox": row["mailbox"],
        "recipients": sorted(json.loads(row["recipients"])),
        "subject": row["subject"],
        "body_sha256": row["body_sha256"],
        "content_sha256": row["content_sha256"],
        "attachments": sorted(
            ({"name": f["name"], "sha256": f["sha256"]} for f in json.loads(row["attachments"])), key=dump
        ),
    }


def approved(c, auth, row, policy):
    """Whether an OK bound to this exact frozen send lets it go: given from its preview by someone who may still approve
    it under the effective rule, after the server showed them this content, and not used yet. Nothing else approves a
    send: no message, no GitHub Issue, no standing allowance."""
    rid = row["approval_id"]
    found = task_asks.grant(c, rid) if rid else None
    if not found or found["subject"] != SUBJECT + row["id"]:
        return False
    if not task_asks.passes(c, rid, SUBJECT + row["id"], content(row), row["actor"]):
        return False
    if not can_ok(c, auth, found["ok_by_id"], row["mailbox"], row["bot"], policy):
        return False
    return seen(c, found["ok_by_id"], row)


def seen(c, actor, row):
    """Whether the server showed `actor` this whole frozen email (`shown_to`, from GET /api/v2/email/sends/{id}): an OK
    of a send counts only from someone it was shown to. The generic OK preview never shows the body."""
    return bool(
        c.execute(
            "SELECT 1 FROM email_send_previews WHERE send_id=? AND actor=? AND content_sha256=?",
            (row["id"], actor, row["content_sha256"]),
        ).fetchone()
    )


REFERENCES_READ = 5  # the newest Message-IDs of a reply's References that are looked up in Gmail


def _original(c, row):
    """The message a send answers, as Gmail has it now (backend/mail_facts.py), or None."""
    if not row["reply_to_id"]:
        return None
    from . import mail_facts

    return mail_facts.message(c, row["mailbox"], row["reply_to_id"])


def resolve_reply(c, mailbox, snap, supplied_reply="", supplied_thread=""):
    """The (Gmail id, thread) of the message a reply's In-Reply-To or References names, found in Gmail by its
    Message-ID; ('', '') for an email that answers nothing."""
    from . import mail_facts

    ids = re.findall(r"<[^<>\s]+>", snap["in_reply_to"] or snap["references"])
    original = None
    for reference in list(reversed(ids))[:REFERENCES_READ]:
        found = mail_facts.by_message_id(c, mailbox, reference)
        if found:
            original = {"msg_id": found["id"], "thread_id": found["threadId"]}
            break
    if (supplied_reply and (not original or supplied_reply != original["msg_id"])) or (
        supplied_thread and (not original or supplied_thread != original["thread_id"])
    ):
        raise Problem("reply", "Reply identifiers must match the frozen MIME headers and the Gmail message", 422)
    if (snap["in_reply_to"] or snap["references"]) and not original:
        raise Problem("reply", "Gmail has no message in this mailbox that this reply answers", 422)
    return (original["msg_id"], original["thread_id"]) if original else ("", "")


def known(c, mailbox, address):
    """Whether this inbox knows `address`: Tico sent it mail, or (in Gmail) it wrote to the inbox or the inbox to it."""
    if OE.reached(c, mailbox, address):
        return True
    from . import mail_facts

    return mail_facts.known(c, mailbox, address)


def warm(c, row):
    """Ask Gmail, under a read, what deciding this send will ask (the message it answers, whether its recipients are
    known), so the decision inside the write finds it cached and holds the write lock for no Gmail call."""
    _original(c, row)
    policy = effective(c, row["mailbox"], row["bot"])
    if policy["ask_new_address"] or any(rule["value"] == "known_addresses" for rule in policy["recipient_rules"]):
        for address in json.loads(row["recipients"]):
            if not known(c, row["mailbox"], address):
                break  # the decision stops at the first unknown address too


def audience(c, recipients):
    domains = access.company_domains(c)
    return (
        "internal"
        if domains and recipients and all(access.inside(address, domains) for address in recipients)
        else "external"
    )


def own_writing(row):
    """A bot's own email, not one Tico composed for an automation or the calendar: the lint and the reviewer read it."""
    return bool(row["bot"]) and not row["automation_action_id"] and not row["calendar_action_id"]


def why(found):
    """A decision's reasons as its detail, naming the setting behind a refusal: `never` says which layer set Never.
    The reasons themselves stay codes."""
    layer = (found.get("policy") or {}).get("sources", {}).get("mode", "company")
    return ", ".join(
        reason + " (" + layer + " setting)" if reason == "never" else reason for reason in found["reasons"]
    )


def decision(c, auth, row, *, dispatch=False, wait_for_gmail=False):
    """The send rules' outcome for this frozen email: allow, ask or blocked. A Gmail fact it needs (the message it
    answers, whether a recipient is known) that Gmail cannot give now raises its retryable Problem; with
    `wait_for_gmail` it is `wait` instead (reason `gmail_unchecked`) unless a rule blocks it anyway: the email is queued
    and the drain decides it again, with Gmail's answer, before it can go."""
    policy = effective(c, row["mailbox"], row["bot"])
    reasons, blocked, unchecked = [], [], []

    def fact(read, default):
        try:
            return read()
        except Problem as unread:
            if not (wait_for_gmail and unread.retryable):
                raise
            unchecked.append(unread.code)
            return default

    def known_to(address):
        return fact(lambda: known(c, row["mailbox"], address), True)

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
    original = fact(lambda: _original(c, row), None)
    is_reply = bool(
        original
        and row["thread_id"] == original["thread_id"]
        and original["from_addr"].lower() in recipients
        and original["from_addr"].lower() != row["mailbox"]
    )
    legality = checks.get("legal_or_money")
    if not isinstance(legality, bool):
        reasons.append("legal_or_money_or_unclassified")
    if policy["mode"] == "ask_first":
        reasons.append("ask_first")
    if policy["what"] == "replies_only" and not is_reply and not (row["reply_to_id"] and unchecked):
        reasons.append("new_message")
    if policy["ask_new_address"] and any(not known_to(a) for a in recipients):
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
    if checks.get("ok_again"):
        reasons.append("revised_after_ok")
    if any(rule["value"] == "known_addresses" for rule in policy["recipient_rules"]) and any(
        not known_to(a) for a in recipients
    ):
        blocked.append("known_addresses_only")
    if any(rule["value"] == "internal" for rule in policy["recipient_rules"]) and outside:
        blocked.append("internal_only")
    if row["automation_action_id"]:
        found = c.execute(
            "SELECT a.*,m.owner_id,m.enabled,m.deleted_at,m.flow FROM automation_mail_actions a "
            "JOIN automations m ON m.id=a.automation_id WHERE a.id=?",
            (row["automation_action_id"],),
        ).fetchone()
        from .automation_actions import acts_as, send_on

        if (
            not found
            or found["deleted_at"]
            or not found["enabled"]
            or found["state"] not in ("claimed", "waiting_send")
            or acts_as(c, {"flow": json.loads(found["flow"])}) != row["actor"]
            or not send_on(c, row["mailbox"])
        ):
            blocked.append("automation_revoked")
    if row["calendar_action_id"]:
        action = c.execute("SELECT * FROM calendar_actions WHERE id=?", (row["calendar_action_id"],)).fetchone()
        if not action or action["requested_by"] != row["actor"] or action["status"] not in ("running", "succeeded"):
            blocked.append("calendar_revoked")
    if c.execute(
        "SELECT 1 FROM email_suppressed_recipients WHERE mailbox=? AND address IN ("
        + ",".join("?" for _ in recipients)
        + ") LIMIT 1",
        (row["mailbox"], *recipients),
    ).fetchone():
        blocked.append("bounce_suppression")
    if original:
        headers = original.get("send_facts") or {}
        auto = str(headers.get("auto-submitted") or "").lower()
        sender = original["from_addr"].lower()
        # Header facts come from Gmail; the sender check also catches a bounce without them.
        if (
            (auto and auto != "no")
            or sender.startswith(("mailer-daemon@", "postmaster@"))
            or str(headers.get("precedence") or "").lower() in ("bulk", "list", "junk")
        ):
            blocked.append("reply_loop")
        if c.execute(
            "SELECT 1 FROM outbound_email WHERE mailbox=? AND reply_to_id=? AND id<>? "
            "AND queue_state IN ('queued','attempting','done','unknown') LIMIT 1",
            (row["mailbox"], row["reply_to_id"], row["id"]),
        ).fetchone():
            blocked.append("already_replied")
    lifted = approved(c, auth, row, policy)
    if unchecked and not blocked:
        return {
            "outcome": "wait",
            "reasons": ["gmail_unchecked", *reasons],
            "policy": policy,
            "approval_id": row["approval_id"],
            "approved": False,
        }
    if reasons and not lifted:
        return {
            "outcome": "blocked" if blocked else "ask",
            "reasons": blocked + reasons,
            "policy": policy,
            "approval_id": row["approval_id"],
            "approved": False,
        }
    if dispatch:
        day = H.now()[:10]
        for field, clause, params in (
            ("daily_cap", "actor=?", (row["actor"],)),
            ("mailbox_daily_cap", "mailbox=?", (row["mailbox"],)),
            ("company_daily_cap", "1=1", ()),
        ):
            count = c.execute(
                "SELECT count(*) FROM outbound_email WHERE " + clause + " AND id<>? "
                "AND queue_state IN ('done','attempting','unknown') AND substr(coalesce(provider_attempted_at,claimed_at,created_at),1,10)=?",
                (*params, row["id"], day),
            ).fetchone()[0]
            if count >= policy[field]:
                blocked.append(field)
        last = c.execute(
            "SELECT max(coalesce(provider_attempted_at,claimed_at)) FROM outbound_email WHERE mailbox=? AND id<>? "
            "AND queue_state IN ('done','attempting','unknown')",
            (row["mailbox"], row["id"]),
        ).fetchone()[0]
        if last and last > H.shift(H.now(), seconds=-policy["pacing_seconds"]):
            blocked.append("pacing")
    return {
        "outcome": "blocked" if blocked else "allow",
        "reasons": blocked,
        "policy": policy,
        "approval_id": row["approval_id"],
        "approved": lifted,
    }


def public(row):
    keys = (
        "id",
        "mailbox",
        "actor",
        "bot",
        "via",
        "automation_action_id",
        "calendar_action_id",
        "content_sha256",
        "body_sha256",
        "subject",
        "approval_id",
        "task_id",
        "approval_task_id",
        "state",
        "created_at",
        "send_after",
        "claimed_at",
        "finished_at",
        "provider_message_id",
        "detail",
        "draft_id",
    )
    return {
        **{key: row[key] for key in keys},
        **{key: json.loads(row[key]) for key in ("recipients", "attachments", "decision", "classification")},
        # Stored as "" when the send starts a new thread; null says "not a reply" on the wire.
        **{key: row[key] or None for key in ("thread_id", "reply_to_id")},
    }


# What a task's row shows of a send that belongs to it or whose OK sits on it (`email_send`): the board card's and the
# email tab's quiet line ("Sending · 1:40", "Sent 5:23 AM") and the toast after Approve read it.
TASK_FIELDS = ("id", "state", "send_after", "finished_at", "detail")
OPEN_STATES = ("waiting_approval", "pending", "dispatching")
ENDED_STATES = ("sent", "failed", "canceled", "blocked", "unknown")
SHOWN_ENDED_HOURS = 24  # a send that ended is shown this long
ENDED_REACH_DAYS = 7  # how far back an ended send's own window (`send_after`) may have opened and still be shown


def for_tasks(c, ids):
    """{task id: the newest of its sends still open or ended in the last SHOWN_ENDED_HOURS, as TASK_FIELDS}, for the
    tasks among `ids` that a send belongs to (`task_id`) or whose OK it sits on (`approval_task_id`). Read by state and
    window (the outbound_email_dispatch index), so a page of tasks reads only the sends that may be shown, however many
    the company has sent."""
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return {}
    now = H.now()
    marks = ",".join("?" * len(ids))
    mine = f"(task_id IN ({marks}) OR approval_task_id IN ({marks}))"
    fields = ",".join(TASK_FIELDS)
    # An open send's matter dates come in the same read (DATES_SQL): its not before, on its own case's row.
    rows = c.execute(
        f"SELECT task_id,approval_task_id,{fields},created_at,s.rowid AS n,{DATES_SQL} AS dates,mailbox,bot "
        "FROM outbound_email s "
        f"WHERE queue_state IN ({','.join('?' * len(OPEN_STATES))}) AND {mine} "
        f"UNION ALL SELECT task_id,approval_task_id,{fields},created_at,rowid AS n,NULL,mailbox,bot FROM outbound_email "
        f"WHERE queue_state IN ({','.join('?' * len(ENDED_STATES))}) AND send_after>=? AND finished_at>=? AND {mine}",
        (
            *(OE.STATES.get(state, state) for state in OPEN_STATES),
            *ids,
            *ids,
            *(OE.STATES.get(state, state) for state in ENDED_STATES),
            H.shift(now, days=-ENDED_REACH_DAYS),
            H.shift(now, hours=-SHOWN_ENDED_HOURS),
            *ids,
            *ids,
        ),
    ).fetchall()
    wanted, newest = set(ids), {}
    for row in rows:
        for task in {row["task_id"], row["approval_task_id"]} & wanted:
            held = newest.get(task)
            if held is None or (row["created_at"], row["n"]) > (held["created_at"], held["n"]):
                newest[task] = row
    out, layers, policies = {}, {}, {}

    def read(scope):
        if scope not in layers:
            layers[scope] = saved(c, scope)
        return layers[scope]

    def policy(mailbox, bot):
        # Each inbox and agent's policy once a call, the company's settings once for all of them.
        if (mailbox, bot) not in policies:
            policies[mailbox, bot] = effective(c, mailbox, bot, read)
        return policies[mailbox, bot]

    for task, row in newest.items():
        out[task] = {key: row[key] for key in TASK_FIELDS}
        if task != row["task_id"]:
            continue  # a row the send's OK sits on alone: no matter of its own, so no `not_before` that would wipe it
        # `not_before`: the day a send still waits for past its undo deadline (its matter's not before, in the company's
        # time), as the preview says it; else null. Only on its own case's row, whose readers read the matter.
        held = latest_bound((row["dates"] or "").split(","))
        if held and row["state"] == "waiting_approval":
            # Its undo deadline is what an OK given now would set.
            due = H.shift(now, seconds=policy(row["mailbox"], row["bot"])["undo_seconds"])
        else:
            due = row["send_after"]
        out[task]["not_before"] = in_company_zone(held) if held and held > max(due, now) else None
    return out


def held_changed(c, task_id):
    """A matter's dates changed (backend/risk.py): when a send of its case is still to go, the case's row is sent again
    (a `tasks` change, kind email_send), so the day it is held for reads right everywhere."""
    if c.execute(
        "SELECT 1 FROM outbound_email WHERE task_id=? AND queue_state IN ('waiting_approval','queued') LIMIT 1",
        (task_id,),
    ).fetchone():
        c.execute(
            "INSERT INTO changes(topic,kind,subject_id,created_at) VALUES('tasks','email_send',?,?)", (task_id, H.now())
        )


def moved(c, sid):
    """A send's state moved: one `tasks` change for the task it belongs to and one for the task its OK sits on, so
    every open list, board and task view is sent those tasks again with the send's new `email_send` (backend/events.py
    streams them) instead of reading the send again and again."""
    row = c.execute("SELECT task_id,approval_task_id FROM outbound_email WHERE id=?", (sid,)).fetchone()
    if not row:
        return
    now = H.now()
    for task in dict.fromkeys(t for t in (row["task_id"], row["approval_task_id"]) if t):
        c.execute("INSERT INTO changes(topic,kind,subject_id,created_at) VALUES('tasks','email_send',?,?)", (task, now))


def settle_automation(c, row, state, detail="", message=""):
    if not row["automation_action_id"]:
        return
    from .automations import _mail_report

    action = c.execute("SELECT * FROM automation_mail_actions WHERE id=?", (row["automation_action_id"],)).fetchone()
    if action and action["state"] not in ("done", "failed", "canceled"):
        _mail_report(
            c,
            action,
            "done" if state == "sent" else "failed",
            {"ok": state == "sent", "message": message, "detail": detail or state},
        )


def send_title(row):
    """What a send's OK is titled with: its subject and first recipient, so two sends waiting side by side differ."""
    subject = " ".join(str(row["subject"] or "").split())
    subject = (subject[:99].rstrip() + "…" if len(subject) > 100 else subject) or "(no subject)"
    recipients = json.loads(row["recipients"] or "[]")
    to = (" → " + recipients[0] + (f" +{len(recipients) - 1}" if len(recipients) > 1 else "")) if recipients else ""
    return "Send: " + subject + to


def request_approval(c, auth, row, policy, why="Asked again", renew=False):
    """Ask for an OK of this frozen send, of someone who may approve it (`approver`; the company's first person when
    nobody can, so the send is held where someone sees it), on the send's own task (the case) when its sender may ask
    there (`asks_on`): an OK bound to a send is never replaced by the bot's next ask there (task_engine._replace_open),
    so each send waits on the case with its own OK. Otherwise, and for a send with no task, on a task of its own titled
    with the email (`send_title`). An earlier OK of this send is withdrawn with `why` once the new one is asked
    (task_asks.ask_ok; `renew`: even one for the same content, asked again of someone who may approve it now). The OK's id is the send's `approval_id`, its task the send's `approval_task_id`."""
    person = approver(c, auth, row, policy) or H.human_actor(H.default_human(c))
    case = H.task(c, row["task_id"]) if row["task_id"] else None
    asked = task_asks.ask_ok(
        c,
        row["actor"],
        person,
        "Send this email?",
        shows=["email:" + row["id"]],
        subject=SUBJECT + row["id"],
        content=content(row),
        title=send_title(row),
        task_id=case["id"] if case and asks_on(c, auth, row["actor"], person, case) else None,
        why=why,
        renew=renew,
    )
    OE.update(c, row["id"], approval_id=asked["requirement_id"], approval_task_id=asked["task_id"])


def asks_on(c, auth, actor, person, task):
    """Whether `actor` may ask `person` for an OK on this task (task_calls.task_ask): it is open, `actor` works it, and
    `person` reads it already, as the approval page checks it (auth.task: a private task's readers, and Read on each
    agent it involves). An ask never makes a reader of anyone. Checked here so a send it may not ask about there is
    still asked for, on a task of its own."""
    if task["status"] != "open" or not H.task_can_work(c, actor, task):
        return False
    try:
        auth.task(c, auth.identity_for_actor(c, person), task["id"], tags=False)
    except Problem:
        return False
    return True


def shown_to(c, who, row):
    """The whole frozen email was put in front of `who`. Only a person's own session counts as having seen it
    (task_asks.own_click): that showing is recorded, and the OK they give is bound to it. Returns the hash their OK
    sends back, or None when the send has no OK to give."""
    own = task_asks.own_click(who)
    if own:
        c.execute(
            "INSERT INTO email_send_previews VALUES(?,?,?,?) ON CONFLICT(send_id,actor) DO UPDATE SET "
            "shown_at=CASE WHEN content_sha256=excluded.content_sha256 THEN shown_at ELSE excluded.shown_at END,"
            "content_sha256=excluded.content_sha256",
            (row["id"], who.actor, row["content_sha256"], H.now()),
        )
    bound = task_asks.grant(c, row["approval_id"]) if row["approval_id"] else None
    if not bound or bound["subject"] != SUBJECT + row["id"]:
        return None
    return task_asks.show(c, who.actor, row["approval_id"], content(row), own_click=own)


def ok_given(c, grant, ctx):
    """In the transaction of an OK of a send (task_asks.ON_OK): the person who gave it may approve this send under the
    inbox's rule now, and was shown the whole email (`seen`). Checked again before it is sent (`approved`)."""
    auth = getattr(ctx, "auth", None)
    sid = str(grant["subject"]).removeprefix(SUBJECT)
    row = OE.get(c, sid)
    if not row or row["state"] not in ACTIVE:
        raise Problem("state", "This email is no longer waiting to send", 409)
    if auth is None or not can_ok(
        c, auth, grant["ok_by_id"], row["mailbox"], row["bot"], effective(c, row["mailbox"], row["bot"])
    ):
        raise Problem("forbidden", "This send follows its inbox's approval rule", 403)
    if not seen(c, grant["ok_by_id"], row):
        # Refused here, not found out at the send: an OK `approved` would not count is never given.
        raise Problem("email_not_shown", "Open the full email before you OK it", 409)


def existing(c, auth, actor, body, operation_key="", request=None, draft_verified=True, snap=None):
    """What `enqueue` reads to find a send it already has, before it writes anything: its operation's receipt
    (`prior`), a verified draft's sends still to go (`drafts`, `same_draft` when the newest has this content), the case
    it is for (`task_id`), its idempotency `key` and the send already under it (`old`, legacy keys included). Read
    only: the send route asks it too, before the rule check (`duplicate`)."""
    mailbox = body.mailbox.strip().lower()
    snap = snap or snapshot(body.raw, mailbox)
    operation_key = operation_key or body.operation_id
    asked = request_hash(request or body)
    prior = receipt(c, actor, operation_key, mailbox, asked, snap["content_sha256"]) if operation_key else None
    draft_id = body.draft_id if draft_verified else ""
    # A verified draft's sends still to go, newest first, whoever queued them (the sender may send from this inbox,
    # checked by the caller). Requeued, the draft is a new version: its own send, never merged with another by content.
    # Only the same content again (its newest send's) changes nothing.
    drafts = revised(c, mailbox, draft_id)
    task_id = body.task_id or None
    if not task_id and drafts and drafts[0]["task_id"] and reads_task(c, auth, actor, drafts[0]["task_id"]):
        # The draft's earlier send names its case: kept only for a sender who reads it, else the send has none.
        task_id = drafts[0]["task_id"]
    same_draft = bool(drafts) and snap["content_sha256"] in (
        drafts[0]["content_sha256"],
        json.loads(drafts[0]["classification"]).get("source_sha256"),
    )
    if draft_id:
        key, old = "draft:" + H.new_id(), None
    else:
        # Without a verified draft, the same email for the same case (or with none) is the same send.
        key = digest(dump([mailbox, snap["content_sha256"], snap["recipients"], task_id or "no task"]))
        old = c.execute("SELECT * FROM outbound_email WHERE idempotency_key=?", (key,)).fetchone()
        if not old:
            # Keyed before keys named the case: the same send only for the same case, or with none for both.
            legacy = digest(dump([mailbox, snap["content_sha256"], snap["recipients"]]))
            hit = c.execute("SELECT * FROM outbound_email WHERE idempotency_key=?", (legacy,)).fetchone()
            old = hit if hit and (hit["task_id"] or None) == (task_id or None) else None
    return {
        "operation_key": operation_key,
        "asked": asked,
        "prior": prior,
        "drafts": drafts,
        "same_draft": same_draft,
        "task_id": task_id,
        "key": key,
        "old": old,
    }


def duplicate(c, auth, actor, body, operation_key="", request=None, draft_verified=True, snap=None):
    """Read only, before the rule check: whether `enqueue` will queue nothing new for this email (`existing`: a send it
    has, by its receipt, its draft or its key, another sender's included, which it refuses), or refuse it outright (a
    receipt for other content). `enqueue` decides again inside its write."""
    try:
        found = existing(c, auth, actor, body, operation_key, request, draft_verified, snap)
    except Problem:
        return True
    old = found["old"]
    return bool(found["prior"] or found["same_draft"] or (old and old["state"] in (*ACTIVE, "sent", "unknown")))


def enqueue(
    c,
    auth,
    actor,
    body,
    classification,
    via="",
    operation_key="",
    rules=None,
    wait_for_gmail=False,
    request=None,
    draft_verified=True,
):
    """Freeze, decide and queue one send. `rules` is the org rule check's in-write half (`rule_gate`): a held email is
    queued blocked with the hold's text, and a warned one carries the warning line in its frozen copy. With
    `wait_for_gmail` (an email Tico composes inside another write, a calendar invitation), Gmail not answering the
    decision's questions queues it pending and unchecked (`decision`) instead of refusing it. `request` is the request
    as its caller sent it (before a draft was frozen into `body`): an operation id is bound to its hash.
    `draft_verified`: Gmail read the draft named (`frozen_draft`), or Tico's own server code named it; only then does
    the email replace that draft's earlier sends. A caller's `draft_id` beside its own `raw` is only a label."""
    mailbox = body.mailbox.strip().lower()
    bot = H.actor_id(actor) if actor.startswith("bot:") else ""
    if not access.may(c, actor, mailbox)["send"]:
        raise Problem("forbidden", "The sender cannot send from this inbox", 403)
    raw = body.raw
    snap = snapshot(raw, mailbox)
    reply_id, thread_id = resolve_reply(c, mailbox, snap, body.reply_to_id, body.thread_id)
    found = existing(c, auth, actor, body, operation_key, request, draft_verified, snap)
    operation_key, asked, drafts, task_id, key, old = (
        found[k] for k in ("operation_key", "asked", "drafts", "task_id", "key", "old")
    )
    draft_id = body.draft_id if draft_verified else ""
    if found["prior"]:
        return public(found["prior"])
    case = c.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone() if task_id else None
    if case and case["status"] == "closed":
        # Read in this write: a close that won the race with this send leaves nothing to go.
        raise Problem("task_closed", "The task this email is for was closed", 409)
    if found["same_draft"]:
        for row in drafts[1:]:
            retire(c, row, REPLACED)
        if operation_key:
            remember(c, actor, operation_key, drafts[0]["id"], asked)
        return public(drafts[0])
    # Whether a version it replaces had an OK asked for it: the new one needs its own, whatever the edit took out.
    ok_again = any(r["approval_id"] or json.loads(r["classification"]).get("ok_again") for r in drafts)
    if old and old["state"] in (*ACTIVE, "sent", "unknown"):
        if old["actor"] != actor:
            raise Problem("duplicate", "This email is already queued or sent", 409)
        if operation_key:
            remember(c, actor, operation_key, old["id"], asked)
        return public(old)
    if old:
        OE.update(c, old["id"], idempotency_key="retired:" + old["id"])
    if (
        c.execute(
            "SELECT count(*) FROM outbound_email WHERE queue_state IN ('queued','waiting_approval','attempting')"
        ).fetchone()[0]
        >= 1000
    ):
        raise Problem("queue_full", "The email queue is full", 429)
    if (
        c.execute(
            "SELECT coalesce(sum(length(raw)),0) FROM outbound_email WHERE queue_state IN ('queued','waiting_approval','attempting')"
        ).fetchone()[0]
        + len(raw)
        > 128 * 1024 * 1024
    ):
        raise Problem("queue_full", "The email queue content limit is reached", 429)
    groups = snap["recipient_headers"]
    checks = {**classification, "copied": sorted(set(groups["cc"]) | set(groups["bcc"]))}
    if ok_again:
        # It replaces a version an OK was asked for: the new one needs its own, whatever the edit took out.
        checks["ok_again"] = True
    gate = rules(c) if rules else None
    if gate:
        checks["rules"] = gate["outcome"]
        if gate.get("warning_line"):
            # A Warn rule: the recipient's copy carries the line, and that copy is what is frozen and approved.
            from .mail_lib.compose import with_warning

            raw = with_warning(raw, gate["warning_line"])
            checks["source_sha256"] = snap["content_sha256"]
            snap = snapshot(raw, mailbox)
    now, ident = H.now(), H.new_id()
    row = {
        "id": ident,
        "mailbox": mailbox,
        "actor": actor,
        "bot": bot,
        "via": "automation:" + body.automation_action_id
        if body.automation_action_id
        else "calendar:" + body.calendar_action_id
        if body.calendar_action_id
        else via,
        "automation_action_id": body.automation_action_id or None,
        "calendar_action_id": body.calendar_action_id or None,
        "content_sha256": snap["content_sha256"],
        "idempotency_key": key,
        "operation_key": operation_key,
        "recipients": dump(snap["recipients"]),
        "attachments": dump(snap["attachments"]),
        "subject": snap["subject"],
        "body_sha256": snap["body_sha256"],
        "raw": raw,
        # Stored only when Gmail read the draft or Tico named it: what a revision is found by (`revised`).
        "draft_id": draft_id,
        "thread_id": thread_id,
        "reply_to_id": reply_id,
        # An OK is asked for this snapshot once it is written (`request_approval`): nothing named in the request is
        # one. `body.approval_id` is only ever a person's message the second reviewer reads (`review_context`).
        "approval_id": "",
        "task_id": task_id,
        "approval_task_id": None,
        "classification": dump(checks),
        "decision": "{}",
        "state": "pending",
        "created_at": now,
        "send_after": H.shift(now, seconds=effective(c, mailbox, bot)["undo_seconds"]),
        "detail": "",
    }
    found = decision(c, auth, row, wait_for_gmail=wait_for_gmail)
    if gate and gate["outcome"] == "held":
        found = {**found, "outcome": "blocked", "reasons": ["org_rule", *found["reasons"]], "held": gate.get("held")}
    row["decision"] = dump(found)
    row["state"] = {"allow": "pending", "wait": "pending", "ask": "waiting_approval", "blocked": "blocked"}[
        found["outcome"]
    ]
    if row["state"] == "blocked":
        text = (gate or {}).get("held", {}).get("text") if found.get("held") else ""
        row.update(raw="", finished_at=now, detail=text or why(found))
    OE.insert(c, row, audience=audience(c, snap["recipients"]))
    if operation_key:
        remember(c, actor, operation_key, ident, asked)
    if row["state"] == "waiting_approval" and not found["approved"]:
        # Only an OK bound to this exact snapshot releases it.
        request_approval(c, auth, row, found["policy"])
    # The draft's earlier versions go once the new one's ask is written, so its case never moves on in between.
    for older in drafts:
        retire(c, older, REPLACED)
    if classification.get("status") == "classified":
        H.event(
            c,
            actor,
            "judge.call",
            "email.sending",
            {
                "label": "email.sending",
                "questions": classification.get("questions", 1),
                **{k: v for k, v in classification.items() if k not in ("lint", "questions")},
            },
        )
    H.event(c, actor, "email.queued", ident, {"mailbox": mailbox, "state": row["state"]})
    moved(c, ident)
    return public(OE.get(c, ident))


def in_company_zone(ts):
    """A timestamp as the company's own time, with its offset: its date part is the company's day."""
    from .updates import ZONE

    return (H.parse_ts(ts) or datetime.now(timezone.utc)).astimezone(ZONE).isoformat()


def not_before(c, task_id):
    """The earliest a send for this task may go, as a timestamp: the latest not before of its matter's open key dates
    (backend/risk.py: "records may be produced no earlier than"), or "" when it has none. A send past its undo window
    (`send_after`) waits for it too, read as it stands each time the queue looks (`_lease`)."""
    if not task_id:
        return ""
    found = c.execute(
        "SELECT starts_at FROM matter_dates WHERE task_id=? AND done_at IS NULL AND starts_at IS NOT NULL", (task_id,)
    ).fetchall()
    return latest_bound(r[0] for r in found)


def latest_bound(values):
    """The latest of these matter not-before values (a day or a UTC time, backend/risk.py) as a timestamp, or ""."""
    from . import risk

    found = [risk.date_bound(v, end=False) for v in values if v]
    return max(found).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z" if found else ""


# The matter dates of a send `s`, open and with a not before, in one column: what `latest_bound` reads.
DATES_SQL = (
    "(SELECT group_concat(d.starts_at, ',') FROM matter_dates d WHERE d.task_id=s.task_id AND d.done_at IS NULL "
    "AND d.starts_at IS NOT NULL)"
)


def held_clause(now):
    """SQL true for a send `s` that its matter's not before still holds at `now`, and its arguments: a day is held
    while it is after the company's day now (its bound is its first second, risk.date_bound), a UTC time while it is
    after now. Read in the query, so a held send's row (and its MIME) is never loaded to find that out."""
    from .updates import ZONE

    at = H.parse_ts(now) or datetime.now(timezone.utc)
    clause = (
        "EXISTS (SELECT 1 FROM matter_dates d WHERE d.task_id=s.task_id AND d.done_at IS NULL "
        "AND d.starts_at > CASE WHEN length(d.starts_at)=10 THEN ? ELSE ? END)"
    )
    return clause, (
        at.astimezone(ZONE).strftime("%Y-%m-%d"),
        at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


# The files of a task (its own, and the pages linked to it, backend/page_links.py) with a version of these bytes, each
# with whether it is archived (the file, or its page).
ARCHIVED_SQL = (
    "SELECT DISTINCT f.*, max(f.archived, coalesce(p.archived, 0)) AS gone FROM bot_file_versions v "
    "JOIN bot_files f ON f.id=v.file_id LEFT JOIN pages p ON p.file_id=f.id WHERE v.digest=? "
    "AND (f.task_id=? OR f.id IN (SELECT page_id FROM page_links WHERE target=?))"
)


def with_archived(c, auth, who, row, send):
    """The send as `who` reads its preview: each frozen file that is a version of an archived file or page of its task
    (its own or linked to it), and of no file still in use there, marked `archived`, counting only the files `who` may see (files.file_visible). Its OK
    says so, so an outdated copy is not sent by mistake."""
    if not row["task_id"]:
        return send
    from . import files as task_files

    cache = {}

    def archived(sha):
        seen = [
            f
            for f in c.execute(ARCHIVED_SQL, (sha, row["task_id"], "task:" + row["task_id"])).fetchall()
            if task_files.file_visible(c, auth, who, f, cache)
        ]
        return bool(seen) and all(f["gone"] for f in seen)

    files = [{**f, "archived": True} if archived(f.get("sha256")) else f for f in send["attachments"]]
    return {**send, "attachments": files}


REPLACED = "A new version of the email replaced it"


def reads_task(c, auth, actor, task_id):
    """Whether `actor` reads this task as the API checks a caller (auth.task)."""
    from .auth import Identity

    try:
        who = auth.identity_for_actor(c, actor) if actor.startswith("human:") else Identity(actor, "bot")
        auth.task(c, who, task_id, tags=False)
    except Problem:
        return False
    return True


def request_hash(request):
    """The hash an operation id is bound to: the request as its caller sent it."""
    return digest(dump(request.model_dump()))


def receipt(c, actor, operation_key, mailbox, asked, content_sha256=None):
    """The send this operation queued, when it is this same request (`asked`, its `request_hash`), else None when the
    operation queued nothing yet. Another request under the same operation id is refused. A receipt written before
    requests were bound is matched by the content its send froze (`content_sha256`; None: not known yet)."""
    prior = c.execute(
        "SELECT s.*,o.request_sha256 FROM email_send_operations o JOIN outbound_email s ON s.id=o.send_id "
        "WHERE o.actor=? AND o.operation_key=?",
        (actor, operation_key),
    ).fetchone()
    if not prior:
        return None
    if not prior["request_sha256"] and content_sha256 is None:
        return None
    froze = (prior["content_sha256"], json.loads(prior["classification"]).get("source_sha256"))
    same = asked == prior["request_sha256"] if prior["request_sha256"] else content_sha256 in froze
    if prior["mailbox"] != mailbox or not same:
        raise Problem("operation", "This operation already froze different email content", 409)
    return prior


def remember(c, actor, operation_key, sid, request_sha256):
    """The operation's receipt: its send, and the hash of the request that queued it."""
    c.execute(
        "INSERT INTO email_send_operations(actor,operation_key,send_id,request_sha256) VALUES(?,?,?,?)",
        (actor, operation_key, sid, request_sha256),
    )


def retire(c, row, why):
    """A send still to go that will not go: canceled with `why`, its frozen copy cleared and its OK withdrawn."""
    if not OE.finish(c, row, "canceled", detail=why):
        return
    settle_automation(c, row, "canceled", why)
    if row["approval_id"]:
        task_asks.withdraw(c, row["approval_id"], why)
    H.event(c, H.TICO_ACTOR, "email.canceled", row["id"], {"mailbox": row["mailbox"], "why": why})
    moved(c, row["id"])


def task_closed(c, task_id):
    """A task was closed (task_effects.moved): each send still to go that is for it, or whose OK waits on it, is
    canceled in the same write, approved or not, never sent when its time comes. Not when the close is the OK's own
    task ending because that OK was taken back (task_asks.withdraw revokes it first): the send is asked again."""
    for row in c.execute(
        "SELECT * FROM outbound_email s WHERE queue_state IN ('waiting_approval','queued') AND (task_id=? OR (approval_task_id=? "
        "AND NOT EXISTS (SELECT 1 FROM ok_grants g WHERE g.requirement_id=s.approval_id AND g.revoked_at IS NOT NULL)))",
        (task_id, task_id),
    ).fetchall():
        retire(c, row, TASK_CLOSED)


def revised(c, mailbox, draft_id):
    """The sends this Gmail draft of this inbox froze before, still to go (waiting for an OK or in their undo window),
    newest first, whoever queued them: a revision of the draft by anyone who may send from the inbox updates the newest
    in place, new content and a new OK, and retires the rest (older data may hold several), never a second send beside
    it. Empty for an email not frozen from a draft."""
    if not draft_id:
        return []
    return c.execute(
        "SELECT * FROM outbound_email WHERE queue_state IN ('waiting_approval','queued') AND mailbox=? AND draft_id=? "
        "ORDER BY rowid DESC",
        (mailbox, draft_id),
    ).fetchall()


def turn_task(c, who):
    """The task a bot's turn works on (the run's own task, else the task its message is in), or None."""
    if not who.attempt_id:
        return None
    found = c.execute(
        "SELECT a.task_id,j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
    ).fetchone()
    if not found:
        return None
    if found["task_id"]:
        return str(found["task_id"])
    message = H.message(c, found["message_id"])
    return H.message_task_id(message, H.conversation(c, message["conversation_id"])) if message else None


def lint_findings(c, actor, mailbox, snap, reply_id):
    """The deterministic lint (backend/mail_lib/lint.py) on an email's frozen copy: {errors, warnings}, each finding
    {id, severity, message, fix}. A bot's signature is checked against its own name."""
    from .mail_lib import lint

    bot = (H.bot(c, H.actor_id(actor)) or {}) if str(actor).startswith("bot:") else {}
    groups = snap["recipient_headers"]
    domains = tuple(str(d) for d in access.company_domains(c)) or (str(mailbox).rpartition("@")[2],)
    found = lint.run(
        snap["body"],
        snap["subject"],
        groups["to"],
        [*groups["cc"], *groups["bcc"]],
        slug=bot.get("slug", ""),
        reply_to=reply_id,
        is_reply=bool(reply_id),
        internal_domains=domains,
    )
    keep = ("id", "severity", "message", "fix")
    return {
        "errors": [{k: item.get(k, "") for k in keep} for item in found["errors"]],
        "warnings": [{k: item.get(k, "") for k in keep} for item in found["warnings"]],
    }


def lint_errors(c, actor, mailbox, snap, reply_id):
    """The deterministic lint's error ids on a bot's own email, read on the frozen copy."""
    return sorted({item["id"] for item in lint_findings(c, actor, mailbox, snap, reply_id)["errors"]})


def review_context(c, auth, actor, body, mailbox, snap):
    """What the second reviewer reads beside the email: the thread it answers, read from Gmail now, and the approver's
    instruction, when the send names a person's message that may approve it. Both bounded."""
    from . import gmail_live

    # A thread is only ever resolved through Gmail (mail_facts), so a server credential covers it here. A Gmail failure
    # propagates as retryable rather than the email being reviewed against a thread the reviewer never saw.
    _reply, thread_id = resolve_reply(c, mailbox, snap, body.reply_to_id, body.thread_id)
    found = gmail_live.thread(c, mailbox, thread_id)[-5:] if thread_id else []
    rows = [
        {"from_addr": r["from"], "subject": r["subject"], "body": r["body"], "snippet": r["snippet"]} for r in found
    ]
    thread = "\n\n----\n\n".join(
        "From: " + r["from_addr"] + "\nSubject: " + r["subject"] + "\n\n" + (r["body"] or r["snippet"]) for r in rows
    )[:THREAD_CHARS]
    instruction = ""
    message = H.message(c, body.approval_id) if body.approval_id else None
    bot = H.actor_id(actor)
    if (
        message
        and message.get("recipient_id") == actor
        and can_ok(c, auth, message.get("sender_id"), mailbox, bot, effective(c, mailbox, bot))
    ):
        instruction = str(message.get("body") or "")[:INSTRUCTION_CHARS]
    return {"thread": thread, "approver_instruction": instruction}


def says_attached_without_files(snap):
    """The email's own words say files are attached or enclosed, and it carries none. Quoted lines (`>`) and an earlier
    message quoted below the reply are not its own words."""
    if snap["attachments"]:
        return False
    text = str(snap.get("body") or "")
    found = QUOTE_START.search(text)
    own = text[: found.start()] if found else text
    return any(
        SAYS_ATTACHED.search(line) and not NOT_ATTACHED.search(line)
        for line in own.splitlines()
        if not line.lstrip().startswith(">")
    )


def reviewed(found, snap, why=None):
    """The second reviewer's verdict with the words that go with it: `review_reason` is empty on a send and a sentence
    on anything else. An email that says files are attached and has none is stopped for exactly that, whatever the
    model answered; a stop without one of `STOPPED` and a review that gave no verdict say so, and what to do."""
    if says_attached_without_files(snap):
        return {**found, "review": False, "review_reason": STOPPED["missing_file"]}
    if found["review"] is True:
        return {**found, "review_reason": ""}
    if found["review"] is False:
        return {**found, "review_reason": STOPPED.get(why or "") or NO_REASON}
    return {**found, "review_reason": UNANSWERED.get(found["status"], UNANSWERED["unavailable"])}


def classify(app, store, snap, review=None):
    """One decision-model call: whether the email concerns legal or money matters and, for a bot's own email
    (`review`, its thread and approver's instruction), the second reviewer's verdict and its reason (`reviewed`).
    Unknown asks a person: this is triage, not a defense against malicious mail."""
    from .judge import audit_detail, server_engine
    from clients.judge import JudgeError

    def done(found, why=None):
        return found if review is None else reviewed(found, snap, why)

    with store.read() as c:
        engine = server_engine(store, c, app.state.judge)
    visible = snap["visible_bodies"]
    if not snap["classification_complete"] or not visible or sum(len(part["text"]) for part in visible) > 16000:
        return done({"legal_or_money": None, "review": None, "status": "incomplete_content"})
    if engine is None:
        return done({"legal_or_money": None, "review": None, "status": "unavailable"})
    questions = {
        "legal_or_money": {
            "type": "choice",
            "instructions": "Does this email concern legal or money matters?",
            "criteria": {"yes": "Legal, contracts, payments, finance or money", "no": "None of these matters"},
        }
    }
    state = {"subject": snap["subject"], "body": "\n\n".join(part["type"] + ":\n" + part["text"] for part in visible)}
    if review is not None:
        questions["review"] = {
            "type": "choice",
            "instructions": REVIEW,
            "criteria": {
                "send": "Nothing a careful reviewer would stop",
                "stop": "A commitment, an unsupported claim or a confirmed time",
            },
        }
        questions["review_reason"] = {
            "type": "choice",
            "instructions": REVIEW_WHY,
            "criteria": {**STOPPED, "none": "None of these: nothing a careful reviewer would stop"},
        }
        state.update(
            thread=review["thread"] or "(no incoming thread: a first contact)",
            attachments=[f["name"] for f in snap["attachments"]] or "(no files attached)",
            **({"approver_instruction": review["approver_instruction"]} if review["approver_instruction"] else {}),
        )
    try:
        result = engine(state, questions, "email.sending")
        answers = result.get("answers") or {}
        value = (answers.get("legal_or_money") or {}).get("choice")
        verdict = (answers.get("review") or {}).get("choice")
        return done(
            {
                "legal_or_money": value == "yes" if value in ("yes", "no") else None,
                "review": verdict == "send" if verdict in ("send", "stop") else None,
                "questions": len(questions),
                "status": "classified",
                **audit_detail(result),
            },
            (answers.get("review_reason") or {}).get("choice"),
        )
    except (JudgeError, ValueError, TypeError):
        return done({"legal_or_money": None, "review": None, "status": "unavailable"})


def _queued(c, mailbox, lease=True):
    """The sends `_lease` decides in its next pass over this inbox: the waiting ones it reconciles, and the due ones."""
    rows = c.execute(
        "SELECT * FROM outbound_email WHERE mailbox=? AND queue_state='waiting_approval' ORDER BY checked_at,rowid LIMIT 100",
        (mailbox,),
    ).fetchall()
    if lease:
        rows += _due(c, mailbox, H.now(), 20)
    return rows


def _due(c, mailbox, now, limit):
    """The sends of this inbox that may go now, oldest first: past their undo window (`send_after`) and their matter's
    not before, read as it stands now (a date moved later holds one; moved earlier or cleared, it goes at its undo
    deadline). A held send never takes the place of one that is due."""
    held, args = held_clause(now)
    return c.execute(
        "SELECT s.* FROM outbound_email s WHERE s.mailbox=? AND s.queue_state='queued' AND s.send_after<=? AND NOT "
        + held
        + " ORDER BY s.created_at,s.rowid LIMIT ?",
        (mailbox, now, *args, limit),
    ).fetchall()


def _why_asked_again(c, auth, row, bound, standing, policy):
    """Why an OK of this send is withdrawn and asked again: its giver was never shown the whole email, or the inbox's
    approval rule no longer lets them (or whoever it waits on) approve it."""
    giver = bound.get("ok_by_id") if standing == task_asks.OK else None
    if giver and can_ok(c, auth, giver, row["mailbox"], row["bot"], policy) and not seen(c, giver, row):
        return "The email was OK'd without opening it in full: open it and OK it again"
    return "The email's approval rule changed"


TASK_CLOSED = "Its task was closed"


def _lease(c, auth, mailbox, runner_id, *, permit=False, lease=True):
    """One pass over an inbox's queue inside a write: abandoned leases recovered, approvals reconciled, and the first
    due send leased to `runner_id`. `permit` (the server's own dispatch) also issues the dispatch permit in the same
    write. Returns the leased row with its lease id, or None."""
    now = H.now()
    # Only an issued permit can have reached a provider; an abandoned claim is safe to lease again.
    for stale in c.execute(
        "SELECT * FROM outbound_email WHERE mailbox=? AND queue_state='attempting' AND claimed_at<? LIMIT 100",
        (mailbox, H.shift(now, minutes=-5)),
    ).fetchall():
        if stale["provider_attempted_at"]:
            if not OE.finish(c, stale, "unknown", detail=UNCONFIRMED):
                continue
            settle_automation(c, stale, "unknown")
            _outcome(c, stale, "unknown", UNCONFIRMED)
        else:
            OE.release(c, stale)
        moved(c, stale["id"])
    # Rotate approval reconciliation independently of due sends; a waiting prefix cannot starve delivery.
    waiting = c.execute(
        "SELECT * FROM outbound_email WHERE mailbox=? AND queue_state='waiting_approval' ORDER BY checked_at,rowid LIMIT 100",
        (mailbox,),
    ).fetchall()
    due = _due(c, mailbox, now, 1000) if lease else []
    for row in [*waiting, *due]:
        case = (
            c.execute("SELECT status FROM tasks WHERE id=?", (row["task_id"],)).fetchone() if row["task_id"] else None
        )
        if case and case["status"] == "closed":
            # Its case was closed (before the close canceled its sends too): it never goes.
            retire(c, row, TASK_CLOSED)
            continue
        OE.update(c, row["id"], checked_at=now)
        found = decision(c, auth, row)
        rid = row["approval_id"]
        bound = task_asks.grant(c, rid) if rid else None
        bound = bound if bound and bound["subject"] == SUBJECT + row["id"] else None
        standing = task_asks.standing(c, rid) if bound else None
        if bound and standing == task_asks.SENT_BACK and not bound["filed"]:
            # Sent back on its case: this version never goes. It waits for the next version of its draft, whose new
            # ask replaces this one once it is written (task_asks.ask_ok), so the case never moves on in between;
            # nothing here withdraws it.
            if row["state"] != "waiting_approval":
                OE.wait_for_approval(c, row["id"], row["decision"])
                moved(c, row["id"])
            continue
        if standing in (task_asks.SENT_BACK, task_asks.WITHDRAWN):
            # Sent back on a task of its own, withdrawn, or pushed past on its task: this snapshot is not sent. A new
            # email is a new OK.
            reason = "approval_declined" if standing == task_asks.SENT_BACK else "approval_withdrawn"
            found["outcome"], found["reasons"] = "blocked", [reason]
        if found["outcome"] == "blocked":
            if not OE.finish(c, row, "blocked", detail=why(found), decision=dump(found), extend_window=True):
                continue
            settle_automation(c, row, "blocked", why(found))
            if bound:
                task_asks.withdraw(c, rid, "The email was not sent: " + why(found))
            moved(c, row["id"])
            continue
        if found["outcome"] == "ask":
            OE.wait_for_approval(c, row["id"], dump(found))
            if row["state"] != "waiting_approval":
                moved(c, row["id"])
            # An OK that no longer lets it go (its giver may not approve it now), or an ask whose person may not, is
            # asked again of someone who may: task_asks.ask_ok withdraws the old one.
            asked_of = _asked_of(c, rid) if standing == task_asks.WAITING else None
            stale = standing == task_asks.OK or (
                standing == task_asks.WAITING
                and not can_ok(c, auth, asked_of, row["mailbox"], row["bot"], found["policy"])
            )
            if not bound or stale:
                # The new ask is written first, then the old one withdrawn (task_asks.ask_ok, `renew`): the case never
                # moves on between the two.
                why_again = _why_asked_again(c, auth, row, bound, standing, found["policy"]) if bound else ""
                request_approval(c, auth, row, found["policy"], why_again or "Asked again", renew=bool(bound))
            continue
        if row["state"] == "waiting_approval":
            OE.queue(c, row["id"], H.shift(now, seconds=found["policy"]["undo_seconds"]), dump(found))
            moved(c, row["id"])
            continue
        if max(row["send_after"], H.shift(row["created_at"], seconds=found["policy"]["undo_seconds"])) > now:
            continue
        found = decision(c, auth, row, dispatch=True)
        if found["outcome"] != "allow":
            OE.update(c, row["id"], decision=dump(found), detail=why(found))
            continue
        token = OE.claim(c, row, now, runner_id, dump(found), audience(c, json.loads(row["recipients"])))
        if not token:
            continue
        leased = OE.get(c, row["id"])
        if permit and not _permit(c, leased, found):
            OE.release(c, leased)
            continue
        moved(c, row["id"])
        return leased
    return None


def _asked_of(c, rid):
    """Who an OK of a send waits on now: the person named, or the one it was given to."""
    from . import task_requirements as R

    requirement = R.get(c, rid) if rid else None
    return R.person_of(c, requirement, requirement["visit_id"]) if requirement and requirement.get("visit_id") else None


def _permit(c, row, found):
    """The one write after which a provider may have the email: a lost answer from here on is unknown. The OK that let
    it go is used in this same write, so it lets nothing else go."""
    if not OE.permit(c, row, dump(found)):
        return False
    if found["approved"]:
        # Claimed here, before the provider is called; its task ends with the provider's answer (`_finish`).
        task_asks.use(c, row["approval_id"], SUBJECT + row["id"], content(row), row["actor"])
    H.event(c, row["actor"], "email.dispatch", row["id"], {"mailbox": row["mailbox"]})
    return True


UNCONFIRMED = "The connector did not confirm delivery"


def _outcome(c, row, status, detail=""):
    """What the provider's answer means beyond the send: the OK's task ends done only when the email went, closed
    with what happened otherwise (the OK stays used: nothing else goes under it), and a Finance email reports to its
    invoice (finance/delivery.py `outcome`)."""
    rid = row["approval_id"]
    if rid and status == "sent":
        task_asks.settle(c, rid, "Sent the email.")
    elif rid:
        words = "Not sent: " + (detail or "the mail service refused it") if status == "failed" else UNCONFIRMED
        task_asks.not_done(c, rid, words[:300] + ". Nothing else goes out under this OK.")
    if row["via"] == "finance":
        from .finance import delivery as finance_delivery

        finance_delivery.outcome(c, dict(row), status, detail)


def _outside_send_thread(c, row, thread_id):
    from . import outside_intake

    if not thread_id or (row["thread_id"] and thread_id != row["thread_id"]):
        return None
    box = access.row_by_address(c, row["mailbox"])
    if not box or not access.feeds_directory(c, box):
        return None
    connection = box["id"]
    thread = H.conversation(c, outside_intake.conversation_id("gmail", connection, thread_id))
    if (
        not thread
        or thread["connection_id"] != connection
        or thread["parent_id"]
        or thread["deleted_at"]
        or not thread["entity_id"]
    ):
        return None
    return thread


def _record_outside_send(c, row, message, thread_id) -> str | None:
    thread = _outside_send_thread(c, row, thread_id)
    if not thread:
        return None
    connection = thread["connection_id"]
    if c.execute(
        "SELECT 1 FROM messages WHERE connection_id=? AND source_message_id=?", (connection, message)
    ).fetchone():
        return None
    receipt = H._write_message(
        c,
        row["actor"],
        None,
        "Sent an email in Gmail.",
        thread,
        "say",
        {},
        None,
        None,
        outside=("gmail", connection, message),
        outside_direction="outbound",
    )
    return receipt["id"]


def _finish(c, row, status, message="", detail="", *, thread_id="") -> str | None:
    if row["state"] == "sent" or not OE.finish(
        c, row, status, message=message, detail=detail, thread_id=thread_id, extend_window=True
    ):
        return None
    receipt_id = _record_outside_send(c, row, message, thread_id) if status == "sent" and thread_id else None
    moved(c, row["id"])
    H.event(c, row["actor"], "email." + status, row["id"], {"mailbox": row["mailbox"], "provider_message_id": message})
    settle_automation(c, row, status, detail, message)
    _outcome(c, row, status, detail)
    return receipt_id


_CUTOVER_ROWS = (
    "FROM outbound_actions INDEXED BY outbound_email_cutover "
    "WHERE channel='email' AND json_extract(metadata,'$.cutover_unconfirmed')=1 ORDER BY id"
)
_CUTOVER_EXISTS = f"SELECT 1 {_CUTOVER_ROWS} LIMIT 1"
_CUTOVER_BATCH = f"SELECT * FROM outbound_email WHERE id IN (SELECT id {_CUTOVER_ROWS} LIMIT 100) ORDER BY id"


def _report_cutover(store):
    """Finish the approval and automation bookkeeping for old sends whose provider answer was lost."""
    with store.read() as c:
        if not c.execute(_CUTOVER_EXISTS).fetchone():
            return
    with store.transaction(durable=True, notify=True) as c:
        rows = c.execute(_CUTOVER_BATCH).fetchall()
        for row in rows:
            OE.update(c, row["id"], cutover_unconfirmed=None)
            settle_automation(c, row, "unknown", row["detail"])
            rid = row["approval_id"]
            if rid and task_asks.standing(c, rid) != task_asks.USED:
                task_asks.withdraw(c, rid, row["detail"])
            _outcome(c, row, "unknown", row["detail"])
            H.event(c, row["actor"], "email.unknown", row["id"], {"mailbox": row["mailbox"]})
            moved(c, row["id"])


# inbox -> when the drain last reached it (monotonic): the inbox waiting longest goes first. One small entry per
# Workspace inbox the process has sent from, so it is never pruned; it only orders inboxes, never decides a send.
_SERVED = {}


def drain(store, auth, limit=20, budget=None):
    """The server's own dispatch, on its drain worker (backend/drains.py): each inbox the server holds a credential for
    (the Workspace key, or a person's own grant) with queued mail is reconciled, and its due sends go to Gmail one at a
    time (pacing usually allows one a pass). A token is taken before any permit, so a credential that fails leaves the
    email pending, never unknown; a grant that can only read fails its due emails, which it can never send. Gmail is
    called under no write lock. With a `budget`, nothing new is leased once it is spent; the inboxes not reached go
    first in the next pass."""
    import time

    from . import drains, gmail_live, google_tokens, task_engine

    if store.settings.rehearsal or store.settings.demo:
        return 0
    _report_cutover(store)
    with store.read() as c:
        boxes = [
            r[0]
            for r in c.execute(
                "SELECT DISTINCT mailbox FROM outbound_email WHERE queue_state IN ('waiting_approval','queued','attempting')"
            ).fetchall()
        ]
    boxes.sort(key=lambda box: _SERVED.get(box, 0.0))
    sent = 0
    for mailbox in boxes:
        if not drains.left(budget):
            break
        _SERVED[mailbox] = time.monotonic()
        token = refused = None
        with store.read() as c:
            if _due(c, mailbox, H.now(), 1):
                try:
                    token = google_tokens.token(c, mailbox, "gmail", write=True)
                except Problem as problem:
                    # A failing key or a grant Google refused: the email waits; Tools > Settings > Email says why.
                    # A grant that can only read, or no credential at all, can never send it: it fails.
                    gone = problem.code == "not_connected" and not google_tokens.covered(c, mailbox)
                    refused = problem if problem.code == "read_only" or gone else None
        if refused is not None:
            with store.transaction(durable=True, notify=True) as c:
                for row in _due(c, mailbox, H.now(), limit):
                    _finish(c, row, "failed", "", refused.detail)
            continue
        try:
            with store.read() as c:
                for queued in _queued(c, mailbox, lease=token is not None):
                    warm(c, queued)
                # Nothing to lease, nothing waiting on an OK and nothing being handed over (only sends held for later):
                # no write this pass.
                idle = (
                    token is None
                    and not c.execute(
                        "SELECT 1 FROM outbound_email WHERE mailbox=? AND queue_state IN ('waiting_approval','attempting') LIMIT 1",
                        (mailbox,),
                    ).fetchone()
                )
        except Problem as unread:
            # Gmail could not tell a decision what it needs: this inbox's mail waits, as it was, for a later pass.
            LOG.info("email drain left an inbox for later: %s", unread.code)
            continue
        if idle:
            continue
        for _ in range(limit):
            if not drains.left(budget):
                break  # the rest waits for the next pass
            try:
                with store.transaction(durable=True, notify=True) as c:
                    row = _lease(c, auth, mailbox, SERVER, permit=True, lease=token is not None)
            except Problem as unread:
                LOG.info("email drain left an inbox for later: %s", unread.code)
                break
            if not row:
                break
            status, message, detail, thread_id = gmail_live.send(mailbox, token, row["raw"], row["thread_id"])
            receipt_id = None
            with store.transaction(durable=True, notify=True) as c:
                current = OE.get(c, row["id"])
                if current and current["state"] == "dispatching" and current["lease_id"] == row["lease_id"]:
                    receipt_id = _finish(c, current, status, message, detail, thread_id=thread_id)
            # An interrupted metadata read leaves the confirmed send and its receipt saved.
            if receipt_id:
                provider_timestamp_ms = gmail_live.sent_timestamp_ms(mailbox, token, message, thread_id)
                if provider_timestamp_ms is not None:
                    with store.transaction(notify=True) as c:
                        receipt = c.execute(
                            "UPDATE messages SET refs=json_set(refs,'$.provider_timestamp_ms',?) "
                            "WHERE id=? AND deleted_at IS NULL RETURNING conversation_id, seq",
                            (provider_timestamp_ms, receipt_id),
                        ).fetchone()
                        if receipt:
                            task_engine.customer_receipt_timestamped(c, receipt["conversation_id"], receipt["seq"])
            sent += status == "sent"
    return sent


def _raced(c):
    """The rule gate of an email first found already queued: reached only when the write would queue it after all."""
    raise Problem("changed", "This email changed while it was checked. Send it again.", 409, retryable=True)


def rule_gate(app, store, who, snap):
    """The org rule checks of a bot's own email, before its write: the in-write half for `enqueue`, or None when the
    sender is not a bot writing (a person's own mail is not checked). The Directory's structured rules and the judged
    check meet the mail in one verdict (org_rule_check.mail_prepare). A structured rule holds the mail on the same held
    path as a judged one, and a check that cannot complete holds it too (fail closed). The judged check fails open, as
    it always has: an error is a `rules.unchecked` outcome, never a lost send. When the judged check cannot be prepared
    at all, the structured rules still decide the mail inside the write."""
    from . import org_rule_check as rules

    found = rules.checked_as(who, store.settings.assistant_bot)
    if not found or not H.is_bot(found[1]):
        return None
    groups = snap["recipient_headers"]
    to, cc = groups["to"], [*groups["cc"], *groups["bcc"]]
    addresses = [*groups["to"], *groups["cc"], *groups["bcc"]]
    try:
        prep = rules.mail_prepare(
            store,
            app.state.judge,
            who,
            found[0],
            to,
            cc,
            snap["subject"],
            snap["body"],
            contacts=addresses,
        )
    except Exception as exc:  # noqa: BLE001  (the judged check fails open, as every judged check does)
        LOG.warning("The org rule check of an email failed: %s", type(exc).__name__)
        prep = None

    def gate(c):
        if prep is None:
            result = rules.mail_unprepared(c, found, to, cc, snap["subject"], snap["body"], addresses)
        else:
            result = rules.mail_decide(c, prep)
        # The email link's one outcome event (backend/rule_links.py), in the mail's write: a held mail is not sent.
        rule_links.record(c, rule_links.mailed(found, result), stopped=True)
        return result

    return gate


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
        for key, block in (
            ("blocked_recipients", pol.get("blocklist")),
            ("owner_handles", pol.get("owner_handles_personally")),
        ):
            extra = sorted(set(wanted(block)) - set(company.get(key, [])))
            if extra:
                changes[key] = sorted(set(company.get(key, [])) | set(extra))
        if mapping(pol.get("global")).get("send_enabled") is not True:
            changes["mode"] = "never"  # the file turned sending off unless it said otherwise
        caps = mapping(pol.get("defaults"))
        for key, source in (("daily_cap", "max_sends_per_day"), ("max_outside_recipients", "max_external_recipients")):
            value = caps.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < company.get(key, DEFAULTS[key]):
                changes[key] = value
        if changes:
            c.execute(
                "INSERT INTO email_sending_policies VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                "settings=excluded.settings,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                ("company", dump({**company, **changes}), H.now(), "registry"),
            )
        for mailbox, settings in mapping(pol.get("mailboxes")).items():
            if mapping(settings).get("paused") is True:
                scope = "inbox:" + str(mailbox).strip().lower()
                inbox = {k: v for k, v in saved(c, scope).items() if k in DEFAULTS}
                c.execute(
                    "INSERT INTO email_sending_policies VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                    "settings=excluded.settings,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                    (scope, dump({**inbox, "mode": "never"}), H.now(), "registry"),
                )
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

    def can_cancel(c, who, row):
        if who.actor == row["actor"] or (
            row["bot"] and who.role in ("owner", "human") and auth.bot_manager(c, who, row["bot"])
        ):
            return True
        try:
            access._manager(c, who, access._require_row(c, access._row(c, row["mailbox"])["id"]), "cancel sends")
            return True
        except (Problem, TypeError):
            return False

    @app.get(
        "/api/v2/email/sending-policy",
        **api_v2("Email sending", "getEmailSendingPolicy", "Company sending policy", "EmailSendingPolicy"),
    )
    def read_company(request: Request):
        company(request.state.identity)
        with store.read() as c:
            return policy_view(c)

    @app.put(
        "/api/v2/email/sending-policy",
        **api_v2(
            "Email sending",
            "setEmailSendingPolicy",
            "Set company sending policy (an admin, acting as themselves)",
            "EmailSendingPolicy",
        ),
    )
    def write_company(request: Request, body: PolicyUpdate):
        company(request.state.identity, write=True)
        return mutate(
            request,
            body,
            lambda c: (update_policy(c, request.state.identity, "company", body), policy_view(c))[1],
            durable=True,
        )

    @app.get(
        "/api/v2/email/inboxes/{mailbox}/sending-policy",
        **api_v2(
            "Email sending",
            "getInboxSendingPolicy",
            "Inbox policy with effective rule and inheritance",
            "EmailSendingPolicy",
        ),
    )
    def read_inbox(request: Request, mailbox: str):
        with store.read() as c:
            return policy_view(
                c,
                inbox(c, mailbox, request.state.identity),
                H.actor_id(request.state.identity.actor) if request.state.identity.actor.startswith("bot:") else "",
            )

    @app.put(
        "/api/v2/email/inboxes/{mailbox}/sending-policy",
        **api_v2(
            "Email sending",
            "setInboxSendingPolicy",
            "Set inbox policy (its person or a shared inbox admin)",
            "EmailSendingPolicy",
        ),
    )
    def write_inbox(request: Request, mailbox: str, body: PolicyUpdate):
        def work(c):
            address = inbox(c, mailbox, request.state.identity, manage=True)
            update_policy(c, request.state.identity, "inbox:" + address, body)
            return policy_view(c, address)

        return mutate(request, body, work, durable=True)

    @app.get(
        "/api/v2/bots/{bot}/email-sending-policy",
        **api_v2(
            "Email sending",
            "getBotEmailSendingPolicy",
            "Bot policy, optionally resolved for an inbox",
            "EmailSendingPolicy",
        ),
    )
    def read_bot(request: Request, bot: str, mailbox: str = ""):
        with store.read() as c:
            ref = bot_ref(c, request.state.identity, bot)
            if mailbox and not access.may(c, "bot:" + ref, mailbox)["read"]:
                raise Problem("forbidden", "The bot cannot use this inbox", 403)
            return policy_view(c, mailbox.strip().lower(), ref)

    @app.put(
        "/api/v2/bots/{bot}/email-sending-policy",
        **api_v2(
            "Email sending",
            "setBotEmailSendingPolicy",
            "Set bot override (a person who manages it)",
            "EmailSendingPolicy",
        ),
    )
    def write_bot(request: Request, bot: str, body: PolicyUpdate):
        def work(c):
            ref = bot_ref(c, request.state.identity, bot, write=True)
            update_policy(c, request.state.identity, "bot:" + ref, body)
            return policy_view(c, bot=ref)

        return mutate(request, body, work, durable=True)

    def frozen_draft(who, body):
        """The request with the draft's MIME and thread as Gmail holds them now: what is sent is what was frozen here,
        whatever the draft says later. The caller must be able to read the mailbox to read its draft. The draft is named
        by its draft id or by its message's id; one Gmail no longer has is said so, with where to find the drafts."""
        from . import gmail_live

        mailbox = body.mailbox.strip().lower()
        with store.read() as c:
            if not access.may(c, who, mailbox)["read"]:
                raise Problem("forbidden", "The sender cannot use this inbox", 403)
            named = gmail_live.draft_resolve(c, mailbox, body.draft_id)
            if not named:
                raise Problem(
                    "not_found",
                    f"Draft {body.draft_id} no longer exists in {mailbox}: it was sent, discarded or replaced. "
                    f"`tico email drafts {mailbox}` lists its drafts",
                    404,
                )
            found = gmail_live.draft_raw(c, mailbox, named["id"])
        # The version the caller saw: named by its message id, or `draft_message`. A draft changed since is refused,
        # never frozen with words or recipients the caller did not read.
        expected = body.draft_message or (body.draft_id if body.draft_id == named["message"] else "")
        if expected and found["message"] and expected != found["message"]:
            raise Problem(
                "draft_changed",
                f"Draft {named['id']} in {mailbox} changed since you read it (its message is now {found['message']}). "
                f"Read it again with `tico email draft-show {mailbox} {named['id']}` before sending it",
                409,
                extra={"draft": named["id"], "message": found["message"]},
            )
        body = body.model_copy(update={"draft_id": named["id"]})
        if len(found["raw"]) > MAX_RAW * 4 // 3 + 8:
            raise Problem("raw", "This draft is too large to send through Tico; send it from Gmail", 413)
        # Gmail files every draft in a thread, a new email in a thread of its own: only a draft that answers a message
        # (In-Reply-To or References) is sent in its thread.
        snap = snapshot(found["raw"], mailbox)
        answers = snap["in_reply_to"] or snap["references"]
        return body.model_copy(
            update={"raw": found["raw"], "thread_id": body.thread_id or (found["thread"] if answers else "")}
        )

    @app.post(
        "/api/v2/email/sends",
        **api_v2(
            "Email sending",
            "queueEmail",
            "Freeze email content for approval and the undo window; identical content and recipients deduplicate",
            "EmailSendResult",
        ),
    )
    def queue(request: Request, body: Enqueue):
        who = request.state.identity
        # The receipt is bound to the request as the caller sent it, not to the draft's MIME read from Gmail: a retry
        # after a lost answer replays the send it queued (after the access checks), before anything reads Gmail, so
        # it holds even when the draft was edited or discarded since, or Gmail is down.
        asked = body
        with store.read() as c:
            if store.saved(c, who, request.url.path, request.headers.get("idempotency-key")):
                if not access.may(c, who.actor, body.mailbox.strip().lower())["send"]:
                    raise Problem("forbidden", "The sender cannot send from this inbox", 403)

                def unchecked(c):
                    # Only a replay skips the checks; a receipt gone since it was seen is never queued unchecked.
                    raise Problem("receipt_expired", "This send's receipt expired; retry with a new operation id", 409)

                return mutate(request, asked, unchecked, durable=True)
        operation = body.operation_id or request.headers.get("Idempotency-Key", "")
        mailbox = body.mailbox.strip().lower()
        if operation:
            # A retry of an operation finds its send before anything reads Gmail (its draft may have changed or gone
            # since), through the normal write with today's checks: the inbox, and the task it names.
            with store.read() as c:
                done = receipt(c, who.actor, operation, mailbox, request_hash(asked))
            if done:

                def replay(c):
                    if not access.may(c, who.actor, mailbox)["send"]:
                        raise Problem("forbidden", "The sender cannot send from this inbox", 403)
                    found = receipt(c, who.actor, operation, mailbox, request_hash(asked)) or done
                    if found["task_id"]:
                        auth.task(c, who, found["task_id"], tags=False)  # the task of the send it returns
                    return {"send": public(found)}

                return mutate(request, asked, replay, durable=True)
        if not body.raw:
            if not body.draft_id:
                raise Problem("raw", "Send the frozen MIME (raw) or name a Gmail draft (draft_id)", 422)
            body = frozen_draft(who, body)
        with store.read() as c:
            if body.calendar_action_id or body.automation_action_id:
                # Tico books calendars and runs automations on the server; it enqueues those emails itself.
                raise Problem("forbidden", "Only the server sends a calendar or automation email", 403)
            actor = who.actor
            if not access.may(c, actor, body.mailbox.strip().lower())["send"]:
                raise Problem("forbidden", "The sender cannot send from this inbox", 403)
            if body.task_id:
                task_id = auth.resolve_task(c, who, body.task_id)
                auth.task(c, who, task_id, tags=False)
            else:
                task_id = turn_task(c, who)
            body = body.model_copy(update={"task_id": task_id or ""})
            mailbox = body.mailbox.strip().lower()
            snap = snapshot(body.raw, mailbox)
            # What deciding it inside the write will ask Gmail, asked here, under no write lock.
            reply_id, _thread = resolve_reply(c, mailbox, snap, body.reply_to_id, body.thread_id)
            warm(
                c,
                {
                    "mailbox": mailbox,
                    "bot": H.actor_id(actor) if actor.startswith("bot:") else "",
                    "reply_to_id": reply_id,
                    "recipients": dump(snap["recipients"]),
                },
            )
            # A bot's own email (not one Tico composed for an automation or the calendar) is linted and reviewed.
            writing = actor.startswith("bot:") and not body.automation_action_id and not body.calendar_action_id
            review = review_context(c, auth, actor, body, mailbox, snap) if writing else None
            lint = (
                lint_errors(
                    c, actor, mailbox, snap, resolve_reply(c, mailbox, snap, body.reply_to_id, body.thread_id)[0]
                )
                if writing
                else []
            )
        classification = {**classify(app, store, snap, review), "lint": lint}
        # Every bot's writing, the Assistant's for its person included, meets the org rules (`rule_gate` answers None
        # for a person's own mail); Tico's own automation and calendar email does not.
        composed = body.automation_action_id or body.calendar_action_id
        operation_key = body.operation_id or request.headers.get("Idempotency-Key", "")
        idem = request.headers.get("Idempotency-Key") or ""
        with store.read() as c:
            # No rule check is spent on an email the write will not queue: no usable Idempotency-Key (the write refuses
            # it), or one `enqueue` answers with a send it has or refuses (`duplicate`). The write decides again, and an
            # email it would queue after all is refused for a retry (`_raced`).
            again = rule_links.is_agent(store, who) and (
                not idem
                or len(idem) > 200
                or duplicate(c, auth, actor, body, operation_key, asked, draft_verified=not asked.raw, snap=snap)
            )
        rules = None if composed else _raced if again else rule_gate(app, store, who, snap)
        via = who.via or ("agent:" + who.agent if who.agent else "")
        return mutate(
            request,
            asked,
            lambda c: {
                "send": enqueue(
                    c,
                    auth,
                    actor,
                    body,
                    classification,
                    via=via,
                    rules=rules,
                    operation_key=operation_key,
                    request=asked,
                    # Gmail read the draft named only when no `raw` came with it (`frozen_draft`).
                    draft_verified=not asked.raw,
                )
            },
            durable=True,
        )

    @app.get(
        "/api/v2/email/sends",
        **api_v2(
            "Email sending",
            "listEmailSends",
            "Pending sends or the inbox send audit, paged and byte capped",
            "EmailSendList",
        ),
    )
    def sends(request: Request, mailbox: str, pending: bool = False, cursor: str = "", limit: int = 50):
        with store.read() as c:
            address = inbox(c, mailbox, request.state.identity)
            sql, args = "SELECT * FROM outbound_email WHERE mailbox=?", [address]
            if pending:
                sql += " AND queue_state IN ('queued','waiting_approval')"
            if cursor:
                sql += " AND (created_at,id)<(SELECT created_at,id FROM outbound_email WHERE id=?)"
                args.append(cursor)
            cap = min(100, max(1, limit))
            rows = c.execute(sql + " ORDER BY created_at DESC,id DESC LIMIT ?", (*args, cap + 1)).fetchall()
            out, spent = [], 0
            for row in rows[:cap]:
                item = public(row)
                item["can_cancel"] = row["state"] in ("pending", "waiting_approval") and can_cancel(
                    c, request.state.identity, row
                )
                size = len(dump(item).encode())
                if out and spent + size > LIST_BYTES:
                    break
                out.append(item)
                spent += size
            more = len(out) < len(rows)
            return {"sends": out, "next_cursor": out[-1]["id"] if more and out else None}

    def reads(c, who, task_id):
        """Whether `who` reads this task (auth.task): its matter's dates are said only to its readers."""
        try:
            return bool(task_id) and bool(auth.task(c, who, task_id, tags=False))
        except Problem:
            return False

    def preview_row(c, request, sid):
        row = OE.get(c, sid)
        if not row:
            raise Problem("not_found", "Send not found", 404)
        if request.state.identity.actor != row["actor"] and not can_ok(
            c, auth, request.state.identity.actor, row["mailbox"], row["bot"], effective(c, row["mailbox"], row["bot"])
        ):
            inbox(c, row["mailbox"], request.state.identity)
        return row

    @app.get(
        "/api/v2/email/sends/{sid}",
        **api_v2(
            "Email sending",
            "getEmailSend",
            "Frozen preview and audit (inbox readers, the sender or an authorized approver)",
            "EmailSendDetail",
        ),
    )
    def send_detail(request: Request, sid: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        who = request.state.identity
        with store.transaction(durable=True) as c:
            row = preview_row(c, request, sid)
            snap = snapshot(row["raw"], row["mailbox"]) if row["raw"] else None
            shown = shown_to(c, who, row) if snap else None
            # Who each address is to, as its frozen headers say (the stored `recipients` is all of them in one list);
            # empty once the frozen copy is gone, as `body` is.
            groups = (snap or {}).get("recipient_headers") or {}
            held = (
                not_before(c, row["task_id"]) if row["state"] in OPEN_STATES and reads(c, who, row["task_id"]) else ""
            )
            # Its undo deadline: set once it may go, else what an OK now would set.
            undo = effective(c, row["mailbox"], row["bot"])["undo_seconds"]
            due = row["send_after"] if row["state"] != "waiting_approval" else H.shift(H.now(), seconds=undo)
            return {
                "send": with_archived(c, auth, who, row, public(row)),
                # The later day a send still to go is held for (its matter's not before past its undo deadline), or
                # null.
                "not_before": in_company_zone(held) if held > max(due, H.now()) else None,
                "body": (snap or {}).get("body", ""),
                "bodies": (snap or {}).get("visible_bodies", []),
                "preview_complete": bool(snap),
                "to": groups.get("to", []),
                "cc": groups.get("cc", []),
                "bcc": groups.get("bcc", []),
                # What the OK button sends back: POST /api/v2/tasks/{task}/asks/{approval_id}/ok {"shown_hash"}.
                "shown_hash": shown,
            }

    @app.get(
        "/api/v2/email/sends/{sid}/attachments/{part}",
        **api_v2(
            "Email sending",
            "getFrozenEmailAttachment",
            "Download the exact frozen attachment (the send's readers or approvers)",
        ),
    )
    def send_attachment(request: Request, sid: str, part: int):
        with store.read() as c:
            row = preview_row(c, request, sid)
            file = next((file for file in json.loads(row["attachments"]) if file["part"] == part), None)
            if not row["raw"] or not file:
                raise Problem("not_found", "The frozen file is no longer available", 404)
            data = attachment(row["raw"], part)
            return Response(
                data,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file["name"], safe=""),
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "sandbox",
                    "Cache-Control": "no-store",
                },
            )

    @app.get(
        "/api/v2/email/inboxes/{mailbox}/suppressed-recipients",
        **api_v2(
            "Email sending", "listEmailSuppressions", "Inbox admin's bounce suppression list", "EmailSuppressionList"
        ),
    )
    def suppressed(request: Request, mailbox: str, cursor: str = "", limit: int = 50):
        with store.read() as c:
            address = inbox(c, mailbox, request.state.identity, manage=True)
            rows = c.execute(
                "SELECT address,reason,created_at FROM email_suppressed_recipients WHERE mailbox=? "
                "AND address>? ORDER BY address LIMIT ?",
                (address, cursor, min(100, max(1, limit)) + 1),
            ).fetchall()
            cap = min(100, max(1, limit))
            return {
                "recipients": [dict(row) for row in rows[:cap]],
                "next_cursor": rows[cap - 1]["address"] if len(rows) > cap else None,
            }

    @app.delete(
        "/api/v2/email/inboxes/{mailbox}/suppressed-recipients/{address}",
        **api_v2("Email sending", "removeEmailSuppression", "Restore sending to a bounced address (inbox admin)"),
    )
    def unsuppress(request: Request, mailbox: str, address: str):
        def work(c):
            box = inbox(c, mailbox, request.state.identity, manage=True)
            c.execute("DELETE FROM email_suppressed_recipients WHERE mailbox=? AND address=?", (box, address.lower()))
            H.event(c, request.state.identity.actor, "email.unsuppressed", box, {"address": address.lower()})
            return {"ok": True}

        return mutate(request, Empty(), work, durable=True)

    @app.post(
        "/api/v2/email/sends/{sid}/cancel",
        **api_v2(
            "Email sending",
            "cancelEmailSend",
            "Cancel before dispatch; delivered mail cannot be recalled",
            "EmailSendResult",
        ),
    )
    def cancel(request: Request, sid: str):
        def kept(row):
            # Canceling never touches Gmail: the draft a send was frozen from stays in Drafts, to edit, send or discard.
            if not row["draft_id"]:
                return {}
            return {
                "draft": {
                    "id": row["draft_id"],
                    "kept": True,
                    "detail": f"The Gmail draft {row['draft_id']} is kept in {row['mailbox']}'s Drafts; "
                    f"`tico email discard {row['mailbox']} {row['draft_id']}` discards it",
                }
            }

        def work(c):
            row = OE.get(c, sid)
            if not row or not can_cancel(c, request.state.identity, row):
                raise Problem("forbidden", "Only the sender, its manager or an inbox admin cancels a send", 403)
            if row["state"] == "canceled":
                return {"send": public(row), **kept(row)}
            if row["state"] not in ("pending", "waiting_approval"):
                raise Problem("state", "This email can no longer be canceled; delivered email cannot be recalled", 409)
            OE.finish(c, row, "canceled", detail=row["detail"])
            moved(c, sid)
            settle_automation(c, row, "canceled")
            if row["approval_id"]:
                task_asks.withdraw(c, row["approval_id"], "The email was canceled.")
            H.event(c, request.state.identity.actor, "email.canceled", sid, {})
            return {"send": public(OE.get(c, sid)), **kept(row)}

        return mutate(request, Empty(), work, durable=True)
