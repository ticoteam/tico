"""Action kinds (spec §5): which are live, the right each needs, the approval kind each would ask under, and the
phase-0 default gates that stand in for gate rules (§6.4) until the rules engine arrives."""
from collections.abc import Collection
from dataclasses import dataclass

from . import rights as R
from .org import SYSTEM, Org

# Every §5 kind, plus right_grant / right_revoke, which §7.4 needs and §5 does not list.
ALL = ("categorize", "journal", "reconcile", "close_period", "reopen_period",
       "bill_create", "bill_update", "bill_approve", "bill_schedule", "bill_mark_paid", "bill_void",
       "invoice_create", "invoice_send", "invoice_record_payment", "invoice_write_off", "invoice_void",
       "payment_create", "payment_cancel", "transfer_create", "recipient_add", "card_create", "card_update_limit", "card_freeze",
       "card_unfreeze", "card_cancel", "card_resolve", "reconcile_undo", "connection_create", "payment_settings",
       "budget_create", "budget_update", "allocation_set", "projection_set",
       "view_grant", "view_revoke", "private_lines_set",
       "rule_create", "rule_update", "rule_disable", "rule_enable", "rule_test", "contact_create", "contact_update",
       "account_create", "account_update", "right_grant", "right_revoke",
       # not in §5: the Finance tab's Access and Sources pages (§12), attaching receipts and documents (§8.4), and
       # linking a bank row to payments marked paid when the automatic match cannot tell which (feed_match)
       "view_defaults_set", "connection_sync", "connection_disconnect", "connection_map", "attach",
       "feed_match")
LIVE = ("journal", "categorize", "account_create", "account_update", "budget_create", "budget_update",
        "allocation_set", "projection_set", "view_grant", "view_revoke", "private_lines_set", "close_period",
        "reopen_period", "right_grant", "right_revoke",
        # phase 1: banks, cards, payments, reconciliation and gate rules
        "reconcile", "reconcile_undo", "connection_create", "payment_settings", "card_create", "card_update_limit",
        "card_freeze", "card_unfreeze", "card_cancel", "card_resolve", "recipient_add", "payment_create",
        "payment_cancel", "transfer_create",
        "rule_create", "rule_update", "rule_enable", "rule_disable", "rule_test",
        "view_defaults_set", "connection_sync", "connection_disconnect", "connection_map",
        # phase 2: contacts, bills, invoices and attachments
        "contact_create", "contact_update", "bill_create", "bill_update", "bill_approve", "bill_schedule",
        "bill_mark_paid", "bill_void", "invoice_create", "invoice_send", "invoice_record_payment",
        "invoice_write_off", "invoice_void", "attach", "feed_match")
NEEDS: dict[str, str | tuple[str, ...]] = {
         "journal": R.BOOK, "categorize": R.BOOK, "account_create": R.BOOK, "account_update": R.BOOK,
         "close_period": R.BOOK, "reopen_period": R.BOOK, "budget_create": R.BUDGETS, "budget_update": R.BUDGETS,
         "allocation_set": R.BUDGETS, "projection_set": R.BUDGETS, "reconcile": R.BOOK, "reconcile_undo": R.BOOK,
         "card_create": R.CARDS, "card_update_limit": R.CARDS, "card_freeze": R.CARDS, "card_unfreeze": R.CARDS,
         "card_cancel": R.CARDS, "card_resolve": R.CARDS, "recipient_add": R.PAY, "payment_create": R.PAY,
         "payment_cancel": R.PAY, "transfer_create": R.PAY,
         "rule_create": R.RULES, "rule_update": R.RULES, "rule_enable": R.RULES, "rule_disable": R.RULES,
         "rule_test": R.RULES,
         # phase 2: contacts are payables' or receivables' (the planner asks for the right the contact's kind needs)
         "contact_create": (R.AP, R.AR), "contact_update": (R.AP, R.AR),
         "bill_create": R.AP, "bill_update": R.AP, "bill_approve": R.AP, "bill_schedule": R.AP, "bill_mark_paid": R.AP,
         "bill_void": R.AP, "invoice_create": R.AR, "invoice_send": R.AR, "invoice_record_payment": R.AR,
         "invoice_write_off": R.AR, "invoice_void": R.AR, "attach": (R.BOOK, R.AP, R.AR),
         "feed_match": (R.AP, R.AR)}
