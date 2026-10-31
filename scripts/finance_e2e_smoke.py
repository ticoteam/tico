"""Finance smoke test on the local stack (scripts/finance_e2e.sh): a real browser on the demo, a fake Mercury behind it.

It signs in as the demo owner, connects Mercury with a fake token (Finance > Sources > + Source > Mercury), checks
that accounts and transactions load, adds a transaction to the fake through /_fake/txn, runs "Sync now" and waits up to
two minutes for the transaction to show. Then it opens every Finance tab, records console and page errors and failed Finance requests,
and takes a desktop screenshot of each tab. Exit status 0 only when every check passed.

  uv run --no-project --with playwright python scripts/finance_e2e_smoke.py
  (run `uv run --no-project --with playwright python -m playwright install chromium` once)

Options: --base (default http://localhost:18766), --fake (default http://127.0.0.1:18790),
--shots (default /Volumes/PNYRP60PSSD/orca/workspaces/tico/_finance-e2e-shots).
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

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
        self.tab_errors: dict[str, list[str]] = {}
        self.current = "setup"

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)
        return bool(ok)


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
                while time.time() < deadline and not shown:
                    go(page, args.base, "#/finance/transactions")
                    shown = SMOKE_VENDOR in body_text(page)
                    if not shown:
                        page.wait_for_timeout(4000)
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
        browser.close()
    return finish(run, args.shots)


def finish(run: Run, shots: str) -> int:
    ok = all(c["ok"] for c in run.checks)
    with open(os.path.join(shots, "smoke-results.json"), "w") as out:
        json.dump({"checks": run.checks, "ok": ok}, out, indent=2)
    print(f"\n{sum(1 for c in run.checks if c['ok'])}/{len(run.checks)} checks passed; results in "
          f"{os.path.join(shots, 'smoke-results.json')}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
