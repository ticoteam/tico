#!/usr/bin/env python3
"""Rehearses Tico Finance's spreadsheet history import against a running local demo.

Test sheets for Acme (acme.example) are written to a temp folder and sent through the demo's import API: a dry run
for each, then the commit where the sheet should post. A sheet that should post must post exactly its totals in every
report it touches. A sheet that should be refused must be refused with a reason, and must post nothing. Shapes the
spec does not cover pass either way, so long as they never post a wrong figure silently; each is listed with its
outcome.

Only a loopback demo is accepted. Its owner token is in the demo's data directory:

    python3 scripts/finance_import_rehearsal.py --token-file DEMO_DATA/local-owner.token [--base URL] [--year 2023]

The demo must hold no transactions at all: the checks model every month. The exit code is 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import http.cookiejar
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

LOOPBACK = re.compile(r"http://(127\.0\.0\.1|localhost):\d{2,5}")
HEAD = ("month", "line", "amount")
STANDARD = {"Sales revenue": "4000", "Software subscriptions": "6100", "Office rent": "6900", "Cash at bank": "1000"}
TITLES = {
    1: "formats: CSV with a month, line and amount header",
    2: "layouts: one long layout, one month form per file",
    3: "numbers: whole cents, no dollar figures read silently",
    4: "structure: blank rows, column order, zero rows, teams",
    5: "accounts and mapping: codes, ids, inactive and opening accounts",
    6: "P&L results, labels and balances match the sheet",
    7: "repeats, conflicts, closed months, undo, itemized overlap",
    8: "limits and safety: row, byte, name and cap limits; formulas stay text",
    9: "rights: anonymous and cross-origin calls are refused",
}
Entry = tuple[str, str, int]          # (month, sheet line, cents)


def _months(year: int) -> list[str]:
    return [f"{year}-{m:02d}" for m in range(1, 13)]


def _eom(month: str) -> str:
    return f"{month}-{calendar.monthrange(int(month[:4]), int(month[5:7]))[1]:02d}"


def _fold(name: str) -> str:
    """A line name as the import keys it: runs of spaces collapse, then case folds (core/raw_totals.line_name)."""
    return " ".join(name.split()).casefold()


def _csv(rows: list[list[str]], header: tuple[str, ...] = HEAD, eol: str = "\n") -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator=eol)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def _cells(entries: list[Entry]) -> list[list[str]]:
    return [[month, name, str(cents)] for month, name, cents in entries]


def _contains(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_contains(v, needle) for v in value.values())
    if isinstance(value, list):
        return any(_contains(v, needle) for v in value)
    return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw or b"null")
    except ValueError:
        return raw[:300].decode("utf-8", "replace")


class Demo:
    """The demo's HTTP API as its owner. Writes carry the Origin header the server requires."""

    def __init__(self, base: str, token: str) -> None:
        self.base = base
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                                  NoRedirect)

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None,
                opener: Any = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        head = {"Content-Type": "application/json", "Origin": self.base, **(headers or {})}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=head)
        try:
            with (opener or self.opener).open(req, timeout=300) as resp:
                return resp.status, _decode(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, _decode(e.read())

    def sign_in(self) -> None:
        url = f"{self.base}/api/v2/local-signin?token={urllib.parse.quote(self.token)}&next=/"
        try:
            self.opener.open(urllib.request.Request(url), timeout=30)
        except urllib.error.HTTPError as e:
            if e.code not in (302, 303):
                raise SystemExit(f"sign-in failed: HTTP {e.code}") from None
        status, _ = self.request("GET", "/api/v2/finance/chart")
        if status != 200:
            raise SystemExit(f"sign-in did not reach Finance: HTTP {status}")

    def act(self, kind: str, payload: dict[str, Any], dry: bool = False) -> dict[str, Any]:
        status, body = self.request("POST", "/api/v2/finance/actions",
                                    {"kind": kind, "payload": payload, "dry_run": dry},
                                    {"Idempotency-Key": uuid.uuid4().hex})
        if status != 200 or not isinstance(body, dict):
            return {"http": status, "status": f"http {status}", "body": body}
        return body

    def get(self, path: str) -> Any:
        status, body = self.request("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {path} gave HTTP {status}")
        return body


@dataclass
class Check:
    number: int
    title: str
    failures: list[str] = field(default_factory=list)
    passed: int = 0

    def expect(self, ok: bool, message: str) -> bool:
        if ok:
            self.passed += 1
        else:
            self.failures.append(message)
        return ok


class Rehearsal:
    def __init__(self, demo: Demo, year: int, workdir: Path) -> None:
        self.demo = demo
        self.year = year
        self.workdir = workdir
        self.checks = {n: Check(n, title) for n, title in TITLES.items()}
        self.shapes: list[tuple[int, str, str, str]] = []
        self.files = 0
        self.refusal_months = _months(year - 2)      # refused or dry-run only, never committed
        self.commit_months = _months(year - 1) + _months(year)
        self.chart: dict[str, dict[str, Any]] = {}
        self.code_by_id: dict[str, str] = {}
        self.net: dict[str, dict[str, int]] = {}     # month -> account code -> cents the books should show
        self.live: dict[str, str] = {}               # month -> basis of its standing import
        self.imports: dict[str, tuple[str, dict[str, int]]] = {}   # entry id -> (month, totals)
        self.vendor_id = ""
        self.bills: dict[str, dict[str, int]] = {}   # month -> account code -> cents of approved bills (accrual only)
        self.numbers: dict[str, str] = {}            # bill id -> bill number, to find its reversal
        self.standard: tuple[str, str, dict[str, str], str] = ("", "", {}, "")

    # Setup and helpers.

    def load_chart(self) -> None:
        self.chart, self.code_by_id = {}, {}
        for account in self.demo.get("/api/v2/finance/chart")["accounts"]:
            self.chart[account["number"]] = account
            self.code_by_id[account["id"]] = account["number"]

    def code_of(self, ref: str) -> str:
        return ref if ref in self.chart else self.code_by_id.get(ref, ref)

    def natural(self, code: str, cents: int) -> int:
        return -cents if self.chart[code]["type"] == "income" else cents

    def commit_month(self) -> str:
        return self.commit_months.pop(0)

    def record(self, number: int, label: str, outcome: str, detail: str) -> None:
        self.shapes.append((number, label, outcome, str(detail)[:120]))

    def count(self, start: str, end: str) -> int:
        total, cursor = 0, None
        while True:
            query = {"start": start, "end": end, "limit": 200, **({"cursor": cursor} if cursor else {})}
            body = self.demo.get("/api/v2/finance/transactions?" + urllib.parse.urlencode(query))
            total += len(body["rows"])
            cursor = body.get("next")
            if not cursor:
                return total

    def total_rows(self) -> int:
        return self.count("2000-01-01", "2100-12-31")

    def precondition(self) -> None:
        busy = self.total_rows()
        if busy:
            raise SystemExit(f"the demo has {busy} transactions: start a fresh demo, the checks model every month")

    def send(self, label: str, text: str, mapping: dict[str, Any], *, basis: str = "cash",
             source_name: str | None = None, dry: bool = False) -> dict[str, Any]:
        """Writes the sheet to the temp folder, then sends it through the import action."""
        self.files += 1
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:50]
        (self.workdir / f"{self.files:03d}-{slug}.csv").write_text(text, encoding="utf-8", newline="")
        payload: dict[str, Any] = {"csv": text, "basis": basis, "mapping": mapping}
        if source_name is not None:
            payload["source_name"] = source_name
        return self.demo.act("import_raw_totals", payload, dry=dry)

    def refuse(self, check: Check, label: str, text: str, mapping: dict[str, Any], code: str, reason: str,
               **kwargs: Any) -> bool:
        """A sheet that must be refused: its dry run and its commit both say why, and the commit posts nothing."""
        before = self.total_rows()
        dry = self.send(label, text, mapping, dry=True, **kwargs)
        ok = check.expect(dry.get("status") == "failed" and dry.get("code") == code and reason in (dry.get("reason") or ""),
                          f"{label}: expected {code} naming '{reason}', got {dry.get('status')}/{dry.get('code')}: "
                          f"{dry.get('reason') or dry.get('body')}")
        if ok:
            real = self.send(label, text, mapping, **kwargs)
            check.expect(real.get("status") == "failed" and real.get("code") == code,
                         f"{label}: the commit gave {real.get('status')}/{real.get('code')}")
            check.expect(self.total_rows() == before, f"{label}: a refused sheet posted rows")
        self.record(check.number, label, "refused" if ok else "UNEXPECTED", dry.get("reason") or dry.get("status"))
        return ok

    def accepts(self, check: Check, label: str, text: str, mapping: dict[str, Any], rows: int, **kwargs: Any) -> bool:
        """A sheet within the limits: its dry run commits every row."""
        dry = self.send(label, text, mapping, dry=True, **kwargs)
        ok = check.expect(dry.get("status") == "committed" and dry.get("impact", {}).get("rows") == rows,
                          f"{label}: dry run gave {dry.get('status')}: {dry.get('reason') or dry.get('body')}")
        self.record(check.number, label, "accepted (dry)" if ok else "UNEXPECTED",
                    f"{rows} rows" if ok else dry.get("reason") or dry.get("status"))
        return ok

    def _add(self, month: str, totals: dict[str, int], sign: int) -> None:
        bucket = self.net.setdefault(month, {})
        for code, cents in totals.items():
            bucket[code] = bucket.get(code, 0) + sign * cents

    def _bill(self, month: str, totals: dict[str, int], sign: int) -> None:
        bucket = self.bills.setdefault(month, {})
        for code, cents in totals.items():
            bucket[code] = bucket.get(code, 0) + sign * cents

    def void_month(self, check: Check, number: str, month: str) -> str:
        """The month a voided bill's reversal is dated in, read back from the books in the bill's own month: a void posts
        on the bill's date (spec §4.1), or in the open period when that date is closed."""
        window = urllib.parse.urlencode({"start": f"{month}-01", "end": _eom(month), "limit": 200})
        for row in self.demo.get(f"/api/v2/finance/transactions?{window}")["rows"]:
            is_reversal = row.get("source") == "bill" and str(row.get("memo", "")).startswith(f"Void bill {number}")
            if is_reversal and row.get("amount") == -5000:
                return str(row["date"])[:7]
        check.expect(False, f"no reversal for bill {number} in {month}")
        return ""

    def _by_month(self, entries: list[Entry], mapping: dict[str, Any]) -> dict[str, dict[str, int]]:
        keyed = {_fold(name): self.code_of(str(ref)) for name, ref in mapping.items()}
        out: dict[str, dict[str, int]] = {}
        for month, name, cents in entries:
            totals = out.setdefault(month, {})
            code = keyed[_fold(name)]
            totals[code] = totals.get(code, 0) + cents
        return out

    # Checks against the reports.

    def pnl(self, month: str, basis: str) -> dict[str, Any]:
        return self.demo.get(f"/api/v2/finance/pnl?start={month}-01&end={_eom(month)}&period=month&basis={basis}")

    def expected(self, month: str, basis: str) -> dict[str, int]:
        """What the month's P&L should show on a basis: the sheets, plus approved bills on the accrual basis only."""
        totals = dict(self.net.get(month, {}))
        if basis == "accrual":
            for code, cents in self.bills.get(month, {}).items():
                totals[code] = totals.get(code, 0) + cents
        return totals

    def check_month(self, check: Check, label: str, month: str) -> None:
        for basis in ("cash", "accrual"):
            # The P&L lists income and expense accounts only; the bank balance is checked on the balance sheet instead.
            want = {self.chart[code]["id"]: value for code, value in self.expected(month, basis).items()
                    if value and self.chart[code]["type"] in ("income", "expense")}
            body = self.pnl(month, basis)
            rows = {row["account_id"]: row for row in body["rows"]}
            got = {aid: row["total"] for aid, row in rows.items() if row["total"]}
            allowed = set(want)
            for aid in want:
                parent = rows.get(aid, {}).get("parent_id") or ""
                while parent and parent not in allowed:      # parent rollups show their children's totals
                    allowed.add(parent)
                    parent = rows.get(parent, {}).get("parent_id") or ""
            wrong = [self.code_by_id.get(aid, aid) for aid in set(want) | set(got)
                     if got.get(aid, 0) != want.get(aid, 0) or (aid not in allowed and got.get(aid, 0))]
            check.expect(not wrong, f"{label}: {month} {basis} P&L differs on accounts {sorted(wrong)}")
            standing = self.live.get(month)
            shown = [item for item in body.get("imported", []) if item.get("month") == month]
            if standing is None or standing == basis:
                check.expect(not shown, f"{label}: {month} {basis} view labels an import it does not state: {shown}")
            else:
                check.expect(shown == [{"month": month, "basis": standing}],
                             f"{label}: {month} {basis} view should label the {standing}-basis import, got {shown}")

    def check_balance(self, check: Check, label: str, month: str) -> None:
        body = self.demo.get(f"/api/v2/finance/balance-sheet?as_of={_eom(month)}")
        got = next((a["balance"] for a in body["assets"] if a["code"] == "1000"), None)
        want = sum(totals.get("1000", 0) for m, totals in self.net.items() if m <= month)
        check.expect(got == want, f"{label}: bank balance at {_eom(month)} is {got}, the sheets say {want}")

    def verify(self, check: Check, label: str, month: str) -> None:
        self.check_month(check, label, month)
        self.check_balance(check, label, month)

    def post(self, check: Check, label: str, text: str, mapping: dict[str, Any], entries: list[Entry], *,
             basis: str = "cash", source_name: str | None = None) -> dict[str, Any] | None:
        """A sheet that must post: the dry run names each account's impact, the commit posts, and every month it
        touches matches the sheet in both P&L bases, with the right label, and in the bank balance."""
        dry = self.send(label, text, mapping, basis=basis, source_name=source_name, dry=True)
        if not check.expect(dry.get("status") == "committed",
                            f"{label}: dry run gave {dry.get('status')}: {dry.get('reason') or dry.get('body')}"):
            return None
        expected = self._by_month(entries, mapping)
        want: dict[str, int] = {}
        for totals in expected.values():
            for code, cents in totals.items():
                want[code] = want.get(code, 0) + self.natural(code, cents)
        impact = {a["code"]: a["total"] for a in dry["impact"]["accounts"] if a["total"]}
        check.expect(impact == {c: v for c, v in want.items() if v},
                     f"{label}: dry-run impact {impact} is not {want}")
        check.expect(dry["impact"]["rows"] == len(entries), f"{label}: dry run counts {dry['impact']['rows']} rows")
        real = self.send(label, text, mapping, basis=basis, source_name=source_name)
        result = real.get("result") or {}
        if not check.expect(real.get("status") == "committed" and not result.get("already_imported"),
                            f"{label}: commit gave {real.get('status')}: {real.get('reason') or real.get('body')}"):
            return real
        posted = {item["month"]: item["entry_id"] for item in result.get("months", [])}
        check.expect(sorted(posted) == sorted(expected), f"{label}: posted months {sorted(posted)}, sheet has {sorted(expected)}")
        for month, entry_id in posted.items():
            self.imports[entry_id] = (month, expected.get(month, {}))
            self.live[month] = basis
            self._add(month, expected.get(month, {}), +1)
        for month in sorted(expected):
            self.verify(check, label, month)
        self.record(check.number, label, "posts", f"{basis} basis, {len(posted)} month(s), batch {result.get('batch_id')}")
        return real

    def journal_reverse(self, check: Check, label: str, entry_id: str) -> None:
        month, totals = self.imports[entry_id]
        res = self.demo.act("journal", {"reverses": entry_id})
        check.expect(res.get("status") == "committed", f"{label}: reversing {month} gave {res.get('status')}: {res.get('reason')}")
        if res.get("status") == "committed":
            self._add(month, totals, -1)
            self.live.pop(month, None)
            self.verify(check, label, month)

    # The checks.

    def check1(self) -> None:
        c, y = self.checks[1], self.year - 2
        refusals = [
            ("xlsx bytes", "PK\x03\x04\x14\x00\x06\x00[Content_Types].xml\n", "the header is month"),
            ("JSON text", f'{{"rows": [{{"month": "{y}-01"}}]}}\n', "the header is month"),
            ("tab-separated", f"month\tline\tamount\n{y}-01\tSoftware subscriptions\t100\n", "the header is month"),
            ("semicolon-separated", f"month;line;amount\n{y}-01;Software subscriptions;100\n", "the header is month"),
            ("blank text", "", "csv is required"),
            ("header only", "month,line,amount\n", "the CSV has no rows"),
            ("duplicate column", f"month,month,line,amount\n{y}-01,{y}-01,Software subscriptions,100\n", "the header is month"),
            ("missing amount column", f"month,line\n{y}-01,Software subscriptions\n", "the header is month"),
            ("notes column", f"month,line,amount,notes\n{y}-01,Software subscriptions,100,vendor\n", "the header is month"),
        ]
        for label, text, reason in refusals:
            self.refuse(c, label, text, STANDARD, "invalid", reason)
        m = self.commit_month()
        text = _csv([[m, "Café subscriptions", "12345"]], header=("Month", "Line", "Amount"), eol="\r\n")
        self.post(c, "CRLF, capitalised header, accented line", text, {"Café subscriptions": "6100"},
                  [(m, "Café subscriptions", 12345)])
        bom = self.commit_month()
        self.post(c, "a leading byte order mark is stripped", "\ufeff" + _csv([[bom, "Software subscriptions", "100"]]),
                  STANDARD, [(bom, "Software subscriptions", 100)])

    def check2(self) -> None:
        c, y = self.checks[2], self.year - 2
        self.refuse(c, "wide: months across the top", f"Line,{y}-01,{y}-02\nSales revenue,4100,4200\n", STANDARD,
                    "invalid", "the header is month")
        self.refuse(c, "two month forms in one file",
                    f"month,line,amount\n{y}-01,Sales revenue,4100\nJan {y},Software subscriptions,100\n", STANDARD,
                    "invalid", "month is YYYY-MM")
        for form in (f"Jan {y}", f"{y}-01-31", f"1/31/{y}", "January", f"{y}-1", f"{y}-13", f"{y}-01-01", f"{y}/01"):
            self.refuse(c, f"one month form: {form}", f"month,line,amount\n{form},Sales revenue,4100\n", STANDARD,
                        "invalid", "month is YYYY-MM")
        m = self.commit_month()
        entries: list[Entry] = [(m, "Sales revenue", 410000), (m, "Software subscriptions", 48000),
                                (m, "Office rent", 250000), (m, "Cash at bank", 300000)]
        cells = [[m, "Sales revenue", "410000", "leadership"], [m, "Software subscriptions", "48000", ""],
                 [m, "Office rent", "250000", "leadership"], [m, "Cash at bank", "300000", ""]]
        self.post(c, "long layout with a team column", _csv(cells, header=(*HEAD, "team")), STANDARD, entries)

    def check3(self) -> None:
        c, y = self.checks[3], self.year - 2

        def one(amount: str) -> str:
            return f"month,line,amount\n{y}-01,Software subscriptions,{amount}\n"

        for amount in ['"$1,234.56"', '"(1,234.56)"', "$1,234.56", "1234.56", "-1234.56", "0.00", "1e5", "=1+1",
                       "+100", '"12,345"', "1 234", "1234 USD", "--1234", "", "-"]:
            reason = "has 4 fields" if amount == "$1,234.56" else "amount is whole cents"
            self.refuse(c, f"amount {amount}", one(amount), STANDARD, "invalid", reason)
        for amount in ("١٢٣٤", "１２３４"):      # Arabic-Indic and fullwidth digits: ASCII digits only
            self.refuse(c, f"digits {amount}", one(amount), STANDARD, "invalid", "amount is whole cents")
        m = self.commit_month()
        text = (f"month,line,amount\n{m}, Office rent , 1234\n{m},Software subscriptions,-1234\n"
                f"{m},Sales revenue,000001234\n")
        self.post(c, "spaces, a negative figure and leading zeros", text, STANDARD,
                  [(m, "Office rent", 1234), (m, "Software subscriptions", -1234), (m, "Sales revenue", 1234)])

    def check4(self) -> None:
        c, y = self.checks[4], self.year - 2
        m = self.commit_month()
        lines = ["amount,line,month,team", "", f"5000,Office rent,{m},leadership", f"700,Office rent,{m},",
                 f"0,Sales revenue,{m},", f"48000,  SOFTWARE SUBSCRIPTIONS ,{m},", "", f"410000,Sales revenue,{m},leadership", ""]
        self.post(c, "column order, blank rows, a zero row, two teams on one line", "\n".join(lines), STANDARD,
                  [(m, "Office rent", 5000), (m, "Office rent", 700), (m, "  SOFTWARE SUBSCRIPTIONS ", 48000),
                   (m, "Sales revenue", 410000)])
        refusals = [
            ("section header row", f"month,line,amount\n,Revenue,\n{y}-01,Sales revenue,100\n", "month is YYYY-MM"),
            ("title row above the header", f"Acme history {y}\nmonth,line,amount\n{y}-01,Sales revenue,100\n",
             "the header is month"),
            ("total row, unmapped", f"month,line,amount\n{y}-01,Sales revenue,100\n{y}-01,Total expenses,100\n",
             "no account is mapped for: Total expenses"),
            ("trailing empty column", f"month,line,amount,\n{y}-01,Sales revenue,100,\n", "the header is month"),
            ("extra field on a row", f"month,line,amount\n{y}-01,Sales revenue,100,extra\n", "has 4 fields"),
            ("same line twice in a month", f"month,line,amount\n{y}-01,Sales revenue,100\n{y}-01,sales revenue,50\n",
             "repeats"),
            ("unknown team", f"month,line,amount,team\n{y}-01,Sales revenue,100,Nope\n",
             "no team on the org chart for: Nope"),
            ("team name in another case", f"month,line,amount,team\n{y}-01,Sales revenue,100,Leadership\n",
             "no team on the org chart for: Leadership"),
            ("every row zero", f"month,line,amount\n{y}-01,Sales revenue,0\n", "every row is 0"),
        ]
        for label, text, reason in refusals:
            self.refuse(c, label, text, STANDARD, "invalid", reason)
        spaced = self.commit_month()
        self.post(c, "a double space inside a line name is one space", _csv([[spaced, "Software  subscriptions", "100"]]),
                  STANDARD, [(spaced, "Software  subscriptions", 100)])
        dollars = self.commit_month()
        warned = self.send("every amount ends in 00", _csv([[dollars, "Sales revenue", "290000"],
                                                             [dollars, "Office rent", "300000"]]),
                           {"Sales revenue": "4000", "Office rent": "6900"}, dry=True)
        c.expect(warned.get("status") == "committed" and any("00" in w for w in warned.get("impact", {}).get("warnings", [])),
                 f"a sheet of whole dollars gave no dry-run warning: {warned.get('status')} {warned.get('impact', {}).get('warnings')}")

    def _retired_account(self) -> None:
        if "6977" not in self.chart:
            created = self.demo.act("account_create", {"code": "6977", "name": "Acme retired line", "type": "expense"})
            self.checks[5].expect(created.get("status") == "committed", f"account 6977 create gave {created.get('status')}")
            self.load_chart()
        if self.chart["6977"].get("active", True):
            retired = self.demo.act("account_update", {"account_id": self.chart["6977"]["id"], "active": False})
            self.checks[5].expect(retired.get("status") == "committed", f"retiring 6977 gave {retired.get('status')}")
            self.load_chart()

    def check5(self) -> None:
        c, y = self.checks[5], self.year - 2
        m = self.commit_month()
        mapping = {"Sales revenue": "4000", " Software subscriptions ": self.chart["6100"]["id"],
                   "Office rent": "6900", "Cash at bank": "1000", "Unused line": "6100"}
        entries: list[Entry] = [(m, "Sales revenue", 100000), (m, "Software subscriptions", 20000),
                                (m, "Office rent", 30000), (m, "Cash at bank", 40000)]
        self.post(c, "mapping by code and id, unused mapping lines", _csv(_cells(entries)), mapping, entries)
        self._retired_account()
        probe = f"month,line,amount\n{y}-01,Sales revenue,100\n"
        for label, bad, reason in [
            ("unknown account code", {**STANDARD, "Sales revenue": "9999"}, "no account 9999 for Sales revenue"),
            ("opening balance account", {**STANDARD, "Sales revenue": "3900"}, "maps to the opening balance account"),
            ("retired account", {**STANDARD, "Sales revenue": "6977"}, "account 6977 is inactive"),
            ("two names fold to one", {**STANDARD, "sales REVENUE": "4000"}, "two mapping names fold to"),
            ("blank account", {**STANDARD, "Sales revenue": "  "}, "is an account id or code"),
            ("number instead of code", {**STANDARD, "Sales revenue": 4000}, "is an account id or code"),
            ("line not in the mapping", {k: v for k, v in STANDARD.items() if k != "Cash at bank"},
             "no account is mapped for"),
        ]:
            sheet = probe if label != "line not in the mapping" else _csv(_cells([(f"{y}-01", "Cash at bank", 5)]))
            self.refuse(c, label, sheet, bad, "invalid" if label != "unknown account code" else "not_found", reason)
        too_many = {f"Line {k:03d}": "6100" for k in range(501)}
        self.refuse(c, "501 mapping lines", _csv([[f"{y}-01", "Line 000", "1"]]), too_many, "invalid",
                    "mapping holds 1 to 500 lines")

    def check6(self) -> None:
        c = self.checks[6]
        m = self.commit_month()
        mapping = {**STANDARD, "Software subs": "6100"}
        entries: list[Entry] = [(m, "Sales revenue", 410000), (m, "Software subscriptions", 48000),
                                (m, "Software subs", 7000), (m, "Office rent", 250000), (m, "Cash at bank", 300000)]
        text = _csv(_cells(entries))
        first = self.post(c, "cash history, two lines on one account", text, mapping, entries)
        if first is not None:
            self.standard = (m, text, mapping, first["result"]["batch_id"])
        m2 = self.commit_month()
        accrual: list[Entry] = [(m2, "Sales revenue", 1000), (m2, "Office rent", 200)]
        self.post(c, "accrual history", _csv(_cells(accrual)), STANDARD, accrual, basis="accrual")

    def check7(self) -> None:
        c = self.checks[7]
        m, text, mapping, batch = self.standard
        again = self.send("same file again", text, mapping)
        c.expect(again.get("status") == "committed" and again["result"].get("already_imported")
                 and again["result"].get("batch_id") == batch, f"same file again gave {again.get('status')}: {again.get('result')}")
        lines = text.splitlines()
        reordered = "\n".join([lines[0], *reversed(lines[1:])]) + "\n"
        again = self.send("reordered, another source name", reordered, mapping, source_name="Acme history, copy")
        c.expect(again.get("status") == "committed" and again["result"].get("already_imported")
                 and again["result"].get("batch_id") == batch, f"reordered copy gave {again.get('status')}: {again.get('result')}")
        self.record(7, "same content in another order, another source name", "already imported", batch)
        self.verify(c, "after the repeats", m)
        self.refuse(c, "same months on accrual basis", text, mapping, "conflict", "already has raw totals",
                    basis="accrual")
        mc = self.commit_month()
        closed_text = _csv([[mc, "Sales revenue", "9000"]])
        self.act_check(c, "close_period", {"month": mc}, "close the month")
        self.refuse(c, "closed month", closed_text, {"Sales revenue": "4000"}, "period_closed", "is closed")
        self.act_check(c, "reopen_period", {"month": mc}, "reopen the month")
        self.post(c, "reopened month posts", closed_text, {"Sales revenue": "4000"}, [(mc, "Sales revenue", 9000)])
        pa, pb = self.commit_month(), self.commit_month()
        two: list[Entry] = [(pa, "Sales revenue", 5000), (pa, "Software subscriptions", 700),
                            (pb, "Sales revenue", 6000), (pb, "Office rent", 800)]
        two_text = _csv(_cells(two))
        first = self.post(c, "two-month file", two_text, STANDARD, two)
        if first is not None:
            entries = {item["month"]: item["entry_id"] for item in first["result"]["months"]}
            self.journal_reverse(c, "undo one month", entries[pa])
            self.refuse(c, "one month undone, the other still stands", two_text, STANDARD, "conflict",
                        "already has raw totals")
            self.journal_reverse(c, "undo the other month", entries[pb])
            self.post(c, "file posts again once every month is undone", two_text, STANDARD, two)
        po = self.commit_month()
        bill_id = self.bill(c, po, 5000)
        overlap = _csv([[po, "Software subscriptions", "4800"], [po, "Sales revenue", "9000"]])
        self.refuse(c, "month with an approved bill", overlap, STANDARD, "conflict", "already has transactions")
        if bill_id:
            voided = self.demo.act("bill_void", {"bill_id": bill_id})
            c.expect(voided.get("status") == "committed", f"voiding the bill gave {voided.get('status')}: {voided.get('reason')}")
            if voided.get("status") == "committed":
                # The reversal is dated on the bill's own date, in the bill's month, which takes it back out.
                day = self.void_month(c, self.numbers[bill_id], po)
                if day:
                    c.expect(day == po, f"the reversal of the voided bill is dated {day}, not {po}")
                    self._bill(day, {"6100": 5000}, -1)
                    self.verify(c, "after the bill is voided", day)
                    self.record(7, "voided bill: reversal month", f"dated {day}", f"the bill's own month {po}")
                self.verify(c, "after the bill is voided", po)
        self.post(c, "month freed of the bill", overlap, STANDARD,
                  [(po, "Software subscriptions", 4800), (po, "Sales revenue", 9000)])
        pr = self.commit_month()
        rows_r: list[Entry] = [(pr, "Software subscriptions", 4800), (pr, "Sales revenue", 9000)]
        raw_r = self.post(c, "import, then a bill into the same month", _csv(_cells(rows_r)), STANDARD, rows_r)
        bill_r = self.bill(c, pr, 5000, approve=False)
        if bill_r and raw_r is not None:
            res = self.demo.act("bill_approve", {"bill_id": bill_r})
            refused = c.expect(res.get("status") == "failed" and res.get("code") == "conflict",
                               f"a bill approved into a month holding raw totals gave {res.get('status')}: the expense "
                               "is counted by both the import and the bill")
            self.record(7, "bill approved into a month holding raw totals", "refused" if refused else "DOUBLE COUNT",
                        res.get("reason") or "posted")
            # Once that month's raw entry is reversed, the same bill posts into it, once (spec §4.1).
            raw_entry = next(item["entry_id"] for item in raw_r["result"]["months"] if item["month"] == pr)
            self.journal_reverse(c, "undo the month's raw totals", raw_entry)
            res = self.demo.act("bill_approve", {"bill_id": bill_r})
            c.expect(res.get("status") == "committed",
                     f"after the raw totals were undone, approving the bill gave {res.get('status')}: {res.get('reason')}")
            if res.get("status") == "committed":
                self._bill(pr, {"6100": 5000}, +1)
                self.verify(c, "after the bill posted", pr)
                self.record(7, "bill posts once its raw month is undone", "posted", f"month {pr}")

    def act_check(self, check: Check, kind: str, payload: dict[str, Any], what: str) -> None:
        res = self.demo.act(kind, payload)
        check.expect(res.get("status") == "committed", f"{what} gave {res.get('status')}: {res.get('reason')}")

    def bill(self, check: Check, month: str, cents: int, approve: bool = True) -> str:
        if not self.vendor_id:
            contact = self.demo.act("contact_create", {"name": f"Acme vendor {uuid.uuid4().hex[:6]}", "kind": "vendor"})
            self.vendor_id = str((contact.get("result") or {}).get("contact_id") or "")
            check.expect(bool(self.vendor_id), f"creating a vendor gave {contact.get('status')}: {contact.get('reason')}")
        number = uuid.uuid4().hex[:8]
        created = self.demo.act("bill_create", {"vendor_id": self.vendor_id, "number": number,
                                                "bill_date": f"{month}-10", "terms_days": 30,
                                                "lines": [{"account_id": self.chart["6100"]["id"], "amount": cents}]})
        bill_id = str((created.get("result") or {}).get("bill_id") or "")
        self.numbers[bill_id] = number
        if not check.expect(bool(bill_id), f"bill_create gave {created.get('status')}: {created.get('reason')}"):
            return ""
        if approve:
            res = self.demo.act("bill_approve", {"bill_id": bill_id})
            check.expect(res.get("status") == "committed", f"bill_approve gave {res.get('status')}: {res.get('reason')}")
            if res.get("status") == "committed":
                self._bill(month, {"6100": cents}, +1)
                self.verify(check, "after the bill", month)
        return bill_id

    def no_wrong_total(self, check: Check, label: str, month: str, pairs: list[tuple[str, str]]) -> None:
        """Rows whose sum is past what the ledger holds. The dry run must refuse, and the commit must refuse too with
        nothing posted: a commit that posts such a sum would book a figure the books cannot hold."""
        rows = _csv([[month, name, amount] for name, amount in pairs])
        mapping = {name: "6100" for name, _ in pairs}
        before = self.total_rows()
        dry = self.send(label, rows, mapping, dry=True)
        dry_ok = check.expect(dry.get("status") == "failed" and dry.get("code") == "invalid",
                              f"{label}: the dry run gave {dry.get('status')}/{dry.get('code')}: {dry.get('reason')}")
        real = self.send(label, rows, mapping)
        posted = self.total_rows() - before
        commit_ok = check.expect(real.get("status") == "failed" and posted == 0,
                                 f"{label}: the commit gave {real.get('status')} and posted {posted} rows")
        self.record(8, label, "refused" if dry_ok and commit_ok else "UNEXPECTED",
                    dry.get("reason") or real.get("reason") or dry.get("status") or "")

    def check8(self) -> None:
        c = self.checks[8]
        months = self.refusal_months
        counts = [417] * 10 + [415] * 2
        big: list[Entry] = [(months[i], f"Vendor {k:03d}", 100) for i, n in enumerate(counts) for k in range(n)]
        mapping = {f"Vendor {k:03d}": "6100" for k in range(417)}
        self.accepts(c, "5000 rows across 12 months", _csv(_cells(big)), mapping, 5000)
        self.refuse(c, "5001 rows", _csv(_cells(big)) + f"{months[0]},Vendor 417,100\n", mapping, "invalid",
                    "more than 5000 rows")
        names = {f"Line {k:03d}": "6100" for k in range(500)}
        month_rows: list[Entry] = [(months[0], f"Line {k:03d}", 100) for k in range(500)]
        self.refuse(c, "500 lines in one month (501 with the offset)", _csv(_cells(month_rows)), names, "invalid",
                    "has more than 500 lines")
        self.accepts(c, "499 lines in one month", _csv(_cells(month_rows[:499])),
                     {k: v for k, v in names.items() if int(k[5:]) < 499}, 499)
        long_name = "x" * 201
        self.refuse(c, "line name of 201 characters", f"month,line,amount\n{months[0]},{long_name},100\n",
                    {long_name: "6100"}, "invalid", "line is a name of 1 to 200 characters")
        self.accepts(c, "line name of 200 characters", f"month,line,amount\n{months[0]},{'x' * 200},100\n",
                     {"x" * 200: "6100"}, 1)
        self.refuse(c, "team of 81 characters", f"month,line,amount,team\n{months[0]},Software subscriptions,100,{'y' * 81}\n",
                    STANDARD, "invalid", "team is 80 characters at most")
        one = f"month,line,amount\n{months[0]},Software subscriptions,100\n"
        self.refuse(c, "source name of 121 characters", one, STANDARD, "invalid",
                    "source_name is longer than 120 characters", source_name="s" * 121)
        self.accepts(c, "source name of 120 characters", one, STANDARD, 1, source_name="s" * 120)
        self.refuse(c, "file over 512 KB", "month,line,amount\n" + f"{months[0]},Software subscriptions,1\n" * 20000,
                    STANDARD, "invalid", "longer than")
        self.no_wrong_total(c, "twelve 18-digit amounts, net past 64 bits", months[0],
                            [(f"Big {k:02d}", "999999999999999999") for k in range(12)])
        self.no_wrong_total(c, "twenty 18-digit amounts, net zero, gross past 64 bits", months[0],
                            [(f"Plus {k:02d}", "999999999999999999") for k in range(10)]
                            + [(f"Minus {k:02d}", "-999999999999999999") for k in range(10)])
        res = self.demo.act("import_raw_totals", {"csv": "month,line,amount\n" + "x" * 2_280_000, "basis": "cash",
                                                  "mapping": STANDARD}, dry=True)
        c.expect(res.get("http") == 413, f"a 2.28 MB request gave HTTP {res.get('http')}, not 413")
        self.record(8, "2.28 MB request body", "refused" if res.get("http") == 413 else "UNEXPECTED", f"HTTP {res.get('http')}")
        formula = '=HYPERLINK("https://acme.example/ledger","ledger") <script>alert("acme")</script>'
        mf = self.commit_month()
        self.post(c, "formula and script text in the source name", _csv([[mf, "Software subscriptions", "900"]]),
                  STANDARD, [(mf, "Software subscriptions", 900)], source_name=formula)
        shown = self.demo.get(f"/api/v2/finance/transactions?start={mf}-01&end={_eom(mf)}&limit=200")
        c.expect(_contains(shown, formula), "the source name does not come back as the same text in the transactions")

    def check9(self) -> None:
        c = self.checks[9]
        anonymous = urllib.request.build_opener(NoRedirect)
        before = self.total_rows()
        payload = {"kind": "import_raw_totals", "dry_run": True,
                   "payload": {"csv": f"month,line,amount\n{self.year - 2}-01,Software subscriptions,100\n", "basis": "cash",
                               "mapping": STANDARD}}
        status, _ = self.demo.request("POST", "/api/v2/finance/actions", payload, {"Idempotency-Key": uuid.uuid4().hex},
                                      opener=anonymous)
        c.expect(status >= 400, f"an anonymous import got HTTP {status}")
        status, _ = self.demo.request("GET", f"/api/v2/finance/pnl?start={self.year - 2}-01-01&end={self.year - 2}-01-31",
                                      opener=anonymous)
        c.expect(status >= 400, f"an anonymous P&L read got HTTP {status}")
        status, _ = self.demo.request("POST", "/api/v2/finance/actions", payload,
                                      {"Idempotency-Key": uuid.uuid4().hex, "Origin": "http://evil.example.invalid"})
        c.expect(status >= 400, f"a cross-origin import got HTTP {status}")
        c.expect(self.total_rows() == before, "a refused call posted rows")
        self.record(9, "anonymous and cross-origin calls", "refused", "see check 9 failures if any")
        self.record(9, "bookkeeper and bot grants", "not over HTTP",
                    "the demo cannot mint non-owner identities; backend/tests/test_finance_raw_totals.py covers them")

    def run(self) -> bool:
        self.load_chart()
        self.precondition()
        steps = [self.check1, self.check2, self.check3, self.check4, self.check5, self.check6, self.check7,
                 self.check8, self.check9]
        for number, step in enumerate(steps, 1):
            try:
                step()
            except Exception as error:   # a crash fails its check; the others still run
                self.checks[number].failures.append(f"raised {type(error).__name__}: {error}")
        return all(not check.failures for check in self.checks.values())

    def report(self) -> None:
        for number, check in self.checks.items():
            verdict = "PASS" if not check.failures else "FAIL"
            print(f"check {number} {verdict}: {check.title} ({check.passed} expectations met)")
            for failure in check.failures:
                print(f"    - {failure}")
        print("\nshapes:")
        for number, label, outcome, detail in self.shapes:
            print(f"  [{number}] {outcome:<16} {label}: {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rehearse the Finance history import on a local demo.")
    parser.add_argument("--base", default="http://127.0.0.1:18861", help="a loopback demo URL")
    parser.add_argument("--token-file", required=True, type=Path, help="the demo's local-owner.token")
    parser.add_argument("--year", type=int, default=2023, help="the year whose months the checks use")
    parser.add_argument("--keep", action="store_true", help="keep the generated sheets in the temp folder")
    args = parser.parse_args(argv)
    if not LOOPBACK.fullmatch(args.base):
        parser.error("--base must be a loopback demo: http://127.0.0.1:PORT or http://localhost:PORT")
    if not 2000 <= args.year <= 2100:
        parser.error("--year must be between 2000 and 2100")
    demo = Demo(args.base, args.token_file.read_text().strip())
    demo.sign_in()
    workdir = Path(tempfile.mkdtemp(prefix="finance-rehearsal-"))
    rehearsal = Rehearsal(demo, args.year, workdir)
    try:
        ok = rehearsal.run()
        rehearsal.report()
    finally:
        if args.keep:
            print(f"\nsheets kept in {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    passed = sum(1 for check in rehearsal.checks.values() if not check.failures)
    print(f"\n{passed} of {len(rehearsal.checks)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