# Kinds whose authority is a who-may rule rather than a right: grants, privacy, rights.
ADMIN_ONLY = ("private_lines_set", "right_grant", "right_revoke", "connection_create", "connection_sync",
              "connection_disconnect", "connection_map")
# Only the owner turns direct send on or off for the company (§9) and sets what each role sees by default (§7.5).
OWNER_ONLY = ("payment_settings", "view_defaults_set")
# System work may freeze cards (a budget at cutoff, §6.4 rule 4) and nothing else card-side.
SYSTEM_KINDS = ("card_freeze",)
SPEND = ("payment_create",)


def approval_kind(kind: str) -> str:
    """The Tico approval kind: `spend` for money out, else `finance` with the action kind in the payload. Never
    `spend_limit`: approving one makes Tico apply it as a usage-cap proposal (backend/proposals.py)."""
    return "spend" if kind in SPEND else "finance"


@dataclass(frozen=True)
class Gate:
    """What the default gates say: `allow`, or `needs_approval` from `who` (owner | finance_admin | manager), with the
    rule that said it."""
    outcome: str
    who: str = ""
    rule: str = ""


ALLOW = Gate("allow")


def authority(org: Org, actor: str, kind: str, held: Collection[str]) -> Gate | None:
    """The right check. None: the actor may ask. A Gate: they may not ask outright, only request through it (a
    teammate without `finance.budgets` requests and their manager approves, §4.4). Raises nothing; the caller turns
    a missing right with no fallback into a denial (returned as Gate("deny"))."""
    if actor == SYSTEM and kind in SYSTEM_KINDS:
        return None
    if kind in OWNER_ONLY:
        return None if org.owner and actor == org.owner else Gate("deny", rule="right:owner")
    if kind in ADMIN_ONLY:
        if not R.is_admin(org, actor, held):
            return Gate("deny", rule="right:finance.admin")
        return Gate("needs_approval", "owner", "default:bot-admin") if actor in org.bots else None
    need = NEEDS.get(kind)
    if need is None:
        return None
    anyof = need if isinstance(need, tuple) else (need,)
    if any(R.has(org, actor, x, held) for x in anyof):
        return None
    right = anyof[0]
    if right == R.BUDGETS and actor != SYSTEM:
        return Gate("needs_approval", "manager", "default:budget-request")
    return Gate("deny", rule=f"right:{right}")


def default_gate(org: Org, actor: str, kind: str) -> Gate:
    """The code's floor under the defaults (gates.FIXED): closing or reopening a period asks the owner (unless the owner
    asks, §6.4 rule 6), and a bot settling a cut-off card creation (card_resolve) asks a finance admin. The seeded
    gate rules (gates.DEFAULTS) say the same for closing, and the others are editable rules."""
    owner = bool(org.owner) and actor == org.owner
    if kind in ("close_period", "reopen_period") and not owner and actor != SYSTEM:
        return Gate("needs_approval", "owner", "default:close")
    if actor in org.bots and kind == "card_resolve":
        # Whether Mercury issued a cut-off card is a person's call: a bot's answer waits for a finance admin.
        return Gate("needs_approval", "finance_admin", "default:bot-card-resolve")
    return ALLOW


