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
from backend.store import H, encode
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


def test_receipts_are_found_by_the_hourly_check(api):
    """Receipts run on the hourly Books check, not a settlement trigger (spec 11): a settled card charge with no receipt
    is listed by the next check, which opens its one task; a check with nothing new is quiet (no task)."""
    store = api.app.state.store
    hire(api)
    fake = FakeMercury()
    connect(store, fake)
    fake.txns["txn-dep"] = dict(MERCURY["txn_deposit"])
    fake.txns["txn-fee"] = dict(MERCURY["txn_bank_fee"])
    fake.txns["txn-card-1"] = dict(MERCURY["txn_card_posted"])      # a card charge, posted, with no receipt
    sync_once(store, fake)
    with store.transaction() as c:
        first = BK.check(c, api.app.state.auth, H.now())
    assert first["task"] and first["items"] >= 1, first
    with store.read() as c:
        body = c.execute("SELECT body FROM tasks WHERE id=?", (first["task"],)).fetchone()[0]
    assert "missing_receipt (1)" in body and "Acme Cloud" in body, body
    with store.transaction() as c:
        quiet = BK.check(c, api.app.state.auth, H.now())
    assert quiet["task"] is None and quiet["items"] == 0, quiet


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


def test_close_period_takes_the_month_and_nothing_else(api):
    """close_period takes only `month` (kinds.py SHAPES): the close asks for approval, and a note is refused as an
    unknown field. The variance note goes on the task (close-month.md step 3)."""
    token = hire(api)
    err, asked = ask(api, token, "close_period", {"month": "2026-10"}, "close:2026-10")
    assert not err and asked["status"] == "needs_approval", asked
    err, noted = ask(api, token, "close_period", {"month": "2026-10", "note": "Software over plan by 12%"},
                     "close:2026-10:note")
    assert err or noted["status"] != "needs_approval", noted


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


def test_watch_duplicates_and_price_rises_are_readable(api):
    """books-watch.md reads the duplicate and price-increase checks as metrics (plain code, no model call): the same two
    charges of one amount to one payee on one day are one duplicate; price_increases is quiet with only two charges."""
    store = api.app.state.store
    token = hire(api)
    fake = FakeMercury()
    connect(store, fake)
    fake.txns["txn-dep"] = dict(MERCURY["txn_deposit"])
    fake.txns["txn-card-1"] = dict(MERCURY["txn_card_posted"])
    fake.txns["txn-card-2"] = dict(MERCURY["txn_card_posted"], id="txn-card-2")
    sync_once(store, fake)
    err, dupes = call(api, "hub_finance_metric", {"name": "duplicate_charges"}, token=token)
    assert not err and dupes["unit"] == "count" and dupes["value"] == 1 and "as_of" in dupes, dupes
    assert "Acme Cloud" in dupes["labels"][0], dupes
    err, rises = call(api, "hub_finance_metric", {"name": "price_increases"}, token=token)
    assert not err and rises["unit"] == "count" and rises["value"] == 0 and "as_of" in rises, rises


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


def test_gap_sales_tax_is_readable_before_approval(api):
    """AGENT.md 'Sales tax return due': the amount tax_remit shows is checked against the year's sales tax workpaper before
    approval. The workpaper is a whole read for someone who sees everything (the hire helper's Everything grant)."""
    token = hire(api)
    err, authority = ask(api, token, "contact_create", {"name": "California CDTFA", "kind": "vendor"}, "vendor:cdtfa")
    assert not err and authority["status"] == "committed", authority
    err, jurisdiction = ask(api, token, "tax_jurisdiction_add", {"code": "US-CA", "name": "California sales tax",
                                                                 "authority_id": authority["result"]["contact_id"],
                                                                 "due_day": 28}, "tax-jurisdiction:US-CA")
    assert not err and jurisdiction["status"] == "committed", jurisdiction
    err, report = call(api, "hub_finance_report", {"kind": "sales_tax", "year": 2026}, token=token)
    assert not err and report["year"] == 2026 and "rows" in report, report
    assert any(r["code"] == "US-CA" for r in report["rows"]), report


