"""Scripted turns of the built-in Bookkeeper (slug `bookkeeper`; templates/catalog/bookkeeper/AGENT.md and playbooks).

Each test is one accounting job from spec section 11. The Bookkeeper runs with its own run token and only its
hub_finance_* tools (the `call` helper of test_mcp.py, the same MCP door a bot uses), against a fake Mercury. Each call
asserts what the tool returned. Tests named `test_gap_*` check an instruction the product does not carry out: they are
strict xfails whose reason says what is missing, so the run stays green, and a fix makes them XPASS and fail until the
marker is removed.

Accounts: no hub tool lists the chart, so the ids of Revenue, Software and the like come from the books (`chart`), and
the gap is a test of its own (`test_gap_the_bot_can_list_accounts`)."""
from pathlib import Path

import pytest

from backend import bookkeeper as BK
from backend.finance import actions as FA
from backend.store import encode
from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401
from backend.tests.test_finance_fixtures import MERCURY, OWNER, FakeMercury, account, act, connect, sync_once  # noqa: F401
from backend.tests.test_mcp import call, rpc

CONFIG = {"template": "bookkeeper", "name": "bookkeeper", "runtime": "fake", "status": "active"}
PAYEE = {"name": "Northwind Hosting", "method": "ach", "account_number": "000099998888", "routing_number": "000000000"}


def hire(api):
    """The Bookkeeper as the product makes it once Finance is set up: its template, its default rights (book, ap, ar,
    pay, cards: backend/bookkeeper.py grant_defaults), and the Everything view the owner is asked to grant. Returns a
    run token for the bot, the credential its hub calls carry."""
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
        c.execute("INSERT INTO bots(slug,display_name,state) VALUES('bookkeeper','Bookkeeper','active')")
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('bookkeeper',?,'ana')", (encode(CONFIG),))
        assert BK.grant_defaults(c)
        granted = act(c, OWNER, "view_grant", {"subject": BK.ACTOR, "what": "everything"})
        assert granted.status == "committed", granted
    machine = runner(api)                 # enrolling a computer can place the bot already: take its generation
    with store.read() as c:
        placed = c.execute("SELECT runner_id, generation FROM assignments WHERE bot=?", (BK.SLUG,)).fetchone()
    if not placed or placed["runner_id"] != machine["runner_id"]:
        assign(api, machine, BK.SLUG, generation=placed["generation"] if placed else 0)
    ready(api, machine, [BK.SLUG])
    post(api, "chat/" + BK.SLUG, {"text": "Books check: work the queue."})
    return claim(api, machine, BK.SLUG)["token"]


def mercury(api):
    """Acme's Mercury connected as the owner, with a customer deposit and a wire fee in the feed, synced once."""
    store = api.app.state.store
    fake = FakeMercury()
    connect(store, fake)
    fake.txns["txn-dep"] = dict(MERCURY["txn_deposit"])
    fake.txns["txn-fee"] = dict(MERCURY["txn_bank_fee"])
    sync_once(store, fake)
    return fake


def chart(api, code):
    """A chart account's id, read from the books (no hub tool lists the chart: see test_gap_the_bot_can_list_accounts)."""
    with api.app.state.store.read() as c:
        return account(c, code)


def ask(api, token, kind, payload, op, **extra):
    """The bot asks for one Finance action, its operation id as the action's key: (tool error flag, result)."""
    return call(api, "hub_finance_act", {"kind": kind, "payload": payload, "operation_id": op, **extra}, token=token)


def vendor_bill(api, token, number="NW-1042", amount=45_000):
    """A vendor and its bill, entered and approved the way the Bookkeeper enters one (AGENT.md, Each Books check, 2)."""
    err, vendor = ask(api, token, "contact_create", {"name": "Northwind Hosting", "kind": "vendor"}, "vendor:northwind")
    assert not err and vendor["status"] == "committed", vendor
    err, made = ask(api, token, "bill_create", {
        "vendor_id": vendor["result"]["contact_id"], "number": number, "bill_date": "2026-10-20", "terms_days": 30,
        "origin": "email", "lines": [{"account_id": chart(api, "6100"), "amount": amount,
                                      "description": "Hosting, October"}]}, f"bill-create:{number}")
    assert not err and made["status"] == "committed", made
    bill_id = made["result"]["bill_id"]
    err, approved = ask(api, token, "bill_approve", {"bill_id": bill_id}, f"bill-approve:{bill_id}")
    assert not err and approved["status"] == "committed", approved
    return bill_id


