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
       "payment_create", "payment_cancel", "recipient_add", "card_create", "card_update_limit", "card_freeze",
       "card_unfreeze", "card_cancel", "card_resolve", "card_save", "card_unsave", "reconcile_undo",
       "connection_create", "payment_settings",
       "budget_create", "budget_update", "allocation_set", "projection_set",
       "view_grant", "view_revoke", "private_lines_set",
       "rule_create", "rule_update", "rule_disable", "rule_enable", "rule_test", "contact_create", "contact_update",
       "account_create", "account_update", "right_grant", "right_revoke",
       # not in §5: the Finance tab's Access and Sources pages (§12), attaching receipts and documents (§8.4), and
       # linking a bank row to payments marked paid when the automatic match cannot tell which (feed_match)
       "view_defaults_set", "connection_sync", "connection_disconnect", "connection_map", "attach",
       "feed_match", "mirror_start", "mirror_stop", "mirror_tags", "mirror_xero_location", "tax_jurisdiction_add",
       "tax_remit",
       "import_raw_totals",
       # shared records (backend/records): items, locations and projects
       "item_create", "item_update", "location_create", "location_update", "project_create", "project_update",
       # purchase orders and estimates (backend/finance/purchasing.py)
       "po_create", "po_send", "po_approve", "po_close", "po_void", "estimate_create", "estimate_send",
       "estimate_accept", "estimate_decline", "estimate_convert")
LIVE = ("journal", "categorize", "account_create", "account_update", "budget_create", "budget_update",
        "allocation_set", "projection_set", "view_grant", "view_revoke", "private_lines_set", "close_period",
        "reopen_period", "right_grant", "right_revoke",
        # phase 1: banks, cards, payments, reconciliation and gate rules
        "reconcile", "reconcile_undo", "connection_create", "payment_settings", "card_create", "card_update_limit",
        "card_freeze", "card_unfreeze", "card_cancel", "card_resolve", "recipient_add", "payment_create",
        "payment_cancel",
        "rule_create", "rule_update", "rule_enable", "rule_disable", "rule_test",
        "view_defaults_set", "connection_sync", "connection_disconnect", "connection_map",
        # phase 2: contacts, bills, invoices and attachments
        "contact_create", "contact_update", "bill_create", "bill_update", "bill_approve", "bill_schedule",
        "bill_mark_paid", "bill_void", "invoice_create", "invoice_send", "invoice_record_payment",
        "invoice_write_off", "invoice_void", "attach", "feed_match",
        # mirror mode (spec §10): a provider's books are the books
        "mirror_start", "mirror_stop", "mirror_tags", "mirror_xero_location",
        # phase 3: the vendor accounts a card is saved on (bot cards, §8.3)
        "card_save", "card_unsave",
        # phase 3: sales tax jurisdictions and remittances (finance/tax.py)
        "tax_jurisdiction_add", "tax_remit",
        # history imports: a spreadsheet's totals as raw totals (finance/raw_totals.py)
        "import_raw_totals",
        # shared records (backend/records): items, locations and projects
        "item_create", "item_update", "location_create", "location_update", "project_create", "project_update",
        # purchase orders and estimates
        "po_create", "po_send", "po_approve", "po_close", "po_void", "estimate_create", "estimate_send",
        "estimate_accept", "estimate_decline", "estimate_convert")
