#!/usr/bin/env python3
"""A rehearsal of one full Tico Finance month on a local stack: the demo (Acme) and a fake Mercury, both on this
computer. It is NOT the proof of Finance: real Mercury credentials are not available yet, so this finds what would
break on the real run and nothing more.

It drives the running system only through its HTTP API (the routes behind the hub_finance_* tools in clients/hubtools.py)
and the fake's control endpoints. It writes nothing to any database and calls no internal function. Nothing here
reaches api.mercury.com, QuickBooks, Xero, Stripe or HQ: the demo refuses outbound connections, and the fake answers
on 127.0.0.1 only.

  python3 scripts/finance_month_rehearsal.py                 starts its own demo (18811) and fake Mercury (18812)
  python3 scripts/finance_month_rehearsal.py --keep          leaves them running afterwards (their PIDs are printed)

Exit status: 0 only when every check passed and no step was reported as not possible locally; 1 when a check failed;
3 when every check passed but some step cannot be run here (listed in the report). Everything the script starts is
stopped by the PID it started, and its demo folder is a new temporary folder that is removed on exit.
"""
import argparse
import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_PORT = 18811
FAKE_PORT = 18812
DEMO_HOST = "127.0.0.1"
OWNER_EMAIL = "ana@acme.example"
APPROVER = "ben@acme.example"
FAKE_TOKEN = "fake-token-rehearsal-0001"
SIGNING_SECRET = "rehearsal-mercury-signing-secret"
HOOK_ID = "mercury-rehearsal"
NOT_POSSIBLE = "not possible locally"


class Ledger:
    """Every check, in order. A check is one assertion with what it saw."""

    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def check(self, step: str, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append({"step": step, "name": name, "status": "pass" if ok else "fail", "detail": str(detail)[:400]})
        print(("PASS " if ok else "FAIL ") + f"[{step}] {name}" + (f" - {detail}" if detail else ""), flush=True)
        return bool(ok)

    def gap(self, step: str, name: str, reason: str) -> None:
        self.rows.append({"step": step, "name": name, "status": "not_possible", "detail": reason})
        print(f"GAP  [{step}] {name} - {NOT_POSSIBLE}: {reason}", flush=True)

    def failed(self) -> int:
        return sum(1 for r in self.rows if r["status"] == "fail")

    def gaps(self) -> int:
        return sum(1 for r in self.rows if r["status"] == "not_possible")


class Api:
    """The demo's HTTP API as the owner (the only identity the demo can sign in with over HTTP)."""

    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/") + "/api/v2/"
        self.token = token
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, path: str, body: object = None, query: dict | None = None,
                key: str | None = None) -> tuple[int, object]:
        url = self.base + path.lstrip("/")
        if query:
            url += "?" + "&".join(f"{k}={urllib.request.quote(str(v), safe='')}" for k, v in query.items()
                                  if v is not None)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        if method != "GET":
            headers["Idempotency-Key"] = key or ("rh-" + uuid.uuid4().hex)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as answer:
                status, raw = answer.status, answer.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        try:
            parsed: object = json.loads(raw or b"null")
        except ValueError:
            parsed = raw.decode("utf-8", "replace")
        self.calls.append({"method": method, "path": path, "status": status})
        return status, parsed

    def get(self, path: str, **query: object) -> object:
        status, out = self.request("GET", path, query=query)
        if status != 200:
            raise StepError(f"GET {path} -> {status} {str(out)[:200]}")
        return out

    def act(self, kind: str, payload: dict, key: str | None = None, dry_run: bool = False) -> dict:
        """POST /finance/actions. Returns the answer whatever its status; the caller checks status and code."""
        status, out = self.request("POST", "finance/actions",
                                   {"kind": kind, "payload": payload, "dry_run": dry_run}, key=key)
        if not isinstance(out, dict):
            out = {"raw": out}
        out["_http"] = status
        return out


class StepError(Exception):
    """A step that cannot go on: the failure is recorded, and the next step runs."""


class Fake:
    """The fake Mercury's control endpoints (scripts/fake_mercury.py)."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None, auth: bool = False) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + FAKE_TOKEN
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as answer:
                return answer.status, json.loads(answer.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"null")
            except ValueError:
                return e.code, raw.decode("utf-8", "replace")

    def mercury(self, path: str) -> object:
        """A Mercury-shaped read through the fake's API (the same paths Tico reads)."""
        status, out = self.call("GET", "/api/v1" + path, auth=True)
        if status != 200:
            raise StepError(f"fake GET {path} -> {status}")
        return out


