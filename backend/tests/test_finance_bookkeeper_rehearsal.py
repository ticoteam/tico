"""Bookkeeper rehearsal, checks 1-6 (scripts/finance_bookkeeper_rehearsal.py): in-process against the built-in
Bookkeeper (backend/bookkeeper.py) and the instructions it ships (templates/catalog/bookkeeper). A known gap is an
xfail with strict=True that names it: the run shows it, and the script counts it as not passed. Synthetic fixtures."""

import re
import uuid
from pathlib import Path

import pytest

from backend import bookkeeper as BK
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.core import kinds as K
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


@pytest.mark.xfail(strict=True, reason="GAP: spec 11 Receipts runs on card settlement; no settlement trigger exists, "
                   "receipts come from the hourly Books check only")
def test_c3_receipts_have_a_settlement_trigger(environment):
    api = built(environment)
    ensure(api)
    assert any("settle" in t for t in _triggers(api)), sorted(_triggers(api))


@pytest.mark.xfail(strict=True, reason="GAP: invoice-overdue playbook says send one reminder through Finance, but no "
                   "reminder action (invoice_remind) exists and invoice_send only marks drafts as sent")
def test_c3_a_reminder_action_exists(environment):
    api = built(environment)
    ensure(api)
    assert "invoice_remind" in _kinds(api, _token(api))


@pytest.mark.xfail(strict=True, reason="GAP: spec 11 Watch lists unused subscriptions; docs/bookkeeper.md says they "
                   "are not watched")
def test_c3_watch_covers_unused_subscriptions(environment):
    text = (Path(BK.__file__).read_text() + (TEMPLATE / "playbooks" / "books-watch.md").read_text()).lower()
    assert "unused" in text


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


@pytest.mark.xfail(strict=True, reason="GAP: close-month playbook asks for close_period with the note, but the kind "
                   "takes only month (kinds.py), so the variance note is not stored with the approval; decide: add "
                   "the note to the kind, or drop it from the playbook")
def test_c4_close_period_takes_the_note_the_close_playbook_passes(environment):
    api = built(environment)
    ensure(api)
    err, out = call(api, "hub_finance_act", {"kind": "help", "payload": {"kind": "close_period"}}, token=_token(api))
    fields = {f["name"] for f in out["fields"]}
    playbook = (TEMPLATE / "playbooks" / "close-month.md").read_text()
    assert "note" not in playbook or "note" in fields, sorted(fields)


# ----------------------------------------------------------------------------- check 6: failures are visible
@pytest.mark.xfail(strict=True, reason="GAP: finance.sync.failed has no Bookkeeper routine; the failure only queues "
                   "a sync_failed item that owners see with view.everything (reads.py), and AGENT.md says note it for "
                   "BotOps")
def test_c6_a_failed_sync_reaches_the_owner_without_botops(environment):
    api = built(environment)
    ensure(api)
    assert "finance.sync.failed" in _triggers(api), sorted(_triggers(api))


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
