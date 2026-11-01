"""Finance over HTTP (API v2, spec §5 and §8): budgets, the P&L, expenses and one line's detail, each cut to the caller's
own view; requesting a Finance action (or a dry run of one); and reading an action's status. The Finance tab and the
hub tools call these.

A write goes through `mutate`, and its Idempotency-Key is the action's idempotency key too. `store.mutate` replays a
saved answer without running the call again, so the saved answer is only the action's id, kind and status: the first
call also carries the result it just made, and a retry (the saved answer, or once it has lapsed Finance's own replay of
the action) reads the rest afresh through the caller's current view: a reason that names amounts shows only while that
view still covers them."""
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import Field

from . import company_server, files, hook_relay
from .finance import actions as finance
from .finance import books_link
from .finance import due_dates as finance_due_dates
from .finance import equipment as equipment_planner
from .finance import reads as finance_reads
from .models import ID, Contract
from .store import Problem

STATUS = {"not_found": 404, "denied": 403, "invalid": 422, "conflict": 409, "not_available": 409,
          "period_closed": 409, "unbalanced": 422}
Group = Literal["department", "team", "teammate", "bot", "person", "vendor", "account", "location", "class", "project",
                "location_class", "location_project", "class_project"]
DAY = r"^\d{4}-\d{2}-\d{2}$"
Aging = Literal["current", "1_30", "31_60", "61_90", "over_90"]
Report = Literal["pnl", "pnl_by", "balance_sheet", "cash_flow", "budget_vs_actual", "ap_aging", "ar_aging", "spend_by",
                 "revenue_by_item"]
Metric = Literal["cash", "burn", "runway_months", "revenue", "spend", "budget_remaining", "budget_pct_used", "ap_due",
                 "ar_overdue", "uncategorized_count", "unreconciled_days"]


class FinanceConnectionRequest(Contract):
    """Connect a bank or Stripe with a stored credential. Mercury's token: the approver is a Mercury user other than the
    token's creator, who approves payments in Mercury; without one, payments stay off. Stripe's restricted key needs no
    approver."""
    provider: Literal["mercury", "stripe"] = "mercury"
    credential_id: str = Field(min_length=1, max_length=120)
    name: str | None = Field(None, max_length=120)
    approver_email: str | None = Field(None, max_length=200)
    token_creator_email: str | None = Field(None, max_length=200)
    server_ip: str | None = Field(None, max_length=64)


class FinanceActionRequest(Contract):
    """One Finance action: its kind (`budget.create`, `entry.post`, ...), its payload, and whether only to say what would
    happen."""
    kind: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class FinanceBooksRequest(Contract):
    """Connect QuickBooks or Xero: HQ holds the app's secret and sends the browser to the provider's consent page."""
    provider: Literal["quickbooks", "xero"]


BOOKS_RETURN = "/api/v2/finance/books/return"    # HQ sends the browser here, with the flow id the connect added
CLAIM = r"^[A-Za-z0-9._~-]{1,200}$"


def problem(e):
    return Problem(e.code, e.message, STATUS.get(e.code, 400))


def books_problem(e: books_link.BrokerFailed) -> Problem:
    """A books step HQ could not do, as a refusal the page can act on. HQ's own text never reaches the caller."""
    if isinstance(e, books_link.ReconnectRequired):
        return Problem("reconnect_required", "The books grant no longer works: connect them again in Finance", 409)
    if isinstance(e, books_link.NotConnected):
        return Problem("not_connected", "HQ holds no books grant for this company: connect them again in Finance", 409)
    if e.code == "revoke_failed":      # HQ kept the connection: a disconnect asked again is the retry
        return Problem("revoke_failed", "HQ could not take the books connection off yet, so it is kept: disconnect "
                       "again in a moment", 502, retryable=True)
    if e.code == "vault_write_failed":  # HQ's grant stands and Tico could not keep the rotated pair: connect again
        return Problem(
            "vault_write_failed",
            "Tico could not keep the books grant while taking it off, so the connection is kept: "
            "connect the books again in Finance, then disconnect",
            502, retryable=True, extra={"reason": "reconnect_required"},
        )
    return Problem("books_unavailable", "HQ could not finish that books step: try again in a moment", 502)