class Stack:
    """The demo and the fake, started by this script (or not at all with --base/--fake). Stopped by PID."""

    def __init__(self, demo_port: int, fake_port: int, data_dir: Path, log_dir: Path) -> None:
        self.demo_port, self.fake_port = demo_port, fake_port
        self.data_dir, self.log_dir = data_dir, log_dir
        self.procs: list[tuple[str, subprocess.Popen]] = []

    def _port_free(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((DEMO_HOST, port)) != 0

    def _wait(self, url: str, seconds: float) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                urllib.request.urlopen(url, timeout=2)
                return True
            except urllib.error.HTTPError:
                return True
            except OSError:
                time.sleep(0.5)
        return False

    def start(self) -> None:
        for port in (self.fake_port, self.demo_port):
            if not self._port_free(port):
                raise SystemExit(f"port {port} already answers on this computer; this rehearsal starts its own stack "
                                 "there and will not use something it did not start")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        fake = subprocess.Popen(["python3", str(ROOT / "scripts" / "fake_mercury.py"), "--port", str(self.fake_port)],
                                cwd=ROOT, stdout=open(self.log_dir / "fake.log", "w"), stderr=subprocess.STDOUT)
        self.procs.append(("fake Mercury", fake))
        if not self._wait(f"http://{DEMO_HOST}:{self.fake_port}/_fake/calls", 30):
            raise SystemExit("the fake Mercury did not start")
        env = dict(os.environ, TICO_MERCURY_BASE=f"http://{DEMO_HOST}:{self.fake_port}/api/v1")
        demo = subprocess.Popen(["python3", "-m", "backend.demo", "--port", str(self.demo_port),
                                 "--data-dir", str(self.data_dir)],
                                cwd=ROOT, env=env, stdout=open(self.log_dir / "demo.log", "w"),
                                stderr=subprocess.STDOUT)
        self.procs.append(("demo", demo))
        if not self._wait(f"http://{DEMO_HOST}:{self.demo_port}/", 240):
            raise SystemExit("the demo did not start; see " + str(self.log_dir / "demo.log"))

    def stop(self) -> None:
        for name, proc in reversed(self.procs):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
            print(f"stopped {name} (pid {proc.pid}, exit {proc.returncode})", flush=True)
        self.procs = []


def sign_mercury(body: bytes, secret: str = SIGNING_SECRET) -> str:
    """Mercury-Signature: t=<unix>,v1=<hex HMAC-SHA256 over '<t>.<body>'> (backend/trigger_presets.py `_stripe`)."""
    stamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def today() -> date:
    return date.today()


class Context:
    def __init__(self, api: Api, fake: Fake, ledger: Ledger, demo_base: str) -> None:
        self.api, self.fake, self.ledger = api, fake, ledger
        self.demo_base = demo_base
        self.state: dict[str, object] = {}

    def check(self, step: str, name: str, ok: bool, detail: str = "") -> bool:
        return self.ledger.check(step, name, ok, detail)

    def gap(self, step: str, name: str, reason: str) -> None:
        self.ledger.gap(step, name, reason)


# ------------------------------------------------------------------------------------------------ the month
PAST_MONTH = "2026-10"                 # the month this rehearsal closes (today is in November)
EXPECTED_RULES = ("floor:budget-cutoff-freeze", "rule:new-recipient", "rule:payment-over-1000",
                  "rule:bot-card-single-use-over-200", "rule:bot-card-standing", "rule:bot-books-over-5000",
                  "rule:bot-new-account", "rule:close-reopen-owner")
BOOKKEEPER_RIGHTS = {"finance.book", "finance.ap", "finance.ar", "finance.pay", "finance.cards"}
CARD_VENDORS = ["FIGMA", "SLACK", "OPENROUTER", "ADOBE", "NOTION", "ZOOM", "GITHUB", "LINEAR", "CANVA", "DROPBOX",
                "ZAPIER", "AWS"]


def october_seed() -> list[dict]:
    """Forty invented transactions in October: 28 on the checking account, 12 on the IO credit card. Every name starts
    with REH so the rehearsal can count its own rows."""
    rows = []
    for i in range(28):
        customer = i % 7 == 0
        amount = round((150.0 + i * 7.25) * (1 if customer else -1), 2)
        rows.append({"amount": amount, "counterparty": f"REH BANK {i:02d} {'CUSTOMER' if customer else 'VENDOR'}",
                     "date": f"{PAST_MONTH}-{1 + i:02d}", "kind": "bank"})
    for i in range(12):
        rows.append({"amount": -round(19.99 + i * 3.5, 2), "counterparty": f"REH CARD {i:02d} {CARD_VENDORS[i]}",
                     "date": f"{PAST_MONTH}-{2 + i:02d}", "kind": "credit card"})
    return rows


def wait_for(fn, seconds: float, every: float = 2.0):
    """Poll `fn` until it returns a truthy value or the time is up; returns the last value."""
    deadline = time.time() + seconds
    value = fn()
    while not value and time.time() < deadline:
        time.sleep(every)
        value = fn()
    return value


def committed(out: dict) -> bool:
    return out.get("_http") == 200 and out.get("status") == "committed"


def feed_rows(api: Api, q: str) -> list[dict]:
    out = api.get("finance/transactions", source="feed", q=q, limit=200)
    return [r for r in (out.get("rows") or []) if r.get("kind") == "feed"]


def run_step1(ctx: Context) -> None:
    step, api, fake = "1 setup", ctx.api, ctx.fake
    # Forty rows go into the fake before Tico connects, so the first sync is a real backfill of them.
    seeded = [fake.call("POST", "/_fake/txn", row)[0] for row in october_seed()]
    ctx.check(step, "fake Mercury holds the seeded transactions before connecting", seeded.count(200) == 40,
              f"{seeded.count(200)} of 40 accepted")
    # A second token whose approver is its own creator: Tico must say payments are off and why, and must not fall back.
    _, neg = api.request("POST", "credentials", {"name": "Mercury (approver check)", "kind": "token",
                                                  "secret": "fake-token-approver-check"})
    st, out = api.request("POST", "finance/connections", {
        "provider": "mercury", "credential_id": neg["id"], "name": "Mercury (approver check)",
        "approver_email": OWNER_EMAIL, "token_creator_email": OWNER_EMAIL})
    result = out.get("result") or {} if isinstance(out, dict) else {}
    ctx.check(step, "a token whose creator is also the approver is refused for payments",
              st == 200 and result.get("payments_enabled") is False
              and "approver" in str(result.get("approval_note") or ""),
              f"HTTP {st}, payments_enabled={result.get('payments_enabled')}, note={result.get('approval_note')}")
    neg_cid = str(result.get("connection_id") or "")
    if neg_cid:
        dis = api.act("connection_disconnect", {"connection_id": neg_cid})
        ctx.check(step, "the approver check connection is disconnected again", committed(dis),
                  f"{dis.get('status')} {dis.get('code')} {dis.get('reason')}")
    # The real connection: an approver other than the token's creator, so payments go through Mercury's queue.
    _, good = api.request("POST", "credentials", {"name": "Mercury (rehearsal)", "kind": "token",
                                                   "secret": FAKE_TOKEN})
    st, out = api.request("POST", "finance/connections", {
        "provider": "mercury", "credential_id": good["id"], "name": "Mercury (rehearsal)",
        "approver_email": APPROVER, "token_creator_email": OWNER_EMAIL})
    result = out.get("result") or {} if isinstance(out, dict) else {}
    ctx.state["connection_id"] = str(result.get("connection_id") or "")
    ctx.check(step, "Mercury connects with a fake token and an approver who is not its creator",
              st == 200 and result.get("payments_enabled") is True, f"HTTP {st}, {str(out)[:160]}")
    sources = api.get("finance/sources")
    groups = {g["group"]: g["rows"] for g in sources["groups"]}
    banks = {r["name"]: r for r in groups.get("banks", [])}
    ctx.check(step, "the two Mercury bank accounts are in the books", {"Acme Checking", "Reserve"} <= set(banks),
              ", ".join(sorted(banks)))
    ctx.check(step, "the Mercury IO card program is in the books", any("Mercury IO" in str(r.get("name")) for r in
                                                                       groups.get("cards", [])))
    pay = [r for r in groups.get("payments", []) if r.get("id") == ctx.state["connection_id"]]
    ctx.check(step, "payments run through Mercury's approval queue on that connection",
              bool(pay) and (pay[0].get("payments") or {}).get("enabled") is True
              and (pay[0].get("payments") or {}).get("mode") == "approval_queue",
              json.dumps(pay[0].get("payments") if pay else None)[:200])
    # The Bookkeeper: it appears off the org chart, with the rights the spec gives it by default.
    bots = {b["slug"]: b for b in api.get("bots")}
    ctx.check(step, "the Bookkeeper appears as an active built-in bot", bots.get("bookkeeper", {}).get("state") == "active")
    access = api.get("finance/access")
    book = next((t for t in access["teammates"] if t["teammate"] == "bot:bookkeeper"), {})
    ctx.check(step, "the Bookkeeper is off the org chart (no team, no manager)",
              book and book.get("team") is None and book.get("reports_to") is None,
              f"team={book.get('team')}, reports_to={book.get('reports_to')}")
    ctx.check(step, "the Bookkeeper holds book, ap, ar, pay and cards",
              set(book.get("rights") or []) == BOOKKEEPER_RIGHTS, ", ".join(sorted(book.get("rights") or [])))
    # The owner grants the Bookkeeper Everything, as the spec asks at setup (its view is not granted by default).
    grant = api.act("view_grant", {"subject_kind": "teammate", "subject": "bot:bookkeeper", "what": "everything",
                                   "whose": "company"})
    ctx.check(step, "the owner grants the Bookkeeper Everything to see the books", committed(grant),
              f"{grant.get('status')} {grant.get('code')} {grant.get('reason')}"[:200])
    rules = {r["id"]: r for r in api.get("finance/rules")["rules"]}
    missing = [rid for rid in EXPECTED_RULES if rid not in rules or not rules[rid].get("active")]
    ctx.check(step, "the default rules for a new company are present and on", not missing,
              f"missing: {missing}" if missing else f"{len(EXPECTED_RULES)} of spec 6.4")


def run_step2(ctx: Context) -> None:
    step, api, fake = "2 bank", ctx.api, ctx.fake
    rows = wait_for(lambda: len(feed_rows(api, "REH")) >= 40 and feed_rows(api, "REH"), 120)
    ctx.check(step, "backfill brings all 40 seeded transactions into the feed", len(rows) == 40, f"{len(rows)} rows")
    checking = next(a for a in fake.mercury("/accounts")["accounts"] if a["id"] == "acct-chk")
    banks = {r["name"]: r for g in api.get("finance/sources")["groups"] if g["group"] == "banks" for r in g["rows"]}
    ctx.check(step, "checking balance loads and matches Mercury",
              banks.get("Acme Checking", {}).get("balance") == round(checking["currentBalance"] * 100),
              f"Tico {banks.get('Acme Checking', {}).get('balance')} vs Mercury {round(checking['currentBalance'] * 100)}")
    # Three more transactions arrive as signed webhook deliveries, one of them twice (a replay must not post twice).
    made = api.request("POST", "triggers", {"id": HOOK_ID, "name": "Mercury rehearsal"}, key="rh-trigger")
    made_trigger = (made[1].get("trigger") or {}) if isinstance(made[1], dict) else {}  # answers are {"trigger": {...}}
    url = str(made_trigger.get("url") or "")
    secret = url.rsplit("/", 1)[-1]
    ctx.check(step, "the Mercury webhook address is made", made[0] == 200 and bool(secret), f"HTTP {made[0]}")
    signed_st, signed = api.request("POST", f"triggers/{HOOK_ID}/signing",
                                    {"secret": SIGNING_SECRET, "preset": "mercury"}, key="rh-sign")
    ctx.check(step, "the webhook checks Mercury's signature with the signing secret",
              signed_st == 200 and isinstance(signed, dict) and (signed.get("trigger") or {}).get("preset") == "mercury",
              f"HTTP {signed_st}")
    ctx.state["hook_path"] = f"hooks/{HOOK_ID}/{secret}"
    accepted = []
    for i in range(3):
        added = fake.call("POST", "/_fake/txn", {"amount": -(40.0 + i), "counterparty": f"REH HOOK {i}",
                                                 "date": f"{PAST_MONTH}-28", "kind": "bank"})[1]
        txn = added["transaction"]
        accepted.append(deliver_mercury(ctx, txn, f"evt-rehearsal-{i}"))
        if i == 0:
            accepted.append(deliver_mercury(ctx, txn, "evt-rehearsal-0"))   # Mercury's retry of the same event
    statuses = [s[0] for s in accepted]
    # Accepted is 202; the replay of the first delivery is answered 200 as a duplicate and posts nothing.
    ctx.check(step, "signed deliveries are accepted (202), and the replay is a duplicate (200)",
              statuses == [202, 200, 202, 202], f"statuses {statuses}")
    bad = api_post_raw(ctx, f"hooks/{HOOK_ID}/{secret}", json.dumps({"id": "evt-forged"}).encode(),
                       {"Mercury-Signature": f"t={int(time.time())},v1=deadbeef", "Content-Type": "application/json"})
    ctx.check(step, "a webhook with a wrong signature is refused (401, signature)", bad[0] == 401, f"HTTP {bad[0]}")
    total = wait_for(lambda: len(feed_rows(api, "REH")) == 43 and 43, 120)
    ctx.check(step, "the webhook rows and the polled rows post once each (43 in all)", total == 43,
              f"{len(feed_rows(api, 'REH'))} rows")


def api_post_raw(ctx: Context, path: str, body: bytes, headers: dict) -> tuple[int, bytes]:
    """A raw POST to a public address (a Mercury webhook): no owner token, its own signature."""
    req = urllib.request.Request(ctx.api.base + path, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def deliver_mercury(ctx: Context, txn: dict, event_id: str) -> tuple[int, bytes]:
    """A signed Mercury webhook delivery for one transaction, to this script's trigger address (the same event id
    twice is a retry)."""
    body = json.dumps({"id": event_id, "resourceType": "transaction", "resourceId": txn["id"],
                       "operationType": "create", "resourceVersion": 1, "occurredAt": "2026-10-28T12:00:00Z",
                       "changedPaths": sorted(txn), "mergePatch": txn, "previousValues": None}).encode()
    headers = {"Mercury-Signature": sign_mercury(body), "Content-Type": "application/json"}
    return api_post_raw(ctx, str(ctx.state["hook_path"]), body, headers)


def run_step3(ctx: Context) -> None:
    step, api = "3 categorize", ctx.api
    accounts = {a["name"]: a["id"] for a in api.get("finance/chart")["accounts"] if a.get("name")}
    ai, software, revenue = accounts["AI services"], accounts["Software"], accounts["Revenue"]
    rule = api.act("rule_create", {
        "kind": "posting", "name": "AI vendors", "words": "OpenRouter and Anthropic charges are AI services.",
        "when": {"counterparty": {"matches": "OPENROUTER|ANTHROPIC"}},
        "outcome": {"outcome": "post", "account_id": ai},
        "examples": [{"input": {"counterparty": "OPENROUTER INC"}, "expect": "post", "account_id": ai}]})
    ctx.check(step, "a posting rule for AI vendors is saved (its example passes)", committed(rule),
              f"{rule.get('status')} {rule.get('reason')}"[:200])
    # A new card charge after the rule arrives as a signed webhook: the posting rule books it to AI services by itself.
    made_st, made = ctx.fake.call("POST", "/_fake/txn", {"amount": -88.80, "counterparty": "REH CARD 99 OPENROUTER",
                                                          "date": f"{PAST_MONTH}-29", "kind": "credit card"})
    ctx.check(step, "the new AI vendor card charge is made on Mercury", made_st == 200, f"HTTP {made_st}")
    hook_st, _ = deliver_mercury(ctx, made["transaction"], "evt-rehearsal-card-99")
    ctx.check(step, "its webhook delivery is accepted (202)", hook_st == 202, f"HTTP {hook_st}")
    rule_row = wait_for(lambda: next(iter(feed_rows(api, "REH CARD 99 OPENROUTER")), None), 120)
    ctx.check(step, "the posting rule books the new AI vendor row as posted",
              rule_row is not None and rule_row.get("status") == "posted" and rule_row.get("entry_id") is not None,
              f"row {json.dumps(rule_row)[:300] if rule_row else 'none'}")
    ai_lines = api.get(f"finance/lines/{ai}", start=f"{PAST_MONTH}-01", end=f"{PAST_MONTH}-31")
    ctx.check(step, "the charge sits on the AI services line", "REH CARD 99" in json.dumps(ai_lines),
              f"AI services lines hold {len(json.dumps(ai_lines))} bytes of detail")
    # Everything else is categorized through the tool, as the Bookkeeper's queue asks. Vendors go to Software, money in
    # to Revenue. Some rows get no team tag, which is allowed and shows as Unattributed.
    items = api.get("finance/inbox", kind="uncategorized", limit=200).get("items", [])
    done, errors = 0, []
    for n, item in enumerate(items):
        amount = int(item.get("amount") or 0)
        account = revenue if amount > 0 else software
        tags = {"team": "leadership"} if n % 3 == 0 else {}
        out = api.act("categorize", {"feed_id": item["feed_id"], "account_id": account, "tags": tags}, key=None)
        if committed(out):
            done += 1
        else:
            errors.append(f"{out.get('code')}:{out.get('reason') or out.get('status')}")
    ctx.check(step, "every uncategorized row is categorized through the tool", done == len(items) and not errors,
              f"{done} of {len(items)}; {errors[:3]}")
    left = api.get("finance/inbox", kind="uncategorized", limit=200).get("items", [])
    ctx.check(step, "no uncategorized rows remain in the Bookkeeper's queue", not left, f"{len(left)} left")
    expenses = api.get("finance/expenses", by="team", start=f"{PAST_MONTH}-01", end=f"{PAST_MONTH}-31")
    ctx.check(step, "unattributed spend is allowed and shows as Unattributed",
              any("Unattributed" in json.dumps(row) for row in (expenses.get("rows") or [])),
              f"groups: {[r.get('label') or r.get('name') for r in (expenses.get('rows') or [])][:6]}")
    ctx.gap(step, "categorized as the Bookkeeper bot",
            "the demo has no bot credential: a bot's HTTP calls need an agent or runner token, and the Bookkeeper runs "
            "on a computer the demo does not have. Every write here is made as the owner (human:ana)")


# ------------------------------------------------------------------------------------------------ steps 4 to 11
FAKE_CHECKING = "acct-chk"
DONE_PAYMENT = {"sent", "submitted", "paid", "settled", "completed"}
REFUSED_PAYMENT = {"failed", "rejected", "cancelled", "canceled", "declined"}


def walk(obj: object):
    """Every dict inside a JSON answer, at any depth, outer ones first."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def find_row(obj: object, **want: object) -> dict | None:
    """The first dict anywhere in an answer whose fields have these values."""
    return next((d for d in walk(obj) if all(d.get(k) == v for k, v in want.items())), None)


def by_field(obj: object, field: str, value: str, key: str = "id") -> str:
    row = find_row(obj, **{field: value})
    return str(row[key]) if row and row.get(key) else ""


def status_of(obj: object, ident: str) -> str | None:
    row = find_row(obj, id=ident)
    return str(row["status"]) if row and row.get("status") is not None else None


def approval_of(out: dict) -> str:
    for d in walk(out):
        if isinstance(d.get("approval"), dict) and d["approval"].get("id"):
            return str(d["approval"]["id"])
        if d.get("approval_id"):
            return str(d["approval_id"])
    return ""


def describe(out: dict) -> str:
    return f"HTTP {out.get('_http')} {out.get('status')} {out.get('code') or ''} {str(out.get('reason') or '')[:160]}"


def account_ids(api: Api) -> dict[str, str]:
    return {a["name"]: a["id"] for a in api.get("finance/chart")["accounts"] if a.get("name")}


def bank_id(api: Api, name: str) -> str:
    for group in api.get("finance/sources")["groups"]:
        if group["group"] == "banks":
            for row in group["rows"]:
                if row.get("name") == name:
                    return str(row["id"])
    raise StepError(f"no bank account named {name!r} in the sources")


def all_feed(api: Api) -> list[dict]:
    rows: list[dict] = []
    cursor = None
    for _ in range(50):
        out = api.get("finance/transactions", source="feed", limit=200, cursor=cursor)
        rows += [r for r in out.get("rows") or [] if r.get("kind") == "feed"]
        cursor = out.get("next")
        if not cursor:
            break
    return rows


def total(obj: object, *names: str) -> float | None:
    for d in walk(obj):
        for name in names:
            value = d.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
    return None


def until(ctx: Context, fn, seconds: float):
    """Polls `fn` and asks the sync to look for new Mercury data between polls (its own pace is slower)."""
    deadline = time.time() + seconds
    value = fn()
    while not value and time.time() < deadline:
        ctx.api.act("connection_sync", {"connection_id": ctx.state["connection_id"]})
        time.sleep(5)
        value = fn()
    return value


def owner_decides(ctx: Context, step: str, out: dict, what: str) -> None:
    """When Tico asks for the owner's approval, the owner gives it (over HTTP the owner is the only signed-in person)."""
    aid = approval_of(out)
    if not aid:
        return
    st, answer = ctx.api.request("POST", f"approvals/{aid}", {"decision": "approved", "note": "rehearsal"})
    ctx.check(step, f"the owner approves {what}", st == 200, f"HTTP {st} {str(answer)[:160]}")


def payment_status(ctx: Context, needle: str) -> str | None:
    rows = ctx.api.get("finance/payments", limit=200)
    row = next((d for d in walk(rows) if "status" in d and needle in json.dumps(d)), None)
    return str(row["status"]) if row else None


def wait_status(ctx: Context, needle: str, done: set[str], seconds: float) -> str | None:
    deadline = time.time() + seconds
    status = payment_status(ctx, needle)
    while status not in done and time.time() < deadline:
        ctx.api.act("connection_sync", {"connection_id": ctx.state["connection_id"]})
        time.sleep(5)
        status = payment_status(ctx, needle)
    return status


def fake_request(ctx: Context, needle: str, seconds: float = 120) -> dict | None:
    """The Mercury send-money request on the fake whose body mentions `needle`."""
    def look() -> dict | None:
        answer = ctx.fake.mercury(f"/request-send-money?accountId={FAKE_CHECKING}")
        return next((r for r in answer.get("requests", []) if needle in json.dumps(r)), None)
    return wait_for(look, seconds)


def pick_user(users: object) -> str:
    rows = [d for d in walk(users) if d.get("id")]
    chosen = next((d for d in rows if "ana" in json.dumps(d).lower()), rows[0] if rows else None)
    if not chosen:
        raise StepError("no Mercury user is in the books to hold a card")
    return str(chosen["id"])


def card_program_id(api: Api) -> str:
    for group in api.get("finance/sources")["groups"]:
        if group["group"] == "cards":
            for row in group["rows"]:
                if "Mercury IO" in str(row.get("name")):
                    return str(row["id"])
    raise StepError("no Mercury IO card program in the sources")


def id_containing(obj: object, text: str) -> str:
    """The id of the first dict in an answer that mentions `text` anywhere in its values."""
    row = next((d for d in walk(obj) if d.get("id") and text in json.dumps(d)), None)
    return str(row["id"]) if row else ""


def amount(value: object) -> float | None:
    """A money figure: a number, or a dict holding one under amount, total or value."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        for key in ("amount", "total", "value", "minor"):
            if isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool):
                return value[key]
    return None


def budget_rows(api: Api) -> list[dict]:
    """The REH budgets as of October's end, with the figures (amount, actual, held, remaining) from their `state`."""
    rows = api.get("finance/budgets", at=f"{PAST_MONTH}-31", limit=200)
    return [{**d, **d["state"]} for d in walk(rows)
            if str(d.get("name") or "").startswith("REH ") and isinstance(d.get("state"), dict)]


def run_step4(ctx: Context) -> None:
    step, api = "4 bills and payments", ctx.api
    acct, chk = account_ids(api), bank_id(api, "Acme Checking")
    made = api.act("contact_create", {"name": "REH Vendor Hosting Co", "kind": "vendor", "emails": ["ap@vendor.example"],
                                       "default_account_id": acct["Software"], "is_1099": True, "tax_id": "12-3456789"})
    ctx.check(step, "a vendor is made with its 1099 flag and tax id", committed(made), describe(made))
    vendor = by_field(api.get("finance/contacts", q="REH Vendor Hosting Co"), "name", "REH Vendor Hosting Co")
    rec = api.act("recipient_add", {"name": "REH Vendor Hosting Co", "method": "ach", "contact_id": vendor,
                                    "money_account_id": chk, "email": "ap@vendor.example",
                                    "account_number": "000123456789", "routing_number": "021000021"})
    ctx.check(step, "the vendor's bank details are saved as a recipient", committed(rec), describe(rec))
    recipients = api.get("finance/recipients")
    recipient = by_field(recipients, "name", "REH Vendor Hosting Co")
    ctx.state["recipient_id"] = recipient
    ctx.check(step, "the recipient list shows no full account number", bool(recipient)
              and "000123456789" not in json.dumps(recipients), f"recipient {recipient or 'none'}")
    bill = api.act("bill_create", {"vendor_id": vendor, "number": "REH-BILL-1", "bill_date": f"{PAST_MONTH}-20",
                                   "terms_days": 30, "memo": "REH hosting, October",
                                   "lines": [{"account_id": acct["Software"], "amount": 250000,
                                              "description": "REH hosting, October"}]})
    ctx.check(step, "a $2,500.00 bill is entered from the vendor", committed(bill), describe(bill))
    bill_id = by_field(api.get("finance/bills", q="REH-BILL-1"), "number", "REH-BILL-1")
    ctx.check(step, "the bill can be found by its number", bool(bill_id), f"id {bill_id or 'none'}")
    if not bill_id:
        return
    approved = api.act("bill_approve", {"bill_id": bill_id})
    ctx.check(step, "the bill is approved (expense debit, accounts payable credit)", committed(approved),
              describe(approved))
    sched = api.act("bill_schedule", {"bill_id": bill_id, "pay_on": today().isoformat(), "money_account_id": chk,
                                      "recipient_id": recipient, "memo": "REH-BILL-1"})
    ctx.check(step, "the bill is scheduled for payment from Acme Checking", committed(sched), describe(sched))
    owner_decides(ctx, step, sched, "the scheduled $2,500.00 payment (over $1,000)")
    req = fake_request(ctx, "REH-BILL-1")
    ctx.check(step, "the payment reaches Mercury's approval queue (pending on the fake)",
              bool(req) and req.get("status") == "pendingApproval",
              f"request {json.dumps(req)[:200] if req else 'none'}")
    if req:
        approved_fake = ctx.fake.call("POST", f"/_fake/approve/{req['requestId']}")
        ctx.check(step, "Mercury's approver approves it on the fake", approved_fake[0] == 200,
                  f"HTTP {approved_fake[0]}")
    status = wait_status(ctx, bill_id, DONE_PAYMENT, 180)
    bill_status = status_of(api.get(f"finance/bills/{bill_id}"), bill_id)
    ctx.check(step, "the approved payment is sent and the bill is paid", status in DONE_PAYMENT
              and bill_status in ("paid", "settled"), f"payment {status}, bill {bill_status}")
    pay = api.act("payment_create", {"recipient_id": recipient, "amount": 30000, "money_account_id": chk,
                                     "memo": "REH-REJECT", "account_id": acct["Software"],
                                     "mode": "approval_queue"})
    ctx.check(step, "a $300.00 payment is queued through the approval queue", committed(pay), describe(pay))
    owner_decides(ctx, step, pay, "the $300.00 payment to the recipient")
    req2 = fake_request(ctx, "REH-REJECT")
    if req2:
        rejected = ctx.fake.call("POST", f"/_fake/reject/{req2['requestId']}")
        ctx.check(step, "Mercury's approver rejects the payment on the fake", rejected[0] == 200,
                  f"HTTP {rejected[0]}")
    status2 = wait_status(ctx, "REH-REJECT", REFUSED_PAYMENT, 120)
    ctx.check(step, "the rejected payment shows as refused in Tico", status2 in REFUSED_PAYMENT,
              f"payment status {status2}")
    ctx.gap(step, "Mercury refuses an approval made by the token's creator",
            "the fake approves as user-ben whatever token made the connection; the setup refusal is checked in step 1")


def run_step5(ctx: Context) -> None:
    step, api = "5 invoices", ctx.api
    acct, chk = account_ids(api), bank_id(api, "Acme Checking")
    made = api.act("contact_create", {"name": "REH Customer Co", "kind": "customer", "emails": ["ap@customer.example"]})
    ctx.check(step, "a customer is made", committed(made), describe(made))
    customer = by_field(api.get("finance/contacts", q="REH Customer Co"), "name", "REH Customer Co")
    made = api.act("invoice_create", {"customer_id": customer, "number": "REH-INV-1", "invoice_date": f"{PAST_MONTH}-15",
                                      "terms_days": 30, "memo": "REH retainer",
                                      "lines": [{"account_id": acct["Revenue"], "amount": 250000,
                                                 "description": "REH retainer, October"}]})
    ctx.check(step, "a $2,500.00 invoice is made to the customer", committed(made), describe(made))
    inv = by_field(api.get("finance/invoices", q="REH-INV-1"), "number", "REH-INV-1")
    sent = api.act("invoice_send", {"invoice_id": inv})
    ctx.check(step, "the invoice is sent (receivable debit, income credit)", committed(sent), describe(sent))
    paid = api.act("invoice_record_payment", {"invoice_id": inv, "amount": 100000, "date": f"{PAST_MONTH}-20",
                                              "money_account_id": chk})
    ctx.check(step, "a $1,000.00 part payment is recorded against it", committed(paid), describe(paid))
    part = status_of(api.get(f"finance/invoices/{inv}"), inv)
    ctx.check(step, "the invoice shows as part paid", "partial" in str(part), f"status {part}")
    late = api.act("invoice_create", {"customer_id": customer, "number": "REH-INV-2", "invoice_date": "2026-09-01",
                                      "terms_days": 30, "lines": [{"account_id": acct["Revenue"], "amount": 50000,
                                                                   "description": "REH overdue work"}]})
    ctx.check(step, "a second invoice, due in October, is made", committed(late), describe(late))
    inv2 = by_field(api.get("finance/invoices", q="REH-INV-2"), "number", "REH-INV-2")
    sent2 = api.act("invoice_send", {"invoice_id": inv2})
    ctx.check(step, "the second invoice is sent", committed(sent2), describe(sent2))
    overdue = api.get("finance/invoices", late="true", limit=200)
    ctx.check(step, "the unpaid invoice past its due date is listed as late", find_row(overdue, id=inv2) is not None,
              f"late list holds {len(json.dumps(overdue))} bytes")
    def overdue_item() -> dict | None:
        inbox = api.get("finance/inbox", kind="invoice_overdue", limit=200)
        return next((d for d in walk(inbox) if d.get("kind") == "invoice_overdue" and inv2 in json.dumps(d)), None)
    item = wait_for(overdue_item, 60)
    ctx.check(step, "the Bookkeeper's inbox holds an invoice_overdue item for the invoice (the hourly watch raises it)",
              bool(item), f"item {json.dumps(item)[:200]}" if item else "no invoice_overdue row for the invoice within 60 s")
    wo = api.act("invoice_write_off", {"invoice_id": inv2, "memo": "REH bad debt, written off"})
    ctx.check(step, "the rest of the second invoice is written off", committed(wo), describe(wo))
    written = status_of(api.get(f"finance/invoices/{inv2}"), inv2)
    ctx.check(step, "the invoice shows as written off (or paid in full by the write-off)",
              written is not None and ("writ" in written or written == "paid"), f"status {written}")


def run_step6(ctx: Context) -> None:
    step, api, fake = "6 cards", ctx.api, ctx.fake
    chk = bank_id(api, "Acme Checking")
    other = account_ids(api)["Other expenses"]
    line = api.act("budget_create", {"name": "REH Card line", "account_id": other, "period": "month", "amount": 100000000,
                                     "at_limit": "decline", "warn_pct": 80, "effective_from": f"{PAST_MONTH}-01"})
    ctx.check(step, "the Other expenses line budget the card budget splits under", committed(line), describe(line))
    line_id = by_field(api.get("finance/budgets", at=f"{PAST_MONTH}-31", limit=200), "name", "REH Card line")
    made = api.act("budget_create", {"name": "REH Card budget", "parent_id": line_id, "account_id": other,
                                     "period": "month", "amount": 50000,
                                     "at_limit": "decline", "warn_pct": 80, "teammate": "human:ana",
                                     "effective_from": f"{PAST_MONTH}-01"})
    ctx.check(step, "a $500.00 monthly budget for a card, declining at its limit", committed(made), describe(made))
    budget = by_field(api.get("finance/budgets", at=f"{PAST_MONTH}-31", limit=200), "name", "REH Card budget")
    user = pick_user(api.get("finance/mercury-users"))
    made = api.act("card_create", {"holder": "human:ana", "budget_id": budget, "amount": 50000, "kind": "person",
                                   "nickname": "REH owner card", "money_account_id": card_program_id(api),
                                   "provider_user_id": user})
    ctx.check(step, "a person card is issued on that budget", committed(made), describe(made))
    card = id_containing(api.get("finance/cards"), "REH owner card")
    fake_card = find_row(fake.mercury("/cards"), nickname="REH owner card") or {}
    limit = fake_card.get("spendLimit")
    if isinstance(limit, dict):
        limit = limit.get("amountCents")
    ctx.check(step, "Mercury's card carries the budget's $500.00 limit", limit in (500, 500.0, 50000),
              f"spendLimit on Mercury {limit}")
    fake_id = fake_card.get("id") or fake_card.get("cardId")
    s1 = fake.call("POST", "/_fake/card-spend", {"card": fake_id, "amount": 120.50, "merchant": "REH CARD SPEND 1",
                                                  "date": f"{PAST_MONTH}-22"})
    ctx.check(step, "a $120.50 card charge is made on Mercury", s1[0] == 200, f"HTTP {s1[0]}")
    row1 = until(ctx, lambda: next(iter(feed_rows(api, "REH CARD SPEND 1")), None), 180)
    ctx.check(step, "the charge comes into Tico's feed on the card", row1 is not None, f"row {bool(row1)}")
    s2 = fake.call("POST", "/_fake/card-spend", {"card": fake_id, "amount": 450.00, "merchant": "REH CARD SPEND 2",
                                                  "date": f"{PAST_MONTH}-22"})
    ctx.check(step, "a $450.00 card charge is made on Mercury (takes the card past $500.00)", s2[0] == 200,
              f"HTTP {s2[0]}")
    frozen = until(ctx, lambda: find_row(api.get("finance/cards", status="frozen"), id=card) if card else None, 240)
    ctx.check(step, "the card past its budget is frozen by the decline (budget cutoff)", frozen is not None,
              f"card {card or 'none'} frozen in Tico: {frozen is not None}")
    mercury_status = fake.mercury(f"/cards/{fake_id}").get("status") if fake_id else None
    ctx.check(step, "Mercury shows the card frozen", mercury_status == "frozen", f"status {mercury_status}")
    row2 = until(ctx, lambda: next(iter(feed_rows(api, "REH CARD SPEND 2")), None), 180)
    txn2 = s2[1].get("transaction", {}) if isinstance(s2[1], dict) else {}
    ctx.check(step, "the charge that went past the limit still posts (actuals always post)",
              (row2 or {}).get("status") == "posted",
              f"feed status {(row2 or {}).get('status')}; Mercury {txn2.get('status')} ({txn2.get('reasonForFailure')})")
    unfrozen = api.act("card_unfreeze", {"card_id": card, "reason": "rehearsal: the owner unfreezes"})
    ctx.check(step, "the owner's unfreeze is refused while the budget is over (spec 738)",
              unfrozen.get("status") == "denied" and "used up" in json.dumps(unfrozen), describe(unfrozen))
    still = fake.mercury(f"/cards/{fake_id}").get("status") if fake_id else None
    ctx.check(step, "Mercury still shows the card frozen while the budget is over", still == "frozen",
              f"status {still}")
    raised = api.act("budget_update", {"budget_id": budget, "amount": 100000, "effective_from": f"{PAST_MONTH}-01"})
    ctx.check(step, "the owner raises the card budget to $1,000.00", committed(raised), describe(raised))
    released = until(ctx, lambda: fake.mercury(f"/cards/{fake_id}").get("status") == "active" if fake_id else False, 240)
    now_status = fake.mercury(f"/cards/{fake_id}").get("status") if fake_id else None
    ctx.check(step, "a raise releases the budget-frozen card on Mercury (spec 738)", bool(released),
              f"status {now_status}")
    spent = sum(int(r.get("amount") or 0) for r in feed_rows(api, "REH CARD SPEND"))
    ctx.check(step, "the two card charges reach the feed at $570.50", spent == -57050, f"feed sum {spent}")
    rec = next((r for r in budget_rows(api) if r.get("name") == "REH Card budget"), {})
    for label, resp in (("1", s1), ("2", s2)):
        print(f"DIAG fake charge {label}: {json.dumps(resp[1], sort_keys=True)[:700]}")
    for row in feed_rows(api, "REH CARD SPEND"):
        print(f"DIAG feed row: {json.dumps(row, sort_keys=True)[:900]}")
    print(f"DIAG card: {json.dumps(find_row(api.get('finance/cards'), id=card) or {}, sort_keys=True)[:1200]}")
    print(f"DIAG budget: {json.dumps(rec, sort_keys=True)[:1500]}")
    ctx.check(step, "card spend reconciles: the budget's actual is the two charges", rec.get("actual") == 57050,
              f"budget actual {rec.get('actual')}, held {rec.get('held')}, remaining {rec.get('remaining')}")


def run_step7(ctx: Context) -> None:
    step, api = "7 budgets", ctx.api
    acct = account_ids(api)
    base = {"period": "month", "at_limit": "warn", "warn_pct": 80, "effective_from": f"{PAST_MONTH}-01"}
    made = api.act("budget_create", {"name": "REH Software", "account_id": acct["Software"], "amount": 300000, **base})
    ctx.check(step, "a $3,000.00 Software budget", committed(made), describe(made))
    parent = by_field(api.get("finance/budgets", at=f"{PAST_MONTH}-31", limit=200), "name", "REH Software")
    made = api.act("budget_create", {"name": "REH Software tools", "parent_id": parent, "account_id": acct["Software"],
                                     "team": "leadership", "amount": 100000, **base})
    ctx.check(step, "a $1,000.00 child budget under it (split by team)", committed(made), describe(made))
    rows = budget_rows(api)
    names = {str(r.get("name")) for r in rows}
    ctx.check(step, "the two REH budgets are in October's budget list",
              {"REH Software", "REH Software tools"} <= names, ", ".join(sorted(names)))
    child = next((r for r in rows if r.get("name") == "REH Software tools"), {})
    ctx.check(step, "the child budget sits under its parent", child.get("parent_id") == parent,
              f"parent_id {child.get('parent_id')}")
    bad = []
    for r in rows:
        if r.get("amount") is None:
            continue
        expect = r["amount"] - (r.get("actual") or 0) - (r.get("held") or 0) - (r.get("allocated") or 0)
        if expect != r.get("remaining"):
            bad.append((r.get("name"), r.get("remaining"), expect))
    ctx.check(step, "remaining equals amount less actuals, holds and allocations", bool(rows) and not bad,
              f"{len(rows)} budgets; mismatches {bad[:3]}")
    ai_row = find_row(api.get("finance/budgets", at=f"{PAST_MONTH}-31", limit=200), name="AI services") or {}
    ai = ai_row.get("state") if isinstance(ai_row.get("state"), dict) else {}
    ctx.check(step, "the AI services budget (the demo's own) holds the AI charge and posts it though over",
              (ai.get("actual") or 0) >= 8880, f"state {json.dumps(ai)[:200]}")
    # Each at-limit budget sits on its own new expense line; a $100.00 payment on that line is over its $50.00 budget.
    # A held payment stays queued with the budget named as its reason (payments.py _hold).
    new_lines = [api.act("account_create", {"code": code, "name": name, "type": "expense"})
                 for code, name in (("6210", "REH Travel"), ("6220", "REH Meals"))]
    ctx.check(step, "two new expense lines for the at-limit budgets", all(committed(n) for n in new_lines),
              " / ".join(describe(n) for n in new_lines))
    ids = account_ids(api)
    for label, line, at_limit, memo in (("REH Ask budget", "REH Travel", "ask", "REH-ASK"),
                                        ("REH Hold budget", "REH Meals", "hold_payment", "REH-HOLD")):
        made = api.act("budget_create", {"name": label, "account_id": ids.get(line, ""), "period": "month",
                                         "amount": 5000, "at_limit": at_limit, "warn_pct": 80,
                                         "effective_from": f"{PAST_MONTH}-01"})
        ctx.check(step, f"a $50.00 monthly budget on {line} at {at_limit}", committed(made), describe(made))
        pay = api.act("payment_create", {"recipient_id": ctx.state["recipient_id"], "amount": 10000,
                                         "money_account_id": bank_id(api, "Acme Checking"), "memo": memo,
                                         "account_id": ids.get(line, ""), "mode": "approval_queue"})
        ctx.check(step, f"a $100.00 payment on {line} is queued for approval", committed(pay), describe(pay))
        owner_decides(ctx, step, pay, f"the $100.00 payment on {line}")
        row = next((d for d in walk(api.get("finance/payments", limit=200)) if "status" in d and memo in json.dumps(d)), {})
        held_for = str(row.get("last_error") or row.get("reason") or "")
        ctx.check(step, f"at {at_limit} the over-budget payment waits (queued), not sent, and names {label}",
                  row.get("status") == "queued" and label in held_for,
                  f"status {row.get('status')}, reason {held_for[:160]}")
    ctx.gap(step, "AI services Gateway meter and the AI-services cutoff",
            "the meter is read and sent by HQ's gateway_sync.py only; the demo's finance tick runs drain_events and "
            "watch_documents, and the cutoff's key cap goes through HQ, which this stack cannot reach")


def run_step8(ctx: Context) -> None:
    step, api = "8 reconcile and close", ctx.api
    acct, chk = account_ids(api), bank_id(api, "Acme Checking")
    feed_all = all_feed(api)
    def account_of(row: dict) -> str | None:
        ma = row.get("money_account")
        return str(ma.get("id")) if isinstance(ma, dict) else ma
    feed = [r for r in feed_all if account_of(r) == chk and str(r.get("date") or "") <= f"{PAST_MONTH}-31"]
    seen = sorted({str((r.get("money_account") or {}).get("name") if isinstance(r.get("money_account"), dict)
                       else r.get("money_account")) for r in feed_all})[:6]
    ctx.check(step, "October's bank rows for Acme Checking are found", bool(feed),
              f"{len(feed)} rows; money_account values {seen}")
    statement = sum(int(r.get("amount") or 0) for r in feed)
    rec = api.act("reconcile", {"money_account_id": chk, "statement_end": f"{PAST_MONTH}-31",
                                "statement_balance": statement, "cleared": [r["id"] for r in feed],
                                "opening_balance": 0})
    ctx.check(step, "October's bank rows reconcile to the statement balance", committed(rec), describe(rec))
    closed = api.act("close_period", {"month": PAST_MONTH})
    ctx.check(step, "October closes", committed(closed), describe(closed))
    late_entry = {"date": f"{PAST_MONTH}-15", "memo": "REH late entry",
                  "lines": [{"account_id": acct["Software"], "amount": 1000},
                            {"account_id": acct["Revenue"], "amount": -1000}]}
    bad = api.act("journal", late_entry)
    ctx.check(step, "a posting into the closed month is refused (period_closed)",
              bad.get("status") != "committed" and bad.get("code") == "period_closed", describe(bad))
    pnl = api.get("finance/pnl", start=f"{PAST_MONTH}-01", end=f"{PAST_MONTH}-31")
    columns = pnl if isinstance(pnl, dict) else {}
    nets, incomes, expenses = (columns.get(k) for k in ("net", "income", "expense"))
    pnl_net = sum(nets) if isinstance(nets, list) else None
    pnl_inc = sum(incomes) if isinstance(incomes, list) else None
    pnl_exp = sum(expenses) if isinstance(expenses, list) else None
    ctx.check(step, "October's P&L net is its income less its expense",
              None not in (pnl_net, pnl_inc, pnl_exp) and pnl_net == pnl_inc - pnl_exp,
              f"net {pnl_net}, income {pnl_inc}, expense {pnl_exp}")
    ctx.gap(step, "the closed month's stored snapshot totals agree with the P&L",
            "not possible over HTTP: the close checklist and the P&L return only the snapshot's restatement count, "
            "not its stored totals (pnl_json; backend/finance/reads.py:1781)")
    bs = api.get("finance/balance-sheet", as_of=f"{PAST_MONTH}-31")
    totals = bs.get("totals") if isinstance(bs, dict) and isinstance(bs.get("totals"), dict) else {}
    assets, liab, equity = (amount(totals.get(k)) for k in ("asset", "liability", "equity"))
    ctx.check(step, "the balance sheet at October's end balances",
              None not in (assets, liab, equity) and assets == liab + equity,
              f"assets {assets}, liabilities {liab}, equity {equity}; totals {json.dumps(totals)[:200]}")
    reopened = api.act("reopen_period", {"month": PAST_MONTH})
    ctx.check(step, "the owner reopens October", committed(reopened), describe(reopened))
    fix = api.act("journal", {**late_entry, "memo": "REH correction after close"})
    ctx.check(step, "a correction posts into the reopened month", committed(fix), describe(fix))
    again = api.act("close_period", {"month": PAST_MONTH})
    ctx.check(step, "October closes again", committed(again), describe(again))
    pnl2 = api.get("finance/pnl", start=f"{PAST_MONTH}-01", end=f"{PAST_MONTH}-31")
    inc2 = sum(pnl2.get("income") or []) if isinstance(pnl2, dict) else None
    exp2 = sum(pnl2.get("expense") or []) if isinstance(pnl2, dict) else None
    moved = pnl_inc is not None and pnl_exp is not None and inc2 == pnl_inc + 1000 and exp2 == pnl_exp + 1000
    ctx.check(step, "the re-closed month's P&L shows the correction in income and in expense (no restatement flag asserted)",
              moved, f"income {pnl_inc} -> {inc2}, expense {pnl_exp} -> {exp2}")


def run_step9(ctx: Context) -> None:
    step, api = "9 tax", ctx.api
    jur = api.act("tax_jurisdiction_add", {"code": "US-CA", "name": "California sales tax", "due_day": 28})
    ctx.check(step, "a sales tax jurisdiction is added (due on the 28th after each quarter)", committed(jur),
              describe(jur))
    st, sales = api.request("GET", "finance/tax/sales-tax", query={"year": 2026})
    ctx.check(step, "the sales tax workpaper for 2026 answers", st == 200, f"HTTP {st}")
    jurisdiction = find_row(sales, code="US-CA") if st == 200 else None
    due = [v for k, v in (jurisdiction or {}).items() if "due" in k]
    ctx.check(step, "the jurisdiction carries a due date", bool(due), f"due fields {due}")
    st_csv, csv_text = api.request("GET", "finance/tax/sales-tax.csv", query={"year": 2026})
    ctx.check(step, "the sales tax CSV downloads", st_csv == 200 and isinstance(csv_text, str) and "," in csv_text,
              f"HTTP {st_csv}")
    st1, form = api.request("GET", "finance/tax/1099", query={"year": 2026})
    ctx.check(step, "the 1099 workpaper for 2026 answers", st1 == 200, f"HTTP {st1}")
    vendor_row = next((d for d in walk(form) if "REH Vendor Hosting Co" in d.values()), None) if st1 == 200 else None
    ctx.check(step, "the vendor's $2,500.00 bill payment is on its 1099 line",
              bool(vendor_row) and 250000 in vendor_row.values(),
              f"row {json.dumps(vendor_row)[:200] if vendor_row else 'none'}")
    ctx.check(step, "card charges are left out of the 1099", st1 == 200 and "OPENROUTER" not in json.dumps(form),
              "searched the 1099 for the card vendor's name")
    threshold = total(form, "threshold")
    ctx.check(step, "the 2026 threshold is $2,000", threshold in (2000, 200000), f"threshold {threshold}")
    st1c, form_csv = api.request("GET", "finance/tax/1099.csv", query={"year": 2026})
    ctx.check(step, "the 1099 CSV downloads", st1c == 200 and isinstance(form_csv, str), f"HTTP {st1c}")


def run_step10(ctx: Context) -> None:
    step, api = "10 raw import", ctx.api
    acct = account_ids(api)
    mapping = {"Revenue": acct["Revenue"], "Software": acct["Software"], "Other": acct["Other expenses"]}
    csv_text = "month,line,amount\n2026-09,Revenue,1500000\n2026-09,Software,420000\n2026-09,Other,250000\n"
    imp = api.act("import_raw_totals", {"csv": csv_text, "basis": "accrual", "mapping": mapping,
                                        "source_name": "REH invented September sheet"})
    ctx.check(step, "an invented September P&L is imported as totals", committed(imp), describe(imp))
    pnl = api.get("finance/pnl", start="2026-09-01", end="2026-09-30")
    ctx.check(step, "the imported figures are labelled Imported on the September P&L",
              "imported" in json.dumps(pnl).lower(), json.dumps(pnl)[:160])
    refused = api.act("import_raw_totals", {"csv": f"month,line,amount\n{PAST_MONTH},Revenue,100\n",
                                            "basis": "accrual", "mapping": mapping,
                                            "source_name": "REH closed-month check"})
    ctx.check(step, "an import into the closed October is refused (period_closed)",
              refused.get("status") != "committed" and refused.get("code") == "period_closed", describe(refused))


def run_step11(ctx: Context) -> None:
    step, api = "11 access", ctx.api
    access = api.get("finance/access")
    ctx.check(step, "the owner may view as another teammate", access.get("can_view_as") is True,
              f"can_view_as={access.get('can_view_as')}")
    defaults = access.get("defaults") or {}
    ctx.check(step, "managers see payroll as totals or hidden by default (spec 7.5 to confirm)",
              defaults.get("manager_payroll") in ("totals", "hidden"), json.dumps(defaults)[:200])
    st, _ = api.request("GET", "finance/pnl", query={"start": f"{PAST_MONTH}-01", "end": f"{PAST_MONTH}-31",
                                                     "view_as": "bot:bookkeeper"})
    ctx.check(step, "the owner views the P&L as the Bookkeeper", st == 200, f"HTTP {st}")
    ctx.gap(step, "the manager, member and bot identities each see their own cut",
            "over HTTP the only identity is the owner (ana); the demo mints no other tokens")
    ctx.gap(step, "a person with no rights is refused",
            "needs a second signed-in person with no rights; the demo has none")
    ctx.gap(step, "the view_as audit row is written",
            "not scripted in this run: finance.note_view_as (backend/finance_routes.py:108) writes it, and no read "
            "route for it was checked")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep", action="store_true", help="leave the demo and fake running afterwards")
    parser.add_argument("--report", default="", help="write the check ledger as JSON to this path")
    parser.add_argument("--only", default="", help="run only steps whose number is listed, e.g. 1,2,3")
    args = parser.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    ledger = Ledger()
    data_dir = Path(tempfile.mkdtemp(prefix="tico-finance-rehearsal-"))
    log_dir = data_dir.parent / (data_dir.name + "-logs")
    stack = Stack(DEMO_PORT, FAKE_PORT, data_dir / "demo", log_dir)
    try:
        stack.start()  # a failed start still reaches the finally, which stops whatever did start
        token = (data_dir / "demo" / "local-owner.token").read_text().strip()
        ctx = Context(Api(f"http://{DEMO_HOST}:{DEMO_PORT}", token), Fake(f"http://{DEMO_HOST}:{FAKE_PORT}"), ledger,
                      f"http://{DEMO_HOST}:{DEMO_PORT}")
        steps = [("1", run_step1), ("2", run_step2), ("3", run_step3), ("4", run_step4), ("5", run_step5),
                 ("6", run_step6), ("7", run_step7), ("8", run_step8), ("9", run_step9), ("10", run_step10),
                 ("11", run_step11)]
        for number, fn in steps:
            if only and number not in only:
                continue
            try:
                fn(ctx)
            except Exception as exc:  # a failed step is recorded, and the next one still runs
                ledger.check(f"{number}", "the step ran to its end", False,
                             f"{type(exc).__name__}: {str(exc)[:300]} | {traceback.format_exc().splitlines()[-2:]}")
    finally:
        if args.keep:
            for name, proc in stack.procs:
                print(f"left running: {name} (pid {proc.pid}); stop it by that PID. demo folder: {data_dir}", flush=True)
        else:
            stack.stop()
            shutil.rmtree(data_dir, ignore_errors=True)
    if args.report:
        Path(args.report).write_text(json.dumps(ledger.rows, indent=2))
    failed, gaps = ledger.failed(), ledger.gaps()
    print(f"\n{len(ledger.rows) - failed - gaps} passed, {failed} failed, {gaps} not possible locally", flush=True)
    if failed:
        return 1
    return 3 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
