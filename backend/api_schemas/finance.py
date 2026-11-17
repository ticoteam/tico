# pyright: strict
"""The answers of the Finance routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

# Finance (backend/finance_routes.py): every read is cut to the caller's own view.
DOC_CONTACT = {
    "type": ["object", "null"],
    "properties": {"id": {"type": ["string", "null"]}, "name": {"type": ["string", "null"]}},
}


DOC_PAYMENT = {
    "anyOf": [
        obj(
            {
                "id": "n",
                "status": {
                    "type": "string",
                    "enum": [
                        "queued",
                        "sending",
                        "awaiting_bank_approval",
                        "submitted",
                        "settled",
                        "returned",
                        "failed",
                        "canceled",
                        "unknown",
                        "denied",
                        "expired",
                        "withdrawn",
                    ],
                },
                "amount": "i",
                "on": "s",
                "error": "n",
            }
        ),
        {"type": "null"},
    ],
    "description": "A bill's latest payment: its state, the day it was asked for and why it ended, if it did. No id "
    "when the scheduled ask ended before a payment existed (denied, expired, withdrawn or failed)",
}


SCHEMAS.update(
    {
        "FinanceBudgetState": obj(
            {
                "spent_from": {
                    "type": "string",
                    "enum": ["books", "platform", "mixed"],
                    "description": "Where an ad budget's actual comes from: the books, the ad platform's daily spend "
                    "or both (by month)",
                },
                "as_of": {
                    "type": ["string", "null"],
                    "description": "The ad platforms' last import, when their spend counts",
                },
                "reported": {
                    "type": ["integer", "null"],
                    "description": "Cents: an ad budget's platform spend shown beside actual and never in it (its vendor "
                    "is not the platform's own contact, so nothing is swapped); null otherwise",
                },
            }
        ),
        "FinanceBudgetRow": obj({"id": "s", "name": "s", "platform": "s", "state": ref("FinanceBudgetState")}),
        "FinanceBudgets": obj(
            {
                "budgets": items(ref("FinanceBudgetRow")),
                "more": "b",
                "on": "s",
                "view_version": "i",
            }
        ),
        "FinanceBrief": obj(
            {
                "lines": items("s"),
                "as_of": "s",
                "changed_since": "n",
                "version": "i",
            },
            required=["lines", "as_of", "version"],
        ),
        "FinanceReport": obj({"view_version": "i"}),
        "FinancePnl": obj(
            {
                "start": "s",
                "end": "s",
                "period": "s",
                "basis": "s",
                "view": "s",
                "team": "n",
                "teammate": "n",
                "budget_as_of": "n",
                "columns": items(
                    obj(
                        {
                            "start": "s",
                            "end": "s",
                            "label": "s",
                            "state": {"type": "string", "enum": ["past", "current", "future"]},
                        }
                    )
                ),
                "filter": obj(
                    {
                        "teams": items({"type": "string"}),
                        "teammates": items({"type": "string"}),
                        "who": {"type": "string", "enum": ["both", "bots", "people"]},
                        "unattributed": "b",
                    }
                ),
                "rows": items(
                    obj(
                        {
                            "account_id": "s",
                            "code": "s",
                            "name": "s",
                            "type": "s",
                            "parent_id": "s",
                            "private": "b",
                            "section": {"type": "string", "enum": ["revenue", "cost_of_revenue", "operating", "other"]},
                            "actual": {"type": ["array", "null"], "items": {"type": "integer"}},
                            "total": {"type": ["integer", "null"]},
                            "budget": {"type": ["array", "null"], "items": {"type": ["integer", "null"]}},
                            "projection": {"type": ["array", "null"], "items": {"type": "integer"}},
                        }
                    )
                ),
                "income": items({"type": "integer"}),
                "expense": items({"type": "integer"}),
                "net": items({"type": "integer"}),
                "excluded": "b",
                "view_version": "i",
                "teammates": items(
                    obj({"teammate": "s", "kind": "s", "spent": "i", "budget": {"type": ["integer", "null"]}})
                ),
            },
            required=[
                "start",
                "end",
                "period",
                "basis",
                "view",
                "columns",
                "rows",
                "income",
                "expense",
                "net",
                "excluded",
                "view_version",
            ],
        ),
        "FinanceBalanceSheet": obj(
            {
                "as_of": "s",
                "currency": "s",
                "scoped": "b",
                "view_version": "i",
                "closed_through": "n",
                "totals": obj({"asset": "i", "liability": "i", "equity": "i"}),
                **{
                    k: items(
                        obj(
                            {
                                "account_id": "n",
                                "code": "s",
                                "name": "s",
                                "subtype": "s",
                                "parent_id": "n",
                                "balance": "i",
                                "entry_sources": items({"type": "string"}),
                                "sources": items(
                                    obj(
                                        {
                                            "money_account_id": "s",
                                            "name": "s",
                                            "provider": "s",
                                            "last4": "n",
                                            "provider_balance": {"type": ["integer", "null"]},
                                            "balance_as_of": "n",
                                            "due_out": obj({"count": "i", "amount": "i"}),
                                        }
                                    )
                                ),
                            }
                        )
                    )
                    for k in ("assets", "liabilities", "equity")
                },
            }
        ),
        "FinanceTransactions": obj(
            {
                "rows": items(
                    obj(
                        {
                            "kind": {"type": "string", "enum": ["feed", "entry"]},
                            "id": "s",
                            "date": "s",
                            "amount": "i",
                            "currency": "s",
                            "counterparty": "s",
                            "description": "s",
                            "status": "s",
                            "pending": "b",
                            "card_id": "n",
                            "entry_id": "n",
                            "reconciled": "b",
                            "money_account": {"type": ["object", "null"]},
                            "lines": items({"type": "object", "additionalProperties": True}),
                            "account_id": "s",
                            "account": "s",
                            # a bank or card row: {id, name} (its vendor); an entry's line: the vendor's id
                            "vendor": {"anyOf": [{"type": "string"}, obj({"id": "s", "name": "s"})]},
                            "vendor_name": "s",
                            "memo": "s",
                            "source": "s",
                            "reversed": "b",
                            "tags": "o",
                        },
                        required=["kind", "id", "date", "amount", "currency"],
                    )
                ),
                "next": "n",
                "currency": "s",
                "view_version": "i",
            }
        ),
        "FinanceCards": obj(
            {
                "cards": items(
                    obj(
                        {
                            "id": "s",
                            "provider": "s",
                            "holder": "s",
                            "nickname": "s",
                            "last4": "n",
                            "kind": "s",
                            "status": "s",
                            "limit": {"type": ["integer", "null"]},
                            "limit_interval": "n",
                            "provider_limit": {"type": ["integer", "null"]},
                            "sync_status": "s",
                            "currency": "s",
                            "money_account_id": "n",
                            "budget": {"type": ["object", "null"]},
                            "allocation": {"type": ["integer", "null"]},
                            "spent": {"type": ["integer", "null"]},
                            "expires_at": "n",
                            "created_at": "s",
                        }
                    )
                ),
                "next": "n",
                "view_version": "i",
            }
        ),
        "FinanceSources": obj(
            {
                "groups": items(
                    obj(
                        {
                            "group": {
                                "type": "string",
                                "enum": [
                                    "banks",
                                    "cards",
                                    "payments",
                                    "payroll",
                                    "bills_invoices",
                                    "tico",
                                    "accounting",
                                    "manual",
                                ],
                            },
                            "name": "s",
                            "count": "i",
                            "rows": items({"type": "object", "additionalProperties": True}),
                        }
                    )
                ),
                "view_version": "i",
            }
        ),
        "FinanceAccess": obj(
            {
                "teams": items(obj({"id": "s", "name": "s", "parent": "n", "lead": "n"})),
                "teammates": items(
                    obj(
                        {
                            "teammate": "s",
                            "kind": "s",
                            "team": "n",
                            "reports_to": "n",
                            "default": {
                                "type": "string",
                                "enum": ["everything", "team_expenses", "own_expenses", "none"],
                            },
                            "default_team": "n",
                            "grants": items({"type": "object", "additionalProperties": True}),
                            "rights": {"type": ["array", "null"], "items": {"type": "string"}},
                            "attribute": {"type": ["boolean", "null"]},
                            "may_grant": "b",
                            "budgets": "i",
                        }
                    )
                ),
                "private_lines": items(
                    obj(
                        {
                            "account_id": "s",
                            "name": "s",
                            "others_see": "s",
                            "full_detail": {"type": ["array", "null"], "items": {"type": "string"}},
                        }
                    )
                ),
                "defaults": obj({"manager": "s", "member": "s", "manager_payroll": "s"}),
                "can_view_as": "b",
                "can_edit_defaults": "b",
                "view_version": "i",
            }
        ),
        "FinanceAction": obj(
            {"status": "s", "kind": "s", "action_id": "n", "code": "s", "reason": "s"}, required=["status", "kind"]
        ),
        "FinanceStatementReview": obj(
            {
                "token": "s",
                "expires_at": "s",
                "money_account_id": "s",
                "file_name": "s",
                "file_format": "s",
                "start": "s",
                "end": "s",
                "card": "b",
                "last4": "n",
                "signs": obj({"flipped": "b", "by": "s"}),
                "new": "i",
                "duplicate": "i",
                "tie": obj(
                    {
                        "opening": "i",
                        "added": "i",
                        "removed": "i",
                        "ends": "i",
                        "statement_says": {"type": ["integer", "null"]},
                        "off_by": "i",
                    }
                ),
                "rows": items(
                    obj(
                        {
                            "id": "s",
                            "date": "s",
                            "description": "s",
                            "amount": "i",
                            "vendor": obj({"id": "s", "name": "s"}),
                            "attribution": {"type": ["object", "null"]},
                            "status": "s",
                        }
                    )
                ),
            },
            required=["token", "expires_at", "tie", "rows"],
        ),
        "FinanceTransfers": obj(
            {
                "transfers": items(
                    obj(
                        {
                            "id": "s",
                            "status": {
                                "type": "string",
                                "enum": ["queued", "submitted", "unknown", "failed", "posted", "canceled"],
                            },
                            "amount": "i",
                            "currency": "s",
                            "memo": "s",
                            "from": obj({"id": "s", "name": "s", "last4": "n"}),
                            "to": obj({"id": "s", "name": "s", "last4": "n"}),
                            "created_by": "s",
                            "created_at": "s",
                            "settled_at": "n",
                            "last_error": "n",
                        },
                        required=["id", "status", "amount", "currency", "from", "to"],
                    )
                ),
                "next_cursor": "n",
            }
        ),
        "FinanceSalesTax": obj(
            {
                "year": "i",
                "currency": "s",
                "rows": items(
                    obj(
                        {
                            "period": "s",
                            "code": "s",
                            "jurisdiction": "s",
                            "taxable_sales": "i",
                            "exempt_sales": "i",
                            "tax_collected": "i",
                            "tax_remitted": "i",
                            "due": "i",
                            "due_on": "n",
                        },
                        required=["period", "code", "taxable_sales", "exempt_sales", "tax_collected", "due"],
                    )
                ),
            }
        ),
        "Finance1099": obj(
            {
                "year": "i",
                "currency": "s",
                "threshold": "i",
                "due_on": "s",
                "rows": items(
                    obj(
                        {
                            "vendor_id": "s",
                            "vendor": "s",
                            "tin_on_file": "b",
                            "cash_paid": "i",
                            "card_paid_excluded": "i",
                            "threshold": "i",
                            "reportable": "b",
                        },
                        required=["vendor_id", "vendor", "cash_paid", "reportable"],
                    )
                ),
            }
        ),
        "FinanceLineAttribution": obj(
            {
                "line_id": "s",
                "source": "s",
                "rule_id": "n",
                "rule_name": "n",
                "set_by": "n",
                "set_at": "n",
                "can_change": "b",
                "tags": "o",
                "amount": "i",
                "splits": items(obj({"teammate": "s", "team": "s", "share_bps": "i", "amount": "i"})),
                "history": items(
                    obj(
                        {
                            "id": "s",
                            "source": "s",
                            "rule_id": "n",
                            "splits": items(obj({"teammate": "s", "team": "s", "share_bps": "i"})),
                            "set_by": "s",
                            "set_at": "s",
                            "superseded_at": "n",
                        }
                    )
                ),
            }
        ),
        "FinanceAttributionRules": obj(
            {
                "rules": items(
                    obj(
                        {
                            "id": "s",
                            "name": "s",
                            "match": obj({"kind": "s", "id": "s", "label": "s"}),
                            "splits": items(obj({"teammate": "s", "team": "s", "share_bps": "i"})),
                            "enabled": "b",
                            "apply_from": "n",
                            "created_by": "s",
                            "updated_at": "s",
                        }
                    )
                ),
                "can_change": "b",
            }
        ),
        "FinanceAttributionDimensions": obj(
            {
                "dimensions": items(
                    obj(
                        {
                            "kind": "s",
                            "id": "s",
                            "name": "s",
                            "lines": "i",
                            "amount": "i",
                            "rule_id": "n",
                            "splits": items(obj({"teammate": "s", "team": "s", "share_bps": "i"})),
                        }
                    )
                ),
                "can_change": "b",
                "view_version": "i",
            }
        ),
        "FinanceMercuryUsers": obj(
            {"users": items(obj({"id": "s", "name": "s", "connection_id": "s", "email": "n", "updated_at": "s"}))}
        ),
        "FinanceAdAccount": obj(
            {
                "id": "s",
                "tool_id": "s",
                "external_id": "s",
                "name": "s",
                "currency": "s",
                "bot_slug": "n",
                "active": "b",
            }
        ),
        "FinanceAdAccounts": obj(
            {
                "tool_id": "s",
                "tool_name": "s",
                "platform": {"type": "string", "enum": ["google_ads", "meta_ads"]},
                "platform_label": "s",
                "import": obj(
                    {
                        "saved": "b",
                        "enabled": "b",
                        "history_from": "s",
                        "account_id": "s",
                        "account_name": "s",
                        "status": {"type": "string", "enum": ["never", "ok", "reconnect", "failed"]},
                        "last_import_at": "n",
                        "last_error": "s",
                        "run_requested": "b",
                        "every_hours": "i",
                    }
                ),
                "slots": items(
                    obj(
                        {
                            "slot": "s",
                            "label": "s",
                            "required": "b",
                            "credential_id": "s",
                            "options": items(obj({"id": "s", "name": "s"})),
                        }
                    )
                ),
                "books_lines": items(obj({"id": "s", "code": "s", "name": "s"})),
                "elsewhere": {
                    "type": ["object", "null"],
                    "description": "One of this tool's ad accounts another enabled import already reads (one import "
                    "per ad account), so this one can't be switched on; null when none",
                    "properties": {
                        "tool_id": {"type": "string"},
                        "tool_name": {"type": "string"},
                        "account": {"type": "string", "description": "The ad account, as its platform shows it"},
                    },
                },
                "month": "s",
                "month_label": "s",
                "accounts": items(
                    obj(
                        {
                            "id": "s",
                            "external_id": "s",
                            "name": "s",
                            "currency": "s",
                            "bot_slug": "n",
                            "active": "b",
                            "month_to_date": "i",
                        }
                    )
                ),
            }
        ),
        "FinanceConnectionResult": obj(
            {"action_id": "n", "kind": "s", "status": "s", "code": "s", "reason": "s", "approver": "n", "result": "o"},
            required=["status", "kind"],
        ),
        "FinanceBillsAddress": obj({"bills_email": "n", "inbox_id": "n"}),
        "FinanceBills": obj(
            {
                "bills": items(ref("FinanceDocumentRow")),
                "next_cursor": "n",
                "open": ref("FinanceOpenTotal"),
                "currency": "s",
                "bills_email": "n",
                "view_version": "i",
            }
        ),
        "FinanceChart": obj(
            {
                "accounts": items(
                    obj(
                        {
                            "id": "s",
                            "number": "s",
                            "name": "s",
                            "type": "s",
                            "subtype": "s",
                            "parent_id": "n",
                            "company_wide": "b",
                            "attributable": items(obj({"team": "s", "teammate": "s"}, required=[])),
                        },
                        required=["id", "number", "name", "type"],
                    )
                ),
                "more": "b",
                "view_version": "i",
            }
        ),
        "FinancePayFrom": obj(
            {
                "accounts": items(
                    obj(
                        {
                            "id": "s",
                            "name": "s",
                            "provider": "s",
                            "last4": "n",
                            "balance": {"type": ["integer", "null"]},
                            "available_balance": {"type": ["integer", "null"]},
                            "balance_as_of": "n",
                        },
                        required=["id", "name"],
                    )
                ),
                "currency": "s",
                "view_version": "i",
            }
        ),
        "FinanceFeedExpected": obj(
            {
                "feed_id": "s",
                "kind": {"type": "string", "enum": ["bill", "invoice"]},
                "amount": "i",
                "date": "s",
                "expected": items(
                    obj(
                        {
                            "settlement_id": "s",
                            "kind": "s",
                            "document_id": "s",
                            "number": "s",
                            "contact_id": "s",
                            "contact_name": "s",
                            "amount": "i",
                            "marked_on": "s",
                        }
                    )
                ),
                "more": "b",
                "view_version": "i",
            }
        ),
        "FinanceInvoices": obj(
            {
                "invoices": items(ref("FinanceDocumentRow")),
                "next_cursor": "n",
                "open": ref("FinanceOpenTotal"),
                "currency": "s",
                "view_version": "i",
            }
        ),
        "FinanceOpenTotal": obj(
            {
                "count": "i",
                "remaining": "i",
                "more": "b",
                "aging": obj({k: "i" for k in ("current", "1_30", "31_60", "61_90", "over_90")}),
            }
        ),
        "FinanceDocumentRow": obj(
            {
                "id": "s",
                "kind": {"type": "string", "enum": ["bill", "invoice"]},
                "number": "s",
                "vendor": DOC_CONTACT,
                "customer": DOC_CONTACT,
                "date": "s",
                "due_date": "n",
                "status": "s",
                "currency": "s",
                "total": "i",
                "paid": "i",
                "remaining": "i",
                "aging": "n",
                "days_late": "i",
                "memo": "s",
                "entry_id": "n",
                "attachments": "i",
                "created_by": "s",
                "created_at": "s",
                "origin": "s",
                "approved_by": "n",
                "approved_at": "n",
                "pay_on": "n",
                "payment": DOC_PAYMENT,
                "sent_at": "n",
                "written_off": "i",
                "payment_link": "n",
                "account": {"type": ["object", "null"], "additionalProperties": True},
                "split": "b",
                "team": "n",
                "paid_on": "n",
                "paid_by": "n",
            },
            required=["id", "kind", "number", "date", "status", "currency", "total", "paid", "remaining"],
        ),
        "FinanceDocument": {
            "allOf": [
                ref("FinanceDocumentRow"),
                obj(
                    {
                        "lines": items(
                            obj(
                                {
                                    "id": "s",
                                    "account": {"type": ["object", "null"], "additionalProperties": True},
                                    "amount": "i",
                                    "description": "s",
                                    "tags": "o",
                                    "taxable": "b",
                                },
                                required=["id", "account", "amount", "description", "tags"],
                            )
                        ),
                        "settlements": items(
                            obj(
                                {
                                    "id": "s",
                                    "from_kind": {
                                        "type": "string",
                                        "enum": ["payment", "feed", "credit", "account", "expected"],
                                    },
                                    "from_id": "n",
                                    "amount": "i",
                                    "settled_on": "s",
                                    "entry_id": "n",
                                    "voided_at": "n",
                                    "created_by": "s",
                                }
                            )
                        ),
                        "attachments": items(obj({"id": "s", "file_id": "s", "created_by": "s", "created_at": "s"})),
                        "actions": items(
                            obj(
                                {
                                    "action_id": "s",
                                    "kind": "s",
                                    "status": "s",
                                    "actor": "s",
                                    "approval_id": "n",
                                    "approval_to": "n",
                                    "rule_id": "n",
                                    "created_at": "s",
                                }
                            )
                        ),
                        "settlement_count": "i",
                        "settlements_more": "b",
                        "attachment_count": "i",
                        "attachments_more": "b",
                        "terms_days": {"type": ["integer", "null"]},
                        "schedule": {"type": ["object", "null"]},
                        "view_version": "i",
                    },
                    required=["lines", "settlements", "attachments", "actions"],
                ),
            ]
        },
        "FinanceContacts": obj(
            {
                "contacts": items(
                    obj(
                        {
                            "id": "s",
                            "name": "s",
                            "kind": {"type": "string", "enum": ["vendor", "customer", "both"]},
                            "emails": items({"type": "string"}),
                            "default_account": {"type": ["object", "null"], "additionalProperties": True},
                            "is_1099": "b",
                            "has_tax_id": "b",
                            "tax_id_last4": "n",
                            "recipient_id": "n",
                            "active": "b",
                            "owed": obj({"bills": "i", "invoices": "i"}, required=[]),
                            "created_at": "s",
                        },
                        required=["id", "name", "kind", "active"],
                    )
                ),
                "next_cursor": "n",
                "view_version": "i",
            }
        ),
        "FinanceInbox": obj(
            {
                "items": items(
                    obj(
                        {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "uncategorized",
                                    "bill_to_enter",
                                    "unmatched_receipt",
                                    "missing_receipt",
                                    "bill_due",
                                    "invoice_overdue",
                                    "budget_near_limit",
                                    "sync_failed",
                                    "period_ready",
                                    "unreconciled",
                                    "bill_email",
                                ],
                            },
                            "id": "s",
                            "label": "s",
                        }
                    )
                ),
                "counts": {"type": "object", "additionalProperties": {"type": "integer"}},
                "more": items({"type": "string"}),
                "next_cursor": "n",
                "currency": "s",
                "view_version": "i",
            }
        ),
        "FinanceReportOf": obj(
            {
                "kind": {
                    "type": "string",
                    "enum": [
                        "pnl",
                        "pnl_by",
                        "balance_sheet",
                        "cash_flow",
                        "budget_vs_actual",
                        "ap_aging",
                        "ar_aging",
                        "spend_by",
                        "campaigns",
                    ],
                },
                "prior": {"type": "object", "additionalProperties": True},
            },
            required=["kind"],
        ),
        "FinanceOrderRow": obj(
            {
                "id": "s",
                "number": "s",
                "contact": obj({"id": "s", "name": "s"}),
                "date": "s",
                "status": "s",
                "currency": "s",
                "total": "i",
                "memo": "s",
            },
            required=["id", "number", "contact", "date", "status", "currency", "total", "memo"],
        ),
        "Estimates": obj(
            {"estimates": items(ref("FinanceOrderRow")), "next_cursor": "n"}, required=["estimates", "next_cursor"]
        ),
        "Locations": obj(
            {"locations": items(ref("Location")), "next_cursor": "n"}, required=["locations", "next_cursor"]
        ),
        "Location": obj(
            {"id": "s", "name": "s", "kind": "s", "parent_id": "n", "address": "o", "archived_at": "n"},
            required=["id", "name", "kind", "address"],
        ),
        "Items": obj({"items": items(ref("Item")), "next_cursor": "n"}, required=["items", "next_cursor"]),
        "ItemOne": obj({"item": ref("Item")}, required=["item"]),
        "Item": obj(
            {
                "id": "s",
                "name": "s",
                "sku": "s",
                "kind": {"type": "string", "enum": ["service", "inventory", "non_inventory", "bundle", "category"]},
                "unit": "s",
                "price": {"type": ["integer", "null"]},
                "currency": "s",
                "archived_at": "n",
                "income_account_id": "n",
                "expense_account_id": "n",
                "asset_account_id": "n",
            },
            required=["id", "name", "sku", "kind", "unit", "currency"],
        ),
        "Projects": obj({"projects": items(ref("Project")), "next_cursor": "n"}, required=["projects", "next_cursor"]),
        "ProjectOne": obj({"project": ref("Project")}, required=["project"]),
        "Project": obj(
            {
                "id": "s",
                "name": "s",
                "customer_id": "n",
                "status": {"type": "string", "enum": ["open", "closed"]},
                "archived_at": "n",
            },
            required=["id", "name", "status"],
        ),
        "FinanceSearch": obj(
            {
                "results": items(
                    obj(
                        {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "entry",
                                    "feed",
                                    "bill",
                                    "invoice",
                                    "contact",
                                    "rule",
                                    "card",
                                    "item",
                                    "location",
                                    "project",
                                    "vendor",
                                ],
                            },
                            "id": "s",
                            "label": "s",
                        }
                    )
                ),
                "next_cursor": "n",
                "currency": "s",
                "view_version": "i",
            }
        ),
        "FinanceUnattributed": obj(
            {
                "lines": items(
                    obj(
                        {
                            "id": "s",
                            "date": "s",
                            "entry_id": "s",
                            "account": obj({"id": "s", "code": "s", "name": "s"}),
                            "amount": "i",
                            "description": "s",
                            "memo": "s",
                            "vendor": obj({"id": "s", "name": "s"}),
                            "source": "s",
                        }
                    )
                ),
                "next_cursor": "n",
                "start": "s",
                "end": "s",
                "as_of": "s",
                "currency": "s",
                "view_version": "i",
            },
            required=["lines", "start", "end", "as_of", "view_version"],
        ),
        "FinanceSubscriptionRow": obj(
            {
                "id": "s",
                "vendor": obj({"id": "s", "name": "s"}),
                "status": "s",
                "cadence": "s",
                "amount": "i",
                "monthly": "i",
                "seats": {"type": ["integer", "null"]},
                "next_charge_on": "n",
                "card": {
                    "type": ["object", "null"],
                    "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                },
                "attribution": items(obj({"to": "s", "share": "f"})),
                "source": "s",
                "as_of": "s",
            },
            required=[
                "id",
                "vendor",
                "status",
                "cadence",
                "amount",
                "monthly",
                "next_charge_on",
                "card",
                "attribution",
            ],
        ),
        "FinanceSubscriptions": obj(
            {"rows": items(ref("FinanceSubscriptionRow")), "monthly_total": "i", "as_of": "s"},
            required=["rows", "monthly_total", "as_of"],
        ),
        "FinanceCampaigns": obj(
            {
                "month": "s",
                "campaigns": items(
                    obj(
                        {
                            "id": "s",
                            "name": "s",
                            "platform": "s",
                            "ad_account": obj({"id": "s", "name": "s"}),
                            "owner": obj({"team": "s", "teammate": "s", "inherited": "b"}),
                            "month": obj(
                                {
                                    "spend": "i",
                                    "clicks": "i",
                                    "conversions": "f",
                                    "per_conversion": {"type": ["integer", "null"]},
                                }
                            ),
                            "as_of": "s",
                        }
                    )
                ),
                "totals": obj(
                    {
                        "spend": "i",
                        "clicks": "i",
                        "conversions": "f",
                        "per_conversion": {"type": ["integer", "null"]},
                    }
                ),
                "as_of": "s",
            },
            required=["month", "campaigns", "totals", "as_of"],
        ),
        "FinanceReceipts": obj(
            {
                "rows": items(
                    obj(
                        {
                            "id": "s",
                            "file": obj({"id": "s", "name": "s"}),
                            "read": obj(
                                {
                                    "amount": {"type": ["integer", "null"]},
                                    "date": {"type": ["string", "null"]},
                                    "vendor": {**obj({"id": "s", "name": "s"}), "type": ["object", "null"]},
                                }
                            ),
                            "match": {
                                **obj(
                                    {
                                        "feed": obj({"id": "s", "date": "s", "amount": "i", "description": "s"}),
                                        "confidence": {"type": ["number", "null"]},
                                    }
                                ),
                                "type": ["object", "null"],
                            },
                            "status": "s",
                            "as_of": "s",
                        }
                    )
                ),
                "next": {"type": ["string", "null"]},
                "as_of": "s",
            },
            required=["rows", "next", "as_of"],
        ),
        "FinanceMetric": obj(
            {
                "name": "s",
                "value": {"type": ["integer", "string", "null"]},
                "unit": {"type": "string", "enum": ["minor", "percent", "months", "count", "days"]},
                "as_of": "s",
                "data_as_of": "n",
                "stale": "b",
                "currency": "s",
                "view_version": "i",
            },
            required=["name", "value", "unit", "as_of"],
        ),
        "FinanceCloseChecklist": obj(
            {
                "month": "s",
                "start": "s",
                "end": "s",
                "status": "s",
                "ready": "b",
                "checks": items(obj({"key": "s", "ok": "b", "label": "s"})),
                "view_version": "i",
            }
        ),
        "FinanceActionKinds": obj(
            {
                "kinds": items(obj({"kind": "s", "words": "s", "live": "b"})),
                "kind": "s",
                "words": "s",
                "right": "s",
                "live": "b",
                "fields": items(obj({"name": "s", "type": "s", "required": "b", "words": "s"})),
            },
            required=[],
        ),
    }
)
