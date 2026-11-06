"""Bookkeeper rehearsal, checks 1-6 (scripts/finance_bookkeeper_rehearsal.py): in-process against the built-in
Bookkeeper (backend/bookkeeper.py) and the instructions it ships (templates/catalog/bookkeeper). A known gap is an
xfail with strict=True that names it: the run shows it, and the script counts it as not passed. Synthetic fixtures."""

import json
import re
import uuid
from pathlib import Path

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
        return [dict(r) for r in c.execute("SELECT trigger_type, name, flow FROM automations "
                                           "WHERE deleted_at IS NULL AND flow LIKE '%bookkeeper%'")]


def _triggers(api):
    return {r["trigger_type"] for r in _routines(api)} | {
        m for r in _routines(api) for m in re.findall(r"finance\.[a-z_.]+", r["flow"])}


# ----------------------------------------------------------------------------- check 1, 2 and 5: built, guarded, offered the right tools
def _c1_built_with_default_rights_and_no_org_chart_seat(api, token):
    ensure(api)
    with api.app.state.store.read() as c:
        assert BK.built(c)
        assert _bot_rights(c) == DEFAULT_RIGHTS


def _c1_archive_refused_as_a_system_bot(api, token):
    bot = api.get("/api/v2/bots/" + BK.SLUG, headers=signed_in()).json()
    body = {"expected_revision": bot.get("revision", 0), "successor": None, "revoke_agent": True}
    r = api.post("/api/v2/bots/" + BK.SLUG + "/archive", json=body, headers=signed_in())
    assert r.status_code == 409 and "system_bot" in r.text, r.text


def _c1_no_delete_route_for_the_bot(api, token):
    r = api.delete("/api/v2/bots/" + BK.SLUG, headers=signed_in())
    assert r.status_code in (404, 405), r.status_code


def _c2_the_bot_is_offered_the_six_server_finance_tools(api, token):
    names = _names(api, token)
    assert set(SPEC_TOOLS) <= names, sorted(set(SPEC_TOOLS) - names)
    assert "hub_finance_card_fill" not in names      # local to the computer; not offered by the server yet


def _c2_the_bot_is_not_offered_approvals_group_edits_or_archive(api, token):
    assert not set(NEVER_OFFERED) & _names(api, token)


def _c5_the_bot_holds_pay_but_cannot_decide_an_approval(api, token):
    with api.app.state.store.read() as c:
        rights = _bot_rights(c)
    assert "finance.pay" in rights
    assert "hub_approval_approve" not in _names(api, token)



def _c2_close_period_by_the_bot_is_not_committed(api, token):
    err, out = call(api, "hub_finance_act", {"kind": "close_period", "operation_id": "close-1",
                                             "payload": {"month": "2026-09"}}, token=token)
    assert out.get("status") != "committed", out


def _c2_rule_create_is_refused_once_finance_rules_is_revoked(api, token):
    """The default Bookkeeper holds finance.rules and may propose a rule (turns test: needs_approval); without it a
    rule_create is refused before anything is written."""
    store = api.app.state.store
    with store.transaction() as c:
        owner = "human:" + api.app.state.auth.owner_id(c)
        assert FA.act(c, owner, "right_revoke", {"teammate": BK.ACTOR, "right": "finance.rules"},
                      "revoke-rules").status == "committed"
    err, out = call(api, "hub_finance_act", {"kind": "rule_create", "operation_id": "rule-1", "payload": {}},
                    token=token)
    assert out.get("status") == "denied" and "finance.rules" in out.get("reason", ""), out