# ------------------------------------------------------------------------------------------------ payload shapes
# Each kind's payload, field by field: (name, type, words). A name ending in "?" is optional. Types: str, day
# (YYYY-MM-DD), month (YYYY-MM), int, minor (integer minor units), bool, list[...], obj, or a | b choices. What
# `actions.help` and the hub tools show; the planners accept exactly these fields.
Field = tuple[str, str, str]
_LINE = "list[{account_id, amount: minor, description?, tags?: obj}]"
_RULE: tuple[Field, ...] = (
    ("kind?", "gate|posting", "gate (before an action) or posting (on a new feed row or bill draft); default gate"),
    ("name", "str", "short name"), ("words?", "str", "the rule in plain words"),
    ("when", "obj", "conditions on the action's or row's facts (amount, counterparty, account, tags.*, actor...)"),
    ("outcome", "obj", "gate: allow|deny|needs_approval|limit; posting: {outcome: post, account_id, tags?, "
     "contact_id?, split?: [{account_id, share: basis points, tags?}]} or {outcome: ask}"),
    ("examples", "list[obj]", "cases that must pass before the rule saves: {input, expect} (posting: expect "
     "post|ask|none, account_id?)"),
    ("priority?", "int", "posting rules: lower runs first (default 100)"),
    ("external_rule_id?", "str", "the company rule this one carries out"))