# ----------------------------------------------------------------------------- categorize (Books check, item 4)
def test_categorize_turn(api):
    token = hire(api)
    mercury(api)
    err, queue = call(api, "hub_finance_inbox", {"kind": "uncategorized"}, token=token)
    assert not err and {r["label"].split(" · ")[0] for r in queue["items"]} == {"Acme Customer", "Mercury"}, queue
    err, found = call(api, "hub_finance_search", {"kind": "feed", "q": "Mercury"}, token=token)
    fee = next(r for r in found["results"] if r["provider_kind"] == "wireFee")
    # A posted row: tag its line (the lines form of categorize: no account id needed).
    err, tagged = ask(api, token, "categorize", {"lines": [{"line_id": fee["lines"][0]["line_id"],
                                                          "tags": {"team": "web"}}]}, f"categorize:{fee['id']}")
    assert not err and tagged["status"] == "committed", tagged
    # The new customer deposit: categorize the row into Revenue (the account id from the books, see the module note).
    deposit = next(r for r in queue["items"] if r["label"].startswith("Acme Customer"))
    err, posted = ask(api, token, "categorize", {"feed_id": deposit["feed_id"], "account_id": chart(api, "4000")},
                      f"categorize:{deposit['feed_id']}")
    assert not err and posted["status"] == "committed", posted
    err, again = ask(api, token, "categorize", {"feed_id": deposit["feed_id"], "account_id": chart(api, "4000")},
                     f"categorize:{deposit['feed_id']}")
    assert not err and again["action_id"] == posted["action_id"], again          # a retry acts once
    err, left = call(api, "hub_finance_inbox", {"kind": "uncategorized"}, token=token)
    assert not err and deposit["feed_id"] not in {r["feed_id"] for r in left["items"]}, left


def test_categorize_proposes_a_posting_rule(api):   # AGENT.md step 4: a pattern repeats, so propose a rule
    token = hire(api)
    err, out = ask(api, token, "rule_create", {
        "kind": "posting", "name": "Mercury fees", "when": {"counterparty": "Mercury"},
        "outcome": {"outcome": "post", "account_id": chart(api, "6900")},
        "examples": [{"input": {"counterparty": "Mercury"}, "expect": "post", "account_id": chart(api, "6900")}]},
        "rule:mercury-fees")
    assert not err and out["status"] == "needs_approval", out


# ----------------------------------------------------------------------------- bills (Books check 2; playbook bill-due)
def test_bills_turn(api):
    token = hire(api)
    bill_id = vendor_bill(api, token)
    err, edited = ask(api, token, "bill_update", {"bill_id": bill_id, "memo": "October hosting"}, f"bill-update:{bill_id}")
    assert not err and edited["status"] == "committed", edited
    err, found = call(api, "hub_finance_search", {"kind": "bill", "q": "NW-1042"}, token=token)
    assert not err and [r["id"] for r in found["results"]] == [bill_id], found
    err, queue = call(api, "hub_finance_inbox", {"kind": "bill_to_enter"}, token=token)
    assert not err and queue["items"] == [], queue


# ----------------------------------------------------------------------------- pay (playbook: bill-due.md)
def test_pay_turn(api):
    token = hire(api)
    fake = mercury(api)
    bill_id = vendor_bill(api, token)
    err, bill = call(api, "hub_finance_search", {"kind": "bill", "q": "NW-1042"}, token=token)
    assert not err and bill["results"][0]["id"] == bill_id, bill
    # The payee is the bill's vendor: a payment to any other recipient is refused ("that recipient is not this bill's vendor").
    err, vendor = call(api, "hub_finance_search", {"kind": "contact", "q": "Northwind"}, token=token)
    assert not err and vendor["results"], vendor
    err, payee = ask(api, token, "recipient_add", {**PAYEE, "contact_id": vendor["results"][0]["id"]},
                     "recipient:northwind")
    assert not err and payee["status"] == "committed", payee
    recipient_id = payee["result"]["recipient_id"]
    sync_once(api.app.state.store, fake)
    with api.app.state.store.read() as c:
        money = c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0]
    body = {"recipient_id": recipient_id, "amount": 45_000, "money_account_id": money,
            "account_id": chart(api, "2000"), "obligation": f"bill:{bill_id}"}
    err, preview = ask(api, token, "payment_create", body, f"bill-due:{bill_id}:due_soon", dry_run=True)
    assert not err and preview["status"] == "needs_approval", preview
    err, asked = ask(api, token, "payment_create", body, f"bill-due:{bill_id}:due_soon")
    assert not err and asked["status"] == "needs_approval" and asked["kind"] == "payment_create", asked
    assert asked["approver"] == OWNER, asked                      # money out waits for one person: the owner here
    assert fake.sent("request_send_money") == []                  # nothing was sent to the bank
    assert get(api, f"approvals/{asked['approval_id']}")["kind"] == "spend"