def test_bookkeeper_rehearsal_setup_and_guards(environment):
    """One Bookkeeper, built once: default rights and seat, the system-bot guards, the six tools and none of the approval tools, close and rule refusals. The right revoke runs last."""
    api = built(environment)
    ensure(api)
    token = _token(api)
    _c1_built_with_default_rights_and_no_org_chart_seat(api, token)
    _c1_archive_refused_as_a_system_bot(api, token)
    _c1_no_delete_route_for_the_bot(api, token)
    _c2_the_bot_is_offered_the_six_server_finance_tools(api, token)
    _c2_the_bot_is_not_offered_approvals_group_edits_or_archive(api, token)
    _c5_the_bot_holds_pay_but_cannot_decide_an_approval(api, token)
    _c2_close_period_by_the_bot_is_not_committed(api, token)
    _c2_rule_create_is_refused_once_finance_rules_is_revoked(api, token)


# ----------------------------------------------------------------------------- check 3 and 6: triggers, quiet runs and visible failures
def _c3_an_empty_queue_is_a_quiet_check(api, token):
    before = len(checks(api))
    out = check(api)
    assert out.get("task") is None and len(checks(api)) == before


def _c3_the_four_seeded_triggers_are_routines(api, token):
    wanted = {"finance.bill.due_soon", "finance.invoice.overdue", "finance.period.ready"}
    assert wanted <= _triggers(api), sorted(_triggers(api))


def _c3_receipts_are_found_by_the_hourly_check(api, token):
    """Spec 11 Receipts: a settled card charge with no receipt is found by the hourly Books check (missing_receipt is a
    check kind, answered by no per-row routine). The live rows are in test_receipts_are_found_by_the_hourly_check."""
    assert "missing_receipt" in FR.INBOX_KINDS and "missing_receipt" not in BK.ON_EVENT
    assert not any("settle" in t for t in _triggers(api)), sorted(_triggers(api))


def _c3_a_reminder_action_exists(api, token):
    assert "invoice_remind" in _kinds(api, token)


def _c3_watch_covers_unused_subscriptions(api, token):
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


def _c6_a_failed_sync_reaches_the_owner_without_botops(api, token):
    """A bank connection whose sync fails tells its owner what failed, once for the failure run: one finance.sync.failed
    event and one keeper notice to the owner. A retry that still fails says nothing more. No BotOps task is involved."""
    from backend.finance import sync
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
        notes = c.execute("SELECT recipient_id, body FROM messages WHERE kind='notice' AND body LIKE 'Finance could not sync%'"
                          ).fetchall()
    assert events == 1 and [n["recipient_id"] for n in notes] == [owner], (events, [dict(n) for n in notes])
    assert "Acme checking" in notes[0]["body"], notes[0]["body"]


def test_bookkeeper_rehearsal_quiet_check_and_visible_failures(environment):
    """One Bookkeeper: the empty queue is a quiet check, the seeded routines, receipts and unused subscriptions, and a failed sync reaches the owner."""
    api = built(environment)
    ensure(api)
    token = _token(api)
    _c3_an_empty_queue_is_a_quiet_check(api, token)
    _c3_the_four_seeded_triggers_are_routines(api, token)
    _c3_receipts_are_found_by_the_hourly_check(api, token)
    _c3_a_reminder_action_exists(api, token)
    _c3_watch_covers_unused_subscriptions(api, token)
    _c6_a_failed_sync_reaches_the_owner_without_botops(api, token)


# ----------------------------------------------------------------------------- check 2 and 4: the instructions match the code
def _c2_the_instructions_only_ask_for_actions_its_default_rights_allow(api, token):
    """Every action kind the instructions name with a backticked literal needs a right the default Bookkeeper holds."""
    text = "\n".join(p.read_text() for p in INSTRUCTIONS)
    named = {k for k in re.findall(r"`([a-z]+_[a-z_]+)`", text) if k in K.NEEDS}
    assert named, "the instructions name no action kind at all"
    for kind in sorted(named):
        need = K.NEEDS[kind]
        needed = {need} if isinstance(need, str) else set(need)
        assert needed & DEFAULT_RIGHTS, (kind, sorted(needed))


