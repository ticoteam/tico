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
       "payment_create", "recipient_add", "card_create", "card_update_limit", "card_freeze", "card_cancel",
       "budget_create", "budget_update", "allocation_set", "projection_set",
       "view_grant", "view_revoke", "private_lines_set",
       "rule_create", "rule_update", "rule_disable", "rule_test", "contact_create", "contact_update",
       "account_create", "account_update", "right_grant", "right_revoke")
LIVE = ("journal", "categorize", "account_create", "account_update", "budget_create", "budget_update",
        "allocation_set", "projection_set", "view_grant", "view_revoke", "private_lines_set", "close_period",
        "reopen_period", "right_grant", "right_revoke")
NEEDS = {"journal": R.BOOK, "categorize": R.BOOK, "account_create": R.BOOK, "account_update": R.BOOK,
         "close_period": R.BOOK, "reopen_period": R.BOOK, "budget_create": R.BUDGETS, "budget_update": R.BUDGETS,
         "allocation_set": R.BUDGETS, "projection_set": R.BUDGETS}
# Kinds whose authority is a who-may rule rather than a right: grants, privacy, rights.
ADMIN_ONLY = ("private_lines_set", "right_grant", "right_revoke")
SPEND = ("payment_create",)
# A bot categorizing or posting more than this in one action (an entry's debits, or the lines it tags) asks a finance
# admin (§6.4 rule 5).
BOT_CATEGORIZE_LIMIT = 500_000


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
    if kind in ADMIN_ONLY:
        if not R.is_admin(org, actor, held):
            return Gate("deny", rule="right:finance.admin")
        return Gate("needs_approval", "owner", "default:bot-admin") if actor in org.bots else None
    right = NEEDS.get(kind)
    if right is None or R.has(org, actor, right, held):
        return None
    if right == R.BUDGETS and actor != SYSTEM:
        return Gate("needs_approval", "manager", "default:budget-request")
    return Gate("deny", rule=f"right:{right}")


def default_gate(org: Org, actor: str, kind: str, *, total: int = 0, new_account: bool = False) -> Gate:
    """The phase-0 defaults (§6.4): closing or reopening a period asks the owner (unless the owner asks); a bot's
    categorization or entry totalling over $5,000, or a bot's new account, asks a finance admin."""
    owner = bool(org.owner) and actor == org.owner
    if kind in ("close_period", "reopen_period") and not owner and actor != SYSTEM:
        return Gate("needs_approval", "owner", "default:close")
    if actor in org.bots and kind in ("categorize", "journal") and total > BOT_CATEGORIZE_LIMIT:
        return Gate("needs_approval", "finance_admin", "default:bot-over-5000")
    if actor in org.bots and new_account:
        return Gate("needs_approval", "finance_admin", "default:bot-new-account")
    return ALLOW