# ----------------------------------------------------------------------------- invoices (playbook: invoice-overdue.md)
def test_invoices_turn(api):
    token = hire(api)
    fake = mercury(api)
    err, customer = ask(api, token, "contact_create", {"name": "Acme Customer", "kind": "customer"}, "customer:acme")
    assert not err and customer["status"] == "committed", customer
    err, made = ask(api, token, "invoice_create", {
        "customer_id": customer["result"]["contact_id"], "number": "INV-1042", "invoice_date": "2026-10-01",
        "terms_days": 30, "lines": [{"account_id": chart(api, "4000"), "amount": 50_000,
                                     "description": "October support"}]}, "invoice:INV-1042")
    assert not err and made["status"] == "committed", made
    invoice_id = made["result"]["invoice_id"]
    err, sent = ask(api, token, "invoice_send", {"invoice_id": invoice_id}, f"invoice-send:{invoice_id}")
    assert not err and sent["status"] == "committed", sent
    err, found = call(api, "hub_finance_search", {"kind": "invoice", "q": "INV-1042"}, token=token)
    assert not err and found["results"][0]["id"] == invoice_id and found["results"][0]["status"] == "sent", found
    # The payment is in the feed: record it against the invoice (invoice-overdue.md, step 2).
    err, feed = call(api, "hub_finance_search", {"kind": "feed", "q": "Acme Customer"}, token=token)
    deposit = next(r for r in feed["results"] if r["amount"] == 50_000)
    err, paid = ask(api, token, "invoice_record_payment", {"invoice_id": invoice_id, "amount": 50_000,
                                                           "feed_id": deposit["id"]}, f"invoice-paid:{invoice_id}")
    assert not err and paid["status"] == "committed", paid
    assert fake.sent("request_send_money") == []


@pytest.mark.xfail(strict=True, reason="GAP: invoice-overdue.md step 3 says to send one reminder for the stage through "
                   "Finance. The invoice kinds have no reminder (backend/finance/core/kinds.py: invoice_create, "
                   "invoice_send, invoice_record_payment, invoice_write_off, invoice_void).")
def test_gap_invoice_reminder_is_a_finance_action(api):
    token = hire(api)
    err, helped = call(api, "hub_finance_act", {"kind": "help"}, token=token)
    assert not err and "invoice_remind" in {k["kind"] for k in helped["kinds"]}, helped


# ----------------------------------------------------------------------------- receipts (Books check 3)
def test_receipts_turn(api):
    token = hire(api)
    mercury(api)
    err, queue = call(api, "hub_finance_inbox", {"kind": "unmatched_receipt"}, token=token)
    assert not err and queue["items"] == [], queue
    err, feed = call(api, "hub_finance_search", {"kind": "feed", "q": "Mercury"}, token=token)
    assert not err and feed["results"], feed


@pytest.mark.xfail(strict=True, reason="GAP: attach takes any Library file id without checking that the file exists or "
                   "that the asker may read it: file_ids ['fil_missing'] commits and inserts a fin_attachments row "
                   "(backend/finance/documents.py attach; backend/finance/store/ap.py attach). bill_create's attachments "
                   "have the same gap (documents.py _attach_files).")
def test_gap_attach_refuses_a_file_not_in_the_library(api):
    token = hire(api)
    mercury(api)
    err, feed = call(api, "hub_finance_search", {"kind": "feed", "q": "Mercury"}, token=token)
    fee = feed["results"][0]
    err, refused = ask(api, token, "attach", {"subject_kind": "feed", "subject_id": fee["id"],
                                              "file_ids": ["fil_missing"]}, f"attach:{fee['id']}")
    assert err or refused["status"] in ("failed", "denied"), refused


def test_the_bot_reads_a_file_with_hub_file_read(api):
    """AGENT.md's bill and receipt steps read a Library file with `hub file read` (clients/hubcli.py): the tool is
    offered to the bot, and the instructions name no command the hub does not have (`hub file get` was one)."""
    token = hire(api)
    names = {t["name"] for t in rpc(api, "tools/list", token=token)["result"]["tools"]}
    agent = (Path(__file__).resolve().parents[2] / "templates" / "catalog" / "bookkeeper" / "AGENT.md").read_text()
    assert "hub_file_read" in names and "hub file read" in agent and "hub file get" not in agent


