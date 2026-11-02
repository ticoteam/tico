"""Bookkeeper rehearsal, checks 1-6 (scripts/finance_bookkeeper_rehearsal.py): in-process against the built-in
Bookkeeper (backend/bookkeeper.py) and the instructions it ships (templates/catalog/bookkeeper). A known gap is an
xfail with strict=True that names it: the run shows it, and the script counts it as not passed. Synthetic fixtures."""

import json
import re
import uuid
from pathlib import Path

import pytest

from backend import bookkeeper as BK
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.core import kinds as K
from backend.store import H
from backend.tests.test_bookkeeper import DEFAULT_RIGHTS, built, check, checks, ensure
from backend.tests.test_mcp import call, rpc
from backend.tests.test_onboarding import draft, environment, machine, signed_in  # noqa: F401

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "bookkeeper"
SPEC_TOOLS = ("hub_finance_search", "hub_finance_report", "hub_finance_metric", "hub_finance_act",
              "hub_finance_action", "hub_finance_inbox")
NEVER_OFFERED = ("hub_approval_approve", "hub_approval_decline", "hub_group_update", "hub_bot_archive")
INSTRUCTIONS = [p for p in sorted(TEMPLATE.rglob("*.md"))] + [TEMPLATE / "card.yaml", TEMPLATE / "bot.yaml"]


def _token(api):
    """A run's token for bot:bookkeeper, as a claimed job carries it: a computer enrolls, the bot is assigned to it,
    a person asks it something, and the computer claims the job (the same steps as test_api.setup_attempt, signed in
    as the owner of this environment)."""
    def post(path, body, token=None):
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        headers["Authorization"] = "Bearer " + token if token else signed_in()["Authorization"]
        r = api.post("/api/v2/" + path, json=body, headers=headers)
        assert r.status_code == 200, r.text
        return r.json()
    code = post("enrollments", {"operator": "morgan"})["code"]
    run = post("runners/enroll", {"code": code, "label": "Rehearsal Mac", "platform": "test"})
    with api.app.state.store.read() as c:   # the built one is already on the first computer: move it to this one
        row = c.execute("SELECT generation FROM assignments WHERE bot=?", (BK.SLUG,)).fetchone()
    post("bots/" + BK.SLUG + "/assignment", {"runner_id": run["runner_id"], "expected_generation": row["generation"] if row else 0})
    post("runners/heartbeat", {"version": "test", "platform": "test", "readiness": {BK.SLUG: True}}, run["token"])
    post("chat/" + BK.SLUG, {"text": "Please summarize the current work."})
    return post("jobs/claim", {"bot": BK.SLUG, "start_places": True}, run["token"])["attempt"]["token"]


def _names(api, token):
    return {t["name"] for t in rpc(api, "tools/list", token=token)["result"]["tools"]}


def _kinds(api, token):
    err, out = call(api, "hub_finance_act", {"kind": "help"}, token=token)
    assert not err, out
    return {k["kind"] for k in out["kinds"]}


def _bot_rights(c):
    if not FR.is_set_up(c):
        return set()
    return {r for t in FR.access(c, BK.ACTOR)["teammates"] if t["teammate"] == BK.ACTOR for r in t["rights"]}


def _routines(api):
    """The Bookkeeper's routines as stored: any automation whose flow names the bot."""
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT trigger_type, name, flow_json FROM automations "
                                           "WHERE deleted_at IS NULL AND flow_json LIKE '%bookkeeper%'")]


def _triggers(api):
    return {r["trigger_type"] for r in _routines(api)} | {
        m for r in _routines(api) for m in re.findall(r"finance\.[a-z_.]+", r["flow_json"])}


# ----------------------------------------------------------------------------- check 1: built, guarded
def test_c1_built_with_default_rights_and_no_org_chart_seat(environment):
    api = built(environment)
    ensure(api)
    ensure(api)
    with api.app.state.store.read() as c:
        assert BK.built(c)
        assert _bot_rights(c) == DEFAULT_RIGHTS


def test_c1_archive_refused_as_a_system_bot(environment):
    api = built(environment)
    ensure(api)
    bot = api.get("/api/v2/bots/" + BK.SLUG, headers=signed_in()).json()
    body = {"expected_revision": bot.get("revision", 0), "successor": None, "revoke_agent": True}
    r = api.post("/api/v2/bots/" + BK.SLUG + "/archive", json=body, headers=signed_in())
    assert r.status_code == 409 and "system_bot" in r.text, r.text


def test_c1_no_delete_route_for_the_bot(environment):
    api = built(environment)
    ensure(api)
    r = api.delete("/api/v2/bots/" + BK.SLUG, headers=signed_in())
    assert r.status_code in (404, 405), r.status_code