def install(app, store, auth, mutate):
    # Equipment photos are Library files: the read check needs this app's Auth (files.readable_by).
    equipment_planner.set_file_check(lambda c, actor, file_id: files.readable_by(c, auth, actor, file_id))

    def viewer(request):
        who = request.state.identity
        auth.domain(who)
        return who

    def read(fn):
        try:
            with store.read() as c:
                return fn(c)
        except finance.FinanceError as e:
            raise problem(e) from None

    def as_viewer(request, view_as):
        """The caller, or for the owner the teammate they view Finance as (audited)."""
        who = viewer(request)
        if not view_as or view_as == who.actor:
            return who.actor
        try:
            with store.transaction() as c:
                target = finance_reads.view_as(c, who.actor, view_as)
                finance.note_view_as(c, who.actor, target)
        except finance.FinanceError as e:
            raise problem(e) from None
        return target

    @app.get("/api/v2/finance/budgets")
    def finance_budgets(request: Request, include_inactive: bool = False, limit: int = Query(200, ge=1, le=500),
                        at: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"), view_as: str | None = None):
        """The budgets the caller sees, each with where it stands today (amount, actual, held, remaining), or with
        `at` in the period holding that day: the amount of the version in effect then."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.budgets(c, actor, include_inactive=include_inactive, limit=limit, on=at))

    @app.get("/api/v2/finance/pnl")
    def finance_pnl(request: Request, start: str | None = None, end: str | None = None,
                    period: Literal["day", "week", "month", "quarter", "year"] = "month",
                    basis: Literal["cash", "accrual"] = "cash", view: Literal["company", "expenses"] = "company",
                    team: str | None = None, teammate: str | None = None, budget_as_of: str | None = None,
                    view_as: str | None = None, teams: Annotated[list[str] | None, Query()] = None,
                    teammates: Annotated[list[str] | None, Query()] = None,
                    who: Literal["both", "bots", "people"] = "both", unattributed: bool = False):
        """The P&L the caller sees: income and expense lines by period (past, current and projected columns), with
        budget and projection beside each actual; the expenses view filters by teams and teammates (several at once),
        bots, people or both, and Unattributed."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.pnl(c, actor, start, end, period, basis, view=view, team=team,
                                                teammate=teammate, budget_as_of=budget_as_of, teams=teams or (),
                                                teammates=teammates or (), who=who, unattributed=unattributed))

    @app.get("/api/v2/finance/balance-sheet")
    def finance_balance_sheet(request: Request, as_of: str | None = None, view_as: str | None = None):
        """Assets, liabilities and equity on a day, each line with its sources. Owner-only unless granted."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.balance_sheet(c, actor, as_of))

    @app.get("/api/v2/finance/transactions")
    def finance_transactions(request: Request, source: Literal["all", "feed", "entry"] = "all",
                             money_account_id: str | None = None,
                             status: Literal["new", "matched", "posted", "ignored", "pending"] | None = None,
                             account_id: str | None = None, start: str | None = None, end: str | None = None,
                             q: str | None = Query(None, max_length=100), cursor: str | None = Query(None, max_length=600),
                             limit: int = Query(50, ge=1, le=200), view_as: str | None = None,
                             teams: Annotated[list[str] | None, Query()] = None,
                             teammates: Annotated[list[str] | None, Query()] = None,
                             who: Literal["both", "bots", "people"] = "both", unattributed: bool = False):
        """Feed rows and entries the caller sees, newest first, with filters, search and a cursor; teams, teammates,
        who and unattributed cut it as the P&L's expenses view does."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.transactions(c, actor, source=source, money_account_id=money_account_id,
                                                         status=status, account_id=account_id, start=start, end=end,
                                                         q=q, cursor=cursor, limit=limit, teams=teams or (),
                                                         teammates=teammates or (), who=who,
                                                         unattributed=unattributed))

    @app.get("/api/v2/finance/cards")
    def finance_cards(request: Request, status: Literal["active", "frozen", "cancelled"] | None = None,
                      holder: str | None = None, vendor: str | None = Query(None, max_length=120),
                      q: str = Query("", max_length=100), cursor: str | None = Query(None, max_length=600),
                      limit: int = Query(100, ge=1, le=200), view_as: str | None = None):
        """The cards the caller follows, last 4 only, with allocation, spend this period and where each is saved
        (`saved_on`). `vendor` keeps the cards saved on that vendor; `q` matches nickname, last 4 or holder."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.cards(c, actor, status=status, holder=holder, vendor=vendor, q=q,
                                                  cursor=cursor, limit=limit))

    @app.get("/api/v2/finance/sources")
    def finance_sources(request: Request, group: str | None = None, view_as: str | None = None):
        """Every source of the books, grouped with counts; mapping, sync and access per source. Owner-only unless
        granted."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.sources(c, actor, group=group,
                                                    gateway=finance_reads.gateway_connected(c, store.settings)))

    @app.get("/api/v2/finance/webhook-deliveries")
    def finance_webhook_deliveries(request: Request, limit: int = Query(20, ge=1, le=100)):
        """Calls to Finance's triggers (Mercury) still kept, newest first, with their bodies: for a finance admin who
        sees sources. The ordinary trigger routes never show these bodies."""
        who = viewer(request)
        return read(lambda c: finance_reads.webhook_deliveries(c, who.actor, limit=limit))

    @app.get("/api/v2/finance/access")
    def finance_access(request: Request):
        """Who can see what in Finance: the org chart with defaults and grants, private lines and the defaults."""
        who = viewer(request)
        return read(lambda c: finance_reads.access(c, who.actor))

    @app.get("/api/v2/finance/payments")
    def finance_payments(request: Request, status: Literal["queued", "sending", "awaiting_bank_approval", "submitted",
                                                           "settled", "returned", "failed", "cancelled",
                                                           "unknown"] | None = None,
                         cursor: str | None = Query(None, max_length=100), limit: int = Query(50, ge=1, le=200),
                         view_as: str | None = None):
        """Payments the caller made or whose line they see, newest first, with their lifecycle status."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.payments(c, actor, status=status, cursor=cursor, limit=limit))

    @app.get("/api/v2/finance/transfers")
    def finance_transfers(request: Request, cursor: str | None = Query(None, max_length=100),
                          limit: int = Query(50, ge=1, le=200), view_as: str | None = None):
        """Internal transfers between money accounts the caller sees, newest first, with their status."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.transfers(c, actor, cursor=cursor, limit=limit))

    @app.get("/api/v2/finance/recipients")
    def finance_recipients(request: Request):
        """Current recipients, last 4 only, for teammates who may pay."""
        who = viewer(request)
        return read(lambda c: finance_reads.recipients(c, who.actor))

    @app.get("/api/v2/finance/mercury-users")
    def finance_mercury_users(request: Request):
        """Mercury's users as the last sync read them, to pick a card holder: for teammates who may issue cards. A
        finance admin also sees each email, masked."""
        who = viewer(request)
        return read(lambda c: finance_reads.provider_users(c, who.actor))

    @app.get("/api/v2/finance/reconciliations")
    def finance_reconciliations(request: Request, money_account_id: str | None = None,
                                limit: int = Query(50, ge=1, le=200), view_as: str | None = None):
        """Reconciliations of the money accounts the caller sees, with each account's locked-through day."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.reconciliations(c, actor, money_account_id=money_account_id, limit=limit))

    @app.get("/api/v2/finance/statements")
    def finance_statements(request: Request, money_account_id: str | None = None,
                           limit: int = Query(50, ge=1, le=200), view_as: str | None = None):
        """Bank and card statements for the money accounts the caller sees."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.statements(c, actor, money_account_id=money_account_id, limit=limit))

    @app.get("/api/v2/finance/rules")
    def finance_rules(request: Request, external_rule_id: str | None = Query(None, max_length=120)):
        """Finance gate rules for teammates who may write them; `external_rule_id` lists those linked to one."""
        who = viewer(request)
        return read(lambda c: finance_reads.rules(c, who.actor, external_rule_id=external_rule_id))

    @app.get("/api/v2/finance/bills")
    def finance_bills(request: Request, status: str | None = Query(None, max_length=80),
                      vendor_id: str | None = Query(None, max_length=80), q: str = Query("", max_length=100),
                      start: str | None = Query(None, pattern=DAY), end: str | None = Query(None, pattern=DAY),
                      aging: Aging | None = None, late: bool = False,
                      order: Literal["date", "paid_on"] = "date",
                      cursor: str | None = Query(None, max_length=600), limit: int = Query(50, ge=1, le=200),
                      view_as: str | None = None):
        """Bills the caller sees (payables), newest first, with the open total by aging bucket. `status` takes
        draft|open|scheduled|paid|void|unpaid, comma-separated; `late` keeps those past due; `order` paid_on puts the
        last paid first."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.bills(c, actor, status=status, vendor_id=vendor_id, q=q, start=start,
                                                  end=end, aging=aging, late=late, order=order, cursor=cursor,
                                                  limit=limit))

    @app.get("/api/v2/finance/bills/{bill_id}")
    def finance_bill(request: Request, bill_id: ID, view_as: str | None = None):
        """One bill with its lines, payments against it, attachments and the actions and approvals on it."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.bill(c, actor, bill_id))

    @app.get("/api/v2/purchase-orders")
    def finance_purchase_orders(request: Request, status: str | None = Query(None, max_length=80),
                                vendor_id: str | None = Query(None, max_length=80),
                                q: str = Query("", max_length=100), cursor: str | None = Query(None, max_length=600),
                                limit: int = Query(50, ge=1, le=200), view_as: str | None = None):
        """Purchase orders the caller sees (payables), newest first; `next_cursor` pages. `status` takes
        draft|sent|approved|partial|closed|void, and `q` searches number, memo and vendor name."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.orders(c, actor, status=status, vendor_id=vendor_id, q=q, cursor=cursor,
                                                   limit=limit))

    @app.get("/api/v2/purchase-orders/{po_id}")
    def finance_purchase_order(request: Request, po_id: ID, view_as: str | None = None):
        """One purchase order with its lines and what each line has billed."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.order(c, actor, po_id))

    @app.get("/api/v2/estimates")
    def finance_estimates(request: Request, status: str | None = Query(None, max_length=80),
                          customer_id: str | None = Query(None, max_length=80),
                          q: str = Query("", max_length=100), cursor: str | None = Query(None, max_length=600),
                          limit: int = Query(50, ge=1, le=200), view_as: str | None = None):
        """Estimates the caller sees (receivables), newest first; `next_cursor` pages. `status` takes
        draft|sent|accepted|declined|invoiced, and `q` searches number, memo and customer name."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.estimates(c, actor, status=status, customer_id=customer_id, q=q,
                                                      cursor=cursor, limit=limit))

    @app.get("/api/v2/estimates/{estimate_id}")
    def finance_estimate(request: Request, estimate_id: ID, view_as: str | None = None):
        """One estimate with its lines; each line says whether it is taxable."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.estimate(c, actor, estimate_id))

    @app.get("/api/v2/locations")
    def finance_locations(request: Request, q: str = Query("", max_length=100),
                          archived: Literal["hide", "only", "all"] = "hide",
                          kind: Literal["store", "office", "warehouse", "plant", "customer_site", "vehicle",
                                        "other"] | None = None,
                          customer_id: str | None = Query(None, max_length=80),
                          cursor: str | None = Query(None, max_length=600), limit: int = Query(50, ge=1, le=200),
                          view_as: str | None = None):
        """Locations by name then id; `q` searches names, and `archived` hides the archived ones (the default), shows
        only them, or shows both. `kind` and `customer_id` narrow the list, and each row's `next_due_on` is its earliest
        open due day, or null. `next_cursor` pages."""
        actor = as_viewer(request, view_as)
        equal = {name: value for name, value in (("kind", kind), ("customer_id", customer_id)) if value}
        return read(lambda c: finance_reads.records(c, actor, kind="location", q=q, archived=archived, cursor=cursor,
                                                    limit=limit, equal=equal))

    @app.get("/api/v2/locations/{location_id}")
    def finance_location(request: Request, location_id: ID, view_as: str | None = None):
        """One location, archived or not."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.record(c, actor, kind="location", record_id=location_id))

    @app.get("/api/v2/due-dates")
    def finance_due_date_list(request: Request, q: str = Query("", max_length=100),
                              archived: Literal["hide", "only", "all"] = "hide",
                              subject_type: Literal["location", "equipment", "contact"] | None = None,
                              subject_id: str | None = Query(None, max_length=80),
                              cursor: str | None = Query(None, max_length=600),
                              limit: int = Query(50, ge=1, le=200), view_as: str | None = None):
        """Due dates by name: what they fall on (a location, equipment, or a vendor or customer), the repeat, the next
        day it falls due and the teammate who works it. `q` searches names, and `archived` hides the archived ones (the
        default), shows only them, or shows both. `subject_type` (with `subject_id`) narrows to one record; a merged
        location answers under its record. `next_cursor` pages."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_due_dates.listing(c, actor, q=q, archived=archived, cursor=cursor, limit=limit,
                                                        subject_type=subject_type, subject_id=subject_id))

    @app.get("/api/v2/due-dates/{due_date_id}")
    def finance_due_date_one(request: Request, due_date_id: ID, view_as: str | None = None):
        """One due date with its days, newest first: each day's completion and the task that carried it."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_due_dates.due_date(c, actor, due_date_id=due_date_id))

    @app.get("/api/v2/items")
    def finance_items(request: Request, q: str = Query("", max_length=100),
                      archived: Literal["hide", "only", "all"] = "hide",
                      kind: Literal["service", "inventory", "non_inventory", "bundle", "category", "raw_material",
                                    "assembly"] | None = None,
                      cursor: str | None = Query(None, max_length=600), limit: int = Query(50, ge=1, le=200),
                      view_as: str | None = None):
        """Products and services by name or sku; `archived` hides the archived ones (the default), shows only them, or
        shows both. `kind` narrows the list to one kind. `next_cursor` pages."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.records(c, actor, kind="item", q=q, archived=archived, cursor=cursor,
                                                    limit=limit, equal={"kind": kind} if kind else None))

    @app.get("/api/v2/items/{item_id}")
    def finance_item(request: Request, item_id: ID, view_as: str | None = None):
        """One product or service, archived or not, without the accounts it posts to."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.record(c, actor, kind="item", record_id=item_id))

    @app.get("/api/v2/equipment")
    def finance_equipment(request: Request, q: str = Query("", max_length=100),
                          archived: Literal["hide", "only", "all"] = "hide",
                          kind: Literal["owned", "customer"] | None = None,
                          customer_id: str | None = Query(None, max_length=80),
                          location_id: str | None = Query(None, max_length=80),
                          warranty_ends_by: str | None = Query(None, pattern=DAY),
                          warranty: Literal["ending"] | None = None,
                          due: Literal["soon", "overdue"] | None = None,
                          cursor: str | None = Query(None, max_length=600), limit: int = Query(50, ge=1, le=200),
                          view_as: str | None = None):
        """Equipment and vans by name, serial, make or model; `kind`, `customer_id` and `location_id` narrow it, and
        `warranty_ends_by` keeps the warranties ending on or before that day. `warranty=ending` keeps the warranties
        ending within the next 90 days; `due=soon|overdue` keeps the pieces with a due date soon or past due (each
        row's `next_due_on`). `archived` as on locations; paging counts the filtered list."""
        actor = as_viewer(request, view_as)
        filters = {"kind": kind, "customer_id": customer_id, "location_id": location_id,
                   "warranty_ends_by": warranty_ends_by, "warranty": warranty, "due": due}
        return read(lambda c: finance_reads.records(c, actor, kind="equipment", q=q, archived=archived, cursor=cursor,
                                                    limit=limit, filters={k: v for k, v in filters.items() if v}))

    @app.get("/api/v2/equipment/{equipment_id}")
    def finance_equipment_one(request: Request, equipment_id: ID, view_as: str | None = None):
        """One piece of equipment, archived or not."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.record(c, actor, kind="equipment", record_id=equipment_id))

    @app.get("/api/v2/projects")
    def finance_projects(request: Request, q: str = Query("", max_length=100),
                         archived: Literal["hide", "only", "all"] = "hide",
                         status: Literal["open", "closed"] | None = None,
                         customer_id: str | None = Query(None, max_length=80),
                         cursor: str | None = Query(None, max_length=600), limit: int = Query(50, ge=1, le=200),
                         view_as: str | None = None):
        """Projects by name; `archived` hides the archived ones (the default), shows only them, or shows both. `status`
        and `customer_id` narrow the list. `next_cursor` pages."""
        actor = as_viewer(request, view_as)
        equal = {name: value for name, value in (("status", status), ("customer_id", customer_id)) if value}
        return read(lambda c: finance_reads.records(c, actor, kind="project", q=q, archived=archived, cursor=cursor,
                                                    limit=limit, equal=equal))

    @app.get("/api/v2/projects/{project_id}")
    def finance_project(request: Request, project_id: ID, view_as: str | None = None):
        """One project, archived or not."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.record(c, actor, kind="project", record_id=project_id))

    @app.get("/api/v2/customers")
    def finance_customers(request: Request, q: str = Query("", max_length=100),
                          cursor: str | None = Query(None, max_length=600), limit: int = Query(50, ge=1, le=200),
                          view_as: str | None = None):
        """Active customers by name then id, for pickers: id and name only, to every teammate. `q` matches a name,
        and an email too for a viewer of receivables; `next_cursor` pages."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.customers(c, actor, q=q, cursor=cursor, limit=limit))

    @app.get("/api/v2/customers/{contact_id}")
    def finance_customer(request: Request, contact_id: ID, view_as: str | None = None):
        """A customer's panel: the contact, its active locations, and its open estimates and unpaid invoices where the
        caller sees receivables (null where not)."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.customer_panel(c, actor, contact_id))

    @app.get("/api/v2/finance/invoices")
    def finance_invoices(request: Request, status: str | None = Query(None, max_length=80),
                         customer_id: str | None = Query(None, max_length=80), q: str = Query("", max_length=100),
                         start: str | None = Query(None, pattern=DAY), end: str | None = Query(None, pattern=DAY),
                         aging: Aging | None = None, late: bool = False,
                         cursor: str | None = Query(None, max_length=600), limit: int = Query(50, ge=1, le=200),
                         view_as: str | None = None):
        """Invoices the caller sees (receivables), newest first, with the open total by aging bucket. `status` takes
        draft|sent|partial|paid|void|unpaid, comma-separated; `late` keeps those past due."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.invoices(c, actor, status=status, customer_id=customer_id, q=q,
                                                     start=start, end=end, aging=aging, late=late, cursor=cursor,
                                                     limit=limit))

    @app.get("/api/v2/finance/invoices/{invoice_id}")
    def finance_invoice(request: Request, invoice_id: ID, view_as: str | None = None):
        """One invoice with its lines, payments received, attachments and the actions and approvals on it."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.invoice(c, actor, invoice_id))

    @app.get("/api/v2/finance/contacts")
    def finance_contacts(request: Request, kind: Literal["vendor", "customer", "both"] | None = None,
                         q: str = Query("", max_length=100), active: bool | None = True,
                         cursor: str | None = Query(None, max_length=600), limit: int = Query(50, ge=1, le=200),
                         view_as: str | None = None):
        """Vendors and customers the caller sees, by name, with what their open bills and invoices owe."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.contacts(c, actor, kind=kind, q=q, active=active, cursor=cursor,
                                                     limit=limit))

    @app.get("/api/v2/finance/chart")
    def finance_chart(request: Request, for_: Literal["bill", "invoice", "journal"] | None = Query(None, alias="for"),
                      view_as: str | None = None):
        """The lines the caller may post to (id, number, name, type), for the bill, invoice and journal forms; for
        teammates who write to the books."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.chart(c, actor, for_=for_))

    @app.get("/api/v2/finance/pay-from")
    def finance_pay_from(request: Request, view_as: str | None = None):
        """The connected bank accounts the caller may pay bills from (finance.pay); balances only where the caller
        sees that account."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.pay_from(c, actor))

    @app.get("/api/v2/finance/feed/{feed_id}/expected")
    def finance_feed_expected(request: Request, feed_id: ID, view_as: str | None = None):
        """The payments marked paid a bank row may be the bank side of, for feed_match."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.feed_expected(c, actor, feed_id))

    @app.get("/api/v2/finance/inbox")
    def finance_inbox(request: Request, cursor: str | None = Query(None, max_length=100),
                      limit: int = Query(50, ge=1, le=200), view_as: str | None = None,
                      kind: str | None = Query(None, max_length=40)):
        """The caller's Finance work queue: uncategorized rows, bills to enter, receipts, bills due, overdue invoices,
        budgets near their limit, failed syncs and months ready to close, with counts per kind. With `kind`, that kind
        alone, paged past its 200."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.inbox(c, actor, cursor=cursor, limit=limit, kind=kind))

    @app.get("/api/v2/finance/reports/{kind}")
    def finance_report(request: Request, kind: Report, start: str | None = Query(None, pattern=DAY),
                       end: str | None = Query(None, pattern=DAY),
                       period: Literal["day", "week", "month", "quarter", "year"] = "month",
                       basis: Literal["cash", "accrual"] | None = None, compare: Literal["prior"] | None = None,
                       top_n: int = Query(20, ge=1, le=200), by: Group | None = None, view_as: str | None = None):
        """One report cut to the caller's view: pnl, pnl_by, balance_sheet, cash_flow, budget_vs_actual, ap_aging,
        ar_aging, spend_by or revenue_by_item (`by` picks the grouping; pnl_by defaults to location, spend_by to vendor;
        revenue_by_item needs the receivables view).
        `compare=prior` adds the window before; rows past `top_n` fold into "Other · n"."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.report(c, actor, kind, start=start, end=end, period=period, basis=basis,
                                                   compare=compare, top_n=top_n, by=by))

    @app.get("/api/v2/finance/tax/sales-tax")
    def finance_sales_tax(request: Request, year: int | None = Query(None, ge=2000, le=2100), view_as: str | None = None):
        """The sales tax workpaper for a year (this year when omitted): each jurisdiction's quarters, with taxable sales,
        tax collected, tax remitted, what is due and the day the return falls due. Tax workpapers need full view: anyone
        who does not see everything is refused, CSV included."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.sales_tax_report(c, actor, year))

    @app.get("/api/v2/finance/tax/sales-tax.csv")
    def finance_sales_tax_csv(request: Request, year: int | None = Query(None, ge=2000, le=2100),
                              view_as: str | None = None):
        """The sales tax workpaper as CSV, the same lines as on screen."""
        actor = as_viewer(request, view_as)
        body = read(lambda c: finance_reads.sales_tax_csv(c, actor, year))
        return Response(body, media_type="text/csv; charset=utf-8", headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="sales-tax-%s.csv"' % (year or "this-year")})

    @app.get("/api/v2/finance/tax/1099")
    def finance_form_1099(request: Request, year: int | None = Query(None, ge=2000, le=2100),
                          view_as: str | None = None):
        """The 1099 workpaper for a year (this year when omitted): each flagged vendor's cash paid with card payments
        left out, the threshold, whether a 1099-NEC is due and the day it falls due. Tax workpapers need full view: anyone
        who does not see everything is refused, CSV included."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.form_1099_report(c, actor, year))

    @app.get("/api/v2/finance/tax/1099.csv")
    def finance_form_1099_csv(request: Request, year: int | None = Query(None, ge=2000, le=2100),
                              view_as: str | None = None):
        """The 1099 workpaper as CSV, the same lines as on screen."""
        actor = as_viewer(request, view_as)
        body = read(lambda c: finance_reads.form_1099_csv(c, actor, year))
        return Response(body, media_type="text/csv; charset=utf-8", headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="1099-%s.csv"' % (year or "this-year")})

    @app.get("/api/v2/finance/search")
    def finance_search(request: Request,
                       kind: Literal["entry", "feed", "bill", "invoice", "contact", "rule", "card", "item", "location",
                                     "project", "po", "estimate", "equipment", "due_date"] | None = None,
                       q: str = Query("", max_length=100), status: str | None = Query(None, max_length=80),
                       vendor: str | None = Query(None, max_length=120),
                       contact_id: str | None = Query(None, max_length=80),
                       account_id: str | None = Query(None, max_length=80),
                       money_account_id: str | None = Query(None, max_length=80),
                       start: str | None = Query(None, pattern=DAY), end: str | None = Query(None, pattern=DAY),
                       aging: Aging | None = None,
                       contact_kind: Literal["vendor", "customer", "both"] | None = None, active: bool | None = None,
                       rule_kind: Literal["gate", "posting"] | None = None,
                       equipment_kind: Literal["owned", "customer"] | None = None,
                       customer_id: str | None = Query(None, max_length=80),
                       location_id: str | None = Query(None, max_length=80),
                       warranty_ends_by: str | None = Query(None, pattern=DAY),
                       cursor: str | None = Query(None, max_length=600), limit: int = Query(50, ge=1, le=200),
                       view_as: str | None = None):
        """Search entries, bank and card rows, bills, invoices, contacts, rules and equipment the caller sees. With
        `kind` it pages by `cursor`; without, it returns the first few of each kind. Equipment takes equipment_kind,
        customer_id, location_id and warranty_ends_by (a day)."""
        actor = as_viewer(request, view_as)
        named = {"status": status, "contact_id": contact_id, "account_id": account_id, "vendor": vendor,
                 "money_account_id": money_account_id, "start": start, "end": end, "aging": aging,
                 "kind": contact_kind if kind == "contact" else rule_kind if kind == "rule" else
                 equipment_kind if kind == "equipment" else None,
                 "customer_id": customer_id, "location_id": location_id, "warranty_ends_by": warranty_ends_by,
                 "active": None if active is None else ("1" if active else "0")}
        filters = {k: v for k, v in named.items() if v is not None}
        return read(lambda c: finance_reads.search(c, actor, kind=kind, q=q, filters=filters, limit=limit,
                                                   cursor=cursor))

    @app.get("/api/v2/finance/metrics/{name}")
    def finance_metric(request: Request, name: Metric, budget_id: str | None = Query(None, max_length=80),
                       start: str | None = Query(None, pattern=DAY), end: str | None = Query(None, pattern=DAY),
                       account_id: str | None = Query(None, max_length=80), team: str | None = None,
                       teammate: str | None = None, days: int | None = Query(None, ge=0, le=3650),
                       view_as: str | None = None):
        """One number with when it was worked out (and how fresh the bank data behind it is): cash, burn, runway,
        revenue, spend, a budget's remaining or percent used, bills due, invoices overdue, uncategorized rows,
        days unreconciled."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.metric(c, actor, name, budget_id=budget_id, start=start, end=end,
                                                   account_id=account_id, team=team, teammate=teammate, days=days))

    @app.get("/api/v2/finance/close/{month}")
    def finance_close(request: Request, month: str):
        """A month's close checklist, for teammates who do the books."""
        who = viewer(request)
        return read(lambda c: finance_reads.close_checklist(c, who.actor, month))

    @app.get("/api/v2/finance/action-kinds")
    def finance_action_kinds(request: Request, kind: str | None = Query(None, max_length=80)):
        """Every Finance action kind in a line, or one kind's payload fields, right and approval kind."""
        viewer(request)
        try:
            return finance.help(kind)
        except finance.FinanceError as e:
            raise problem(e) from None

    @app.post("/api/v2/finance/connections")
    def finance_connect(request: Request, body: FinanceConnectionRequest):
        """Connect Mercury or Stripe: the credential is tested and its accounts listed (outside any transaction), then
        the connection_create action maps them into the books. A finance admin's action; the Idempotency-Key is its key."""
        who = viewer(request)
        if not read(lambda c: finance_reads.may_connect(c, who.actor)):
            raise Problem("denied", "connecting a bank is for finance admins", 403)
        probe = finance.stripe_probe if body.provider == "stripe" else finance.mercury_probe
        found, why = probe(store, request.app.state.vault, body.credential_id)
        if why:
            raise Problem("invalid", why, 422)
        key = (request.headers.get("idempotency-key") or "").strip()
        extra = (("name", body.name),) if body.provider == "stripe" else (
            ("name", body.name), ("approver_email", body.approver_email),
            ("token_creator_email", body.token_creator_email), ("server_ip", body.server_ip))
        payload = {"provider": body.provider, "credential_id": body.credential_id, "probe": found,
                   **{k: v for k, v in extra if v}}

        def work(c):
            done = finance.act(c, who.actor, "connection_create", payload, key)
            return {"action_id": done.action_id, "kind": done.kind, "status": done.status, "code": done.code,
                    "reason": done.reason, "approver": done.approver, "result": dict(done.result)}
        saved = mutate(request, body, work)
        if saved.get("status") == "committed":
            finance.banking_ring()                     # after the commit: the worker's first sync starts now
        return saved

    def books_link_of(request):
        """The Gateway link the books broker calls HQ through. A demo or rehearsal copy calls no HQ at all."""
        if company_server.off(store.settings):
            raise Problem("not_available", "Books are not connected from a demo or rehearsal copy", 409)
        with store.read() as c:
            found = hook_relay.link_of(c, request.app.state.vault, store.settings)
        if found is None:
            raise Problem("gateway_key", "Tico's AI Gateway key is not set, so QuickBooks and Xero cannot be reached", 409)
        return found

    def books_admin(request):
        """The signed-in person, who must be a finance admin to connect or disconnect the books."""
        who = viewer(request)
        if not read(lambda c: finance_reads.may_connect(c, who.actor)):
            raise Problem("denied", "connecting the books is for finance admins", 403)
        return who

    @app.post("/api/v2/finance/books/connect")
    def finance_books_connect(request: Request, body: FinanceBooksRequest):
        """Start connecting QuickBooks or Xero for this install's company: HQ's consent page, which the browser goes to,
        and how long that link lives. Finance admins only, on a server linked to its Tico company."""
        who = books_admin(request)
        with store.read() as c:
            company = str((company_server.view(c).get("company") or {}).get("id") or "")
        if not company:
            raise Problem("not_linked", "Link this server to its Tico company in Settings before connecting the books", 409)
        link = books_link_of(request)
        try:
            url, expires = books_link.start(store, link, provider=body.provider, company_id=company, user=who.actor,
                                            return_base=store.settings.public_url.rstrip("/") + BOOKS_RETURN)
        except books_link.BrokerFailed as e:
            raise books_problem(e) from None
        return {"url": url, "expires_in": expires}

    @app.get(BOOKS_RETURN + "/{flow}")
    def finance_books_return(request: Request, flow: str = Path(pattern=r"^[A-Za-z0-9_-]{16,80}$"),
                             outcome: str | None = Query(None, alias="finance", max_length=20),
                             claim: str | None = Query(None, pattern=CLAIM),
                             reason: str | None = Query(None, max_length=80)):
        """HQ sends the browser back here after a books consent. Claims the grant once, keeps it in the vault and makes
        the books connection, then sends the browser to Finance. Only the person who started the consent, and only
        while they may connect, can claim it; the query is trusted for nothing but the claim id."""
        who = books_admin(request)
        link = books_link_of(request)
        page = store.settings.public_url.rstrip("/") + "/#/finance"
        try:
            books_link.finish(store, request.app.state.vault, link, flow=flow, user=who.actor, outcome=outcome or "",
                              reason=reason or "", claim_id=claim or "")
        except books_link.NotStarted:
            raise Problem("not_started", "That books connection was not started by you, or it has ended. Start it "
                                         "again in Finance", 409) from None
        except books_link.BrokerFailed as e:
            return RedirectResponse(page + "?" + urlencode({"books": "error", "reason": e.code}), status_code=302)
        return RedirectResponse(page + "?books=connected", status_code=302)

    @app.post("/api/v2/finance/books/{connection_id}/disconnect")
    def finance_books_disconnect(request: Request, connection_id: ID):
        """Disconnect a QuickBooks or Xero books connection: HQ drops its grant, then the tokens leave the vault. A retry
        after a failed HQ call is safe. Finance admins only."""
        who = books_admin(request)
        link = books_link_of(request)
        try:
            books_link.drop(store, request.app.state.vault, link, connection_id, by=who.actor)
        except books_link.UnknownConnection:
            raise Problem("not_found", "No such books connection", 404) from None
        except books_link.BrokerFailed as e:
            raise books_problem(e) from None
        return {"connection_id": connection_id, "status": "disconnected"}

    @app.get("/api/v2/finance/expenses")
    def finance_expenses(request: Request, start: str | None = None, end: str | None = None,
                         by: Group = "department", account_id: str | None = None, team: str | None = None,
                         teammate: str | None = None, top_n: int = Query(20, ge=1, le=200), view_as: str | None = None):
        """Expenses the caller sees, grouped by department, team, teammate, bot, person, vendor or line."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.expenses(c, actor, start, end, by, account_id=account_id, team=team,
                                                     teammate=teammate, top_n=top_n))

    @app.get("/api/v2/finance/lines/{account_id}")
    def finance_line(request: Request, account_id: str, start: str | None = None, end: str | None = None,
                     limit: int = Query(50, ge=1, le=200), top_n: int = Query(10, ge=1, le=200),
                     vendor: str | None = Query(None, max_length=200), view_as: str | None = None,
                     teams: Annotated[list[str] | None, Query()] = None,
                     teammates: Annotated[list[str] | None, Query()] = None,
                     who: Literal["both", "bots", "people"] = "both", unattributed: bool = False):
        """One line's spend by vendor, team and teammate, its entries and how they were paid, and its projection for
        next month, as far as the caller sees; `vendor` ("" for none) narrows all of it to one vendor, and teams,
        teammates, who and unattributed filter it as the P&L's expenses view does."""
        actor = as_viewer(request, view_as)
        return read(lambda c: finance_reads.line_detail(c, actor, account_id, start, end, limit=limit, top_n=top_n,
                                                        vendor=vendor, teams=teams or (), teammates=teammates or (),
                                                        who=who, unattributed=unattributed))

    @app.post("/api/v2/finance/actions")
    def finance_act(request: Request, body: FinanceActionRequest):
        """Request a Finance action: committed, sent to one person for approval, denied or failed. The Idempotency-Key
        is the action's key, so a retry never acts twice. `dry_run` writes nothing and says what would happen."""
        who = viewer(request)
        key = (request.headers.get("idempotency-key") or "").strip()
        if body.dry_run:
            try:
                with store.transaction() as c:
                    return finance.act(c, who.actor, body.kind, body.payload, key or "dry-run", dry_run=True).as_dict()
            except finance.FinanceError as e:
                raise problem(e) from None
        first = {}

        def work(c):
            done = finance.act(c, who.actor, body.kind, body.payload, key)
            first["result"] = done
            return {"action_id": done.action_id, "kind": done.kind, "status": done.status}
        receipt = body
        if any(k in body.payload for k in finance.BANK_FIELDS) or finance.carries_card_number(body.payload):
            # Bank numbers never reach the idempotency receipt, not even hashed: a keyed fingerprint stands in. A card
            # number is not kept either: the receipt says it was refused (store.mutate hashes what it is given).
            with store.transaction() as c:
                receipt = body.model_copy(update={"payload": finance.receipt_payload(c, body.kind, body.payload)})
        saved = mutate(request, receipt, work)
        if body.kind == "connection_sync" and saved.get("status") == "committed":
            finance.banking_ring()                     # after the commit: the worker syncs it now
        done = first.get("result")
        if done is not None and not done.replayed and done.action_id == saved.get("action_id"):
            return {**saved, "code": done.code, "reason": done.reason, "approval_id": done.approval_id,
                    "approver": done.approver, "rule": done.rule, "result": dict(done.result),
                    "impact": dict(done.impact)}
        if not saved.get("action_id"):
            return saved
        with store.read() as c:
            now = finance_reads.action_for(c, who.actor, saved["action_id"])
        return {**saved, **now} if now else saved

    @app.get("/api/v2/finance/actions/{action_id}")
    def finance_action(request: Request, action_id: ID):
        """An action's status, for whoever asked for it, the person named to decide its approval, or whoever's current
        view covers what it is about. Its reason shows to the approver, and to others only while their view covers it."""
        who = viewer(request)
        with store.read() as c:
            found = finance_reads.action_for(c, who.actor, action_id)
        if found is None:
            raise Problem("not_found", "No such Finance action", 404)
        return found