# ----------------------------------------------------------------------------- reconcile (Books check 6)
def test_reconcile_turn(api):
    token = hire(api)
    mercury(api)
    err, feed = call(api, "hub_finance_search", {"kind": "feed", "q": "Acme"}, token=token)
    deposit = feed["results"][0]
    money = deposit["money_account"]["id"]
    # Reconciling covers posted rows: post the deposit first, as the categorize turn does.
    err, posted = ask(api, token, "categorize", {"feed_id": deposit["id"], "account_id": chart(api, "4000")},
                      f"categorize:{deposit['id']}")
    assert not err and posted["status"] == "committed", posted
    err, fee = call(api, "hub_finance_search", {"kind": "feed", "q": "Mercury"}, token=token)
    # The statement balance is Mercury's balance after both rows: the opening entry (124,765.50) plus 500.00 less 15.00,
    # which is the fixture's currentBalance 125,250.50. Amounts are minor units.
    err, done = ask(api, token, "reconcile", {"money_account_id": money, "statement_end": "2026-10-14",
                                              "statement_balance": 12_525_050,
                                              "cleared": [deposit["id"], fee["results"][0]["id"]]},
                    f"reconcile:{money}:2026-10-14")
    assert not err and done["status"] == "committed" and done["result"]["difference"] == 0, done
    assert done["result"]["status"] == "done", done
    err, metric = call(api, "hub_finance_metric", {"name": "unreconciled_days"}, token=token)
    assert not err and "value" in metric, metric


# ----------------------------------------------------------------------------- close (playbook: close-month.md)
def test_close_turn(api):
    token = hire(api)
    mercury(api)
    err, checklist = call(api, "hub_finance_report", {"kind": "close", "month": "2026-10"}, token=token)
    assert not err, checklist
    err, variance = call(api, "hub_finance_report", {"kind": "budget_vs_actual", "start": "2026-10-01",
                                                     "end": "2026-10-31"}, token=token)
    assert not err, variance
    err, asked = ask(api, token, "close_period", {"month": "2026-10"}, "close:2026-10")
    assert not err and asked["status"] == "needs_approval" and asked["rule"] == "default:close", asked
    assert asked["approver"] == OWNER, asked
    assert get(api, f"approvals/{asked['approval_id']}")["kind"] == "finance"


@pytest.mark.xfail(strict=True, reason="GAP: close-month.md step 3 asks for close_period 'with the month and the note', but "
                   "close_period takes only `month` (backend/finance/core/kinds.py SHAPES close_period).")
def test_gap_close_period_takes_the_note(api):
    token = hire(api)
    err, asked = ask(api, token, "close_period", {"month": "2026-10", "note": "Software over plan by 12%"},
                     "close:2026-10:note")
    assert not err and asked["status"] == "needs_approval", asked


# ----------------------------------------------------------------------------- monthly report (playbook: monthly-report.md)
def test_monthly_report_turn(api):
    token = hire(api)
    mercury(api)
    err, pnl = call(api, "hub_finance_report", {"kind": "pnl", "start": "2026-10-01", "end": "2026-10-31",
                                                "compare": "prior"}, token=token)
    assert not err and "rows" in pnl, pnl
    for args in ({"kind": "balance_sheet", "end": "2026-10-31"},
                 {"kind": "cash_flow", "start": "2026-10-01", "end": "2026-10-31"},
                 {"kind": "budget_vs_actual", "start": "2026-10-01", "end": "2026-10-31"}):
        err, report = call(api, "hub_finance_report", args, token=token)
        assert not err, (args, report)
    err, runway = call(api, "hub_finance_metric", {"name": "runway_months"}, token=token)
    assert not err and runway["unit"] == "months" and "as_of" in runway and "value" in runway, runway


# ----------------------------------------------------------------------------- watch (playbook: books-watch.md)
def test_watch_turn(api):
    token = hire(api)
    mercury(api)
    for name in ("runway_months", "uncategorized_count", "unreconciled_days", "cash"):
        err, metric = call(api, "hub_finance_metric", {"name": name}, token=token)
        assert not err and "as_of" in metric, (name, metric)
    err, queue = call(api, "hub_finance_inbox", {}, token=token)
    assert not err and queue["counts"]["uncategorized"] == 2, queue


