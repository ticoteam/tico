#!/usr/bin/env python3
"""Rehearse Tico Finance's bot tools and rules on a local stack with simulated services.

The eight checks drive the spec's bot tools (section 7.1: the seven `hub_finance_*` tools, on the hub's MCP endpoint and
on the local stdio MCP, plus the `hub finance` CLI) against a running demo and the fake Mercury. This is a rehearsal,
not the proof: real Mercury credentials are never used, and a pass here does not show that the real bank run works.
Exit 0 only when every check passes.

Start the stack first, with a data directory you choose (the demo makes it when empty):

  TICO_MERCURY_BASE=http://127.0.0.1:18842/api/v1 python3 -m backend.demo --port 18841 --data-dir DIR
  python3 scripts/fake_mercury.py --port 18842

Then run this script with --data-dir DIR. Each run makes its own fixtures (a fresh run id in every name, idempotency
key and Mercury token), so it can run again on the same demo. The demo's SQLite file is read in read-only mode, except
one insert per person for a personal token: the API only mints a personal token for the person signed in, and a
rehearsal has no browser sign-in for ben or cara.
"""
import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
TODAY = date.today().isoformat()
# spec 5: the action kinds, as the spec lists them. Both surfaces must list each one as live.
SPEC_KINDS = (
    "categorize", "journal", "reconcile", "close_period", "reopen_period", "reconcile_undo",
    "bill_create", "bill_update", "bill_approve", "bill_schedule", "bill_mark_paid", "bill_void",
    "invoice_create", "invoice_send", "invoice_record_payment", "invoice_write_off", "invoice_void",
    "attach", "payment_create", "payment_cancel", "recipient_add",
    "card_create", "card_update_limit", "card_freeze", "card_unfreeze", "card_cancel",
    "budget_create", "budget_update", "allocation_set", "projection_set",
    "view_grant", "view_revoke", "private_lines_set", "right_grant", "right_revoke",
    "rule_create", "rule_update", "rule_disable", "rule_enable", "rule_test",
    "contact_create", "contact_update", "account_create", "account_update",
)
# spec 7.1: the seven tools. The CLI subcommand is the tool name without `hub_finance_`, with dashes.
SPEC_TOOLS = ("search", "report", "metric", "act", "action", "inbox", "card-fill")
RIGHTS = ("finance.book", "finance.ap", "finance.ar", "finance.pay", "finance.cards")
# business tables a refused or retried action must not change (counted, read-only)
BUSINESS = ("fin_entries", "fin_bills", "fin_payments", "fin_cards", "fin_contacts", "fin_recipients", "fin_budgets")
# each business list's API path, the key its rows come under and the key of its next page's cursor (None when unpaged)
LISTS = {
    "fin_entries": ("/api/v2/finance/transactions?source=entry", "rows", "next"),
    "fin_bills": ("/api/v2/finance/bills", "bills", "next_cursor"),
    "fin_payments": ("/api/v2/finance/payments", "payments", "next_cursor"),
    "fin_cards": ("/api/v2/finance/cards", "cards", "next"),
    "fin_contacts": ("/api/v2/finance/contacts", "contacts", "next_cursor"),
    "fin_recipients": ("/api/v2/finance/recipients", "recipients", None),
    "fin_budgets": ("/api/v2/finance/budgets", "budgets", None),
}
DEFAULT_RULES = ("rule:new-recipient", "rule:payment-over-1000", "rule:bot-card-standing", "rule:bot-books-over-5000",
                 "rule:bot-new-account", "rule:close-reopen-owner")