# ----------------------------------------------------------------------------- year end (spec 11: 1099-NEC by Jan 31)
def test_gap_year_end_1099_list_is_readable(api):
    """AGENT.md asks the owner to review the 1099-NEC flagged vendors before the due date: the workpaper lists them."""
    token = hire(api)
    err, vendor = ask(api, token, "contact_create", {"name": "Northwind Hosting", "kind": "vendor", "is_1099": True},
                      "vendor:northwind-1099")
    assert not err and vendor["status"] == "committed", vendor
    err, flagged = call(api, "hub_finance_report", {"kind": "1099", "year": 2026}, token=token)
    assert not err and flagged["year"] == 2026 and "threshold" in flagged and "rows" in flagged, flagged
    assert any(r["vendor"] == "Northwind Hosting" for r in flagged["rows"]), flagged


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
def test_gap_books_check_kinds_can_be_paged(api):
    """The Books check lists books not reconciled for over a month and emailed bills Finance did not take. Each is a
    kind of hub_finance_inbox, paged like the others: one page of bill_email holds one bill, and next_cursor goes on."""
    store = api.app.state.store
    token = hire(api)
    fake = FakeMercury()
    connect(store, fake)
    fake.txns["txn-old"] = dict(MERCURY["txn_deposit"], id="txn-old", createdAt="2026-08-01T12:00:00Z",
                                postedAt="2026-08-01T12:00:00Z")
    sync_once(store, fake)                  # the first settled row is in August: the account has never been reconciled
    with store.transaction() as c:
        for n, received in ((1, "2026-10-14T09:00:00Z"), (2, "2026-10-15T09:00:00Z")):
            c.execute("INSERT INTO bills_inbound(id,delivery,received_at,sender,subject,name,mime,sha256,state,code,"
                      "updated_at) VALUES(?,'dlv_1',?,'Acme Supplies','Invoice 88','invoice.pdf','application/pdf',?,"
                      "'needs_entry','',?)", (f"bin_{n}", received, "a" * 64, received))
    err, late = call(api, "hub_finance_inbox", {"kind": "unreconciled"}, token=token)
    assert not err and [(i["kind"], i["id"]) for i in late["items"]] == [("unreconciled", "books")], late
    err, first = call(api, "hub_finance_inbox", {"kind": "bill_email", "limit": 1}, token=token)
    assert not err and [i["id"] for i in first["items"]] == ["bin_1"] and first["next_cursor"], first
    err, second = call(api, "hub_finance_inbox", {"kind": "bill_email", "limit": 1, "cursor": first["next_cursor"]},
                       token=token)
    assert not err and [i["id"] for i in second["items"]] == ["bin_2"] and second["next_cursor"] is None, second


def test_gap_the_bot_can_list_accounts(api):
    """categorize, bill_create and rule_create take account ids: the bot finds the chart's lines by code or name with the
    named search kind, paged. The first few of each kind without a kind leave accounts out."""
    token = hire(api)
    err, out = call(api, "hub_finance_search", {"kind": "account", "q": "Software"}, token=token)
    assert not err and any(r.get("code") == "6100" and r["id"] for r in out["results"]), out
    err, page = call(api, "hub_finance_search", {"kind": "account", "limit": 2}, token=token)
    assert not err and len(page["results"]) == 2 and page["next_cursor"], page
    err, rest = call(api, "hub_finance_search", {"kind": "account", "limit": 2, "cursor": page["next_cursor"]},
                     token=token)
    assert not err and not {r["id"] for r in rest["results"]} & {r["id"] for r in page["results"]}, rest
    err, every = call(api, "hub_finance_search", {"q": "Software"}, token=token)
    assert not err and not any(r.get("kind") == "account" for r in every["results"]), every