NEEDS: dict[str, str | tuple[str, ...]] = {
         "journal": R.BOOK, "categorize": R.BOOK, "account_create": R.BOOK, "account_update": R.BOOK,
         "close_period": R.BOOK, "reopen_period": R.BOOK, "budget_create": R.BUDGETS, "budget_update": R.BUDGETS,
         "allocation_set": R.BUDGETS, "projection_set": R.BUDGETS, "reconcile": R.BOOK, "reconcile_undo": R.BOOK,
         "card_create": R.CARDS, "card_update_limit": R.CARDS, "card_freeze": R.CARDS, "card_unfreeze": R.CARDS,
         "card_cancel": R.CARDS, "card_resolve": R.CARDS, "card_save": R.CARDS, "card_unsave": R.CARDS,
         "recipient_add": R.PAY, "payment_create": R.PAY,
         "payment_cancel": R.PAY,
         "rule_create": R.RULES, "rule_update": R.RULES, "rule_enable": R.RULES, "rule_disable": R.RULES,
         "rule_test": R.RULES,
         # phase 2: contacts are payables' or receivables' (the planner asks for the right the contact's kind needs)
         "contact_create": (R.AP, R.AR), "contact_update": (R.AP, R.AR),
         "bill_create": R.AP, "bill_update": R.AP, "bill_approve": R.AP, "bill_schedule": R.AP, "bill_mark_paid": R.AP,
         "bill_void": R.AP, "invoice_create": R.AR, "invoice_send": R.AR, "invoice_record_payment": R.AR,
         "invoice_write_off": R.AR, "invoice_void": R.AR, "attach": (R.BOOK, R.AP, R.AR),
         "feed_match": (R.AP, R.AR), "mirror_start": R.BOOK, "mirror_stop": R.BOOK, "mirror_tags": R.BOOK, "mirror_xero_location": R.BOOK,
         "tax_jurisdiction_add": R.BOOK, "tax_remit": R.AP, "import_raw_totals": R.BOOK,
         "item_create": R.BOOK, "item_update": R.BOOK, "location_create": R.BOOK, "location_update": R.BOOK,
         "project_create": R.BOOK, "project_update": R.BOOK,
         "po_create": R.AP, "po_send": R.AP, "po_approve": R.AP, "po_close": R.AP, "po_void": R.AP,
         "estimate_create": R.AR, "estimate_send": R.AR, "estimate_accept": R.AR, "estimate_decline": R.AR,
         "estimate_convert": R.AR}
# Kinds whose authority is a who-may rule rather than a right: grants, privacy, rights.
ADMIN_ONLY = ("private_lines_set", "right_grant", "right_revoke", "connection_create", "connection_sync",
              "connection_disconnect", "connection_map")
# Only the owner turns direct send on or off for the company (§9) and sets what each role sees by default (§7.5).
OWNER_ONLY = ("payment_settings", "view_defaults_set", "mirror_tags", "mirror_xero_location")
# System work may freeze cards at a budget's cutoff and lift those freezes once the budgets have room (§6.4 rule 4,
# §8.2), and nothing else card-side.
SYSTEM_KINDS = ("card_freeze", "card_unfreeze")
SPEND = ("payment_create",)
# Resolved values kept on the stored action only: a fingerprint of a hidden total must not reach an approval, a
# rule test or anything a requester reads. Drift still compares it, from the stored body.
SERVER_ONLY_KEYS = frozenset({"raise_check"})


def approval_kind(kind: str) -> str:
    """The Tico approval kind: `spend` for money out, else `finance` with the action kind in the payload. Never
    `spend_limit`: approving one makes Tico apply it as a usage-cap proposal (backend/proposals.py)."""
    return "spend" if kind in SPEND else "finance"


@dataclass(frozen=True)
class Gate:
    """What the default gates say: `allow`, or `needs_approval` from `who` (owner | finance_admin | manager |
    budget_owner), with the rule that said it. `person` is the one `budget_owner` names: the parent budget's owner."""
    outcome: str
    who: str = ""
    rule: str = ""
    person: str = ""


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
    """The code's floor under the defaults (gates.FIXED): closing or reopening a period, and importing history,
    asks the owner (unless the owner asks, §6.4 rule 6); a bot settling a cut-off card creation (card_resolve) asks a
    finance admin, and so does a bot recording where its card is saved (§8.3: a person saves the card on the vendor's
    account). The seeded gate rules (gates.DEFAULTS) say the same for closing, and the others are editable rules."""
    owner = bool(org.owner) and actor == org.owner
    if kind in ("close_period", "reopen_period") and not owner and actor != SYSTEM:
        return Gate("needs_approval", "owner", "default:close")
    if kind in ("mirror_start", "mirror_stop") and not owner and actor != SYSTEM:
        return Gate("needs_approval", "owner", "default:mirror")
    if kind == "import_raw_totals" and not owner and actor != SYSTEM:
        return Gate("needs_approval", "owner", "default:raw-import")
    if actor in org.bots and kind == "card_resolve":
        # Whether Mercury issued a cut-off card is a person's call: a bot's answer waits for a finance admin.
        return Gate("needs_approval", "finance_admin", "default:bot-card-resolve")
    if actor in org.bots and kind in ("card_save", "card_unsave"):
        return Gate("needs_approval", "finance_admin", "default:bot-card-vendor")
    return ALLOW


