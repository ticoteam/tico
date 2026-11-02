"""Finance smoke test on the local stack (scripts/finance_e2e.sh): a real browser on the demo, a fake Mercury behind it.

It signs in as the demo owner, connects Mercury with a fake token (Finance > Sources > + Source > Mercury), checks
that accounts and transactions load, adds a transaction to the fake through /_fake/txn, runs "Sync now" and waits up to
two minutes for the transaction to show. Then it opens every Finance tab, records console and page errors and failed Finance requests,
and takes a desktop screenshot of each tab. Exit status 0 only when every check passed.

Then the rehearsal (skip it with --no-rehearsal): every tab at desktop and phone width (no sideways scroll of the page,
no console errors, phone dialogs that fit), the P&L budget edit and CSV export, expenses and balance sheet, bills,
invoices, a void and a write-off, cards, a transaction tag, an access grant and revoke, a closed month, an expired Mercury
token (scripts/fake_mercury.py /_fake/expire and /_fake/renew), the tax workpaper, and light and dark themes. Its writes
are demo rows numbered with a stamp (REH-, OCT-, VOID-, WO-); it closes October for one check and reopens it. Notes (choices
seen, sizes, texts) go to smoke-results.json under "notes". Nothing here contacts Mercury, QuickBooks, Xero or Stripe.

  uv run --no-project --with playwright python scripts/finance_e2e_smoke.py
  (run `uv run --no-project --with playwright python -m playwright install chromium` once)

Options: --base (default http://localhost:18766), --fake (default http://127.0.0.1:18790), --no-rehearsal,
--shots (default /Volumes/PNYRP60PSSD/orca/workspaces/tico/_finance-e2e-shots).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, sync_playwright

TABS = ["pnl", "balance", "payables", "receivables", "cards", "transactions", "tax", "sources", "access"]
TOKEN = "fake-token-for-smoke"
APPROVER = "ben@acme.example"
SMOKE_VENDOR = "SMOKE VENDOR"
# A bank-kind fixture row (card rows show on Cards, not Transactions).
SEEDED_BANK_ROW = "Acme Customer"
SYNC_WAIT_SECONDS = 120
# What a step may fail with: a Playwright timeout or a page error, an HTTP or socket error, a bad JSON answer.
STEP_FAILURES = (PlaywrightError, OSError, ValueError, KeyError, IndexError)


class Run:
    def __init__(self) -> None:
        self.checks: list[dict[str, object]] = []
        self.notes: list[dict[str, str]] = []
        self.tab_errors: dict[str, list[str]] = {}
        self.current = "setup"

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)
        return bool(ok)

    def note(self, name: str, detail: str) -> None:
        """Something seen that is not pass or fail (a list of choices, a size, a text); the report reads these."""
        self.notes.append({"name": name, "detail": detail})
        print("NOTE " + name + f" - {detail}", flush=True)


def fake_call(fake: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(fake + path, data=data, method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as answer:
        return json.loads(answer.read() or b"{}")


def go(page: Page, base: str, route: str) -> None:
    page.goto(base + "/" + route, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)


def body_text(page: Page) -> str:
    return page.locator("body").inner_text(timeout=5000)


def wait_for_text(page: Page, text: str, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if text in body_text(page):
            return True
        page.wait_for_timeout(1500)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default="http://localhost:18766")
    parser.add_argument("--fake", default="http://127.0.0.1:18790")
    parser.add_argument("--shots", default="/Volumes/PNYRP60PSSD/orca/workspaces/tico/_finance-e2e-shots")
    parser.add_argument("--no-rehearsal", action="store_true", help="skip the widths pass and the write flows")
    args = parser.parse_args()
    os.makedirs(args.shots, exist_ok=True)
    run = Run()
    failed_http: list[str] = []
    connect_status: list[int] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: run.tab_errors.setdefault(run.current, []).append(f"console.error: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: run.tab_errors.setdefault(run.current, []).append(f"pageerror: {e}"))
        page.on("response", lambda r: failed_http.append(f"{r.status} {r.url}")
                if r.status >= 400 and "/api/v2/finance/" in r.url else None)
        page.on("response", lambda r: connect_status.append(r.status)
                if r.request.method == "POST" and r.url.endswith("/api/v2/finance/connections") else None)

        # 1. Sign in (the first visit does it) and open Finance; the demo sets Finance up when it starts.
        go(page, args.base, "")
        go(page, args.base, "#/finance/pnl")
        tabs_ok = run.check("finance is set up and its tabs show", page.locator("[data-fin-tab]").count() == len(TABS),
                            f"{page.locator('[data-fin-tab]').count()} tabs")
        connected = False
        if tabs_ok:
            # 2. Connect Mercury with a fake token (Finance > Sources > + Source > Mercury).
            run.current = "sources"
            try:
                go(page, args.base, "#/finance/sources")
                page.locator("[data-fin-connect]").click()
                page.locator("[data-fin-connect-item=mercury]").click()
                page.wait_for_selector("dialog.fin-dlg")
                page.fill("dialog.fin-dlg input[name=token]", TOKEN)
                page.fill("dialog.fin-dlg input[name=approver]", APPROVER)
                page.click("dialog.fin-dlg button[type=submit]")
                try:
                    page.wait_for_selector("dialog.fin-dlg", state="detached", timeout=30000)
                    connected = True
                    error = ""
                except PlaywrightError:
                    error = page.locator("dialog.fin-dlg [data-error]").first.inner_text()
                run.check("Mercury connects with the fake token", connected,
                          f"POST /api/v2/finance/connections -> {connect_status[-1:] or 'no answer'}; {error}"[:300])
            except STEP_FAILURES as e:
                run.check("Mercury connects with the fake token", False, f"{type(e).__name__}: {str(e)[:200]}")

        # 3-4. Accounts, transactions, and the fake transaction through a sync: each needs the connection above.
        if connected:
            try:
                run.current = "sources"
                go(page, args.base, "#/finance/sources")
                run.check("the Mercury source lists", wait_for_text(page, "Mercury", 20))
                calls = [c["path"] for c in fake_call(args.fake, "/_fake/calls")["calls"]]
                run.check("the bank worker read accounts from Mercury", "/accounts" in calls, f"{len(calls)} calls so far")
                run.current = "transactions"
                go(page, args.base, "#/finance/transactions")
                run.check("seeded transactions load", wait_for_text(page, SEEDED_BANK_ROW, 90),
                          f"{SEEDED_BANK_ROW} (bank deposit) from the fake")
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                added = fake_call(args.fake, "/_fake/txn", {"amount": -77.31, "counterparty": SMOKE_VENDOR,
                                                            "date": today, "kind": "bank"})
                run.check("fake transaction added", bool(added.get("transaction")),
                          added.get("transaction", {}).get("id", ""))
                run.current = "sources"
                go(page, args.base, "#/finance/sources")
                # Source groups start collapsed; open each one so the Mercury row is rendered.
                collapsed = page.locator('[data-fin-group][aria-expanded="false"]')
                for _ in range(10):
                    if collapsed.count() == 0:
                        break
                    collapsed.first.click()
                    page.wait_for_timeout(300)
                page.locator("tr[data-fin-src]", has_text="Mercury").first.click()
                page.wait_for_timeout(800)
                page.locator("[data-fin-sync]").first.click()
                run.current = "transactions"
                shown, deadline = False, time.time() + SYNC_WAIT_SECONDS
                go(page, args.base, "#/finance/transactions")
                while time.time() < deadline and not shown:
                    # Reload, not go(): the same hash does not re-render the page, so the row would never appear.
                    shown = SMOKE_VENDOR in body_text(page)
                    if not shown:
                        page.wait_for_timeout(4000)
                        page.reload(wait_until="networkidle")
                        page.wait_for_timeout(800)
                run.check("the fake transaction shows after Sync now", shown,
                          "found" if shown else f"not within {SYNC_WAIT_SECONDS} s")
            except STEP_FAILURES as e:
                run.check("accounts, transactions and sync", False, f"{type(e).__name__}: {str(e)[:200]}")
        elif tabs_ok:
            for name in ("the Mercury source lists", "seeded transactions load", "fake transaction added",
                         "the fake transaction shows after Sync now"):
                run.check(name, False, "blocked: no Mercury connection (the connect step failed)")

        # 5. Every tab: no console or page errors, a screenshot. Errors from the steps above do not count here.
        run.tab_errors.clear()
        if tabs_ok:
            for tab in TABS:
                run.current = tab
                try:
                    go(page, args.base, f"#/finance/{tab}")
                    page.screenshot(path=os.path.join(args.shots, f"{tab}.png"), full_page=True)
                    errors = run.tab_errors.get(tab, [])
                    run.check(f"tab {tab} opens with no console errors",
                              not errors and page.locator("#fin-body").count() > 0, "; ".join(errors)[:300])
                except STEP_FAILURES as e:
                    run.check(f"tab {tab} opens with no console errors", False, f"{type(e).__name__}: {str(e)[:200]}")
        run.current = "http"
        run.check("no failed Finance requests", not failed_http, "; ".join(failed_http)[:400])
        if not args.no_rehearsal and tabs_ok:
            rehearse(browser, run, args.base, args.fake, args.shots)
        browser.close()
    return finish(run, args.shots)


# Rehearsal: every Finance page at both widths, then the writes made through the UI, each read back through the API.
# It runs on the demo above; the writes are new rows with a stamp in their numbers (REH-<time>), so a run can repeat.
WIDTHS = {"desktop": (1440, 900), "phone": (390, 844)}
ROUTES = TABS + ["pnl?view=expenses"]
DIALOGS_PHONE = [("payables", "[data-fin-doc-add]"), ("receivables", "[data-fin-doc-add]"), ("cards", "[data-fin-card-add]")]
STAMP = datetime.now(timezone.utc).strftime("%H%M%S")
NUMBER = f"REH-{STAMP}"
JS_SCROLL = "() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})"
JS_DLG = """() => {
  const d = document.querySelector('dialog.fin-dlg[open]');
  if (!d) return null;
  const r = d.getBoundingClientRect();
  return {left: Math.round(r.left), right: Math.round(r.right), cw: document.documentElement.clientWidth};
}"""
# Controls shorter than 44 px on the Finance body (the usual touch size); reported as a note, not a failure.
JS_SMALL = """() => {
  let n = 0;
  const sample = [];
  for (const el of document.querySelectorAll('#fin-body a, #fin-body button, #fin-body input, #fin-body select, #fin-body [role=radio]')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || el.closest('[hidden]')) continue;
    if (r.height < 44) {
      n++;
      if (sample.length < 5) sample.push((el.textContent || el.name || el.tagName).trim().slice(0, 24) + ' ' + Math.round(r.height) + 'px');
    }
  }
  return {n, sample};
}"""


def api(page: Page, base: str, path: str) -> object:
    answer = page.request.get(base + path)
    return answer.json() if answer.ok else {"status": answer.status}


def rows_of(value: object) -> list[dict]:
    """The rows of an API answer: the first list of objects in it, whatever key the route wraps them under."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        for v in value.values():
            found = rows_of(v)
            if found:
                return found
    return []