@pytest.mark.xfail(strict=True, reason="GAP: books-watch.md names duplicate charges and price increases as Watch checks, "
                   "to be read with the Finance metric and inbox reads. Neither is a metric or inbox kind "
                   "(clients/hubtools.py FINANCE_METRICS, FINANCE_INBOX).")
def test_gap_watch_duplicates_and_price_rises_are_readable(api):
    token = hire(api)
    for name in ("duplicate_charges", "price_increases"):
        err, metric = call(api, "hub_finance_metric", {"name": name}, token=token)
        assert not err, (name, metric)


# ----------------------------------------------------------------------------- sales tax (spec 11)
def test_sales_tax_turn(api):
    token = hire(api)
    err, authority = ask(api, token, "contact_create", {"name": "California CDTFA", "kind": "vendor"}, "vendor:cdtfa")
    assert not err and authority["status"] == "committed", authority
    err, jurisdiction = ask(api, token, "tax_jurisdiction_add", {"code": "US-CA", "name": "California sales tax",
                                                                 "authority_id": authority["result"]["contact_id"],
                                                                 "due_day": 28}, "tax-jurisdiction:US-CA")
    assert not err and jurisdiction["status"] == "committed", jurisdiction
    # The amount is typed: without it tax_remit reads the workpaper, which this test has no sales feed for.
    err, remit = ask(api, token, "tax_remit", {"jurisdiction": "US-CA", "period": "2026-Q3", "amount": 12_000},
                     "tax-remit:US-CA:2026-Q3")
    assert not err and remit["status"] in ("committed", "needs_approval"), remit


@pytest.mark.xfail(strict=True, reason="GAP: AGENT.md 'Sales tax return due' says to check the amount tax_remit shows "
                   "against the quarter's sales tax before approving. No hub read gives sales tax (FINANCE_REPORTS has no "
                   "sales tax kind; the route finance/tax/sales-tax is not a hub tool).")
def test_gap_sales_tax_is_readable_before_approval(api):
    token = hire(api)
    err, report = call(api, "hub_finance_report", {"kind": "sales_tax", "period": "quarter"}, token=token)
    assert not err, report


# ----------------------------------------------------------------------------- year end (spec 11: 1099-NEC by Jan 31)
@pytest.mark.xfail(strict=True, reason="GAP: AGENT.md asks the owner to review the 1099-NEC flagged vendors before the due "
                   "date. No hub read gives the 1099 list: the route finance/tax/1099 is not a hub tool and is not a "
                   "hub_finance_report kind.")
def test_gap_year_end_1099_list_is_readable(api):
    token = hire(api)
    err, flagged = call(api, "hub_finance_report", {"kind": "1099"}, token=token)
    assert not err, flagged


# ----------------------------------------------------------------------------- what the bot may never do (AGENT.md, Never)
def test_bookkeeper_is_refused_what_it_may_never_do(api):
    token = hire(api)
    before = get(api, "finance/access")
    for kind, payload in (("right_grant", {"teammate": BK.ACTOR, "right": "finance.rules"}),
                          ("payment_settings", {"direct_send": True}),
                          ("view_grant", {"subject": BK.ACTOR, "what": "everything"}),
                          ("connection_create", {"provider": "mercury", "credential_id": "cred-mercury"})):
        err, out = ask(api, token, kind, payload, f"never:{kind}")
        assert err or out["status"] != "committed", (kind, out)
    assert get(api, "finance/access") == before


# ----------------------------------------------------------------------------- gaps in what the tools can read
@pytest.mark.xfail(strict=True, reason="GAP: the Books check lists 'unreconciled' and 'bill_email' items, but "
                   "hub_finance_inbox's kind enum has neither (clients/hubtools.py FINANCE_INBOX), so the bot cannot page "
                   "them with its tools.")
def test_gap_books_check_kinds_can_be_paged(api):
    token = hire(api)
    for kind in ("unreconciled", "bill_email"):
        err, out = call(api, "hub_finance_inbox", {"kind": kind}, token=token)
        assert not err, (kind, out)


@pytest.mark.xfail(strict=True, reason="GAP: categorize, bill_create and rule_create need account ids, and no hub tool lists "
                   "the chart: hub_finance_search has no account kind (clients/hubtools.py FINANCE_SEARCH).")
def test_gap_the_bot_can_list_accounts(api):
    token = hire(api)
    err, out = call(api, "hub_finance_search", {"kind": "account", "q": "Software"}, token=token)
    assert not err and any(r.get("code") == "6100" for r in out["results"]), out