# ------------------------------------------------------------------------------------------------ payload shapes
# Each kind's payload, field by field: (name, type, words). A name ending in "?" is optional. Types: str, day
# (YYYY-MM-DD), month (YYYY-MM), int, minor (integer minor units), bool, list[...], obj, or a | b choices. What
# `actions.help` and the hub tools show; the planners accept exactly these fields.
Field = tuple[str, str, str]
_LINE = "list[{account_id?, item_id?, amount: minor, description?, tags?: obj}]"
_INVOICE_LINE = "list[{account_id?, item_id?, amount: minor, description?, tags?: obj, taxable?: bool}]"
_BILL_LINE = "list[{account_id?, item_id?, po_line_id?, amount: minor, description?, tags?: obj}]"
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
    "mirror_start": (("connection_id", "str", "the books connection to mirror"),),
    "mirror_stop": (),
    "mirror_tags": (("enabled", "bool", "send tags as provider classes"),
                    ("map?", "obj", "{tag kind: {tag value: provider class id}}")),
    "mirror_xero_location": (("category_id", "str",
                              "the Xero tracking category whose options are locations ('' for none)"),),
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
                    ("kind?", "standing|person", "standing for a bot (the default for one), person for a teammate; "
                     "single_use is not on Mercury yet"),
                    ("nickname?", "str", ""), ("money_account_id?", "str", ""),
                    ("provider_user_id", "str", "the Mercury user the card is issued to; Mercury issues cards "
                     "to its own users")),
    "card_update_limit": (("card_id", "str", ""), ("amount", "minor", "")),
    "card_freeze": (("card_id", "str", ""), ("budget_id?", "str", ""), ("reason?", "str", "")),
    "card_unfreeze": (("card_id", "str", ""), ("reason?", "str", "")),
    "card_cancel": (("card_id", "str", ""), ("reason?", "str", "")),
    "card_resolve": (("card_id", "str", ""), ("provider_card_id?", "str", ""), ("none?", "bool", "")),
    "card_save": (("card_id", "str", ""), ("vendor", "str", "a vendor contact's id, or the name the vendor goes by")),
    "card_unsave": (("card_id", "str", ""), ("vendor", "str", "the vendor the card was saved on")),
    "recipient_add": (("name", "str", ""), ("method", "ach|wire|check", ""), ("account_type?", "str", ""),
                      ("address?", "obj", ""), ("email?", "str", ""), ("contact_id?", "str", ""),
                      ("replaces?", "str", ""), ("money_account_id?", "str", ""),
                      ("details", "obj", "bank details; sealed, never stored readable")),
    "payment_create": (("recipient_id", "str", "the payee (required unless method is internal)"),
                       ("amount", "minor", ""),
                       ("money_account_id?", "str", "the bank to pay from"),
                       ("memo?", "str", ""), ("obligation?", "str", "one live payment per obligation (bill:<id>)"),
                       ("account_id", "str", "the line it posts to (required unless method is internal)"),
                       ("tags?", "obj", ""),
                       ("mode?", "approval_queue|direct", "default approval_queue"),
                       ("purpose?", "obj", ""),
                       ("method?", "internal", "a move between our own bank accounts: from_account and to_account"),
                       ("from_account?", "str", "internal only: the bank account to take from"),
                       ("to_account?", "str", "internal only: the bank account to move it into, same connection")),
    "payment_cancel": (("payment_id?", "str", ""),
                       ("transfer_id?", "str", "an internal transfer not yet sent (instead of payment_id)")),
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
                    ("lines", _BILL_LINE, "expense lines; amounts positive; po_line_id bills an open purchase order's line"),
                    ("attachments?", "list[str]", "Library file ids"), ("origin?", "manual|email|upload", "")),
    "bill_update": (("bill_id", "str", ""), ("vendor_id?", "str", "draft only"), ("number?", "str", ""),
                    ("bill_date?", "day", "draft only"), ("due_date?", "day", ""), ("terms_days?", "int", ""),
                    ("memo?", "str", ""), ("lines?", _BILL_LINE, "draft only; replaces every line"),
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
                       ("memo?", "str", ""),
                       ("lines", _INVOICE_LINE, "income lines; amounts positive; taxable defaults to true on income "
                                                "lines of an invoice with a sales tax line"),
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
    "tax_jurisdiction_add": (("code", "str", "a short code, such as US-CA"), ("name", "str", "the jurisdiction's name"),
                             ("authority_id?", "str", "a vendor contact its remittances are paid to"),
                             ("due_day?", "int", "the day of the month after each quarter its return falls due, 1 to 28")),
    "tax_remit": (("jurisdiction", "str", "the code of a sales tax jurisdiction"),
                  ("period", "str", "a quarter, like 2026-Q3"),
                  ("amount?", "minor", "default: what the quarter still owes"), ("bill_date?", "day", "default today"),
                  ("due_date?", "day", "the bill's due date"), ("number?", "str", "the authority's reference")),
    "import_raw_totals": (
        ("csv", "str", "a CSV with a header: month (YYYY-MM), line, amount (whole cents), and optionally team"),
        ("basis", "cash|accrual", "the basis the figures were reported on"),
        ("mapping", "obj", "each sheet line name to an account id or code"),
        ("source_name?", "str", "what the file was called, for the record")),
    "item_create": (("name", "str", ""),
                    ("kind", "service|inventory|non_inventory|bundle|category|raw_material|assembly", ""),
                    ("sku?", "str", ""), ("unit?", "str", ""), ("price?", "minor", "in the book's currency"),
                    ("income_account_id?", "str", "the account a sale of it posts to"),
                    ("expense_account_id?", "str", "the account a purchase of it posts to"),
                    ("asset_account_id?", "str", "inventory it holds")),
    "item_update": (("item_id", "str", ""), ("name?", "str", ""),
                    ("kind?", "service|inventory|non_inventory|bundle|category|raw_material|assembly", ""),
                    ("sku?", "str", ""), ("unit?", "str", ""), ("price?", "minor", "a change asks for approval"),
                    ("income_account_id?", "str", ""), ("expense_account_id?", "str", ""),
                    ("asset_account_id?", "str", ""), ("active?", "bool", "")),
    "location_create": (("name", "str", ""), ("kind?", "store|office|warehouse|plant|customer_site|vehicle|other", ""),
                        ("in_books?", "bool",
                         "a mirrored book: false keeps it in Tico; customer sites and vehicles default to false"),
                        ("parent_id?", "str", "the location it sits under"),
                        ("address?", "obj", "up to 10 text fields")),
    "location_update": (("location_id", "str", ""), ("name?", "str", ""),
                        ("kind?", "store|office|warehouse|plant|customer_site|vehicle|other", ""),
                     ("parent_id?", "str", "empty to clear"), ("address?", "obj", ""), ("active?", "bool", "")),
    "project_create": (("name", "str", ""), ("customer_id?", "str", "a customer contact"),
                       ("status?", "open|closed", "")),
    "project_update": (("project_id", "str", ""), ("name?", "str", ""), ("customer_id?", "str", "empty to clear"),
                       ("status?", "open|closed", ""), ("active?", "bool", "")),
    "po_create": (("vendor_id", "str", "a vendor contact"), ("number?", "str", "default PO-0001 onward"),
                  ("po_date", "day", ""), ("memo?", "str", ""), ("lines", _LINE, "expense lines; amounts positive")),
    "po_send": (("po_id", "str", "a draft is marked sent; nothing is emailed"),),
    "po_approve": (("po_id", "str", "holds each line's unbilled amount in its budget; over $1,000 asks the owner"),),
    "po_close": (("po_id", "str", "an approved order closes; what is not billed is released"),),
    "po_void": (("po_id", "str", "a draft, sent or approved order nothing is billed against"),),
    "estimate_create": (("customer_id", "str", "a customer contact"), ("number?", "str", "default EST-0001 onward"),
                        ("estimate_date", "day", ""), ("memo?", "str", ""),
                        ("lines", _LINE, "income lines; amounts positive")),
    "estimate_send": (("estimate_id", "str", "a draft is marked sent; the owner approves it"),),
    "estimate_accept": (("estimate_id", "str", "a sent estimate is accepted"),),
    "estimate_decline": (("estimate_id", "str", "a sent estimate is declined"),),
    "estimate_convert": (("estimate_id", "str", "an accepted estimate becomes one invoice draft, once"),),
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
    "mirror_start": "Switch the books to a provider's books (preview first).",
    "mirror_stop": "Switch a mirrored book back to native.",
    "mirror_tags": "Send tags to the provider as its classes (off by default).",
    "mirror_xero_location": "Pick the Xero tracking category that holds locations (the other is classes).",
    "right_grant": "Give a teammate a Finance right.", "right_revoke": "Take a Finance right back.",
    "reconcile": "Match a statement to the feed and lock it.", "reconcile_undo": "Unlock a reconciliation.",
    "connection_create": "Connect a bank or Stripe.", "connection_sync": "Sync a connection now.",
    "connection_disconnect": "Disconnect a provider.", "connection_map": "Point a money account at a line.",
    "payment_settings": "Turn direct send on or off and set the cash alert.",
    "card_create": "Issue a card on a budget.", "card_update_limit": "Change a card's limit.",
    "card_freeze": "Freeze a card.", "card_unfreeze": "Unfreeze a card.", "card_cancel": "Cancel a card.",
    "card_resolve": "Say whether the bank issued a card whose creation was cut off.",
    "card_save": "Record that a card is saved on a vendor's account, so a bot can buy there with it.",
    "card_unsave": "Record that a card was removed from a vendor's account.",
    "recipient_add": "Add someone to pay (bank details are sealed).",
    "payment_create": "Request a payment, or move money between two of our bank accounts (method internal).", "payment_cancel": "Cancel a queued payment, or an internal transfer not yet sent.",
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
    "tax_jurisdiction_add": "Add a sales tax jurisdiction: its liability account and quarterly due date.",
    "tax_remit": "Remit a quarter's sales tax: a bill to the tax authority, tagged with the quarter.",
    "import_raw_totals": "Import a spreadsheet's history as raw totals: one balanced entry per month.",
    "item_create": "Add a product or service that lines and documents name.",
    "item_update": "Rename, re-account, deactivate, or change the price of an item (a price change asks first).",
    "location_create": "Add a location that lines can be tagged with.",
    "location_update": "Rename, move, or deactivate a location.",
    "project_create": "Add a project that lines can be tagged with.",
    "project_update": "Rename, re-status, or deactivate a project.",
    "po_create": "Draft a purchase order: its lines are what the vendor will bill.",
    "po_send": "Mark a draft purchase order sent.",
    "po_approve": "Approve an order: each line's unbilled amount holds budget until it is billed or closed.",
    "po_close": "Close an approved order: what is not billed is released.",
    "po_void": "Void an order nothing has been billed against.",
    "estimate_create": "Draft an estimate for a customer.",
    "estimate_send": "Mark an estimate sent (the owner approves).",
    "estimate_accept": "Record that the customer accepted an estimate.",
    "estimate_decline": "Record that the customer declined an estimate.",
    "estimate_convert": "Make an accepted estimate one invoice draft (once).",
}


def fields(kind: str) -> tuple[str, ...]:
    """The field names a kind's payload accepts."""
    return tuple(name.rstrip("?") for name, _, _ in SHAPES.get(kind, ()))