# the rule outcomes that leave an action as it is (a limit that still fits leaves a committed action committed)
DECIDED = {"committed": ("allow", "limit"), "needs_approval": ("needs_approval",), "denied": ("deny",)}
# fields that are a call's own clock, not part of the answer
VOLATILE = ("as_of",)
# the leaf parser of `hub finance <sub>`, read from clients/hubcli.py's own parser (argument dests, not help text)
PARSER_SPEC = (
    "import argparse, json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from clients import hubcli\n"
    "top = hubcli.parser()\n"
    "subs = next(a for a in top._actions if isinstance(a, argparse._SubParsersAction))\n"
    "fin = subs.choices['finance']\n"
    "leaf = next(a for a in fin._actions if isinstance(a, argparse._SubParsersAction)).choices[sys.argv[2]]\n"
    "pos = [a.dest for a in leaf._actions if not a.option_strings and not isinstance(a, argparse._SubParsersAction)]\n"
    "opt = [a.dest for a in leaf._actions if a.option_strings and a.dest != 'help']\n"
    "print(json.dumps({'positional': pos, 'options': opt}))\n"
)


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def walk(obj):
    """Every dict inside a JSON value, depth first."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def strip(obj):
    """The payload without a call's own clock fields."""
    if isinstance(obj, dict):
        return {k: strip(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [strip(v) for v in obj]
    return obj


def first_diff(a, b, path=""):
    """The first place two JSON values differ, as (path, left, right), or None."""
    if type(a) is not type(b):
        return (path, str(a)[:120], str(b)[:120])
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return (path + "." + k, str(a.get(k, "<absent>"))[:120], str(b.get(k, "<absent>"))[:120])
            found = first_diff(a[k], b[k], path + "." + k)
            if found:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return (path + ".length", len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            found = first_diff(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else (path, str(a)[:120], str(b)[:120])


def norm(is_err, payload):
    """One reply from either surface, in one shape: status, code, reason, ids, and the raw payload."""
    p = payload if isinstance(payload, dict) else {"text": str(payload)}
    err = p.get("error")
    edict = err if isinstance(err, dict) else {}
    return {
        "status": str(p.get("status") or ("error" if is_err else "ok")),
        "code": str(p.get("code") or edict.get("code") or (err if isinstance(err, str) else "") or ""),
        "reason": str(p.get("reason") or p.get("detail") or p.get("message") or edict.get("detail")
                      or edict.get("message") or p.get("text") or "")[:400],
        "action_id": str(p.get("action_id") or ""),
        "approval_id": str(p.get("approval_id") or ""),
        "approver": str(p.get("approver") or ""),
        "err": bool(is_err),
        "raw": p,
    }


def refused(res):
    """Not committed and not held for approval: a refusal, a conflict, a failure or an error."""
    return res["status"] not in ("committed", "needs_approval", "ok")


def denied(res):
    """Refused by rights, a view or a deny rule (not held for approval)."""
    text = (res["status"] + " " + res["code"] + " " + res["reason"]).lower()
    return res["status"] == "denied" or res["code"] in ("forbidden", "denied") or \
        any(w in text for w in ("denied", "forbidden", "needs finance", "not allowed", "outside your view",
                                "outside the view"))


def made(res, key):
    """An id a committed action made, from its result."""
    return str((res["raw"].get("result") or {}).get(key) or "")


def rule_list(d):
    """The finance rules the owner sees."""
    return d.http("GET", "/api/v2/finance/rules", d.owner)[1].get("rules") or []


def rules_named(d, part):
    """How many of the finance rules have `part` in their name."""
    return sum(1 for r in rule_list(d) if part in str(r.get("name", "")))


def pay_from(d):
    """The bank accounts the owner may pay from."""
    return d.http("GET", "/api/v2/finance/pay-from", d.owner)[1].get("accounts") or []


def banks(d):
    """The bank accounts the owner sees, each with the connection it is linked to (the sources list)."""
    groups = d.http("GET", "/api/v2/finance/sources", d.owner)[1].get("groups") or []
    return [b for g in groups if g.get("group") == "banks" for b in (g.get("rows") or [])]


class Demo:
    """HTTP, MCP and CLI access to the running demo, and read-only reads of its action and audit tables."""

    def __init__(self, base, fake, data_dir):
        self.base, self.fake = base.rstrip("/"), fake.rstrip("/")
        self.data = Path(data_dir)
        self.owner = (self.data / "local-owner.token").read_text().strip()
        self.db_path = self.data / "hub.sqlite"

    def http(self, method, path, token=None, body=None, key=None, base=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if key:
            headers["Idempotency-Key"] = key
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request((base or self.base) + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as answer:
                raw = answer.read()
                return answer.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw[:300].decode(errors="replace")}

    def mcp(self, token, name, args):
        """One tools/call on the hub's MCP endpoint. Returns (is_error, payload)."""
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        _, reply = self.http("POST", "/api/v2/mcp", token, body)
        if "error" in reply:
            return True, reply["error"]
        result = reply.get("result", {})
        payload = result.get("structuredContent")
        if payload is None:
            text = result["content"][0]["text"] if result.get("content") else "null"
            try:
                payload = json.loads(text)
            except ValueError:
                payload = {"text": text}
        return bool(result.get("isError")), payload

    def mcp_tools(self, token):
        _, reply = self.http("POST", "/api/v2/mcp", token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        return reply["result"]["tools"]

    def local_mcp(self, token, extra=()):
        """The local stdio MCP (clients/hubmcp.py, the one a bot turn runs): its tools/list, then `extra` requests."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("HUB_")}
        env["HUB_API_URL"], env["HUB_TOKEN"] = self.base, token
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, *extra]
        done = subprocess.run([sys.executable, "-m", "clients.hubmcp"], cwd=str(REPO), env=env,
                              input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True,
                              timeout=240)
        return [json.loads(x) for x in done.stdout.splitlines() if x.strip().startswith("{")]

    def cli(self, token, argv, bot=None):
        """One `hub` command as the token (and the bot, when given). Returns (rc, payload)."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("HUB_")}
        env["HUB_API_URL"], env["HUB_TOKEN"] = self.base, token
        if bot:
            env["HUB_BOT"] = bot
        done = subprocess.run([sys.executable, str(REPO / "clients" / "hubcli.py"), *argv], cwd=str(REPO), env=env,
                              capture_output=True, text=True, timeout=240)
        text = done.stdout.strip()
        if not text:
            return done.returncode, {"stdout": "", "stderr": done.stderr[-400:]}
        try:
            return done.returncode, json.loads(text)
        except ValueError:
            return done.returncode, {"stdout": text[-400:], "stderr": done.stderr[-400:]}

    def act(self, token, kind, payload=None, op=None, dry=False, surface="mcp"):
        """A finance action through MCP or the CLI, normalized. `op` is the operation id (the idempotency key)."""
        if surface == "cli":
            argv = ["finance", "act", kind, "--payload", json.dumps(payload or {})]
            if op:
                argv += ["--operation-id", op]
            if dry:
                argv.append("--dry-run")
            rc, out = self.cli(token, argv)
            return norm(rc != 0, out)
        args = {"kind": kind, "payload": payload or {}}
        if op:
            args["operation_id"] = op
        if dry:
            args["dry_run"] = True
        is_err, out = self.mcp(token, "hub_finance_act", args)
        return norm(is_err, out)

    def rows(self, sql, args=()):
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def scalar(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def listed(self, path, rows_key, next_key=None):
        """Every row of one finance list the owner sees, across its pages: the same reads the API gives."""
        rows, cursor = [], ""
        while True:
            query = path + ("&" if "?" in path else "?") + "limit=200"
            if cursor:
                query += "&cursor=" + quote(cursor, safe="")
            status, body = self.http("GET", query, self.owner)
            if status != 200 or not isinstance(body, dict):
                raise RuntimeError(f"{path} answered {status}")
            rows += body.get(rows_key) or []
            cursor = str(body.get(next_key) or "") if next_key else ""
            if not cursor:
                return rows

    def counts(self):
        return {t: len(self.listed(*LISTS[t])) for t in BUSINESS}

    def insert_token(self, human, label):
        """A personal token for `human`, stored as the server stores one (sha256 of the plaintext)."""
        token = "tico_pt_" + secrets.token_urlsafe(30)
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            conn.execute("INSERT INTO human_tokens(id,human,label,token_hash,created,created_by) VALUES(?,?,?,?,?,?)",
                         (uuid.uuid4().hex, human, label, sha256(token), time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "human:" + human))
            conn.commit()
        finally:
            conn.close()
        return token

    def fake_calls(self):
        _, body = self.http("GET", "/_fake/calls", base=self.fake)
        return body.get("calls", []) if isinstance(body, dict) else []


class Run:
    def __init__(self):
        self.rows = []
        self.section = "setup"

    def check(self, name, ok, detail=""):
        ok = bool(ok)
        self.rows.append({"section": self.section, "name": name, "ok": ok, "detail": str(detail)[:700]})
        line = ("PASS " if ok else "FAIL ") + f"[{self.section}] {name}"
        if not ok:
            line += f" -- {str(detail)[:500]}"
        print(line, flush=True)
        return ok

    def run_section(self, label, fn, *extra):
        self.section = label
        try:
            fn(*extra)
        except Exception as exc:  # a section that crashes is a failed check, with its reason
            self.check("the section ran to its end", False, f"{type(exc).__name__}: {exc}")


def approve(d, approval_id, decision="approved", note="rehearsal", token=None):
    return d.http("POST", f"/api/v2/approvals/{approval_id}", token or d.owner, {"decision": decision, "note": note},
                  key=f"rh-{uuid.uuid4().hex}")


def approval_state(d, approval_id):
    """pending while the approval has no decision; otherwise the decision it got (approved or declined)."""
    status, body = d.http("GET", f"/api/v2/approvals/{approval_id}", d.owner)
    if status != 200:
        return f"http {status}"
    return "pending" if body.get("decision") in (None, "") else str(body["decision"])


def cli_spec(sub):
    """The positional and option dests one CLI subcommand takes, from clients/hubcli.py's parser."""
    done = subprocess.run([sys.executable, "-c", PARSER_SPEC, str(REPO), sub], cwd=str(REPO), capture_output=True,
                          text=True, timeout=120)
    if done.returncode != 0 or not done.stdout.strip():
        raise RuntimeError(f"could not read `hub finance {sub}`'s parser: {done.stderr[-200:]}")
    spec = json.loads(done.stdout)
    return spec["positional"], set(spec["options"])


def cli_argv(sub, args, positionals):
    argv = ["finance", sub]
    for name in positionals:
        if name in args:
            v = args[name]
            argv.append(json.dumps(v) if isinstance(v, (dict, list)) else str(v))
    for k, v in args.items():
        if k in positionals:
            continue
        flag = "--" + k.replace("_", "-")
        if isinstance(v, bool):
            if v:
                argv.append(flag)
        else:
            argv += [flag, json.dumps(v) if isinstance(v, (dict, list)) else str(v)]
    return argv


def connect_mercury(d, run, rid):
    """A Mercury connection of this run's own: a token stored in the vault, the fake's ben as approver and ana as the
    token's creator, so payments are on (the demo's other connection has payments off on purpose). Returns the bank
    account's id."""
    name = f"Rehearsal Mercury {rid}"
    st, body = d.http("POST", "/api/v2/credentials", d.owner, {"name": name, "kind": "token", "secret": "rh-" + rid},
                      key=f"{rid}-cred")
    row = d.scalar("SELECT id FROM credentials WHERE name=?", (name,))
    cred = row["id"] if row else ""
    run.check("owner stores a rehearsal Mercury token in the vault", st < 300 and bool(cred), (st, str(body)[:200]))
    if not cred:
        return ""
    before = {b.get("connection_id") for b in banks(d)}
    st, body = d.http("POST", "/api/v2/finance/connections", d.owner, {
        "provider": "mercury", "credential_id": cred, "name": name,
        "approver_email": "ben@acme.example", "token_creator_email": "ana@acme.example"}, key=f"{rid}-connect")
    run.check("owner connects the fake Mercury (ben approves, ana created the token)",
              st < 300 and body.get("status") == "committed", (st, str(body)[:300]))
    payable = {a.get("id") for a in pay_from(d)}
    mine = sorted((b for b in banks(d) if b.get("connection_id") not in before and b.get("id") in payable),
                  key=lambda b: str(b.get("name")))
    return str(mine[0]["id"]) if mine else ""


def disable_stale_rules(d, run, rid):
    """Earlier rehearsal runs leave their rules live on the company, and a company rule judges every later action (a
    limit on all journals would deny this run's). Each run turns those off with the owner's audited rule_disable."""
    stale = [r for r in rule_list(d) if str(r.get("name", "")).startswith("rh ") and r.get("active")]
    failed = []
    for i, rule in enumerate(stale):
        res = d.act(d.owner, "rule_disable", {"rule_id": rule["id"]}, op=f"{rid}-stale-{i}")
        if res["status"] != "committed":
            failed.append((rule["name"], res["status"], res["reason"][:80]))
    run.check(f"setup turns off the {len(stale)} rule(s) earlier rehearsal runs left live", not failed, failed[:4])


def fixtures(d, run, rid):
    """Everything this run needs, made fresh: ben manages cara (who holds the finance book right, so her journals wait
    for ben), a finance bot (books, AP, AR, pay, cards, and everything in view) and a bot with no rights, a vendor, two
    payees and a bill."""
    fx = {"rid": rid}
    _, me = d.http("GET", "/api/v2/me", d.owner)
    fx["owner"] = str(me.get("actor", "")) if isinstance(me, dict) else ""
    run.check("the owner's identity is a human actor", fx["owner"].startswith("human:"), me)
    disable_stale_rules(d, run, rid)
    _, b = d.http("GET", "/api/v2/finance/budgets", d.owner)
    fx["ai_budget"] = next((x["id"] for x in walk(b) if x.get("name") == "AI services" and x.get("id")), "")
    _, c = d.http("GET", "/api/v2/finance/chart", d.owner)
    accounts = [x for x in walk(c) if x.get("id") and x.get("type")]
    fx["expense"] = next((x["id"] for x in accounts if x["type"] == "expense"), "")
    fx["asset"] = next((x["id"] for x in accounts if x["type"] == "asset"), "")
    fx["income"] = next((x["id"] for x in accounts if x["type"] == "income"), "")
    run.check("setup finds a budget, an expense, an asset and an income account",
              all(fx[k] for k in ("ai_budget", "expense", "asset", "income")),
              {k: fx[k] for k in ("ai_budget", "expense", "asset", "income")})
    fx["bank"] = connect_mercury(d, run, rid)
    run.check("setup has a payments-enabled bank account for this run", bool(fx["bank"]), fx["bank"])
    fx["ben_token"] = d.insert_token("ben", f"finance rehearsal {rid}")
    fx["cara_token"] = d.insert_token("cara", f"finance rehearsal {rid}")
    st, cara = d.http("POST", "/api/v2/humans/cara", d.owner, {"reports_to": "ben"}, key=f"{rid}-cara-reports-ben")
    run.check("cara reports to ben (so ben manages her)", st == 200 and cara.get("reports_to") == "ben",
              (st, str(cara)[:200]))
    fx["bots"], fx["bot_tokens"] = {}, {}
    for role in ("fin", "plain"):
        slug = f"rh{rid}-{role}"
        st, reg = d.http("POST", "/api/v2/bots/register", d.owner, {
            "slug": slug, "display_name": f"Rehearsal {role}", "description": "Finance rehearsal bot",
            "reports_to": None, "template": "", "build": False, "model": "hermes"}, key=f"{rid}-reg-{role}")
        d.http("POST", f"/api/v2/bots/{slug}/status", d.owner, {"state": "active"}, key=f"{rid}-live-{role}")
        st2, cred = d.http("POST", f"/api/v2/bots/{slug}/agent-credential", d.owner, {}, key=f"{rid}-cred-{role}")
        token = cred.get("token", "") if isinstance(cred, dict) else ""
        fx["bots"][role], fx["bot_tokens"][role] = slug, token
        run.check(f"bot {role} registers, goes active and gets a credential",
                  st < 300 and st2 < 300 and token.startswith("tico-agent-"), (st, st2, str(reg)[:200], str(cred)[:200]))
    fin_bot = "bot:" + fx["bots"]["fin"]
    for right in RIGHTS:
        res = d.act(d.owner, "right_grant", {"teammate": fin_bot, "right": right}, op=f"{rid}-grant-{right}")
        run.check(f"owner grants {right} to the finance bot", res["status"] == "committed", res["raw"])
    for subject, label in ((fin_bot, "the finance bot"), ("human:cara", "cara")):
        res = d.act(d.owner, "view_grant", {"subject_kind": "teammate", "subject": subject, "what": "everything",
                                            "whose": "company"}, op=f"{rid}-view-{subject}")
        run.check(f"owner lets {label} see everything (the Bookkeeper's view, spec 7.5)",
                  res["status"] == "committed", res["raw"])
    res = d.act(d.owner, "right_grant", {"teammate": "human:cara", "right": "finance.book"}, op=f"{rid}-cara-book")
    run.check("owner grants cara finance.book (her journals then wait for ben)", res["status"] == "committed", res["raw"])
    res = d.act(d.owner, "contact_create", {"name": f"Rehearsal Vendor {rid}", "kind": "vendor",
                                            "default_account_id": fx["expense"]}, op=f"{rid}-vendor")
    fx["vendor"] = made(res, "contact_id")
    run.check("owner makes a vendor contact", res["status"] == "committed" and fx["vendor"], res["raw"])
    fx["payee"] = add_payee(d, fx, f"Rehearsal Payee {rid}", run, "payee", "%09d" % secrets.randbelow(10**9))
    fx["fresh_payee"] = add_payee(d, fx, f"Rehearsal Fresh {rid}", run, "fresh", "%09d" % secrets.randbelow(10**9))
    res = d.act(d.owner, "bill_create", {"vendor_id": fx["vendor"], "number": f"RH-{rid}", "bill_date": TODAY,
                                         "terms_days": 30, "lines": [{"account_id": fx["expense"], "amount": 5000,
                                                                      "description": "rehearsal bill"}]},
                op=f"{rid}-bill-setup")
    fx["bill"] = made(res, "bill_id")
    run.check("owner records a bill for the vendor", res["status"] == "committed" and fx["bill"], res["raw"])
    return fx


def add_payee(d, fx, name, run, tag, account):
    """A payee with sealed bank numbers. A new payee asks for approval; the owner's own request is decided by the owner
    the server names, which the approval step below answers."""
    res = d.act(d.owner, "recipient_add", {"name": name, "method": "ach", "contact_id": fx["vendor"],
                                           "account_number": account, "routing_number": "021000021"},
                op=f"{fx['rid']}-{tag}-add")
    if res["status"] == "needs_approval" and res["approval_id"]:
        approve(d, res["approval_id"], note="setup payee")
    found = [r for r in (d.http("GET", "/api/v2/finance/recipients", d.owner)[1].get("recipients") or [])
             if r.get("name") == name]
    run.check(f"owner adds the {tag} payee (its numbers are sealed)",
              res["status"] in ("committed", "needs_approval") and bool(found), res["raw"])
    return str(found[0]["id"]) if found else ""


def check1_surface(d, run, fx):
    hub = d.mcp_tools(d.owner)
    local = d.local_mcp(d.owner)[0]["result"]["tools"]
    spec_names = sorted(f"hub_finance_{t.replace('-', '_')}" for t in SPEC_TOOLS)
    hub_names = sorted(t["name"] for t in hub if t["name"].startswith("hub_finance_"))
    local_names = sorted(t["name"] for t in local if t["name"].startswith("hub_finance_"))
    run.check("the hub's MCP and the local MCP together list the seven hub_finance_* tools (card fill is local, spec 7.1)",
              sorted(set(hub_names) | set(local_names)) == spec_names and set(local_names) == set(spec_names),
              {"hub": hub_names, "local": local_names})
    replies = d.local_mcp(d.owner, [{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "hub_finance_card_fill", "arguments": {"card_id": "crd_x", "merchant": "example.com", "amount": 100}}}])
    text = json.dumps(replies[1]) if len(replies) > 1 else ""
    run.check("card fill on the local MCP is refused as not available yet (spec 8.3)",
              "not_available" in text or "not available" in text.lower(), text[:200])
    helptext = subprocess.run([sys.executable, str(REPO / "clients" / "hubcli.py"), "finance", "--help"],
                              cwd=str(REPO), capture_output=True, text=True, timeout=120)
    shown = helptext.stdout + helptext.stderr
    run.check("the CLI `finance` command lists all seven subcommands",
              all(f" {s} " in shown or f"{{{s}" in shown or f",{s}" in shown or f" {s}," in shown for s in SPEC_TOOLS),
              shown[:300])
    schema = {t["name"]: set((t.get("inputSchema") or {}).get("properties", {}).keys()) for t in hub + local}
    for sub in SPEC_TOOLS:
        tool = "hub_finance_" + sub.replace("-", "_")
        positionals, options = cli_spec(sub)
        cli_args = set(positionals) | options
        mcp_args = schema.get(tool, set())
        run.check(f"{tool} and `hub finance {sub}` take the same arguments", cli_args == mcp_args,
                  f"MCP only: {sorted(mcp_args - cli_args)}; CLI only: {sorted(cli_args - mcp_args)}")
    _, mk = d.mcp(d.owner, "hub_finance_act", {"kind": "help"})
    _, ck = d.cli(d.owner, ["finance", "act", "help"])
    listings = {"MCP": {k["kind"]: k for k in (mk.get("kinds") or []) if isinstance(k, dict)},
                "CLI": {k["kind"]: k for k in (ck.get("kinds") or []) if isinstance(k, dict)}}
    for label, listing in listings.items():
        missing = [k for k in SPEC_KINDS if k not in listing]
        not_live = [k for k in SPEC_KINDS if k in listing and not listing[k].get("live")]
        run.check(f"{label} `act help` lists every spec 5 kind as live", not missing and not not_live,
                  f"missing {missing}; not live {not_live}; CLI keys {list(ck)[:5]}")
    failures = []
    for kind in SPEC_KINDS:
        for label, surface in (("MCP", "mcp"), ("CLI", "cli")):
            res = d.act(d.owner, kind, {}, dry=True, surface=surface)
            if res["err"] and not res["code"]:
                failures.append(f"{label} {kind}: {res['reason'][:100]}")
    run.check("every spec 5 kind is reachable on both surfaces (a dry run gets a structured answer)",
              not failures, failures[:8])


def check2_same_answer(d, run, fx):
    reads = [("search", {"kind": "bill"}), ("report", {"kind": "pnl"}), ("metric", {"name": "cash"}),
             ("inbox", {}), ("action", {"action_id": "act_missing_" + fx["rid"]})]
    for sub, args in reads:
        tool = "hub_finance_" + sub.replace("-", "_")
        _, m = d.mcp(d.owner, tool, args)
        positionals, _ = cli_spec(sub)
        _, c = d.cli(d.owner, cli_argv(sub, args, positionals))
        diff = first_diff(strip(m), strip(c))
        run.check(f"`{sub}` answers the same on MCP and CLI (spec 7.1: same tool, same answer)", diff is None,
                  f"first difference {diff}; MCP {json.dumps(m, sort_keys=True)[:160]} | "
                  f"CLI {json.dumps(c, sort_keys=True)[:160]}")
    payload = {"vendor_id": fx["vendor"], "number": "RH-DRY-" + fx["rid"], "bill_date": TODAY,
               "lines": [{"account_id": fx["expense"], "amount": 1234}]}
    m = d.act(d.owner, "bill_create", payload, dry=True)
    c = d.act(d.owner, "bill_create", payload, dry=True, surface="cli")
    run.check("a dry run of bill_create gives the same status, code and reason on both surfaces",
              all(m[k] == c[k] for k in ("status", "code", "reason")),
              {k: (m[k], c[k]) for k in ("status", "code", "reason")})
    run.check("the dry run wrote nothing (no bill with that number)",
              not d.listed(f"/api/v2/finance/bills?q=RH-DRY-{fx['rid']}", "bills", "next_cursor"))


def check3_bad_input(d, run, fx):
    rid = fx["rid"]
    before = d.counts()
    cases = [
        ("bill without a vendor", "bill_create", {"bill_date": TODAY, "lines": [{"account_id": fx["expense"], "amount": 100}]}),
        ("bill without lines", "bill_create", {"vendor_id": fx["vendor"], "bill_date": TODAY}),
        ("payee without bank details", "recipient_add", {"name": "No Details " + rid, "method": "ach"}),
        ("approval of an unknown bill", "bill_approve", {"bill_id": "bill_nope_" + rid}),
        ("payment of zero", "payment_create", {"recipient_id": fx["payee"], "amount": 0, "account_id": fx["expense"]}),
        ("payment of a negative amount", "payment_create", {"recipient_id": fx["payee"], "amount": -500,
                                                            "account_id": fx["expense"]}),
        ("payment to an unknown payee", "payment_create", {"recipient_id": "rcp_nope_" + rid, "amount": 500,
                                                           "account_id": fx["expense"]}),
        ("card with a negative limit", "card_create", {"holder": "human:ben", "budget_id": fx["ai_budget"], "amount": -1,
                                                      "provider_user_id": "user-ben"}),
    ]
    for label, kind, payload in cases:
        res = d.act(d.owner, kind, payload, op=f"{rid}-bad-" + label.replace(" ", "-"))
        run.check(f"refused, nothing written: {label}", refused(res), res["raw"])
    res = d.act(d.owner, "close_period", {"month": "2019-03"}, op=f"{rid}-close-2019-03")
    run.check("owner closes 2019-03 for the closed-month case", res["status"] == "committed", res["raw"])
    res = d.act(d.owner, "journal", {"date": "2019-03-15", "memo": "into a closed month", "lines": [
        {"account_id": fx["expense"], "amount": 700}, {"account_id": fx["asset"], "amount": -700}]},
                op=f"{rid}-closed-month")
    run.check("a journal into the closed month is refused", refused(res), res["raw"])
    res = d.act(d.owner, "reopen_period", {"month": "2019-03"}, op=f"{rid}-reopen-2019-03")
    run.check("owner reopens 2019-03 again", res["status"] == "committed", res["raw"])
    rc, out = d.cli(d.owner, ["finance", "act", "bill_create", "--payload", "{not json", "--operation-id",
                              f"{rid}-malformed"])
    run.check("malformed JSON on the CLI is refused", rc != 0 or "error" in json.dumps(out).lower(), out)
    is_err, out = d.mcp(d.owner, "hub_finance_act", {"kind": "bill_create", "payload": "{not json",
                                                     "operation_id": f"{rid}-malformed-mcp"})
    run.check("a payload that is not an object is refused on MCP", is_err or "error" in json.dumps(out).lower(),
              str(out)[:200])
    after = d.counts()
    run.check("no business row changed across the refused inputs (the close and reopen net to zero)",
              after == before, {k: (before[k], after[k]) for k in before if before[k] != after[k]})


def check4_retries(d, run, fx):
    rid = fx["rid"]
    bill = {"vendor_id": fx["vendor"], "number": f"RH-R-{rid}", "bill_date": TODAY,
            "lines": [{"account_id": fx["expense"], "amount": 2100}]}
    base = d.counts()
    first = d.act(d.owner, "bill_create", bill, op=f"{rid}-retry-bill")
    once = d.counts()
    again = d.act(d.owner, "bill_create", bill, op=f"{rid}-retry-bill")
    cli_again = d.act(d.owner, "bill_create", bill, op=f"{rid}-retry-bill", surface="cli")
    st, http_again = d.http("POST", "/api/v2/finance/actions", d.owner, {"kind": "bill_create", "payload": bill},
                            key=f"{rid}-retry-bill")
    ids = (first["action_id"], again["action_id"], cli_again["action_id"], str(http_again.get("action_id", "")))
    run.check("a retried bill returns the same action on MCP, CLI and HTTP",
              first["status"] == "committed" and len(set(ids)) == 1 and ids[0] != "", ids)
    run.check("the retried bill is recorded once", once["fin_bills"] == base["fin_bills"] + 1 and
              d.counts()["fin_bills"] == once["fin_bills"], (base["fin_bills"], once["fin_bills"], d.counts()["fin_bills"]))
    pay = {"recipient_id": fx["payee"], "amount": 2500, "money_account_id": fx["bank"], "account_id": fx["expense"],
           "obligation": f"rh-{rid}"}
    calls0 = len(d.fake_calls())
    p1 = d.act(d.owner, "payment_create", pay, op=f"{rid}-pay")
    if p1["status"] == "needs_approval" and p1["approval_id"]:
        approve(d, p1["approval_id"], note="rehearsal payment")
    time.sleep(1)
    pays = d.counts()["fin_payments"]
    d.act(d.owner, "payment_create", pay, op=f"{rid}-pay")
    d.act(d.owner, "payment_create", pay, op=f"{rid}-pay", surface="cli")
    time.sleep(1)
    run.check("a retried payment is recorded once", d.counts()["fin_payments"] == pays and pays > 0,
              (pays, d.counts()["fin_payments"], p1["raw"]))
    posts = [c for c in d.fake_calls()[calls0:] if str(c.get("method", "")).upper() == "POST"]
    run.check("a retried payment sends at most one POST to Mercury", len(posts) <= 1,
              [c.get("path") for c in posts])
    card = {"holder": "human:ben", "budget_id": fx["ai_budget"], "amount": 10000, "kind": "person",
            "nickname": "rh " + rid, "provider_user_id": "user-ben"}
    c1 = d.act(d.owner, "card_create", card, op=f"{rid}-card")
    cards = d.counts()["fin_cards"]
    d.act(d.owner, "card_create", card, op=f"{rid}-card")
    d.act(d.owner, "card_create", card, op=f"{rid}-card", surface="cli")
    run.check("a retried card is issued once", d.counts()["fin_cards"] == cards and c1["status"] == "committed",
              (cards, c1["raw"]))
    lines = [{"account_id": fx["expense"], "amount": 900}, {"account_id": fx["asset"], "amount": -900}]
    jr = {"date": TODAY, "memo": "retry journal", "lines": lines}
    j1 = d.act(d.owner, "journal", jr, op=f"{rid}-journal")
    entries = d.counts()["fin_entries"]
    d.act(d.owner, "journal", jr, op=f"{rid}-journal")
    d.act(d.owner, "journal", jr, op=f"{rid}-journal", surface="cli")
    run.check("a retried journal posts once", d.counts()["fin_entries"] == entries and j1["status"] == "committed",
              (entries, j1["raw"]))
    other = d.act(d.owner, "journal", {"date": TODAY, "memo": "different body", "lines": [
        {"account_id": fx["expense"], "amount": 1000}, {"account_id": fx["asset"], "amount": -1000}]},
                  op=f"{rid}-journal")
    run.check("the same key with a different body is refused as a conflict",
              refused(other) and "conflict" in (other["code"] + other["reason"]).lower() and
              d.counts()["fin_entries"] == entries, other["raw"])


def check5_rights(d, run, fx):
    rid = fx["rid"]
    ben, cara = fx["ben_token"], fx["cara_token"]
    fin, plain = fx["bot_tokens"]["fin"], fx["bot_tokens"]["plain"]
    bill = {"vendor_id": fx["vendor"], "bill_date": TODAY, "lines": [{"account_id": fx["expense"], "amount": 500}]}
    cases = [
        ("owner may draft a bill", d.owner, "bill_create", bill, False),
        ("ben (no finance rights) may not draft a bill", ben, "bill_create", bill, True),
        ("cara (finance.book only) may not draft a bill", cara, "bill_create", bill, True),
        ("the finance bot may draft a bill", fin, "bill_create", bill, False),
        ("the plain bot may not draft a bill", plain, "bill_create", bill, True),
        ("the finance bot may not grant rights", fin, "right_grant",
         {"teammate": "bot:" + fx["bots"]["plain"], "right": "finance.book"}, True),
        ("cara may not pay", cara, "payment_create", {"recipient_id": fx["payee"], "amount": 100,
                                                      "money_account_id": fx["bank"], "account_id": fx["expense"]}, True),
        ("the plain bot may not pay", plain, "payment_create", {"recipient_id": fx["payee"], "amount": 100,
                                                                "money_account_id": fx["bank"], "account_id": fx["expense"]}, True),
    ]
    for label, tok, kind, payload, want_denied in cases:
        res = d.act(tok, kind, payload, dry=True)
        run.check(label, denied(res) if want_denied else not denied(res), res["raw"])
    for label, tok in (("owner", d.owner), ("ben", ben), ("cara", cara), ("finance bot", fin), ("plain bot", plain)):
        is_err, out = d.mcp(tok, "hub_finance_metric", {"name": "cash"})
        allowed = not is_err and "not_found" not in json.dumps(out).lower()
        run.check(f"cash is readable only by a viewer with the sources view ({label})",
                  allowed == (label in ("owner", "cara", "finance bot")), str(out)[:200])
    is_err, out = d.mcp(plain, "hub_finance_inbox", {})
    run.check("a bot without finance rights can still read its inbox", not is_err, str(out)[:200])
    res = d.act(d.owner, "journal", {"date": TODAY, "memo": "rehearsal revenue", "lines": [
        {"account_id": fx["asset"], "amount": 500000}, {"account_id": fx["income"], "amount": -500000}]},
                op=f"{rid}-revenue")
    run.check("owner books a revenue journal, so there is revenue to keep from ben", res["status"] == "committed",
              res["raw"])
    _, own = d.mcp(d.owner, "hub_finance_report", {"kind": "pnl"})
    _, rep = d.mcp(ben, "hub_finance_report", {"kind": "pnl"})
    own_types = {r.get("type") for r in (own.get("rows") or [])} if isinstance(own, dict) else set()
    ben_types = {r.get("type") for r in (rep.get("rows") or [])} if isinstance(rep, dict) else set()
    run.check("the owner's P&L shows the revenue line (so the next check means something)", "income" in own_types,
              sorted(own_types))
    run.check("ben's P&L has expense lines only: no revenue and no balance sheet (spec 7.5)",
              bool(ben_types) and ben_types <= {"expense"}, sorted(ben_types))
    ben_total = sum(rep.get("expense") or []) if isinstance(rep, dict) else None
    own_total = sum(own.get("expense") or []) if isinstance(own, dict) else None
    run.check("ben's expense total is his slice, no more than the company's", isinstance(ben_total, (int, float))
              and isinstance(own_total, (int, float)) and ben_total <= own_total, (ben_total, own_total))
    plain = fx["bot_tokens"]["plain"]
    st_hidden, body_hidden = d.http("GET", f"/api/v2/finance/bills/{fx['bill']}", plain)
    st_none, body_none = d.http("GET", f"/api/v2/finance/bills/bill_nope_{rid}", plain)
    same_shape = json.dumps(body_hidden, sort_keys=True).replace(fx["bill"], "X") == \
        json.dumps(body_none, sort_keys=True).replace(f"bill_nope_{rid}", "X")
    run.check("a bill the plain bot may not see reads as not found, the same as a missing one",
              st_hidden == st_none == 404 and same_shape,
              (st_hidden, st_none, str(body_hidden)[:150], str(body_none)[:150]))
    made = d.act(d.owner, "bill_create", {"vendor_id": fx["vendor"], "bill_date": TODAY,
                                          "lines": [{"account_id": fx["expense"], "amount": 300}]},
                 op=f"{rid}-visibility")
    e1, o1 = d.mcp(plain, "hub_finance_action", {"action_id": made["action_id"]})
    e2, o2 = d.mcp(plain, "hub_finance_action", {"action_id": "act_nope_" + rid})
    r1, r2 = norm(e1, o1), norm(e2, o2)
    run.check("the plain bot reads another person's action as not found, the same as a missing one",
              r1["code"] == r2["code"] == "not_found" and r1["reason"] == r2["reason"],
              (str(o1)[:150], str(o2)[:150]))


def check6_rules(d, run, fx):
    rid = fx["rid"]
    fin_tok, fin_slug = fx["bot_tokens"]["fin"], fx["bots"]["fin"]
    owner, ben = fx["owner"], "human:ben"
    cara = fx["cara_token"]
    _, rules = d.http("GET", "/api/v2/finance/rules", d.owner)
    have = {r.get("id") for r in walk(rules) if r.get("id")}
    run.check("the six spec 6.4 default rules are present", all(x in have for x in DEFAULT_RULES),
              sorted(x for x in have if str(x).startswith("rule:")))

    # 6.4 defaults, as dry runs (nothing written)
    res = d.act(fin_tok, "payment_create", {"recipient_id": fx["fresh_payee"], "amount": 2500,
                                            "money_account_id": fx["bank"], "account_id": fx["expense"]}, dry=True)
    run.check("6.4 #1: a bot's payment to a new payee needs approval", res["status"] == "needs_approval",
              res["raw"])
    res = d.act(fin_tok, "payment_create", {"recipient_id": fx["payee"], "amount": 150000,
                                            "money_account_id": fx["bank"], "account_id": fx["expense"]}, dry=True)
    run.check("6.4 #2: a bot's payment over $1,000 needs the owner", res["status"] == "needs_approval" and
              res["approver"] == owner, res["raw"])
    res = d.act(fin_tok, "card_create", {"holder": "bot:" + fin_slug, "budget_id": fx["ai_budget"],
                                         "amount": 10000, "provider_user_id": "user-ana"}, dry=True)
    run.check("6.4 #3: a bot's standing card for itself asks the finance admin (the owner)",
              res["status"] == "needs_approval" and res["approver"] == owner, res["raw"])
    res = d.act(fin_tok, "journal", {"date": TODAY, "memo": "big", "lines": [
        {"account_id": fx["expense"], "amount": 600000}, {"account_id": fx["asset"], "amount": -600000}]}, dry=True)
    run.check("6.4 #5: a bot's books over $5,000 need approval", res["status"] == "needs_approval", res["raw"])
    res = d.act(fin_tok, "account_create", {"code": "9" + rid[:3], "name": "rh account", "type": "expense"}, dry=True)
    run.check("6.4 #5: a new account by a bot needs approval", res["status"] == "needs_approval", res["raw"])
    res = d.act(fin_tok, "close_period", {"month": "2019-05"}, dry=True)
    run.check("6.4 #6: a bot's close of a month needs the owner", res["status"] == "needs_approval" and
              res["approver"] == owner, res["raw"])
    res = d.act(d.owner, "reopen_period", {"month": "2019-05"}, dry=True)
    run.check("6.4 #6: the owner may reopen a month directly", not denied(res) and res["status"] != "needs_approval",
              res["raw"])

    # gate rules: a bad example stops a save; deny, limit, manager approval, owner approval
    res = d.act(d.owner, "rule_create", {"kind": "gate", "name": f"rh {rid} wrong example",
                                         "when": {"kind": "payment_create", "amount": {"gt": 500000}},
                                         "outcome": {"outcome": "deny", "reason": "x"},
                                         "examples": [{"input": {"kind": "payment_create", "amount": 600000},
                                                       "expect": "allow"}]}, op=f"{rid}-rule-bad")
    run.check("a rule whose example fails is not saved", res["status"] != "committed", res["raw"])
    res = d.act(d.owner, "rule_create", {"kind": "gate", "name": f"rh {rid} deny big payments",
                                         "words": "no payment over $5,000",
                                         "when": {"kind": "payment_create", "amount": {"gt": 500000}},
                                         "outcome": {"outcome": "deny", "reason": "over $5,000"},
                                         "examples": [{"input": {"kind": "payment_create", "amount": 600000},
                                                       "expect": "deny"},
                                                      {"input": {"kind": "payment_create", "amount": 1000},
                                                       "expect": "allow"}]}, op=f"{rid}-rule-deny")
    run.check("a deny rule saves when its examples pass", res["status"] == "committed", res["raw"])
    res = d.act(d.owner, "payment_create", {"recipient_id": fx["payee"], "amount": 600000,
                                            "money_account_id": fx["bank"], "account_id": fx["expense"]}, dry=True)
    run.check("deny beats the owner-approval default for a payment over $5,000",
              res["status"] == "denied" and "over $5,000" in res["reason"], res["raw"])
    before_pay = d.counts()["fin_payments"]
    res = d.act(d.owner, "payment_create", {"recipient_id": fx["payee"], "amount": 600000,
                                            "money_account_id": fx["bank"], "account_id": fx["expense"]}, op=f"{rid}-big")
    run.check("the denied payment writes no payment row",
              refused(res) and d.counts()["fin_payments"] == before_pay, res["raw"])
    res = d.act(d.owner, "rule_create", {"kind": "gate", "name": f"rh {rid} limit journals",
                                         "words": "journals up to $1,000 at a time",
                                         "when": {"kind": "journal"},
                                         "outcome": {"outcome": "limit", "limit": 100000, "per": "action"},
                                         "examples": [{"input": {"kind": "journal", "amount": 5000},
                                                       "expect": "limit"}]}, op=f"{rid}-rule-limit")
    run.check("a limit rule saves", res["status"] == "committed", res["raw"])
    res = d.act(d.owner, "journal", {"date": TODAY, "memo": "under limit", "lines": [
        {"account_id": fx["expense"], "amount": 5000}, {"account_id": fx["asset"], "amount": -5000}]},
                op=f"{rid}-small-journal")
    run.check("a journal under the per-action limit commits", res["status"] == "committed", res["raw"])
    res = d.act(d.owner, "journal", {"date": TODAY, "memo": "over limit", "lines": [
        {"account_id": fx["expense"], "amount": 150000}, {"account_id": fx["asset"], "amount": -150000}]},
                op=f"{rid}-over-journal")
    run.check("a journal over the per-action limit is denied", res["status"] == "denied", res["raw"])
    res = d.act(d.owner, "rule_create", {"kind": "gate", "name": f"rh {rid} cara journals need ben",
                                         "words": "cara's journals wait for her manager",
                                         "when": {"kind": "journal", "actor": "human:cara"},
                                         "outcome": {"outcome": "needs_approval", "who": "manager"},
                                         "examples": [{"input": {"kind": "journal", "actor": "human:cara"},
                                                       "expect": "needs_approval"},
                                                      {"input": {"kind": "journal", "actor": "human:ana"},
                                                       "expect": "allow"}]}, op=f"{rid}-rule-mgr")
    run.check("a manager-approval rule saves", res["status"] == "committed", res["raw"])
    res = d.act(d.owner, "rule_create", {"kind": "gate", "name": f"rh {rid} bot bills need owner",
                                         "words": "bot bills wait for the owner",
                                         "when": {"kind": "bill_create", "actor.kind": "bot"},
                                         "outcome": {"outcome": "needs_approval", "who": "owner"},
                                         "examples": [{"input": {"kind": "bill_create", "actor.kind": "bot"},
                                                       "expect": "needs_approval"}]}, op=f"{rid}-rule-owner")
    run.check("an owner-approval rule for bot bills saves", res["status"] == "committed", res["raw"])

    small = {"date": TODAY, "memo": "cara journal", "lines": [
        {"account_id": fx["expense"], "amount": 100}, {"account_id": fx["asset"], "amount": -100}]}
    res = d.act(cara, "journal", small, dry=True)
    run.check("precedence: needs_approval (manager) beats the limit that also matches a small journal",
              res["status"] == "needs_approval" and res["approver"] == ben, res["raw"])
    held = d.act(cara, "journal", small, op=f"{rid}-held")
    needs = json.dumps(d.http("GET", "/api/v2/needs-you", fx["ben_token"])[1])
    run.check("a held journal has an approval in ben's needs-you (he manages cara)",
              held["status"] == "needs_approval" and held["approval_id"] and held["approval_id"] in needs, held["raw"])
    st, body = approve(d, held["approval_id"], token=fx["ben_token"], note="ben approves")
    time.sleep(0.5)
    row = d.scalar("SELECT status FROM fin_actions WHERE id=?", (held["action_id"],))
    run.check("ben, cara's manager, can decide the held journal, and it then commits",
              st == 200 and row is not None and row["status"] == "committed", (st, str(body)[:150], row))

    # approval does not waive a later deny; a rule change applies to the next action only
    pending = d.act(cara, "journal", small, op=f"{rid}-pending")
    run.check("a second journal waits for ben", pending["status"] == "needs_approval", pending["raw"])
    res = d.act(d.owner, "rule_create", {"kind": "gate", "name": f"rh {rid} freeze cara journals",
                                         "words": "stop cara's journals", "when": {"kind": "journal", "actor": "human:cara"},
                                         "outcome": {"outcome": "deny", "reason": "frozen for rehearsal"},
                                         "examples": [{"input": {"kind": "journal", "actor": "human:cara"},
                                                       "expect": "deny"}]}, op=f"{rid}-rule-freeze")
    run.check("a freeze rule saves", res["status"] == "committed", res["raw"])
    freeze_id = made(res, "rule_id")
    run.check("a rule change leaves a held action as it was (still waiting for ben)",
              approval_state(d, pending["approval_id"]) == "pending", approval_state(d, pending["approval_id"]))
    nxt = d.act(cara, "journal", small, op=f"{rid}-next")
    run.check("the next journal after the change is judged by the new rule (denied)", nxt["status"] == "denied",
              nxt["raw"])
    entries = d.counts()["fin_entries"]
    st, body = approve(d, pending["approval_id"], token=fx["ben_token"], note="approved before the freeze")
    time.sleep(0.5)
    row = d.scalar("SELECT status FROM fin_actions WHERE id=?", (pending["action_id"],))
    run.check("an approval does not waive a later deny: the approved action is refused at use",
              row is not None and row["status"] != "committed" and d.counts()["fin_entries"] == entries,
              (st, row, d.counts()["fin_entries"] - entries))
    res = d.act(d.owner, "rule_disable", {"rule_id": freeze_id}, op=f"{rid}-disable-freeze")
    run.check("owner disables the freeze rule", res["status"] == "committed", res["raw"])
    after = d.act(cara, "journal", small, dry=True)
    run.check("after the freeze is disabled, the next journal waits for ben again (the change applies to the next action)",
              after["status"] == "needs_approval", after["raw"])

    res = d.act(d.owner, "rule_create", {"kind": "watch", "name": f"rh {rid} watch kind",
                                         "when": {"kind": "journal"}, "outcome": {"outcome": "deny"},
                                         "examples": []}, op=f"{rid}-rule-watch")
    run.check("a watch-kind rule is not created through the finance rule API (spec 6.1 keeps watch in Tico's rules)",
              refused(res), res["raw"])
    before_rules = rules_named(d, rid)
    res = d.act(d.owner, "rule_test", {"kind": "gate", "when": {"kind": "journal", "actor": "human:cara"},
                                       "outcome": {"outcome": "deny", "reason": "test"}}, op=f"{rid}-rule-test")
    after_rules = rules_named(d, rid)
    run.check("rule_test returns a result and writes no rule", res["status"] == "committed" and
              after_rules == before_rules, (res["status"], before_rules, after_rules, res["raw"]))
    changed = (res["raw"].get("result") or {}).get("changed") or []
    wrong = [c for c in changed if "was" in c and c["would"] in DECIDED.get(c["was"], ())]
    run.check("rule_test lists only what would change: nothing an action already got", not wrong, wrong[:3])
    stops = [c for c in changed if c.get("was") == "committed" and c.get("would") == "deny"]
    run.check("rule_test lists the committed journal this draft would stop", bool(stops), changed[:3])
    res = d.act(d.owner, "rule_create", {"kind": "posting", "name": f"rh {rid} posting",
                                         "when": {"counterparty": {"matches": "RH" + rid.upper()}},
                                         "outcome": {"outcome": "post", "account_id": fx["expense"]},
                                         "examples": [{"input": {"counterparty": "RH" + rid.upper() + " INC"},
                                                       "expect": "post"}]}, op=f"{rid}-posting")
    run.check("a posting rule saves with its examples (spec 6.1-6.2: the example's expected outcome)",
              res["status"] == "committed", res["raw"])


def check7_waivers(d, run, fx):
    rid = fx["rid"]
    st, body = d.http("POST", f"/api/v2/finance/actions/act_nope_{rid}/waive", d.owner, {"reason": "rehearsal"})
    run.check("no waiver route exists for a finance action (the spec defines no waiver)", st in (404, 405),
              (st, str(body)[:150]))
    over = d.act(fx["bot_tokens"]["fin"], "journal", {"date": TODAY, "memo": "bot over limit", "lines": [
        {"account_id": fx["expense"], "amount": 150000}, {"account_id": fx["asset"], "amount": -150000}]},
                 op=f"{rid}-bot-over")
    run.check("a bot's journal over a limit is denied, with no approval asked (a limit is never approved over)",
              over["status"] == "denied" and over["approval_id"] == "", over["raw"])
    bill = d.act(fx["bot_tokens"]["fin"], "bill_create", {"vendor_id": fx["vendor"], "bill_date": TODAY,
                                                         "lines": [{"account_id": fx["expense"], "amount": 800}]},
                 op=f"{rid}-bot-bill")
    run.check("a bot's bill waits for the owner", bill["status"] == "needs_approval" and bill["approval_id"] != ""
              and bill["approver"] == fx["owner"], bill["raw"])
    if bill["approval_id"]:
        st, body = approve(d, bill["approval_id"], token=fx["ben_token"], note="ben tries")
        run.check("a manager cannot decide an approval that names the owner", 400 <= st < 500,
                  (st, str(body)[:160]))
        run.check("the owner-level approval is still pending after the manager's attempt",
                  approval_state(d, bill["approval_id"]) == "pending", approval_state(d, bill["approval_id"]))


def check8_audit(d, run, fx):
    rid = fx["rid"]
    actions = d.rows("SELECT id, kind, actor, on_behalf_of, rule_id, status FROM fin_actions "
                     "WHERE idempotency_key LIKE ?", (f"{rid}-%",))
    ids = [a["id"] for a in actions]
    events = []
    if ids:
        marks = ",".join("?" for _ in ids)
        events = d.rows(f"SELECT action, actor, target, ts, detail_json FROM events WHERE target IN ({marks}) "
                        f"ORDER BY ts", ids)
    money = [a for a in actions if a["kind"] in ("payment_create", "card_create", "bill_create", "journal")
             and a["status"] == "committed"]
    run.check("every committed money-moving action from this run has an audit row with who and when",
              money and all(any(e["target"] == a["id"] for e in events) for a in money),
              [(a["kind"], a["status"]) for a in money][:6])
    for a in money[:3]:
        ev = next((e for e in events if e["target"] == a["id"]), None)
        if ev:
            detail = json.loads(ev["detail_json"] or "{}")
            run.check(f"the {a['kind']} audit row carries who, when and the status",
                      bool(ev["actor"]) and bool(ev["ts"]) and "status" in detail, detail)
    refusals = [a for a in actions if a["status"] == "denied"]
    run.check("a denied action is audited with the rule that denied it",
              refusals and all(any(e["target"] == a["id"] for e in events) for a in refusals),
              [(a["kind"], a["rule_id"]) for a in refusals][:4])
    bot_rows = [a for a in actions if str(a["actor"]).startswith("bot:")]
    run.check("a bot's action is audited with the bot that acted (spec 5: which bot, under which rule)",
              bool(bot_rows) and all(a["actor"] and a["rule_id"] is not None for a in bot_rows),
              [(a["actor"], a["on_behalf_of"] or "(none: the bot acted on its own)", a["rule_id"])
               for a in bot_rows][:4])
    rule_events = d.rows("SELECT action, actor, target, ts FROM events WHERE action LIKE 'finance.rule_%' AND ts >= ?",
                         (fx.get("started", ""),))
    run.check("rule changes from this run are audited with who and when",
              rule_events and all(e["actor"] and e["ts"] for e in rule_events),
              [(e["action"], e["actor"]) for e in rule_events][:4])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rehearse Tico Finance's bot tools and rules (see the module docstring).")
    parser.add_argument("--demo", default="http://127.0.0.1:18841")
    parser.add_argument("--fake", default="http://127.0.0.1:18842")
    parser.add_argument("--data-dir", required=True, help="the demo's data directory (local-owner.token, hub.sqlite)")
    parser.add_argument("--report", default="", help="write every check's result as JSON to this path")
    args = parser.parse_args(argv)
    d = Demo(args.demo, args.fake, args.data_dir)
    run = Run()
    rid = secrets.token_hex(3)
    try:
        st, _ = d.http("GET", "/healthz", base=d.base)
        run.check("the demo answers on its port", st == 200, st)
        st, _ = d.http("GET", "/_fake/calls", base=d.fake)
        run.check("the fake Mercury answers on its port", st == 200, st)
    except OSError as exc:
        run.check("the demo and the fake are reachable", False, exc)
        return finish(run, args.report)
    started = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    holder = {}
    run.run_section("setup", lambda: holder.update(fixtures(d, run, rid)))
    fx = dict(holder, rid=rid, started=started)
    if not fx.get("bill") or not fx.get("bot_tokens"):
        return finish(run, args.report)
    for label, fn in (("1 tool surface", check1_surface), ("2 same answer", check2_same_answer),
                      ("3 bad input", check3_bad_input), ("4 retries", check4_retries),
                      ("5 rights", check5_rights), ("6 rules", check6_rules),
                      ("7 waivers", check7_waivers), ("8 audit", check8_audit)):
        run.run_section(label, fn, d, run, fx)
    return finish(run, args.report)


def finish(run, report):
    ok = bool(run.rows) and all(r["ok"] for r in run.rows)
    passed = sum(1 for r in run.rows if r["ok"])
    print(f"\n{passed}/{len(run.rows)} checks passed", flush=True)
    if report:
        Path(report).write_text(json.dumps(run.rows, indent=2))
    print("all checks passed" if ok else "NOT all checks passed", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