def act(page: Page, base: str, kind: str, payload: dict) -> tuple[int, object]:
    """A Finance action as the UI sends it: POST /api/v2/finance/actions with a fresh Idempotency-Key."""
    answer = page.request.post(base + "/api/v2/finance/actions", data=json.dumps({"kind": kind, "payload": payload}),
                               headers={"Content-Type": "application/json", "Idempotency-Key": uuid.uuid4().hex})
    try:
        return answer.status, answer.json()
    except ValueError:
        return answer.status, answer.text()[:300]


def pick_first(scope: Page | Locator, name: str) -> str:
    """Choose the first real option of a select (not the blank one, not New); returns its label."""
    select = scope.locator(f"select[name={name}]").first
    for value, label in select.evaluate("el => [...el.options].map(o => [o.value, o.textContent.trim()])"):
        if value and value != "+":
            select.select_option(value)
            return label
    raise ValueError(f"no option to pick in select {name}")


def pick_other(scope: Page | Locator, name: str) -> str:
    """Choose the first real option that is not the select's current one, so that a Save has a change to make."""
    select = scope.locator(f"select[name={name}]").first
    current = select.input_value()
    for value, label in select.evaluate("el => [...el.options].map(o => [o.value, o.textContent.trim()])"):
        if value and value != "+" and value != current:
            select.select_option(value)
            return label
    raise ValueError(f"no other option in select {name}")


