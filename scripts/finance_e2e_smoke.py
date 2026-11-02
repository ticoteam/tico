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
    parser.add_argument("--pass2", action="store_true", help="the second browser pass (items below), not the first")
    parser.add_argument("--items", default="", help="pass 2: item numbers, comma separated (default all, in order)")
    parser.add_argument("--sources-base", default="", help="pass 2 item 12: a second demo with its own data folder")
    parser.add_argument("--sources-fake", default="", help="pass 2 item 12: the fake Mercury behind that demo")
    args = parser.parse_args()
    os.makedirs(args.shots, exist_ok=True)
    run = Run()
    if args.pass2:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            pass2(browser, run, args)
            browser.close()
        return finish(run, args.shots)
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


# Pass 2 (--pass2 [--items 6,1,...] --shots DIR): the second browser pass, on the tasks the first pass did not reach.
# Each item is checked against reports/2026-10-30-finance-spec.md (section 12, with 7.5 view access, 4.2 posting,
# 9 payments and direct send, 8 cards). It runs on the stack demo, already connected to the fake Mercury, except item 12,
# which needs its own demo and fake (--sources-base, --sources-fake). Each write is read back through the API straight
# after it. Items run at 1440 x 900, and at 390 x 844 where the task is a phone task (P2 says which). The fake Mercury is
# the only bank here; nothing reaches a real provider, and Sources never clicks QuickBooks, Xero or Stripe.
P2_ORDER = ["6", "1", "7", "2", "3", "4", "5", "8", "9", "10", "11", "12"]
P2_MONTH = "start=2026-10-01&end=2026-10-31&period=month&basis=accrual"
P2_NOV = "start=2026-11-01&end=2026-11-30&period=month&basis=accrual"   # today's month: the unattributed rows and bills dated today
P2_DAY =datetime.now(timezone.utc).strftime("%Y-%m-%d")
P2_LIVE = f"LIVE {STAMP}"                  # the unattributed fake row for this run (live refresh, then spec 7.5 hides it)
# The teammates a manager (Ben) sees under §7.5: his own, Support's bot, and Ops (Ana, under Support). Not Cara's line.
BEN_SEES = ["bot:support", "human:ana", "human:cara", "human:ben"]


def get_json(page: Page, base: str, path: str) -> tuple[int, object]:
    answer = page.request.get(base + path)
    try:
        return answer.status, answer.json()
    except ValueError:
        return answer.status, answer.text()[:300]


def find_all(value: object, pred) -> list[dict]:
    """Every dict nested anywhere in an answer that pred accepts (the wrapping keys differ by route)."""
    found: list[dict] = []
    if isinstance(value, dict):
        if pred(value):
            found.append(value)
        for v in value.values():
            found.extend(find_all(v, pred))
    elif isinstance(value, list):
        for v in value:
            found.extend(find_all(v, pred))
    return found


def named(value: object, name: str) -> dict:
    hits = find_all(value, lambda d: d.get("name") == name)
    return hits[0] if hits else {}


def has_text(value: object, text: str) -> bool:
    return bool(find_all(value, lambda d: any(v == text for v in d.values() if isinstance(v, str))))