SHAPES: dict[str, tuple[Field, ...]] = {
    "journal": (("date", "day", "entry date"), ("memo?", "str", ""), ("lines", "list[{account_id, amount: minor, "
                "description?, tags?, contact_id?}]", "debits positive, credits negative; they sum to zero"),
                ("basis?", "both|accrual", "accrual: left out of the cash P&L (depreciation, accruals)"),
                ("source?", "str", ""), ("source_id?", "str", ""),
                ("reverses?", "str", "instead of lines: the entry id to reverse (with date? and memo?)")),
    "categorize": (("lines?", "list[{line_id, tags: obj}]", "tags to add or replace on posted lines"),
                   ("mode?", "add|replace", "default add"),
                   ("feed_id?", "str", "instead of lines: post a bank or card row to a line"),
                   ("account_id?", "str", "with feed_id"), ("tags?", "obj", "with feed_id"),
                   ("ignore?", "bool", "with feed_id: leave the row unposted")),
    "account_create": (("code", "str", ""), ("name", "str", ""),
                       ("type", "asset|liability|equity|income|expense", ""), ("subtype?", "str", ""),
                       ("parent_id?", "str", "")),
    "account_update": (("account_id", "str", ""), ("code?", "str", ""), ("name?", "str", ""), ("subtype?", "str", ""),
                       ("parent_id?", "str", ""), ("active?", "bool", "")),
    "close_period": (("month", "month", ""),), "reopen_period": (("month", "month", ""),),
    "budget_create": (("name", "str", ""), ("period?", "month|quarter|year|once", "default month"),
                      ("account_id?", "str", ""), ("catch_all?", "bool", ""), ("team?", "str", ""),
                      ("teammate?", "str", ""), ("vendor?", "str", ""), ("parent_id?", "str", ""),
                      ("owner?", "str", ""), ("warn_pct?", "int", ""),
                      ("at_limit?", "warn|ask|decline|hold_payment", ""),
                      ("rollover?", "bool", ""), ("amount?", "minor", "none: tracked with no amount"),
                      ("effective_from?", "day", "")),
    "budget_update": (("budget_id", "str", ""), ("name?", "str", ""), ("owner?", "str", ""), ("warn_pct?", "int", ""),
                      ("at_limit?", "warn|ask|decline|hold_payment", ""), ("rollover?", "bool", ""),
                      ("active?", "bool", ""),
                      ("amount?", "minor", ""), ("effective_from?", "day", "")),
    "allocation_set": (("budget_id", "str", ""), ("target_kind", "card|gateway_key|teammate", ""),
                       ("target_id", "str", ""),
                       ("amount", "minor", "")),
    "projection_set": (("account_id", "str", ""), ("month", "month", ""), ("amount", "minor", ""),
                       ("team?", "str", ""), ("teammate?", "str", ""),
                       ("method?", "trend|run_rate|recurring|fixed|manual", "")),
    "view_grant": (("subject_kind", "teammate|team", ""), ("subject", "str", ""),
                   ("what", "everything|pnl|expenses|pnl_lines|balance_sheet|bs_lines|payables|receivables|sources",
                    ""),
                   ("whose?", "own|team|company", ""), ("whose_team?", "str", ""), ("account_ids?", "list[str]", ""),
                   ("new_members?", "bool", ""), ("expires_at?", "str", "")),
    "view_revoke": (("grant_id", "str", ""),),
    "private_lines_set": (("account_id", "str", ""), ("private", "bool", ""), ("others_see?", "totals|hidden", ""),
                          ("full_detail?", "list[str]", "")),
    "view_defaults_set": (("manager?", "str", ""), ("member?", "str", ""), ("manager_payroll?", "totals|hidden", "")),
    "right_grant": (("teammate", "str", ""), ("right", "str", "finance.book|ap|ar|pay|cards|budgets|rules|admin"),
                    ("expires_at?", "str", "")),
    "right_revoke": (("teammate", "str", ""), ("right", "str", "")),
    "reconcile": (("money_account_id", "str", ""), ("statement_end", "day", ""), ("statement_balance", "minor", ""),
                  ("cleared", "list[str]", "feed row ids"), ("opening_balance?", "minor", "")),
    "reconcile_undo": (("reconciliation_id", "str", ""),),
    "connection_create": (("provider", "mercury|stripe", ""), ("credential_id", "str", ""), ("name?", "str", ""),
                          ("probe?", "bool", ""), ("approver_email?", "str", ""), ("token_creator_email?", "str", ""),
                          ("server_ip?", "str", "")),
    "connection_sync": (("connection_id", "str", ""),), "connection_disconnect": (("connection_id", "str", ""),),
    "connection_map": (("money_account_id", "str", ""), ("account_id", "str", "")),
    "payment_settings": (("direct_send?", "bool", ""),
                         ("cash_below?", "minor", "finance.cash.below fires when cash drops under this; 0 turns "
                          "it off")),
    "card_create": (("holder", "str", ""), ("budget_id", "str", ""), ("amount", "minor", "monthly limit"),
                    ("nickname?", "str", ""), ("money_account_id?", "str", ""), ("provider_user_id?", "str", "")),
    "card_update_limit": (("card_id", "str", ""), ("amount", "minor", "")),
    "card_freeze": (("card_id", "str", ""), ("budget_id?", "str", ""), ("reason?", "str", "")),
    "card_unfreeze": (("card_id", "str", ""), ("reason?", "str", "")),
    "card_cancel": (("card_id", "str", ""), ("reason?", "str", "")),
    "card_resolve": (("card_id", "str", ""), ("provider_card_id?", "str", ""), ("none?", "bool", "")),
    "recipient_add": (("name", "str", ""), ("method", "ach|wire|check", ""), ("account_type?", "str", ""),
                      ("address?", "obj", ""), ("email?", "str", ""), ("contact_id?", "str", ""),
                      ("replaces?", "str", ""), ("money_account_id?", "str", ""),
                      ("details", "obj", "bank details; sealed, never stored readable")),
    "payment_create": (("recipient_id", "str", ""), ("amount", "minor", ""), ("money_account_id", "str", ""),
                       ("memo?", "str", ""), ("obligation?", "str", "one live payment per obligation (bill:<id>)"),
                       ("account_id", "str", ""), ("tags?", "obj", ""),
                       ("mode?", "approval_queue|direct", "default approval_queue"),
                       ("purpose?", "obj", "")),
    "payment_cancel": (("payment_id", "str", ""),),
    "transfer_create": (("from_account", "str", "one of the company's bank accounts (money account id) to take from"),
                        ("to_account", "str", "another of the company's bank accounts on the same connection"),
                        ("amount", "minor", ""), ("memo?", "str", "")),
    "rule_create": _RULE,
    "rule_update": (("rule_id", "str", ""), *((n if n.endswith("?") else n + "?", t, w) for n, t, w in _RULE
                                              if n != "kind?")),
    "rule_enable": (("rule_id", "str", ""),), "rule_disable": (("rule_id", "str", ""),),
    "rule_test": (("rule_id?", "str", "an existing rule"), ("when?", "obj", "or a draft's conditions"),
                  ("outcome?", "obj", ""), ("kind?", "gate|posting", "a draft's kind; default gate")),
    "contact_create": (("name", "str", ""), ("kind", "vendor|customer|both", ""), ("emails?", "list[str]", ""),
                       ("default_account_id?", "str", "the line a vendor's bills and rows default to"),
                       ("is_1099?", "bool", ""), ("tax_id?", "str", "sealed; only its last 4 show, to finance admins"),
                       ("recipient_id?", "str", "the payment details bills to this vendor are paid to"),
                       ("seats?", "int", "vendors: seats or licenses"),
                       ("renewal_on?", "day", "vendors: the next renewal date")),
    "contact_update": (("contact_id", "str", ""), ("name?", "str", ""), ("kind?", "vendor|customer|both", ""),
                       ("emails?", "list[str]", ""), ("default_account_id?", "str", ""), ("is_1099?", "bool", ""),
                       ("tax_id?", "str", ""), ("recipient_id?", "str", ""), ("active?", "bool", ""),
                       ("seats?", "int", "vendors"), ("renewal_on?", "day", "vendors: a day, or empty to clear")),
    "bill_create": (("vendor_id", "str", "a vendor contact"), ("number?", "str", "the vendor's bill number"),
                    ("bill_date", "day", ""), ("due_date?", "day", "or terms_days"),
                    ("terms_days?", "int", "due this many days after bill_date"), ("memo?", "str", ""),
                    ("lines", _LINE, "expense lines; amounts positive"),
                    ("attachments?", "list[str]", "Library file ids"), ("origin?", "manual|email|upload", "")),
    "bill_update": (("bill_id", "str", ""), ("vendor_id?", "str", "draft only"), ("number?", "str", ""),
                    ("bill_date?", "day", "draft only"), ("due_date?", "day", ""), ("terms_days?", "int", ""),
                    ("memo?", "str", ""), ("lines?", _LINE, "draft only; replaces every line"),
                    ("attachments?", "list[str]", "file ids to add")),
    "bill_approve": (("bill_id", "str", "enters a draft: expense lines debit, accounts payable credit"),),
    "bill_schedule": (("bill_id", "str", ""), ("pay_on", "day", "the payment is requested on this day"),
                      ("money_account_id", "str", "the bank account it is paid from"),
                      ("recipient_id?", "str", "default: the vendor's"), ("memo?", "str", "")),
    "bill_mark_paid": (("bill_id", "str", ""), ("amount?", "minor", "default: what is left"),
                       ("date?", "day", "default today"),
                       ("feed_id?", "str", "the bank or card row that paid it (links it, never posts expense)"),
                       ("money_account_id?", "str", "or the money account it was paid from (its bank row links later)"),
                       ("account_id?", "str", "or another money line; none: a clearing account")),
    "bill_void": (("bill_id", "str", ""), ("reason?", "str", "")),
    "invoice_create": (("customer_id", "str", "a customer contact"), ("number?", "str", ""),
                       ("invoice_date", "day", ""), ("due_date?", "day", "or terms_days"), ("terms_days?", "int", ""),
                       ("memo?", "str", ""), ("lines", _LINE, "income lines; amounts positive"),
                       ("payment_link?", "str", "")),
    "invoice_send": (("invoice_id", "str", "posts accounts receivable debit, income credit; returns the document"),),
    "invoice_record_payment": (("invoice_id", "str", ""), ("amount", "minor", "may be part of what is owed"),
                               ("date?", "day", ""), ("feed_id?", "str", "the bank row it came in on"),
                               ("money_account_id?", "str", "or the money account it went to (its row links later)"),
                               ("account_id?", "str", "or another money line; none: a clearing account")),
    "invoice_write_off": (("invoice_id", "str", ""), ("amount?", "minor", "default: what is left"),
                          ("account_id?", "str", "default: Other expenses"), ("date?", "day", ""),
                          ("memo?", "str", "")),
    "invoice_void": (("invoice_id", "str", ""), ("reason?", "str", "")),
    "attach": (("subject_kind", "entry|feed|bill|invoice|unmatched", "unmatched: receipts waiting for a match"),
               ("subject_id?", "str", "not for unmatched"),
               ("file_ids", "list[str]", "Library file ids (a receipt, a bill's PDF)")),
    "feed_match": (("feed_id", "str", "a settled bank row not yet in the books"),
                   ("settlement_ids", "list[str]", "the waiting marked-paid payments it pays (its expected list); "
                    "their amounts add up to the row's")),
}
WORDS: dict[str, str] = {
    "journal": "Post a balanced entry, or reverse one.",
    "categorize": "Tag posted lines, or post (or ignore) a bank or card row.",
    "account_create": "Add a line to the chart of accounts.", "account_update": "Rename, move or retire a line.",
    "close_period": "Close a month so nothing posts into it.", "reopen_period": "Reopen a closed month.",
    "budget_create": "Make a budget.", "budget_update": "Change a budget (a new version from a date).",
    "allocation_set": "Give a teammate, team or card part of a budget.",
    "projection_set": "Set a line's projection for a month.",
    "view_grant": "Let a teammate or team see more of Finance.", "view_revoke": "Take a view grant back.",
    "private_lines_set": "Make a line private (payroll) and say who sees its detail.",
    "view_defaults_set": "Set what managers and members see by default.",
    "right_grant": "Give a teammate a Finance right.", "right_revoke": "Take a Finance right back.",
    "reconcile": "Match a statement to the feed and lock it.", "reconcile_undo": "Unlock a reconciliation.",
    "connection_create": "Connect a bank or Stripe.", "connection_sync": "Sync a connection now.",
    "connection_disconnect": "Disconnect a provider.", "connection_map": "Point a money account at a line.",
    "payment_settings": "Turn direct send on or off and set the cash alert.",
    "card_create": "Issue a card on a budget.", "card_update_limit": "Change a card's limit.",
    "card_freeze": "Freeze a card.", "card_unfreeze": "Unfreeze a card.", "card_cancel": "Cancel a card.",
    "card_resolve": "Say whether the bank issued a card whose creation was cut off.",
    "recipient_add": "Add someone to pay (bank details are sealed).",
    "payment_create": "Request a payment.", "payment_cancel": "Cancel a queued payment.",
    "transfer_create": "Move money between two of the company's own bank accounts.",
    "rule_create": "Add a gate or posting rule; its examples must pass.", "rule_update": "Change a rule.",
    "rule_enable": "Turn a rule on.", "rule_disable": "Turn a rule off.",
    "rule_test": "Run a rule over the last 90 days you can see; writes nothing.",
    "contact_create": "Add a vendor or customer.", "contact_update": "Change a vendor or customer.",
    "bill_create": "Record a bill as a draft (its lines hold budget).",
    "bill_update": "Change a bill (lines only while it is a draft).",
    "bill_approve": "Enter a draft bill: it posts to payables and its budget hold becomes spend.",
    "bill_schedule": "Pay a bill on a day: a payment is requested then.",
    "bill_mark_paid": "Record that a bill was paid, from a bank or card row, a money account or line.",
    "bill_void": "Void a bill (an entered one is reversed).",
    "invoice_create": "Draft an invoice.", "invoice_send": "Send an invoice: it posts to receivables.",
    "invoice_record_payment": "Record money received on an invoice (part or all).",
    "invoice_write_off": "Write off what a customer will not pay.", "invoice_void": "Void an invoice.",
    "attach": "Attach receipts or documents to an entry, a bank or card row, a bill or an invoice.",
    "feed_match": "Link a bank row to the payments marked paid it is the bank side of (the bank moves once).",
}


def fields(kind: str) -> tuple[str, ...]:
    """The field names a kind's payload accepts."""
    return tuple(name.rstrip("?") for name, _, _ in SHAPES.get(kind, ()))