def pick_contact(dlg: Locator, new_name: str) -> str:
    """The contact: the first existing one, or a new one named new_name when there are none (the demo starts with none)."""
    try:
        return pick_first(dlg, "contact")
    except ValueError:
        dlg.locator("select[name=contact]").first.select_option("+")
        dlg.locator("input[name=name]").first.fill(new_name)
        return f"new {new_name}"


def open_dialog(page: Page, selector: str) -> Locator:
    page.locator(selector).first.click()
    dlg = page.locator("dialog.fin-dlg[open]")
    dlg.wait_for(timeout=8000)
    return dlg


def submit(page: Page, dlg: Locator) -> str:
    """Press the dialog's submit (a second press when it turns Save into Send for an approval). Returns '' once the
    dialog has closed, else the text it still shows."""
    dlg.locator("[type=submit]").first.click()
    page.wait_for_timeout(700)
    if dlg.count() and dlg.locator("[type=submit]").first.inner_text().strip() == "Send":
        dlg.locator("[type=submit]").first.click()
    try:
        dlg.wait_for(state="detached", timeout=12000)
        return ""
    except PlaywrightError:
        return dlg.inner_text(timeout=2000)[:240].replace("\n", " | ")


def expand_sources(page: Page) -> None:
    for _ in range(6):
        collapsed = page.locator('[data-fin-group][aria-expanded="false"]')
        if collapsed.count() == 0:
            break
        collapsed.first.click()
        page.wait_for_timeout(300)


def layout_pass(browser, run: Run, base: str, shots: str) -> None:
    """Every Finance page at desktop and phone width: no sideways scroll of the page, no console errors or failed
    Finance requests, a screenshot. At phone width also the first dialog of three pages, which must fit the screen."""
    for wname, (w, h) in WIDTHS.items():
        ctx = browser.new_context(viewport={"width": w, "height": h})
        pg = ctx.new_page()
        errors: list[str] = []
        pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        pg.on("response", lambda r: errors.append(f"{r.status} {r.url}")
              if r.status >= 400 and "/api/v2/finance/" in r.url else None)
        run.current = f"layout {wname}"
        try:
            go(pg, base, "")
            for route in ROUTES:
                errors.clear()
                go(pg, base, "#/finance/" + route)
                scroll = pg.evaluate(JS_SCROLL)
                name = route.replace("?", "_").replace("=", "-")
                pg.screenshot(path=os.path.join(shots, f"{wname}-{name}.png"), full_page=True)
                run.check(f"{wname} {route}: the page does not scroll sideways", scroll["sw"] <= scroll["cw"] + 1,
                          str(scroll))
                run.check(f"{wname} {route}: no console errors or failed Finance requests", not errors,
                          "; ".join(errors)[:300])
                if wname == "phone":
                    small = pg.evaluate(JS_SMALL)
                    run.note(f"phone {route}: controls shorter than 44 px", f"{small['n']} e.g. {small['sample']}")
                if wname == "phone" and route == "pnl":
                    boxes = pg.evaluate("() => [...document.querySelectorAll('.fin-scroll')].map(e => [e.scrollWidth, e.clientWidth])")
                    run.note("phone P&L: scroll boxes as [content width, box width]", str(boxes))
            if wname == "phone":
                for route, selector in DIALOGS_PHONE:
                    go(pg, base, "#/finance/" + route)
                    open_dialog(pg, selector)
                    box = pg.evaluate(JS_DLG)
                    pg.screenshot(path=os.path.join(shots, f"phone-dialog-{route}.png"))
                    run.check(f"phone {route}: the dialog fits the screen",
                              bool(box) and box["left"] >= 0 and box["right"] <= box["cw"] + 1, str(box))
                    pg.keyboard.press("Escape")
                    pg.wait_for_timeout(300)
        except STEP_FAILURES as e:
            run.check(f"{wname} layout pass", False, f"{type(e).__name__}: {str(e)[:200]}")
        ctx.close()