# ----------------------------------------------------------------------------- check 2: tools and refusals
def test_c2_the_bot_is_offered_the_six_server_finance_tools(environment):
    api = built(environment)
    ensure(api)
    names = _names(api, _token(api))
    assert set(SPEC_TOOLS) <= names, sorted(set(SPEC_TOOLS) - names)
    assert "hub_finance_card_fill" not in names      # local to the computer; not offered by the server yet


def test_c2_the_bot_is_not_offered_approvals_group_edits_or_archive(environment):
    api = built(environment)
    ensure(api)
    assert not set(NEVER_OFFERED) & _names(api, _token(api))


def test_c2_rule_create_is_refused_once_finance_rules_is_revoked(environment):
    """The default Bookkeeper holds finance.rules and may propose a rule (turns test: needs_approval); without it a
    rule_create is refused before anything is written."""
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    with store.transaction() as c:
        owner = "human:" + api.app.state.auth.owner_id(c)
        assert FA.act(c, owner, "right_revoke", {"teammate": BK.ACTOR, "right": "finance.rules"},
                      "revoke-rules").status == "committed"
    err, out = call(api, "hub_finance_act", {"kind": "rule_create", "operation_id": "rule-1", "payload": {}},
                    token=_token(api))
    assert out.get("status") == "denied" and "finance.rules" in out.get("reason", ""), out


def test_c2_close_period_by_the_bot_is_not_committed(environment):
    api = built(environment)
    ensure(api)
    err, out = call(api, "hub_finance_act", {"kind": "close_period", "operation_id": "close-1",
                                             "payload": {"month": "2026-09"}}, token=_token(api))
    assert out.get("status") != "committed", out


def test_c2_the_instructions_only_ask_for_actions_its_default_rights_allow(environment):
    """Every action kind the instructions name with a backticked literal needs a right the default Bookkeeper holds."""
    text = "\n".join(p.read_text() for p in INSTRUCTIONS)
    named = {k for k in re.findall(r"`([a-z]+_[a-z_]+)`", text) if k in K.NEEDS}
    assert named, "the instructions name no action kind at all"
    for kind in sorted(named):
        need = K.NEEDS[kind]
        needed = {need} if isinstance(need, str) else set(need)
        assert needed & DEFAULT_RIGHTS, (kind, sorted(needed))


# ----------------------------------------------------------------------------- check 3: triggers and quiet runs
def test_c3_an_empty_queue_is_a_quiet_check(environment):
    api = built(environment)
    ensure(api)
    before = len(checks(api))
    out = check(api)
    assert out.get("task") is None and len(checks(api)) == before


def test_c3_the_four_seeded_triggers_are_routines(environment):
    api = built(environment)
    ensure(api)
    wanted = {"finance.bill.due_soon", "finance.invoice.overdue", "finance.period.ready"}
    assert wanted <= _triggers(api), sorted(_triggers(api))


def test_c3_receipts_are_found_by_the_hourly_check(environment):
    """Spec 11 Receipts: a settled card charge with no receipt is found by the hourly Books check (missing_receipt is a
    check kind, answered by no per-row routine). The live rows are in test_receipts_are_found_by_the_hourly_check."""
    api = built(environment)
    ensure(api)
    assert "missing_receipt" in FR.INBOX_KINDS and "missing_receipt" not in BK.ON_EVENT
    assert not any("settle" in t for t in _triggers(api)), sorted(_triggers(api))


@pytest.mark.xfail(strict=True, reason="GAP: invoice-overdue playbook says send one reminder through Finance, but no "
                   "reminder action (invoice_remind) exists and invoice_send only marks drafts as sent")
def test_c3_a_reminder_action_exists(environment):
    api = built(environment)
    ensure(api)
    assert "invoice_remind" in _kinds(api, _token(api))


def test_c3_watch_covers_unused_subscriptions(environment):
    """Spec 11: a monthly charge seen three months running on an idle bot's card is an unused subscription. The Books
    watch lists it as its own kind (with a hint), and the Watch playbook names it."""
    from datetime import date

    from backend.finance.core import watch as W
    monthly = [W.Charge(id=f"m{m}", payee="acme cloud", amount=4_900, day=date(2026, m, 5), card="card_a")
               for m in (8, 9, 10)]
    assert [f["kind"] for f in W.unused_subscriptions(monthly, {"card_a": "archived-bot"}, date(2026, 11, 1))] == [
        "unused_subscription"]
    assert "unused_subscription" in BK.WATCH_ORDER and "unused_subscription" in BK.WATCH_HINTS
    assert "unused subscriptions" in (TEMPLATE / "playbooks" / "books-watch.md").read_text()


# ----------------------------------------------------------------------------- check 4: instructions match the code
def test_c4_every_hub_command_the_instructions_name_exists(environment):
    api = built(environment)
    ensure(api)
    names = _names(api, _token(api))
    named = set()
    for path in INSTRUCTIONS:
        named |= {(a, b) for a, b in re.findall(r"hub ([a-z]+) ([a-z]+)", path.read_text())}
    missing = sorted(f"hub {a} {b}" for a, b in named if f"hub_{a}_{b}" not in names)
    assert not missing, missing