def _c4_every_hub_command_the_instructions_name_exists(api, token):
    names = _names(api, token)
    named = set()
    for path in INSTRUCTIONS:
        named |= {(a, b) for a, b in re.findall(r"hub ([a-z]+) ([a-z]+)", path.read_text())}
    missing = sorted(f"hub {a} {b}" for a, b in named if f"hub_{a}_{b}" not in names)
    assert not missing, missing


def _c4_every_act_kind_the_playbooks_name_exists(api, token):
    from backend.finance.core import kinds as K
    from backend.finance import reads as FR
    fields = {name.rstrip("?") for shape in K.SHAPES.values() for name, _, _ in shape}
    known = set(K.ALL) | set(K.NEEDS) | set(FR.INBOX_KINDS) | fields
    text = "\n".join(p.read_text() for p in INSTRUCTIONS)
    named = set(re.findall(r"`((?:bill|invoice|payment|card|budget|tax|rule|recipient|contact|account|allocation|"
                           r"projection|close|reopen|reconcile|categorize|journal|view|right|connection)_[a-z_]+)`", text))
    named = {n for n in named if not n.endswith("_id")}   # `bill_id` names a field, not a kind
    assert named <= known, sorted(named - known)


def _c4_close_period_is_asked_with_the_fields_it_takes(api, token):
    """The kind takes only `month`; the close playbook asks for close_period with that alone and puts the variance note
    on the task, so no playbook line asks for a note on the close."""
    err, out = call(api, "hub_finance_act", {"kind": "help", "payload": {"kind": "close_period"}}, token=token)
    fields = {f["name"] for f in out["fields"]}
    assert fields == {"month"}, sorted(fields)
    asks = [line for line in (TEMPLATE / "playbooks" / "close-month.md").read_text().splitlines()
            if "close_period" in line]
    assert asks and not any("note" in line for line in asks), asks


def _c4_the_payloads_the_playbooks_show_take_those_fields(api, token):
    """Each `hub finance act <kind> --payload '<json>'` a playbook or AGENT.md shows is a kind with a shape, its JSON names
    only fields that kind takes and gives every required one: the example is what a bot copies. Examples wrap over lines,
    so the text is read as one line."""
    text = " ".join(p.read_text() for p in INSTRUCTIONS).replace("\n", " ")
    shown = [(kind, raw) for kind, raw in re.findall(r"hub finance act ([a-z_]+)\s+--payload\s+'(\{.*?\})'", text)
             if kind != "help"]
    assert len(shown) >= 6, shown                 # bill_schedule, payment_create, reconcile, contact_create, ...
    for kind, raw in shown:
        payload = json.loads(re.sub(r"<[a-z_]+>", "1", raw))     # `<month>` and `<stage>` are values the bot fills in
        assert kind in K.SHAPES, kind
        names = {name.rstrip("?") for name, _, _ in K.SHAPES[kind]}
        required = {name for name, _, _ in K.SHAPES[kind] if not name.endswith("?")}
        assert set(payload) <= names, (kind, sorted(set(payload) - names))
        assert required <= set(payload), (kind, sorted(required - set(payload)))


def _c6_sync_failed_items_are_read_only_for_viewers_with_everything(api, token):
    text = Path(FR.__file__).read_text()
    assert re.search(r"sync_failed.{0,400}view\.everything|view\.everything.{0,400}sync_failed", text, re.S)


def test_bookkeeper_rehearsal_instructions_match_the_code(environment):
    """The instructions and playbooks name only commands, kinds, fields and rights the code has."""
    api = built(environment)
    ensure(api)
    token = _token(api)
    _c2_the_instructions_only_ask_for_actions_its_default_rights_allow(api, token)
    _c4_every_hub_command_the_instructions_name_exists(api, token)
    _c4_every_act_kind_the_playbooks_name_exists(api, token)
    _c4_close_period_is_asked_with_the_fields_it_takes(api, token)
    _c4_the_payloads_the_playbooks_show_take_those_fields(api, token)
    _c6_sync_failed_items_are_read_only_for_viewers_with_everything(api, token)