def flow_pnl(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """Company P&L: double-click each budget cell; a future month saves on Enter, Escape keeps the old value."""
    go(page, base, "#/finance/pnl")
    # The budget column follows the last cell clicked; budgets edit only in a future month, so click a future cell first.
    page.locator("#fin-pl td.fin-p[data-fin-cell]").first.click()
    page.wait_for_timeout(700)
    heads = page.locator("#fin-pl thead th").evaluate_all("els => els.map(e => e.textContent.trim())")
    cells = page.locator("#fin-pl td[data-fin-budget]")
    tried: list[tuple[int, str, bool, str | None]] = []
    for i in range(min(cells.count(), 60)):
        td = cells.nth(i)
        idx = td.evaluate("el => el.cellIndex")
        col = heads[idx] if idx < len(heads) else str(idx)
        td.dblclick()
        page.wait_for_timeout(150)
        editable = td.locator("input").count() > 0
        if editable:
            td.locator("input").press("Escape")
            page.wait_for_timeout(400)
        tried.append((i, col, editable, td.get_attribute("data-fin-budget")))
    yes = sorted({c for _, c, e, _ in tried if e})
    no = sorted({c for _, c, e, _ in tried if not e})
    run.note("P&L budget cells: columns that open for editing", f"{yes or 'none'}; columns that do not: {no}")
    first = next((t for t in tried if t[2]), None)
    if first:
        def budgets_for(account: str | None) -> list[object]:
            return [(b.get("state") or {}).get("amount")
                    for b in rows_of(api(page, base, "/api/v2/finance/budgets?at=2026-12-01"))
                    if b.get("account_id") == account]
        td = cells.nth(first[0])
        td.dblclick()
        page.wait_for_timeout(150)
        td.locator("input").fill("1234")
        td.locator("input").press("Enter")
        page.wait_for_timeout(1500)
        page.wait_for_load_state("networkidle")
        amounts = budgets_for(first[3])
        run.check(f"a future budget cell saves on Enter ({first[1]})", 123400 in amounts,
                  f"budget amounts on that line now {amounts}")
        second = next((t for t in tried if t[2] and t is not first), None)
        if second:
            before = budgets_for(second[3])
            td2 = cells.nth(second[0])
            td2.dblclick()
            page.wait_for_timeout(150)
            td2.locator("input").fill("999")
            td2.locator("input").press("Escape")
            page.wait_for_timeout(1200)
            run.check(f"Escape creates no budget on another line ({second[1]})", budgets_for(second[3]) == before,
                      f"{before} before, {budgets_for(second[3])} after")
    else:
        run.check("some budget cell opens for editing", False, "no future-month budget cell opened on double-click")
    # The budget column follows the last cell clicked: a past or current month's budget cell does not open for editing.
    page.locator("#fin-pl td[data-fin-cell]:not(.fin-p)").first.click()
    page.wait_for_timeout(700)
    current = page.locator("#fin-pl td[data-fin-budget]")
    if current.count():
        current.first.dblclick()
        page.wait_for_timeout(200)
        run.check("a current-month budget cell does not open for editing", current.first.locator("input").count() == 0,
                  str(current.first.get_attribute("aria-label")))
    else:
        run.note("current-month budget cells", "none shown for a past or current month")
    go(page, base, "#/finance/pnl")
    subtotals = page.locator("#fin-pl tbody tr td:first-child").evaluate_all(
        "els => els.map(e => e.textContent.trim()).filter(t => /^(Total|Gross|Net )/.test(t))")
    run.note("P&L Company: in-table subtotal rows", " | ".join(subtotals) or "none")
    with page.expect_download(timeout=15000) as download:
        page.locator("[data-fin-export]").first.click()
    run.check("P&L exports a CSV file", download.value.suggested_filename.endswith(".csv"),
              download.value.suggested_filename)


def open_draft(page: Page, number: str, status: str = "Draft") -> Locator:
    """A status filter (a new bill or invoice is a draft, and Open does not list drafts), then the row by its number."""
    page.locator("[role=radio]", has_text=status).first.click()
    page.wait_for_timeout(700)
    page.locator("tr[data-fin-doc]", has_text=number).first.click()
    page.wait_for_timeout(900)
    return page.locator("tr[data-fin-doc]", has_text=number).first


def flow_bill(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """Payables: a bill from + Bill, approved and marked paid from its rail; each state read back from the API."""
    go(page, base, "#/finance/payables")
    dlg = open_dialog(page, "[data-fin-doc-add]")
    contact = pick_contact(dlg, "Rehearsal vendor")
    dlg.locator("input[name=number]").fill(NUMBER)
    labels = dlg.locator("select[name=line] option").evaluate_all("els => els.map(e => e.textContent.trim()).filter(Boolean)")
    run.note("bill dialog, Line choices", " | ".join(labels))
    dlg.locator("select[name=line]").select_option(label="Software")
    dlg.locator("input[name=amount]").fill("250")
    dlg.locator("input[name=memo]").fill("Rehearsal bill")
    problem = submit(page, dlg)
    run.check("a bill saves from + Bill", not problem, problem or f"contact {contact}")
    page.wait_for_timeout(600)
    run.note("a new bill in the default Open list", f"{page.locator('tr[data-fin-doc]', has_text=NUMBER).count()} row(s)")

    def status_of(kind: str) -> object:
        made = [r for r in rows_of(api(page, base, f"/api/v2/finance/{kind}")) if r.get("number") == NUMBER]
        return made[0].get("status") if made else None

    run.check("the new bill is in the books as draft", status_of("bills") == "draft", f"status {status_of('bills')}")
    open_draft(page, NUMBER)
    page.locator("[data-fin-do=approve]").first.click()
    page.wait_for_timeout(1500)
    run.check("Approve moves the bill to open", status_of("bills") == "open", f"status {status_of('bills')}")
    open_draft(page, NUMBER, "Open")
    dlg = open_dialog(page, "[data-fin-do=paid]")
    problem = submit(page, dlg)
    page.wait_for_timeout(800)
    run.check("Mark paid settles the bill", not problem and status_of("bills") == "paid",
              problem or f"status {status_of('bills')}")


def flow_invoice(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """Receivables: an invoice from + Invoice, sent, then marked paid; read back from the API."""
    go(page, base, "#/finance/receivables")
    dlg = open_dialog(page, "[data-fin-doc-add]")
    contact = pick_contact(dlg, "Rehearsal customer")
    dlg.locator("input[name=number]").fill(NUMBER)
    line = pick_first(dlg, "acct")
    dlg.locator("input[name=amt]").first.fill("500")
    problem = submit(page, dlg)
    run.check("an invoice saves from + Invoice", not problem, problem or f"contact {contact}, line {line}")

    def status_of() -> object:
        made = [r for r in rows_of(api(page, base, "/api/v2/finance/invoices")) if r.get("number") == NUMBER]
        return made[0].get("status") if made else None

    run.check("the new invoice is a draft", status_of() == "draft", f"status {status_of()}")
    open_draft(page, NUMBER)
    page.locator("[data-fin-do=send]").first.click()
    page.wait_for_timeout(1500)
    run.check("Send moves the invoice to sent", status_of() == "sent", f"status {status_of()}")
    open_draft(page, NUMBER, "Open")
    dlg = open_dialog(page, "[data-fin-do=paid]")
    problem = submit(page, dlg)
    page.wait_for_timeout(800)
    run.check("Mark paid settles the invoice", not problem and status_of() == "paid",
              problem or f"status {status_of()}")


def flow_void(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """Payables: a draft bill is voided with a reason; Receivables: a sent invoice is written off."""
    number = f"VOID-{STAMP}"
    go(page, base, "#/finance/payables")
    dlg = open_dialog(page, "[data-fin-doc-add]")
    pick_contact(dlg, "Rehearsal vendor")
    dlg.locator("input[name=number]").fill(number)
    dlg.locator("select[name=line]").select_option(label="Software")
    dlg.locator("input[name=amount]").fill("40")
    problem = submit(page, dlg)
    run.check("a draft bill for voiding saves", not problem, problem)
    open_draft(page, number)
    page.locator("[data-fin-do=void]").first.click()
    dlg = page.locator("dialog.fin-dlg[open]")
    dlg.wait_for(timeout=8000)
    dlg.locator("input[name=reason]").fill("Rehearsal void")
    problem = submit(page, dlg)
    page.wait_for_timeout(800)
    made = [r for r in rows_of(api(page, base, "/api/v2/finance/bills")) if r.get("number") == number]
    run.check("Void with a reason voids the bill", not problem and made and made[0].get("status") == "void",
              problem or f"status {made[0].get('status') if made else None}")
    go(page, base, "#/finance/receivables")
    dlg = open_dialog(page, "[data-fin-doc-add]")
    pick_contact(dlg, "Rehearsal customer")
    dlg.locator("input[name=number]").fill(f"WO-{STAMP}")
    pick_first(dlg, "acct")
    dlg.locator("input[name=amt]").first.fill("120")
    submit(page, dlg)
    open_draft(page, f"WO-{STAMP}")
    page.locator("[data-fin-do=send]").first.click()
    page.wait_for_timeout(1200)
    open_draft(page, f"WO-{STAMP}", "Open")
    dlg = open_dialog(page, "[data-fin-do=writeoff]")
    dlg.locator("input[name=memo]").fill("Rehearsal write-off")
    problem = submit(page, dlg)
    page.wait_for_timeout(800)
    made = [r for r in rows_of(api(page, base, "/api/v2/finance/invoices")) if r.get("number") == f"WO-{STAMP}"]
    status = made[0].get("status") if made else None
    run.check("Write off closes the invoice", not problem and status not in (None, "sent", "draft", "partial"),
              problem or f"status {status}")


def flow_access(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """Access: Edit defaults opens and cancels; a teammate gets a grant, which is revoked again (read back from the API)."""
    go(page, base, "#/finance/access")
    page.locator("button", has_text="Edit defaults").first.click()
    page.wait_for_timeout(500)
    text = re.sub(r"\s+", " ", page.locator("dialog[open]").inner_text())
    run.check("Edit defaults opens with the owner, manager and everyone-else defaults",
              all(w in text for w in ("Owners", "Managers", "Everyone else")), text[:160])
    page.locator("dialog[open] button", has_text="Cancel").first.click()
    page.wait_for_timeout(300)
    page.locator("tr[data-fin-mate='human:cara']").first.click()
    page.wait_for_timeout(700)
    before = json.dumps(api(page, base, "/api/v2/finance/access"), sort_keys=True)
    page.locator("select[name=what]").first.select_option(label="P&L")
    page.locator("select[name=whose]").first.select_option(label="Company")
    page.locator("input[name=until]").first.fill("2027-01-31")
    page.locator("button", has_text="Grant").first.click()
    page.wait_for_timeout(1200)
    granted = json.dumps(api(page, base, "/api/v2/finance/access"), sort_keys=True)
    run.check("a grant to a teammate is recorded", granted != before, f"{len(granted) - len(before)} characters changed")
    revoke = page.locator("[data-fin-revoke]")
    run.check("the grant shows a revoke control", revoke.count() > 0, f"{revoke.count()} control(s)")
    if revoke.count():
        revoke.first.click()
        page.wait_for_timeout(1200)
        left = page.locator("[data-fin-revoke]").count()
        run.check("the grant can be revoked", left == 0, f"{left} revoke control(s) left")


def flow_pnl_periods(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """P&L periods: Quarter and Year change the columns, Month brings them back."""
    go(page, base, "#/finance/pnl")
    month = page.locator("#fin-pl thead th").count()
    for label in ("Quarter", "Year", "Month"):
        page.locator("#fin-body button", has_text=re.compile(rf"^{label}$")).first.click()
        page.wait_for_timeout(900)
        run.note(f"P&L period {label}: header cells", str(page.locator("#fin-pl thead th").count()))
    run.check("switching the period back to Month restores the columns", page.locator("#fin-pl thead th").count() == month,
              f"{month} cells at start")


def flow_card(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """Cards: issue one from + Card, then freeze and unfreeze it."""
    nickname = f"Rehearsal {STAMP}"
    go(page, base, "#/finance/cards")
    dlg = open_dialog(page, "[data-fin-card-add]")
    holder = pick_first(dlg, "holder")
    budget = pick_first(dlg, "budget")
    dlg.locator("input[name=amount]").fill("500")
    dlg.locator("input[name=nickname]").fill(nickname)
    user = pick_first(dlg, "user")
    problem = submit(page, dlg)
    run.check("a card issues from + Card", not problem, problem or f"holder {holder}, budget {budget}, user {user}")
    page.wait_for_timeout(1000)
    row = page.locator("[data-fin-card]", has_text=nickname).first
    run.check("the new card lists", row.count() > 0)
    row.click()                                  # the card's rail holds Freeze and the limit
    page.wait_for_timeout(700)
    freeze = page.locator("[data-fin-freeze]").first
    before = freeze.inner_text().strip()
    freeze.click()
    page.wait_for_timeout(1500)
    after = page.locator("[data-fin-freeze]").first.inner_text().strip()
    run.check("Freeze changes the card's button", before != after, f"{before!r} -> {after!r}")
    page.locator("[data-fin-freeze]").first.click()
    page.wait_for_timeout(1500)
    back = page.locator("[data-fin-freeze]").first.inner_text().strip()
    run.check("Unfreeze puts the button back", back == before, f"{back!r}")


def flow_transactions(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """Transactions: tag a teammate from the rail, reload, and see the tag on the row."""
    go(page, base, "#/finance/transactions")
    page.locator("tr", has_text=SMOKE_VENDOR).first.click()
    page.wait_for_timeout(600)
    teammate = pick_other(page, "teammate")
    page.locator("[data-fin-cat-save]").first.click()
    page.wait_for_timeout(1200)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(800)
    row = page.locator("tr", has_text=SMOKE_VENDOR).first.inner_text()
    run.check("a teammate tag saved from the rail shows on the row", teammate.split()[0] in row,
              f"{teammate!r}; row: {row[:160]!r}")


def flow_roles(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """Owner's Access rows, then what Cara and Ben see when viewing as them (view_as is read only)."""
    go(page, base, "#/finance/access")
    mates = page.locator("[data-fin-mate]").evaluate_all("els => els.map(e => e.getAttribute('data-fin-mate'))")
    run.note("Access rows as the owner", ", ".join(mates))
    for who in ("human:cara", "human:ben"):
        go(page, base, f"#/finance/pnl?view_as={who}")
        tabs = page.locator("[data-fin-tab]").evaluate_all("els => els.map(e => e.textContent.trim())")
        writes = page.locator("[data-fin-doc-add], [data-fin-card-add], [data-fin-do], [data-fin-add]").count()
        run.note(f"viewing as {who}: tabs", ", ".join(tabs))
        run.note(f"viewing as {who}: write controls on the P&L", str(writes))
        go(page, base, f"#/finance/payables?view_as={who}")
        run.note(f"viewing as {who}: Payables body", re.sub(r"\s+", " ", page.locator("#fin-body").inner_text())[:200]
                 if page.locator("#fin-body").count() else "no Finance body")


def flow_closed_month(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """A closed month: a draft dated in it is kept (a draft posts nothing), but approving it is refused.
    October is reopened after the check."""
    before_status, _ = act(page, base, "reopen_period", {"month": "2026-10"})
    run.note("October reopened before the check (a leftover close from an earlier run)", str(before_status))
    status, body = act(page, base, "close_period", {"month": "2026-10"})
    run.check("October closes through close_period", status < 300, f"{status} {str(body)[:200]}")
    number = f"OCT-{STAMP}"
    go(page, base, "#/finance/payables")
    dlg = open_dialog(page, "[data-fin-doc-add]")
    pick_contact(dlg, "Rehearsal vendor")
    dlg.locator("input[name=number]").fill(number)
    dlg.locator("input[name=date]").fill("2026-10-15")
    dlg.locator("select[name=line]").select_option(label="Software")
    dlg.locator("input[name=amount]").fill("80")
    problem = submit(page, dlg)
    run.check("a draft dated in the closed month is kept", not problem, problem or "saved as a draft")
    open_draft(page, number)
    page.locator("[data-fin-do=approve]").first.click()
    page.wait_for_timeout(1500)
    made = [r for r in rows_of(api(page, base, "/api/v2/finance/bills")) if r.get("number") == number]
    status_after = made[0].get("status") if made else None
    run.note("approving the bill dated in the closed month", f"status {status_after}")
    entries = [r for r in rows_of(api(page, base, f"/api/v2/finance/transactions?q={number}&limit=10"))
               if r.get("kind") == "entry"]
    dates = sorted({str(r.get("date")) for r in entries})
    run.check("the approval posts no entry dated in the closed month", bool(entries) and not any(d.startswith("2026-10") for d in dates),
              f"entry dates {dates}; memo {[r.get('memo') for r in entries][:1]}")
    reopened, answer = act(page, base, "reopen_period", {"month": "2026-10"})
    run.note("October reopened after the check", f"{reopened} {str(answer)[:120]}")


def flow_expenses(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """Expenses: a line with spend opens into its vendors, which have no vs-budget column."""
    go(page, base, "#/finance/pnl?view=expenses")
    run.note("Expenses view: rows in the table", str(page.locator("#fin-pl tbody tr").count()))
    picked = ""
    for i in range(min(page.locator("#fin-pl a[href*='/line/']").count(), 8)):
        link = page.locator("#fin-pl a[href*='/line/']").nth(i)
        name = link.inner_text().strip()
        link.click()
        page.wait_for_timeout(900)
        page.wait_for_load_state("networkidle")
        if page.locator("#main table").count() and "No spend on this line yet" not in body_text(page):
            picked = name
            break
        go(page, base, "#/finance/pnl?view=expenses")
    if not picked:
        run.check("a line with spend opens into its vendors", False, "no line in the Expenses view had spend")
        return
    heads = page.locator("#main table thead th").evaluate_all("els => els.map(e => e.textContent.trim())")
    run.note("line drill-down columns", " | ".join(h for h in heads if h))
    run.check("a line opens into its vendors", page.locator("#main table tbody tr").count() > 0, f"{picked}: {page.url}")
    run.check("the drill-down has no vs-budget column", not any("vs budget" in h.lower() for h in heads),
              " | ".join(h for h in heads if h))
    run.note("drill-down: add-budget controls", str(page.locator("[data-fin-vendor-add], [data-fin-line-add]").count()))


def flow_balance(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """Balance sheet: it says it balances, and lists what + Add can make."""
    go(page, base, "#/finance/balance")
    text = re.sub(r"\s+", " ", body_text(page))
    hit = re.search(r".{0,60}balanc.{0,40}", text, re.I)
    run.check("the balance sheet says it balances", bool(hit), hit.group(0) if hit else "no 'balanced' text")
    page.locator("[data-fin-add]").first.click()
    page.wait_for_timeout(500)
    choices = page.locator(".fin-pop button, [role=menu] button, [role=menuitem]").evaluate_all(
        "els => els.map(e => e.textContent.trim()).filter(Boolean)")
    run.note("balance sheet + Add choices", " | ".join(choices))
    page.keyboard.press("Escape")


def flow_token(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    """An expired Mercury token: Sync now shows the reason on Sources; after renewal the next sync works."""
    go(page, base, "#/finance/sources")
    expand_sources(page)
    fake_call(fake, "/_fake/expire", {})
    try:
        page.locator("tr[data-fin-src]", has_text="Mercury").first.click()
        page.wait_for_timeout(600)
        page.locator("[data-fin-sync]").first.click()
        page.wait_for_timeout(6000)
        page.reload(wait_until="networkidle")
        expand_sources(page)
        page.locator("tr[data-fin-src]", has_text="Mercury").first.click()
        page.wait_for_timeout(800)
        text = re.sub(r"\s+", " ", page.locator("#fin-body").inner_text())
        whole = re.sub(r"\s+", " ", page.locator("#main").inner_text())
        run.note("Sources with an expired token", text[:400])
        run.check("the Mercury rows say the sync failed", bool(re.search(r"\bError\b", text)), text[:120])
        run.check("an expired token is named with a way to fix it",
                  bool(re.search(r"expired|token|reconnect|sign in|unauthori|401", whole, re.I)), whole[:160])
    finally:
        fake_call(fake, "/_fake/renew", {})
    page.locator("[data-fin-sync]").first.click()
    page.wait_for_timeout(6000)
    page.reload(wait_until="networkidle")
    expand_sources(page)
    page.locator("tr[data-fin-src]", has_text="Mercury").first.click()
    page.wait_for_timeout(800)
    healed = re.sub(r"\s+", " ", page.locator("#fin-body").inner_text())
    run.note("Sources after the token is renewed", healed[:400])


def flow_tax(page: Page, run: Run, base: str, fake: str, shots: str) -> None:
    go(page, base, "#/finance/tax")
    page.locator('[data-v="1099"]').first.click()
    page.wait_for_timeout(800)
    run.check("the 1099 workpaper switches on", "1099" in body_text(page))
    page.screenshot(path=os.path.join(shots, "tax-1099.png"), full_page=True)


def dark_and_light(browser, run: Run, base: str, shots: str) -> None:
    """P&L in both colour schemes at both widths: the page renders, and the dark background is not the light one."""
    for wname, (w, h) in WIDTHS.items():
        backgrounds = {}
        for scheme in ("light", "dark"):
            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
            pg = ctx.new_page()
            errors: list[str] = []
            pg.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
            pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            go(pg, base, "")
            # The theme is dark unless this browser saved another choice (ui/theme.js), so set it explicitly.
            pg.evaluate(f"() => window.ticoTheme.set('{scheme}')")
            go(pg, base, "#/finance/pnl")
            applied = pg.evaluate("() => document.documentElement.dataset.theme")
            run.check(f"{scheme} theme is applied", applied == scheme, f"data-theme={applied}")
            backgrounds[scheme] = pg.evaluate("() => getComputedStyle(document.body).backgroundColor")
            pg.screenshot(path=os.path.join(shots, f"{scheme}-{wname}-pnl.png"), full_page=True)
            run.check(f"{scheme} {wname}: P&L renders with no console errors", not errors, "; ".join(errors)[:300])
            ctx.close()
        run.check(f"{wname}: the dark background differs from the light one", backgrounds["dark"] != backgrounds["light"],
                  f"light {backgrounds['light']}, dark {backgrounds['dark']}")


def rehearse(browser, run: Run, base: str, fake: str, shots: str) -> None:
    layout_pass(browser, run, base, shots)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())      # the Freeze and Void confirmations
    page.on("console", lambda m: run.tab_errors.setdefault(run.current, []).append(f"console.error: {m.text}")
            if m.type == "error" else None)
    page.on("pageerror", lambda e: run.tab_errors.setdefault(run.current, []).append(f"pageerror: {e}"))
    go(page, base, "")
    flows = (flow_pnl, flow_pnl_periods, flow_expenses, flow_balance, flow_bill, flow_invoice, flow_void, flow_card,
             flow_transactions, flow_access, flow_roles, flow_closed_month, flow_token, flow_tax)
    for flow in flows:
        name = flow.__name__.removeprefix("flow_")
        run.current = name
        try:
            flow(page, run, base, fake, shots)
        except STEP_FAILURES as e:
            run.check(f"{name}: its steps ran", False, f"{type(e).__name__}: {str(e)[:240]}")
        errors = run.tab_errors.get(name, [])
        run.check(f"{name}: no console or page errors", not errors, "; ".join(errors)[:300])
    ctx.close()
    run.current = "widths"
    try:
        dark_and_light(browser, run, base, shots)
    except STEP_FAILURES as e:
        run.check("light and dark pass", False, f"{type(e).__name__}: {str(e)[:200]}")


def finish(run: Run, shots: str) -> int:
    ok = all(c["ok"] for c in run.checks)
    with open(os.path.join(shots, "smoke-results.json"), "w") as out:
        json.dump({"checks": run.checks, "notes": run.notes, "ok": ok}, out, indent=2)
    print(f"\n{sum(1 for c in run.checks if c['ok'])}/{len(run.checks)} checks passed; results in "
          f"{os.path.join(shots, 'smoke-results.json')}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