def test_c4_every_act_kind_the_playbooks_name_exists(environment):
    from backend.finance.core import kinds as K
    api = built(environment)
    ensure(api)
    known = set(K.ALL) | set(K.NEEDS)
    text = "\n".join(p.read_text() for p in INSTRUCTIONS)
    named = set(re.findall(r"`((?:bill|invoice|payment|card|budget|tax|rule|recipient|contact|account|allocation|"
                           r"projection|close|reopen|reconcile|categorize|journal|view|right|connection)_[a-z_]+)`", text))
    named = {n for n in named if not n.endswith("_id")}   # `bill_id` names a field, not a kind
    assert named <= known, sorted(named - known)


def test_c4_close_period_is_asked_with_the_fields_it_takes(environment):
    """The kind takes only `month`; the close playbook asks for close_period with that alone and puts the variance note
    on the task, so no playbook line asks for a note on the close."""
    api = built(environment)
    ensure(api)
    err, out = call(api, "hub_finance_act", {"kind": "help", "payload": {"kind": "close_period"}}, token=_token(api))
    fields = {f["name"] for f in out["fields"]}
    assert fields == {"month"}, sorted(fields)
    asks = [line for line in (TEMPLATE / "playbooks" / "close-month.md").read_text().splitlines()
            if "close_period" in line]
    assert asks and not any("note" in line for line in asks), asks


def test_c4_the_payloads_the_playbooks_show_take_those_fields(environment):
    """Each `hub finance act <kind> --payload '<json>'` a playbook or AGENT.md shows is a kind with a shape, its JSON names
    only fields that kind takes and gives every required one: the example is what a bot copies. Examples wrap over lines,
    so the text is read as one line."""
    text = " ".join(p.read_text() for p in INSTRUCTIONS).replace("\n", " ")
    shown = [(kind, raw) for kind, raw in re.findall(r"hub finance act ([a-z_]+)\s+--payload\s+'(\{.*?\})'", text)
             if kind != "help"]
    assert len(shown) >= 6, shown                 # bill_schedule, payment_create, reconcile, contact_create, ...
    for kind, raw in shown:
        payload = json.loads(raw)
        assert kind in K.SHAPES, kind
        names = {name.rstrip("?") for name, _, _ in K.SHAPES[kind]}
        required = {name for name, _, _ in K.SHAPES[kind] if not name.endswith("?")}
        assert set(payload) <= names, (kind, sorted(set(payload) - names))
        assert required <= set(payload), (kind, sorted(required - set(payload)))


# ----------------------------------------------------------------------------- check 6: failures are visible
def test_c6_a_failed_sync_reaches_the_owner_without_botops(environment):
    """A bank connection whose sync fails tells its owner what failed, once for the failure run: one finance.sync.failed
    event and one keeper notice to the owner. A retry that still fails says nothing more. No BotOps task is involved."""
    from backend.finance import sync
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    with store.transaction() as c:
        owner = "human:" + api.app.state.auth.owner_id(c)
        c.execute("INSERT INTO fin_connections(id, provider, purpose, created_at, created_by, name) VALUES(?,?,?,?,?,?)",
                  ("conn-rehearsal", "mercury", "bank", H.now(), owner, "Acme checking"))
    first = H.now()
    # no token stored: the run fails at once (the same failure path as a Mercury outage), and the retry two hours on
    assert sync.connection(store, None, "conn-rehearsal", at=first) == "no_token"
    assert sync.connection(store, None, "conn-rehearsal", at=H.shift(first, hours=2)) == "no_token"
    with store.read() as c:
        events = c.execute("SELECT count(*) FROM fin_events WHERE name='finance.sync.failed'").fetchone()[0]
        notes = c.execute("SELECT to_actor, body FROM messages WHERE kind='notice' AND body LIKE 'Finance could not sync%'"
                          ).fetchall()
    assert events == 1 and [n["to_actor"] for n in notes] == [owner], (events, [dict(n) for n in notes])
    assert "Acme checking" in notes[0]["body"], notes[0]["body"]


def test_c6_sync_failed_items_are_read_only_for_viewers_with_everything(environment):
    text = Path(FR.__file__).read_text()
    assert re.search(r"sync_failed.{0,400}view\.everything|view\.everything.{0,400}sync_failed", text, re.S)


def test_c5_the_bot_holds_pay_but_cannot_decide_an_approval(environment):
    api = built(environment)
    ensure(api)
    with api.app.state.store.read() as c:
        rights = _bot_rights(c)
    assert "finance.pay" in rights
    assert "hub_approval_approve" not in _names(api, _token(api))