def cents(value: object) -> int:
    """A money total from the API: cents as an int, or a list of them (one per column)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, list):
        return sum(cents(v) for v in value)
    return 0


def pnl_rows(data: object) -> dict[str, dict]:
    rows = data.get("rows", []) if isinstance(data, dict) else []
    return {str(r.get("code")): r for r in rows if isinstance(r, dict) and r.get("code")}


def bank_of(balance: object) -> int:
    return cents(named(balance, "Bank").get("balance"))


def tx_row(page: Page, counterparty: str) -> Locator:
    return page.locator("tr[data-fin-tx]").filter(has=page.get_by_text(counterparty, exact=True)).first


def p2_open(browser, run: Run, base: str, width: str):
    w, h = WIDTHS[width]
    ctx = browser.new_context(viewport={"width": w, "height": h})
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    page.on("console", lambda m: run.tab_errors.setdefault(run.current, []).append(f"console.error: {m.text}")
            if m.type == "error" else None)
    page.on("pageerror", lambda e: run.tab_errors.setdefault(run.current, []).append(f"pageerror: {e}"))
    go(page, base, "")
    return ctx, page


def p2_select(scope, selector: str, text: str) -> str:
    """The first option of a select whose label contains text (any case). Returns the label chosen."""
    select = scope.locator(selector).first
    options = select.evaluate("el => [...el.options].map(o => [o.value, o.textContent.trim()])")
    for value, label in options:
        if value and text.lower() in label.lower():
            select.select_option(value)
            return label
    raise ValueError(f"no option with {text!r} in {selector}: {[label for _, label in options][:12]}")


def p2_options(scope, selector: str) -> list[str]:
    select = scope.locator(selector)
    if not select.count():
        return []
    return select.first.evaluate("el => [...el.options].map(o => o.textContent.trim()).filter(Boolean)")


def p2_fill(scope, names: tuple[str, ...], value: str) -> str:
    """Fill the first input of these names that the form has. Returns the name used ('' when the form has none)."""
    for name in names:
        field = scope.locator(f"input[name={name}], textarea[name={name}]")
        if field.count():
            field.first.fill(value)
            return name
    return ""


def p2_toggle(page: Page, label: str) -> None:
    """A button or radio in the Finance body whose whole text is label (P&L Day, Week, Cash, Accrual, Month)."""
    page.locator("#fin-body button, #fin-body [role=radio]").filter(has_text=re.compile(rf"^{label}$")).first.click()
    page.wait_for_timeout(900)


def p2_range(page: Page, start: str, end: str) -> None:
    page.locator("[data-fin-range]").first.click()
    page.wait_for_timeout(500)
    pop = page.locator("#fin-range")
    pop.locator("input[name=start]").first.fill(start)
    pop.locator("input[name=end]").first.fill(end)
    pop.locator("button", has_text=re.compile(r"^Apply$")).first.click()
    page.wait_for_timeout(1200)
    page.wait_for_load_state("networkidle")


def scroll_fit(page: Page, run: Run, label: str, shots: str, file: str) -> None:
    scroll = page.evaluate(JS_SCROLL)
    page.screenshot(path=os.path.join(shots, file), full_page=True)
    run.check(f"{label}: no sideways scroll", scroll["sw"] <= scroll["cw"] + 1, str(scroll))


def dialog_fit(page: Page, run: Run, label: str, shots: str, file: str) -> None:
    box = page.evaluate(JS_DLG)
    page.screenshot(path=os.path.join(shots, file))
    run.check(f"{label}: the dialog fits the screen", bool(box) and box["left"] >= 0 and box["right"] <= box["cw"] + 1,
              str(box))


def p2_bill(page: Page, run: Run, base: str, line_text: str, number: str, amount: str, label: str) -> dict:
    """A bill from + Bill on the line whose label has line_text, then Approve. Returns the bill and its entries as read back."""
    go(page, base, "#/finance/payables")
    dlg = open_dialog(page, "[data-fin-doc-add]")
    pick_contact(dlg, "Rehearsal vendor")
    dlg.locator("input[name=number]").fill(number)
    chosen = p2_select(dlg, "select[name=line]", line_text)
    dlg.locator("input[name=amount]").fill(amount)
    dlg.locator("input[name=memo]").fill("Pass 2 line test")
    problem = submit(page, dlg)
    run.check(f"{label}: the bill saves from + Bill on {chosen!r}", not problem, problem or "saved as a draft")
    if problem:
        return {}
    open_draft(page, number)
    page.locator("[data-fin-do=approve]").first.click()
    page.wait_for_timeout(1500)
    _, bills = get_json(page, base, "/api/v2/finance/bills?limit=200")
    bill = next((r for r in rows_of(bills) if r.get("number") == number), {})
    _, feed = get_json(page, base, f"/api/v2/finance/transactions?q={number}&limit=20")
    entries = [r for r in rows_of(feed) if r.get("kind") == "entry"]
    run.note(f"{label}: bill after Approve", json.dumps(bill, default=str)[:400])
    run.note(f"{label}: entries it posted", json.dumps(entries, default=str)[:700] or "none")
    return {"chosen": chosen, "bill": bill, "entries": entries}


def live_refresh(page: Page, run: Run, base: str, fake: str) -> None:
    """Transactions stays open (no reload) while a second page runs Sync now on Mercury. The spec does not require
    the list to update by itself; this records what happens."""
    go(page, base, "#/finance/transactions")
    live = P2_LIVE
    fake_call(fake, "/_fake/txn", {"amount": -200, "counterparty": "OPS VENDOR", "date": P2_DAY, "kind": "bank"})
    fake_call(fake, "/_fake/txn", {"amount": -12.34, "counterparty": live, "date": P2_DAY, "kind": "bank"})
    other = page.context.new_page()
    other.on("dialog", lambda d: d.accept())
    try:
        go(other, base, "#/finance/sources")
        expand_sources(other)
        other.locator("tr[data-fin-src]", has_text="Mercury").first.click()
        other.wait_for_timeout(600)
        other.locator("[data-fin-sync]").first.click()
        started, seen = time.time(), False
        while time.time() - started < 90 and not seen:
            page.wait_for_timeout(3000)
            seen = live in body_text(page)
        run.check("6 live refresh: a synced row shows on the open Transactions page with no reload", seen,
                  f"after {round(time.time() - started)} s; the spec does not require live refresh")
        if not seen:
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(800)
            run.note("6 live refresh: after a reload", "row shows" if live in body_text(page) else "row missing")
    finally:
        other.close()


def categorize(page: Page, run: Run, base: str, counterparty: str, line_text: str, shots: str) -> None:
    """Inline categorize on a new feed row (its line select saves on change), read back from the feed. A row that is
    already posted has no line select: noted, not failed (an earlier run posted it)."""
    go(page, base, "#/finance/transactions")
    row = tx_row(page, counterparty)
    if not row.count():
        run.check(f"6 inline categorize: the {counterparty} row is on the page", False)
        return
    if not row.locator("select[data-fin-tx-line]").count():
        run.note(f"6 inline categorize {counterparty}", "not exercised: the row is already posted to a line")
        return
    options = p2_options(row, "select[data-fin-tx-line]")
    run.note(f"6 inline categorize: line choices on {counterparty}", " | ".join(options[:20]) or "none")
    choice = next((o for o in options if line_text.lower() in o.lower()), "")
    if not choice:
        run.check(f"6 inline categorize: a {line_text} line is offered for {counterparty}", False, " | ".join(options))
        return
    row.locator("select[data-fin-tx-line]").select_option(label=choice)
    page.wait_for_timeout(1800)
    page.wait_for_load_state("networkidle")
    _, data = get_json(page, base, f"/api/v2/finance/transactions?q={counterparty.replace(' ', '%20')}&limit=20")
    feed = [r for r in rows_of(data) if r.get("counterparty") == counterparty]
    status = feed[0].get("status") if feed else None
    run.check(f"6 inline categorize of {counterparty} to {choice!r} saves", bool(feed) and status != "new",
              f"status {status}; entry {feed[0].get('entry_id') if feed else None}")
    page.screenshot(path=os.path.join(shots, f"item6-categorized-{counterparty.split()[0].lower()}.png"), full_page=True)


def tag_row(page: Page, run: Run, base: str, counterparty: str, teammate: str, expect: str) -> None:
    """Tag a feed row from the rail (teammate select, then Save); read back the teammate on the feed row when it has one."""
    row = tx_row(page, counterparty)
    if not row.count():
        run.check(f"6 tag {counterparty}: the row is on the page", False)
        return
    row.locator("td").first.click()        # the date cell: a click on the line select would not select the row
    page.wait_for_timeout(700)
    # A posted feed row's rail is the categorize form (data-fin-cat) with a Teammate select; a row with no P&L line
    # (an internal transfer) has no tag form at all, so it is noted, not failed.
    teammate_select = page.locator("#fin-rail select[name=teammate]")
    try:
        teammate_select.first.wait_for(timeout=6000)
    except Exception:
        run.note(f"6 tag {counterparty}", "no Teammate select in the rail (a row with no P&L line cannot be tagged)")
        return
    chosen = p2_select(page, "#fin-rail select[name=teammate]", teammate)
    page.locator("#fin-rail [data-fin-cat-save], #fin-rail form[data-fin-tag] [type=submit]").first.click()
    page.wait_for_timeout(900)
    yes = page.locator("#fin-rail [data-fin-cat-yes]")
    if yes.count() and yes.first.is_visible():
        yes.first.click()                  # an approval prompt for the teammate: send it
    page.wait_for_timeout(1500)
    page.wait_for_load_state("networkidle")
    _, data = get_json(page, base, f"/api/v2/finance/transactions?q={counterparty.replace(' ', '%20')}&limit=20")
    feed = [r for r in rows_of(data) if r.get("counterparty") == counterparty]
    tagged = [(l.get("tags") or {}).get("teammate") for r in feed for l in (r.get("lines") or [])]
    run.check(f"6 tag {chosen!r} on {counterparty} reads back on its lines", expect in tagged,
              f"line teammates {tagged}")


# ---- item 6: Transactions (desktop writes, both widths for the filters).
def item6(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    go(page, base, "#/finance/transactions")
    shown = page.locator("tr[data-fin-tx]").count()
    run.check(f"6 {width}: the synced rows list on Transactions", shown >= 5, f"{shown} rows")
    page.screenshot(path=os.path.join(shots, f"item6-transactions-{width}.png"), full_page=True)
    if width == "phone":
        scroll_fit(page, run, "6 phone: Transactions", shots, "item6-transactions-phone-fit.png")
    for status in ("posted", "new", "ignored"):
        page.select_option("select[data-fin-status]", status)
        page.wait_for_timeout(900)
        ui = page.locator("tr[data-fin-tx]").count()
        _, data = get_json(page, base, f"/api/v2/finance/transactions?status={status}&limit=200")
        api_rows = rows_of(data)
        same = ui == len(api_rows) and all(r.get("status") == status for r in api_rows)
        run.check(f"6 {width}: Status {status} lists the matching rows (API agrees)", same,
                  f"UI {ui} rows, API {len(api_rows)}")
        if ui == 0:
            run.check(f"6 {width}: Status {status} with no rows shows an empty state", "No transactions." in body_text(page))
    page.select_option("select[data-fin-status]", "")
    page.wait_for_timeout(700)
    if width == "desktop":
        live_refresh(page, run, base, fake)
        # Post the new rows to a line (inline), then tag them from the rail. P2_LIVE stays untagged: it is the
        # unattributed spend that spec 7.5 keeps from a manager.
        categorize(page, run, base, "OPS VENDOR", "Software", shots)
        categorize(page, run, base, P2_LIVE, "Software", shots)
        categorize(page, run, base, "Acme Customer", "Revenue", shots)
        tag_row(page, run, base, "Mercury IO", "support", "bot:support")   # an internal transfer: no tag form (noted)
        tag_row(page, run, base, "Acme Cloud", "support", "bot:support")
        tag_row(page, run, base, "Mercury", "cara", "human:cara")
        tag_row(page, run, base, "OPS VENDOR", "Ana Rivera", "human:ana")   # "ana" alone also matches "Manager"
    go(page, base, "#/finance/transactions")
    lines = p2_options(page, "select[data-fin-account]")
    run.note(f"6 {width}: Line filter choices", " | ".join(lines[:12]) or "none")
    if len(lines) > 1:
        page.select_option("select[data-fin-account]", label=lines[1])
        page.wait_for_timeout(900)
        account = page.locator("select[data-fin-account]").input_value()
        ui = page.locator("tr[data-fin-tx]").count()
        _, data = get_json(page, base, f"/api/v2/finance/transactions?account_id={account}&limit=200")
        run.check(f"6 {width}: the Line filter ({lines[1]!r}) narrows the rows (API agrees)", ui == len(rows_of(data)),
                  f"UI {ui}, API {len(rows_of(data))}")
        page.select_option("select[data-fin-account]", "")
        page.wait_for_timeout(700)
    else:
        run.check(f"6 {width}: the Line filter offers lines", False, "only the blank choice")


# ---- item 1: manager view (§7.5). Owner totals and Ben's (view_as) totals, the hidden rows, and the owner-only surfaces.
def item1(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    if width == "phone":
        go(page, base, "#/finance/pnl?view=expenses&view_as=human:ben")
        scroll_fit(page, run, "1 phone: Ben's expenses", shots, "item1-ben-expenses-phone.png")
        return
    qs = "".join(f"&teammates={t}" for t in BEN_SEES)
    # November: October's spend is all tagged to Ben's teammates, so the unattributed rows (today's) are the hidden spend.
    _, owner = get_json(page, base, f"/api/v2/finance/pnl?view=expenses&{P2_NOV}")
    _, ben = get_json(page, base, f"/api/v2/finance/pnl?view=expenses&{P2_NOV}&view_as=human:ben")
    _, mine = get_json(page, base, f"/api/v2/finance/pnl?view=expenses&{P2_NOV}{qs}")
    run.note("1 expense totals (cents): owner all, owner for Ben's teammates, Ben's view",
             f"{cents(owner.get('expense'))}, {cents(mine.get('expense'))}, {cents(ben.get('expense'))}")
    run.check("1 Ben's expense total equals the owner's total for Ben's teammates (no hidden spend)",
              cents(ben.get("expense")) == cents(mine.get("expense")) and ben.get("expense") is not None,
              f"Ben {ben.get('expense')}, owner for his teammates {mine.get('expense')}")
    run.check("1 the owner sees more expense than Ben (hidden spend exists to test)",
              cents(owner.get("expense")) > cents(ben.get("expense")),
              f"owner {cents(owner.get('expense'))} vs Ben {cents(ben.get('expense'))}")
    _, tx = get_json(page, base, "/api/v2/finance/transactions?limit=200&view_as=human:ben")
    seen_names = sorted({str(r.get("counterparty")) for r in rows_of(tx)})
    leaked = [n for n in ("Mercury", "Acme Customer", P2_LIVE) if n in seen_names]
    run.check("1 Ben's transactions show no hidden rows (bot:inbox, revenue, unattributed)", not leaked,
              f"Ben sees {seen_names}; leaked {leaked}")
    # Tagged spend shows as entries with no counterparty name, so the check reads amounts: Acme Cloud 4500 (Support's
    # card spend) and Mercury 1500 (Cara's wire fee) were tagged in item 6 and should both be on Ben's list.
    ben_amounts = {abs(int(r.get("amount") or 0)) for r in rows_of(tx)}
    run.note("1 Ben's transactions: amounts he sees (cents)", str(sorted(ben_amounts))[:200])
    run.check("1 Ben's transactions show his teammates' tagged spend (4500 Support, 1500 Cara)",
              {4500, 1500} <= ben_amounts, f"amounts {sorted(ben_amounts)}")
    _, company = get_json(page, base, f"/api/v2/finance/pnl?view=company&{P2_NOV}&view_as=human:ben")
    run.check("1 Ben's company P&L expense is his own expense, not the company total (spec 7.5)",
              cents(company.get("expense")) == cents(ben.get("expense")),
              f"company view {company.get('expense')}, Ben's expenses view {ben.get('expense')}, owner {owner.get('expense')}")
    # "Mercury" is left out: Cara's wire fee is tagged to her, so Ben's search rightly finds it.
    for needle in (P2_LIVE, "Acme Customer"):
        _, hits = get_json(page, base, f"/api/v2/finance/search?q={needle.replace(' ', '%20')}&view_as=human:ben")
        _, owner_hits = get_json(page, base, f"/api/v2/finance/search?q={needle.replace(' ', '%20')}")
        run.check(f"1 search for {needle!r} as Ben does not return the hidden row", not has_text(hits, needle),
                  f"owner search has it: {has_text(owner_hits, needle)}")
    for path, passes in (
        ("balance-sheet", lambda s, d: s != 200 or not rows_of(d)),
        ("bills?limit=5", lambda s, d: s != 200 or not rows_of(d)),
        ("invoices?limit=5", lambda s, d: s != 200 or not rows_of(d)),
        ("sources", lambda s, d: s != 200 or not rows_of(d)),
        ("pnl?view=company&" + P2_MONTH, lambda s, d: s != 200 or cents(d.get("income")) == 0),
    ):
        sep = "&" if "?" in path else "?"
        status, data = get_json(page, base, f"/api/v2/finance/{path}{sep}view_as=human:ben")
        run.check(f"1 Ben (view_as) gets no owner-only {path.split('?')[0]} data (spec 7.5)", passes(status, data),
                  f"status {status}")
    go(page, base, "#/finance/pnl?view=expenses&view_as=human:ben")
    text = body_text(page)
    tabs = page.locator("[data-fin-tab]").evaluate_all("els => els.map(e => e.textContent.trim())")
    run.note("1 Ben's tabs", ", ".join(tabs))
    run.check("1 Ben's expenses page shows no unattributed or revenue row",
              not any(s in text for s in (P2_LIVE, "Acme Customer", "Unattributed")),
              " ".join(text.split())[:160])
    page.screenshot(path=os.path.join(shots, "item1-ben-expenses-desktop.png"), full_page=True)


# ---- item 7: Expenses (org-chart picker, chips, side panel).
def item7(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    go(page, base, "#/finance/pnl?view=expenses")
    if width == "phone":
        page.locator("[data-fin-filter]").first.click()
        page.wait_for_timeout(500)
    picks = page.locator("[data-fin-pick]").evaluate_all(
        "els => els.map(e => (e.getAttribute('data-fin-pick') || '') + ' = ' + ((e.closest('label') || e.parentElement || e).textContent || '').trim())")
    run.note(f"7 {width}: picker values as the owner (value = label)", " | ".join(dict.fromkeys(picks)))
    # The checkbox is visually hidden; pick the one in the tree that is showing (desktop: the side column, phone: the popover).
    page.locator("[data-fin-pick='mate:bot:support']:visible").first.check(force=True)
    page.wait_for_timeout(1000)
    if width == "phone":
        # With the Filter popover still open, can a person tap a chip's remove (x)? Recorded, then the popover is closed.
        try:
            page.locator("#fin-chips [data-fin-clear]").first.click(trial=True, timeout=2000)
            run.check("7 phone: the chip's remove (x) is tappable while the Filter popover is open", True)
        except Exception as e:
            covered = "intercepts pointer events" in str(e)
            run.check("7 phone: the chip's remove (x) is tappable while the Filter popover is open", False,
                      "covered by the open popover" if covered else str(e).splitlines()[0][:160])
        page.locator("[data-fin-filter]").first.click()
        page.wait_for_timeout(500)
    chips = page.locator("#fin-chips .fin-chip").all_inner_texts()
    run.check(f"7 {width}: picking Support adds a filter chip", any("support" in c.lower() for c in chips),
              " | ".join(chips))
    _, api = get_json(page, base, f"/api/v2/finance/pnl?view=expenses&{P2_MONTH}&teammates=bot:support")
    want = sorted(r["name"] for r in api.get("rows", []) if cents(r.get("total")) and r.get("code")) if isinstance(api, dict) else []
    # A collapsed group carries a disclosure mark (▸) after its name; the name is what is compared.
    ui = page.locator("#fin-pl tbody tr td:first-child").evaluate_all("els => els.map(e => e.textContent.replace('▸', '').trim())")
    missing = [n for n in want if n not in ui]
    run.check(f"7 {width}: the filtered table shows every line with Support spend (API agrees)", not missing,
              f"API lines {want}; missing in UI {missing}")
    run.note(f"7 {width}: side panel with Support picked", " ".join(page.locator("#fin-rail").inner_text().split())[:300])
    page.screenshot(path=os.path.join(shots, f"item7-expenses-support-{width}.png"), full_page=True)
    if width == "phone":
        scroll_fit(page, run, "7 phone: Expenses", shots, "item7-expenses-phone-fit.png")
    page.locator("#fin-chips [data-fin-clear]").first.click()
    page.wait_for_timeout(900)
    run.check(f"7 {width}: clearing the chip removes the filter", page.locator("#fin-chips .fin-chip").count() == 0)
    if width == "desktop":
        go(page, base, "#/finance/pnl?view=expenses&view_as=human:ben")
        ben = page.locator("[data-fin-pick]").evaluate_all(
            "els => els.map(e => ((e.getAttribute('data-fin-pick') || '') + ' ' + ((e.closest('label') || e.parentElement || e).textContent || '')).trim())")
        values = page.locator("[data-fin-pick]").evaluate_all("els => els.map(e => e.getAttribute('data-fin-pick') || '')")
        run.check("7 Ben's picker does not offer Unattributed (spec 7.5: company-wide viewers only)",
                  not any(v.startswith("none") for v in values) and not any("unattributed" in p.lower() for p in ben),
                  " | ".join(dict.fromkeys(ben))[:200])


# ---- item 2: Access, View as (owner only, read only, audited per spec 7.5).
def item2(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    go(page, base, "#/finance/access")
    page.locator("[data-fin-viewas]").first.click()
    dlg = page.locator("dialog.fin-dlg[open]")
    dlg.wait_for(timeout=8000)
    run.note(f"2 {width}: View as dialog text", " ".join(dlg.inner_text().split())[:300])
    run.note(f"2 {width}: who choices", " | ".join(p2_options(dlg, "select[name=who]")))
    if width == "phone":
        dialog_fit(page, run, "2 phone: the View as dialog", shots, "item2-viewas-dialog-phone.png")
    chosen = p2_select(dlg, "select[name=who]", "Ben")
    dlg.locator("button", has_text=re.compile(r"^View$")).first.click()
    page.wait_for_timeout(1200)
    banner = page.locator(".fin-as")
    text = banner.first.inner_text() if banner.count() else ""
    run.check(f"2 {width}: View as {chosen!r} shows the viewing banner", "Ben" in text, text[:120] or "no banner")
    page.locator("[data-fin-tab]").first.click()
    page.wait_for_timeout(900)
    writes = page.locator("[data-fin-doc-add], [data-fin-card-add], [data-fin-do], [data-fin-add]").count()
    run.check(f"2 {width}: viewing as Ben, the first tab has no write controls", writes == 0, f"{writes} control(s)")
    page.screenshot(path=os.path.join(shots, f"item2-viewing-as-{width}.png"), full_page=True)
    if width == "phone":
        scroll_fit(page, run, "2 phone: viewing as Ben", shots, "item2-viewing-as-phone-fit.png")
    page.locator("[data-fin-as-end]").first.click()
    page.wait_for_timeout(1000)
    run.check(f"2 {width}: Stop viewing clears the banner", page.locator(".fin-as").count() == 0)


# ---- item 3: the bill line picker. Bank (an asset line) and a group parent (Operating expenses) on a bill.
def item3(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    if width == "phone":
        go(page, base, "#/finance/payables")
        dlg = open_dialog(page, "[data-fin-doc-add]")
        chosen = p2_select(dlg, "select[name=line]", "Bank")
        dialog_fit(page, run, f"3 phone: the bill dialog with {chosen!r} chosen", shots, "item3-bill-dialog-phone.png")
        page.keyboard.press("Escape")
        return
    _, bs0 = get_json(page, base, "/api/v2/finance/balance-sheet")
    # Bills are dated today (November), so the P&L the group-parent bill must move is November's.
    _, pl0 = get_json(page, base, f"/api/v2/finance/pnl?{P2_NOV}")
    bank0, expense0 = bank_of(bs0), cents(pl0.get("expense"))
    p2_bill(page, run, base, "Bank", f"BANK-{STAMP}", "100", "3 Bank line")
    _, bs1 = get_json(page, base, "/api/v2/finance/balance-sheet")
    run.note("3 Bank line: bank balance before and after the approved bill (cents)", f"{bank0} -> {bank_of(bs1)}")
    run.check("3 Bank line: the bank balance moves by the bill amount (asset line, Dr Bank Cr AP)",
              bank_of(bs1) - bank0 == 10000, f"{bank0} -> {bank_of(bs1)}")
    p2_bill(page, run, base, "Operating expenses", f"PARENT-{STAMP}", "100", "3 group parent")
    _, pl1 = get_json(page, base, f"/api/v2/finance/pnl?{P2_NOV}")
    rows0, rows1 = pnl_rows(pl0), pnl_rows(pl1)
    run.note("3 group parent: the 6000 row total before and after (cents)",
             f"{cents(rows0.get('6000', {}).get('total'))} -> {cents(rows1.get('6000', {}).get('total'))}")
    run.note("3 group parent: P&L expense total before and after (cents)", f"{expense0} -> {cents(pl1.get('expense'))}")
    run.check("3 group parent: the P&L expense total rises by the bill once", cents(pl1.get("expense")) - expense0 == 10000,
              f"delta {cents(pl1.get('expense')) - expense0}")
    page.screenshot(path=os.path.join(shots, "item3-bills-desktop.png"), full_page=True)


# ---- item 4: balance sheet dialogs (desktop). Journal entry, asset, loan or credit line, and Import history.
def item4(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    go(page, base, "#/finance/balance")
    _, bs0 = get_json(page, base, "/api/v2/finance/balance-sheet")
    bank0 = bank_of(bs0)

    def add(item: str) -> Locator:
        page.locator("[data-fin-add]").first.click()
        page.wait_for_timeout(400)
        page.locator(f"[data-fin-add-item={item}]").first.click()
        dlg = page.locator("dialog.fin-dlg[open]")
        dlg.wait_for(timeout=8000)
        return dlg

    dlg = add("journal")
    rows = dlg.locator("[data-fin-jl] .fin-jl")
    run.note("4 journal: rows in the dialog", str(rows.count()))
    p2_select(rows.nth(0), "select[name=acct]", "Bank")
    rows.nth(0).locator("input[name=dr]").fill("300")
    p2_select(rows.nth(1), "select[name=acct]", "Gateway")
    rows.nth(1).locator("input[name=cr]").fill("300")
    dlg.screenshot(path=os.path.join(shots, "item4-journal-dialog.png"))
    problem = submit(page, dlg)
    run.check("4 journal entry saves (Dr Bank, Cr Gateway credit)", not problem, problem or "saved")
    _, bs1 = get_json(page, base, "/api/v2/finance/balance-sheet")
    run.check("4 journal: the balance sheet's Bank moves by 300 (cents 30000)", bank_of(bs1) - bank0 == 30000,
              f"{bank0} -> {bank_of(bs1)}")

    dlg = add("asset")
    run.note("4 asset: form fields", ", ".join(dlg.locator("input, select").evaluate_all("els => els.map(e => e.name).filter(Boolean)")))
    p2_fill(dlg, ("name", "label", "title"), "Rehearsal laptop")
    used = p2_fill(dlg, ("cost", "amount", "value", "price"), "1200")
    dlg.screenshot(path=os.path.join(shots, "item4-asset-dialog.png"))
    problem = submit(page, dlg)
    run.check("4 asset saves", not problem, problem or f"amount field {used!r}")
    _, bs2 = get_json(page, base, "/api/v2/finance/balance-sheet")
    run.check("4 asset is on the balance sheet at its cost (120000 cents)", cents(named(bs2, "Rehearsal laptop").get("balance")) == 120000,
              json.dumps(named(bs2, "Rehearsal laptop"), default=str)[:160])

    dlg = add("loan")
    run.note("4 loan: form fields", ", ".join(dlg.locator("input, select").evaluate_all("els => els.map(e => e.name).filter(Boolean)")))
    p2_fill(dlg, ("name", "label", "title"), "Rehearsal credit line")
    used = p2_fill(dlg, ("amount", "balance", "limit", "principal"), "5000")
    dlg.screenshot(path=os.path.join(shots, "item4-loan-dialog.png"))
    problem = submit(page, dlg)
    run.check("4 loan or credit line saves", not problem, problem or f"amount field {used!r}")
    _, bs3 = get_json(page, base, "/api/v2/finance/balance-sheet")
    run.check("4 loan is on the balance sheet at 500000 cents (sign may be negative)",
              abs(cents(named(bs3, "Rehearsal credit line").get("balance"))) == 500000,
              json.dumps(named(bs3, "Rehearsal credit line"), default=str)[:160])

    dlg = add("import")
    run.note("4 import: dialog text", " ".join(dlg.inner_text().split())[:300])
    csv_path = os.path.join(shots, "item4-import-rehearsal.csv")
    with open(csv_path, "w") as out:
        out.write("date,description,amount\n2026-10-20,Rehearsal import,-42.00\n")
    dlg.locator("input[type=file]").first.set_input_files(csv_path)
    page.wait_for_timeout(800)
    preview = dlg.locator("button", has_text=re.compile("Preview", re.I))
    if preview.count():
        preview.first.click()
        page.wait_for_timeout(1500)
    run.note("4 import: after Preview", " ".join(dlg.inner_text().split())[:300])
    dlg.screenshot(path=os.path.join(shots, "item4-import-dialog.png"))
    problem = submit(page, dlg)
    run.note("4 import: after Save", problem or "dialog closed")
    go(page, base, "#/finance/balance")
    run.note("4 import history: text on the Balance page after the import",
             re.sub(r"\s+", " ", body_text(page))[:200])


# ---- item 5: Receivables (Late filter, reminders) and Payables (schedule, reschedule, bills address, direct send).
def late_invoice(page: Page, run: Run, base: str, shots: str) -> None:
    number = f"LATE-{STAMP}"
    go(page, base, "#/finance/receivables")
    dlg = open_dialog(page, "[data-fin-doc-add]")
    pick_contact(dlg, "Rehearsal customer")
    dlg.locator("input[name=number]").fill(number)
    run.note("5 invoice dialog fields", ", ".join(dlg.locator("input").evaluate_all("els => els.map(e => e.name).filter(Boolean)")))
    p2_fill(dlg, ("date", "issued", "invoice_date"), "2026-09-01")
    due_used = p2_fill(dlg, ("due", "due_date"), "2026-09-30")
    pick_first(dlg, "acct")
    dlg.locator("input[name=amt]").first.fill("210")
    problem = submit(page, dlg)
    run.check("5 an invoice dated in September, due 30 September, saves", not problem,
              problem or f"due field {due_used!r}")
    open_draft(page, number)
    page.locator("[data-fin-do=send]").first.click()
    page.wait_for_timeout(1500)
    _, data = get_json(page, base, "/api/v2/finance/invoices?limit=200")
    inv = next((r for r in rows_of(data) if r.get("number") == number), {})
    run.check("5 Send moves the invoice to sent", inv.get("status") == "sent", f"status {inv.get('status')}")
    page.locator("[role=radio]", has_text="Late").first.click()
    page.wait_for_timeout(900)
    listed = page.locator("tr[data-fin-doc]", has_text=number).count()
    run.check("5 the Late filter lists the overdue invoice", listed == 1, f"{listed} row(s); due {inv.get('due_date')}")
    page.screenshot(path=os.path.join(shots, "item5-late-desktop.png"), full_page=True)
    reminders = page.locator("button", has_text=re.compile("remind", re.I)).count()
    run.check("5 no reminder control on Receivables (spec 12: reminders are the Bookkeeper's, not a page control)",
              reminders == 0, f"{reminders} control(s)")


def schedule_and_address(page: Page, run: Run, base: str, shots: str) -> None:
    number = f"SCH-{STAMP}"
    open_draft(page, number, "Open")
    dlg = open_dialog(page, "[data-fin-do=schedule]")
    run.note("5 schedule dialog fields", ", ".join(dlg.locator("input").evaluate_all("els => els.map(e => e.name || e.type).filter(Boolean)")))
    used = p2_fill(dlg, ("date", "when", "scheduled_for", "pay_on", "run_on", "on"), "2026-11-20")
    if not used and dlg.locator("input[type=date]").count():
        dlg.locator("input[type=date]").first.fill("2026-11-20")
        used = "type=date"
    problem = submit(page, dlg)
    run.check("5 Schedule saves a payment date", not problem, problem or f"field {used!r}")
    _, data = get_json(page, base, "/api/v2/finance/bills?limit=200")
    bill = next((r for r in rows_of(data) if r.get("number") == number), {})
    run.check("5 the bill reads back with 2026-11-20 on it", "2026-11-20" in json.dumps(bill, default=str),
              f"status {bill.get('status')}")
    run.note("5 bill after Schedule", json.dumps(bill, default=str)[:400])
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(800)
    status = "Scheduled" if page.locator("[role=radio]", has_text="Scheduled").count() else "Open"
    open_draft(page, number, status)
    dlg = open_dialog(page, "[data-fin-do=schedule]")
    p2_fill(dlg, ("date", "when", "scheduled_for", "pay_on", "run_on", "on"), "2026-11-25")
    problem = submit(page, dlg)
    _, data = get_json(page, base, "/api/v2/finance/bills?limit=200")
    bill = next((r for r in rows_of(data) if r.get("number") == number), {})
    run.check("5 Reschedule moves it to 2026-11-25 (read back)", not problem and "2026-11-25" in json.dumps(bill, default=str),
              problem or f"bill {json.dumps(bill, default=str)[:160]}")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(800)
    dlg = open_dialog(page, "[data-fin-email-set]")
    p2_fill(dlg, ("address",), "bills@rehearsal.example")
    problem = submit(page, dlg)
    run.check("5 the bills address saves", not problem, problem)
    dlg = open_dialog(page, "[data-fin-email-set]")
    kept = dlg.locator("input[name=address]").first.input_value()
    run.check("5 the bills address reads back in the dialog", kept == "bills@rehearsal.example", f"shows {kept!r}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    toggle = page.locator("[data-fin-direct]").first
    before = toggle.evaluate("el => el.checked ?? el.getAttribute('aria-checked')")
    toggle.click()
    page.wait_for_timeout(1200)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(800)
    after = page.locator("[data-fin-direct]").first.evaluate("el => el.checked ?? el.getAttribute('aria-checked')")
    run.check("5 the direct send switch changes and stays changed after a reload (owner only, spec 9)", after != before,
              f"{before} -> {after}")
    page.locator("[data-fin-direct]").first.click()
    page.wait_for_timeout(1200)


def item5(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    if width == "desktop":
        late_invoice(page, run, base, shots)
        p2_bill(page, run, base, "Software", f"SCH-{STAMP}", "90", "5 bill to schedule")
        schedule_and_address(page, run, base, shots)
        return
    go(page, base, "#/finance/receivables")
    page.locator("[role=radio]", has_text="Late").first.click()
    page.wait_for_timeout(900)
    scroll_fit(page, run, "5 phone: Receivables with the Late filter", shots, "item5-late-phone-fit.png")
    go(page, base, "#/finance/payables")
    open_draft(page, f"SCH-{STAMP}", "Open")
    dialog_fit(page, run, "5 phone: the schedule dialog", shots, "item5-schedule-phone.png")
    page.keyboard.press("Escape")


# ---- item 8: P&L range, compare to a budget version, accrual and cash, Day and Week.
def item8(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    go(page, base, "#/finance/pnl")
    p2_range(page, "2026-10-01", "2026-10-15")
    _, data = get_json(page, base, "/api/v2/finance/pnl?start=2026-10-01&end=2026-10-15&period=month&basis=accrual")
    labels = [c.get("label") for c in data.get("columns", []) if isinstance(c, dict)] if isinstance(data, dict) else []
    head = page.locator("#fin-pl thead th").evaluate_all("els => els.map(e => e.textContent.trim())")
    # The API labels a month column "2026-10"; the table shows it as "Oct". The API must honor the range: Oct 1-12
    # (income 0, expense 1500 in the demo) against the whole month (expense 6000).
    shown_labels = ["Oct" if l == "2026-10" else l for l in labels]
    _, narrow = get_json(page, base, "/api/v2/finance/pnl?start=2026-10-01&end=2026-10-12&period=month&basis=accrual")
    run.check(f"8 {width}: the range is applied by the API (Oct 1-12 expense is not the month's)",
              cents(narrow.get("expense")) != cents(data.get("expense")) if isinstance(data, dict) else False,
              f"Oct 1-12 expense {narrow.get('expense')}, Oct 1-15 expense {data.get('expense') if isinstance(data, dict) else None}")
    run.note(f"8 {width}: range label on the page", page.locator("[data-fin-range]").first.inner_text().strip())
    run.check(f"8 {width}: the range Oct 1 to 15 reaches the table (API agrees)", bool(labels) and any(l in " ".join(head) for l in shown_labels),
              f"API columns {labels}; UI head {head}")
    page.screenshot(path=os.path.join(shots, f"item8-range-{width}.png"), full_page=True)
    if width == "phone":
        scroll_fit(page, run, "8 phone: P&L with a range", shots, "item8-range-phone-fit.png")
    if width == "desktop":
        go(page, base, "#/finance/pnl")
        # November, not the October default: the Software budget starts in November, so October shows no version.
        p2_range(page, "2026-11-01", "2026-11-30")
        before = page.locator("#fin-pl thead th").evaluate_all("els => els.map(e => e.textContent.trim())")
        options = p2_options(page, "select[data-fin-compare]")
        run.note("8 compare: budget versions offered", " | ".join(options) or "none")
        if len(options) > 1:
            # Each version is compared with the table as it stood with no version: the whole table text, not the headers.
            base_body = page.locator("#fin-pl").inner_text()
            changed = {}
            for version in options[1:]:
                page.select_option("select[data-fin-compare]", label=version)
                page.wait_for_timeout(1000)
                changed[version] = page.locator("#fin-pl").inner_text() != base_body
            run.note("8 compare: does the table text change with each version", str(changed))
            run.check("8 compare to a budget version changes the table (some version)", any(changed.values()),
                      f"headers before {len(before)} cells; {changed}")
            page.select_option("select[data-fin-compare]", "")
            page.wait_for_timeout(700)
        else:
            run.note("8 compare to a budget version", "not exercised: the demo has no budget version to compare with")
        _, acc = get_json(page, base, f"/api/v2/finance/pnl?{P2_MONTH}")
        _, cash = get_json(page, base, f"/api/v2/finance/pnl?{P2_MONTH.replace('basis=accrual', 'basis=cash')}")
        p2_toggle(page, "Cash")
        run.check("8 Cash basis shows Cash on the page", "Cash" in body_text(page))
        run.note("8 Oct expense total: accrual vs cash (cents)", f"{cents(acc.get('expense'))} vs {cents(cash.get('expense'))}")
        p2_toggle(page, "Accrual")
    go(page, base, "#/finance/pnl")
    p2_range(page, "2026-10-01", "2026-10-31")
    for label in ("Day", "Week"):
        p2_toggle(page, label)
        _, data = get_json(page, base, f"/api/v2/finance/pnl?{P2_MONTH.replace('period=month', 'period=' + label.lower())}")
        cols = [c.get("label") for c in data.get("columns", []) if isinstance(c, dict)] if isinstance(data, dict) else []
        head = page.locator("#fin-pl thead th").evaluate_all("els => els.map(e => e.textContent.trim())")
        if label == "Day":
            # The API returns a column per day in the range (31 for October). The table's day headers read "Oct 26" form.
            day_heads = [h for h in head if re.match(r"^[A-Z][a-z]{2} \d{1,2}$", h)]
            run.note(f"8 {width}: Day header labels on the page", " | ".join(head)[:300])
            run.check(f"8 {width}: Day shows a column for each day of the range (API {len(cols)})", len(day_heads) >= len(cols),
                      f"UI shows {len(day_heads)} day columns: {day_heads[:1]}..{day_heads[-1:]}; API {len(cols)} from {cols[:1]}")
        else:
            run.note(f"8 {width}: Week header labels on the page (API {len(cols)} columns from {cols[:1]})", " | ".join(head)[:300])
    if width == "phone":
        scroll_fit(page, run, "8 phone: P&L Week", shots, "item8-week-phone-fit.png")
    p2_toggle(page, "Month")


# ---- item 9: Cards (limit change, empty states beyond "No cards yet.").
def item9(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    nickname = f"Limit card {STAMP}"
    go(page, base, "#/finance/cards")
    if width == "desktop":
        text = body_text(page)
        run.check("9 Cards with none shows 'No cards yet.'", "No cards yet." in text, " ".join(text.split())[:120])
        page.screenshot(path=os.path.join(shots, "item9-cards-empty.png"), full_page=True)
        dlg = open_dialog(page, "[data-fin-card-add]")
        pick_first(dlg, "holder")
        pick_first(dlg, "budget")
        dlg.locator("input[name=amount]").fill("500")
        dlg.locator("input[name=nickname]").fill(nickname)
        pick_first(dlg, "user")
        problem = submit(page, dlg)
        run.check("9 a card issues for the limit test", not problem, problem)
        page.wait_for_timeout(1000)
    else:
        dlg = open_dialog(page, "[data-fin-card-add]")
        dialog_fit(page, run, "9 phone: the + Card dialog", shots, "item9-card-dialog-phone.png")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    card = page.locator("[data-fin-card]", has_text=nickname).first
    card.click()
    page.wait_for_timeout(700)
    want = "750" if width == "desktop" else "900"
    page.locator("form[data-fin-limit] input[name=amount]").first.fill(want)
    page.locator("form[data-fin-limit] button", has_text=re.compile("Save limit")).first.click()
    page.wait_for_timeout(1500)
    page.wait_for_load_state("networkidle")
    _, data = get_json(page, base, "/api/v2/finance/cards")
    made = find_all(data, lambda d: d.get("nickname") == nickname)
    limits = {k: v for k, v in (made[0].items() if made else []) if "limit" in k}
    run.check(f"9 {width}: the limit saves to {want} (read back)", any(v in (int(want) * 100, int(want)) for v in limits.values()),
              f"limit fields {limits}")
    empties = re.findall(r"No [a-z ]+\.", body_text(page))
    run.note(f"9 {width}: empty-state texts on Cards", " | ".join(sorted(set(empties))) or "none")
    if width == "phone":
        scroll_fit(page, run, "9 phone: Cards with a limit", shots, "item9-cards-phone-fit.png")


# ---- item 10: close and reopen a month other than October (desktop only: the control is an API action).
def item10(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    def marks() -> str:
        return page.locator("#fin-pl-marks").first.inner_text() if page.locator("#fin-pl-marks").count() else ""

    go(page, base, "#/finance/pnl")
    p2_range(page, "2026-09-01", "2026-09-30")
    run.note("10 September marks before the close", marks() or "none")
    status, body = act(page, base, "close_period", {"month": "2026-09"})
    run.check("10 close_period closes September (owner)", status < 300, f"{status} {str(body)[:160]}")
    go(page, base, "#/finance/pnl")
    p2_range(page, "2026-09-01", "2026-09-30")
    shown = marks()
    run.check("10 the September P&L shows Closed", "Closed" in shown, shown[:120])
    page.screenshot(path=os.path.join(shots, "item10-september-closed.png"), full_page=True)
    number = f"SEP-{STAMP}"
    go(page, base, "#/finance/payables")
    dlg = open_dialog(page, "[data-fin-doc-add]")
    pick_contact(dlg, "Rehearsal vendor")
    dlg.locator("input[name=number]").fill(number)
    dlg.locator("input[name=date]").fill("2026-09-15")
    p2_select(dlg, "select[name=line]", "Software")
    dlg.locator("input[name=amount]").fill("80")
    problem = submit(page, dlg)
    run.check("10 a draft dated in September saves", not problem, problem or "saved as a draft")
    open_draft(page, number)
    page.locator("[data-fin-do=approve]").first.click()
    page.wait_for_timeout(1500)
    _, bills = get_json(page, base, "/api/v2/finance/bills?limit=200")
    bill = next((r for r in rows_of(bills) if r.get("number") == number), {})
    _, feed = get_json(page, base, f"/api/v2/finance/transactions?q={number}&limit=20")
    dates = sorted({str(r.get("date")) for r in rows_of(feed) if r.get("kind") == "entry"})
    run.check("10 approving the September bill posts no entry dated in September",
              not any(d.startswith("2026-09") for d in dates), f"bill status {bill.get('status')}; entry dates {dates}")
    st2, body2 = act(page, base, "reopen_period", {"month": "2026-09"})
    run.check("10 reopen_period reopens September (owner)", st2 < 300, f"{st2} {str(body2)[:160]}")
    go(page, base, "#/finance/pnl")
    p2_range(page, "2026-09-01", "2026-09-30")
    run.check("10 after reopen, September no longer shows Closed", "Closed" not in marks(), marks()[:120] or "no marks")
    run.note("10 not exercised", "a non-owner close attempt needs a second sign-in: the action takes the signed-in person, not view_as; "
             "there is no close or reopen control in the Finance tab")


# ---- item 11: vendor drill-down (spend bar, budget, warn level, paid-by, seats, renewal).
def item11(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    if width == "desktop":
        _, contacts = get_json(page, base, "/api/v2/finance/contacts")
        vendor = next((r for r in rows_of(contacts) if r.get("name") == "Rehearsal vendor"), None)
        if vendor is None:
            run.check("11 the Rehearsal vendor contact exists", False, "no such contact")
            return
        status, body = act(page, base, "contact_update", {"contact_id": vendor.get("id"), "seats": 12, "renewal_on": "2026-12-15"})
        run.check("11 contact_update sets seats and a renewal date", status < 300, f"{status} {str(body)[:160]}")
        _, contacts = get_json(page, base, "/api/v2/finance/contacts")
        after = next((r for r in rows_of(contacts) if r.get("name") == "Rehearsal vendor"), {})
        # The contacts list does not carry seats or renewal; the drill-down side panel reads them (checked below).
        run.note("11 contacts API keys for the vendor", str(sorted(after.keys()))[:300])
    go(page, base, "#/finance/pnl?view=expenses")
    links = page.locator("#fin-pl a[href*='/line/']")
    software = links.filter(has_text="Software")
    link = software.first if software.count() else links.first
    href = link.get_attribute("href") or ""
    link.click()
    page.wait_for_timeout(900)
    page.wait_for_load_state("networkidle")
    heads = page.locator("#main table thead th").evaluate_all("els => els.map(e => e.textContent.trim())")
    run.note(f"11 {width}: drill-down columns", " | ".join(h for h in heads if h))
    vendor_rows = page.locator("#main table tbody tr", has_text="Rehearsal vendor")
    run.check(f"11 {width}: the Rehearsal vendor row shows in the drill-down", vendor_rows.count() > 0,
              f"{vendor_rows.count()} row(s) at {href}")
    head_text = " ".join(heads).lower()
    run.check(f"11 {width}: the drill-down table has a budget column", "budget" in head_text, " | ".join(h for h in heads if h))
    if vendor_rows.count():
        # Seats, renewal, paid-by and team spend are in the vendor's side panel (spec 12), not table columns.
        vendor_rows.first.locator("td").first.click()
        page.wait_for_timeout(900)
        rail = " ".join(page.locator("#fin-rail").inner_text().split())
        run.check(f"11 {width}: the vendor's side panel shows seats and renewal", "Seats 12" in rail and "Renews Dec 15" in rail, rail[:200])
        run.check(f"11 {width}: the side panel shows paid-by and spend by team", "PAID BY" in rail.upper() and "BY TEAM" in rail.upper(), rail[:200])
    run.note(f"11 {width}: spend-bar elements", str(page.locator("#main [class*=bar]").count()))
    if width == "desktop" and vendor_rows.count():
        field = vendor_rows.first.locator("input[data-fin-vb]")
        if field.count():
            field.fill("100")
            field.press("Enter")
            page.wait_for_timeout(1500)
            page.wait_for_load_state("networkidle")
            warn = vendor_rows.first.inner_text()
            run.note("11 vendor row after a 100 budget", " ".join(warn.split())[:200])
            # The vendor's budget is a child budget of the line budget: read it from the budgets API by its vendor id.
            _, budget_data = get_json(page, base, "/api/v2/finance/budgets?include_inactive=true")
            budget_rows = (budget_data.get("budgets") if isinstance(budget_data, dict) else budget_data) or []
            amounts = [(r.get("state") or {}).get("amount") for r in budget_rows if r.get("vendor") == vendor.get("id")]
            run.check("11 the vendor budget reads back (100 dollars = 10000 cents)", 10000 in amounts, f"amounts {amounts}")
        else:
            run.note("11 vendor budget", "no budget field on the vendor row")
    if width == "phone":
        scroll_fit(page, run, "11 phone: vendor drill-down", shots, "item11-vendors-phone-fit.png")
    else:
        page.screenshot(path=os.path.join(shots, "item11-vendors-desktop.png"), full_page=True)


# ---- item 12: Sources on a separate demo (its own data folder and fake Mercury). Connect and disconnect Mercury only.
def item12(page: Page, run: Run, base: str, fake: str, shots: str, width: str) -> None:
    go(page, base, "#/finance/sources")
    expand_sources(page)
    _, before_data = get_json(page, base, "/api/v2/finance/sources")
    run.check("12 the separate demo starts with no Mercury connection", not has_text(before_data, "Mercury") and
               "Mercury" not in json.dumps(before_data, default=str), json.dumps(before_data, default=str)[:120])
    page.locator("[data-fin-connect]").first.click()
    page.locator("[data-fin-connect-item=mercury]").first.click()
    page.wait_for_selector("dialog.fin-dlg")
    page.fill("dialog.fin-dlg input[name=token]", TOKEN)
    page.fill("dialog.fin-dlg input[name=approver]", APPROVER)
    page.click("dialog.fin-dlg button[type=submit]")
    try:
        page.wait_for_selector("dialog.fin-dlg", state="detached", timeout=30000)
        error = ""
    except PlaywrightError:
        error = page.locator("dialog.fin-dlg [data-error]").first.inner_text()
    run.check("12 Mercury connects on the separate demo (its own fake)", not error, error[:200] or "connected")
    go(page, base, "#/finance/sources")
    expand_sources(page)
    _, after_data = get_json(page, base, "/api/v2/finance/sources")
    run.check("12 the connection reads back from the API", "Mercury" in json.dumps(after_data, default=str),
              json.dumps(after_data, default=str)[:160])
    page.screenshot(path=os.path.join(shots, "item12-sources-connected.png"), full_page=True)
    # The connection row is named exactly "Mercury"; "Mercury IO" is one of its accounts (a feed row with no Disconnect).
    conn = page.locator("tr[data-fin-src]").filter(has=page.locator("button.fin-src-name", has_text=re.compile(r"^Mercury$")))
    run.check("12 the Mercury connection row is on Sources", conn.count() >= 1, f"{conn.count()} rows")
    conn.first.click()
    page.wait_for_timeout(700)
    page.locator("[data-fin-disconnect]").first.click()
    page.wait_for_timeout(1500)
    if page.locator("dialog.fin-dlg[open]").count():
        submit(page, page.locator("dialog.fin-dlg[open]"))
    page.wait_for_load_state("networkidle")
    _, gone_data = get_json(page, base, "/api/v2/finance/sources")
    mercury = find_all(gone_data, lambda d: "Mercury" in str(d.get("name", "")) or "Mercury" in str(d.get("provider", "")))
    statuses = [str(d.get("status")) for d in mercury]
    run.check("12 Disconnect ends the Mercury connection (read back)",
              not mercury or all(s in ("disconnected", "None", "revoked", "removed") for s in statuses),
              f"Mercury rows {statuses or 'none'}")
    page.screenshot(path=os.path.join(shots, "item12-sources-disconnected.png"), full_page=True)


# Item -> (function, widths it runs at, and why not the other width). Items 4, 10 and 12 run at desktop only: the
# balance sheet dialogs, the close and reopen controls (an API action; no UI control) and Sources are desktop setup tasks.
P2 = {
    "6": (item6, ("desktop", "phone")),
    "1": (item1, ("desktop", "phone")),
    "7": (item7, ("desktop", "phone")),
    "2": (item2, ("desktop", "phone")),
    "3": (item3, ("desktop", "phone")),
    "4": (item4, ("desktop",)),
    "5": (item5, ("desktop", "phone")),
    "8": (item8, ("desktop", "phone")),
    "9": (item9, ("desktop", "phone")),
    "10": (item10, ("desktop",)),
    "11": (item11, ("desktop", "phone")),
    "12": (item12, ("desktop",)),
}


def pass2(browser, run: Run, args) -> None:
    names = [n.strip() for n in args.items.split(",") if n.strip()] or P2_ORDER
    for name in names:
        fn, widths = P2[name]
        if name == "12":
            if not args.sources_base or not args.sources_fake:
                run.check("12: a separate demo is given (--sources-base, --sources-fake)", False,
                          "not run; item 12 must never touch the stack demo's Mercury connection")
                continue
            base, fake = args.sources_base, args.sources_fake
        else:
            base, fake = args.base, args.fake
        for width in widths:
            run.current = f"item {name} {width}"
            ctx, page = p2_open(browser, run, base, width)
            try:
                fn(page, run, base, fake, args.shots, width)
            except STEP_FAILURES as e:
                run.check(f"item {name} {width}: its steps ran", False, f"{type(e).__name__}: {str(e)[:240]}")
            errors = run.tab_errors.get(run.current, [])
            run.check(f"item {name} {width}: no console or page errors", not errors, "; ".join(errors)[:300])
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
