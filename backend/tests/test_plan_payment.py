"""A plan the provider refuses for its billing holds the plan, never the bots on it (backend/login_holds.py payment):
one hold for every agent that selects it, one Needs-you item for the person who holds it, a status line that names it, and
everyone back at work once a turn on it works again (Tico Team, Nov 21: a plan past due held its bots by hand)."""

from backend import login_holds, run_failures
from backend.store import H
from backend.tests.helpers.api import claim, get, headers, post
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.login_holds import PAST_DUE, at
from backend.tests.helpers.model_subscriptions import add, company, machine, on, pick


def refused(api, m, turn):
    """The turn fails at once with Claude Code's billing refusal, as the runner reports it: an error it did not mark as
    a limit."""
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"]}, m["token"])
    post(
        api,
        f"attempts/{turn['id']}/events",
        {"events": [{"seq": 1, "kind": "error", "payload": {"error": PAST_DUE}}]},
        m["token"],
    )
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "failed", "last_seq": 1, "text": PAST_DUE}, m["token"])


def worked(api, m, turn):
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "ok-" + turn["id"]}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, m["token"])


def items(api):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT t.id,t.title,t.status,(SELECT group_concat(m.body,' ') FROM messages m "
                "WHERE m.conversation_id=t.conversation_id) AS body FROM tasks t WHERE t.title LIKE 'Pay for the %' "
                "ORDER BY t.rowid"
            )
        ]


def plan(api):
    [found] = get(api, "model-subscriptions", "ana-test")["subscriptions"]
    return found


def test_a_plan_past_due_holds_every_agent_on_it_and_recovers_once_a_turn_works(api, monkeypatch):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    on(api, "cpo", "claude", "claude-sonnet-5-5")
    sid = add(api, name="brooke@acme.example")["id"]
    m, n = machine(api), machine(api, bot="cpo", label="Other Mac")  # no Claude login of their own
    pick(api, "ops", "claude", sid)
    pick(api, "cpo", "claude", sid)
    post(api, "chat/ops", {"text": "Bug?"})
    turn = claim(api, m)
    refused(api, m, turn)
    sub_login = login_holds.subscription_login(sid)
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, "", sub_login, "claude")
        # The hold covers the plan that both agents selected and leaves their failed-run counters alone.
        assert hold and login_holds.payment(hold["error"])
        assert hold["until"] == H.shift(hold["since"], seconds=login_holds.PAYMENT_WAIT)
        assert run_failures.get(c, "ops") == {}
        # The person's message waits for the plan, not marked failed.
        assert c.execute("SELECT state FROM jobs WHERE id=?", (turn["job_id"],)).fetchone()[0] == "queued"
        focus = H.status(c, "ops")["focus"]
    assert focus.startswith("The Claude plan brooke@acme.example can't be used: payment past due.")
    assert login_holds.BILLING_URL in focus
    assert plan(api)["problem"] == "Payment past due"
    post(api, "chat/cpo", {"text": "Price the Acme plan."})
    other = post(api, "jobs/claim", {"bot": "cpo", "start_places": True}, n["token"])
    assert other["attempt"] is None and "payment past due" in other["waiting"]
    [item] = items(api)
    assert item["title"] == "Pay for the Claude plan brooke@acme.example" and item["status"] == "open"
    assert login_holds.BILLING_URL in item["body"]

    # Still unpaid at the next try: the same one item, a new wait.
    later = H.shift(H.now(), seconds=login_holds.PAYMENT_WAIT + 1)
    at(monkeypatch, later, api)
    again = claim(api, m)
    assert again and again["job_id"] == turn["job_id"] and again["subscriptions"][0]["id"] == sid
    refused(api, m, again)
    assert len(items(api)) == 1
    with api.app.state.store.read() as c:
        assert run_failures.get(c, "ops") == {}

    # Paid: the next try works, and the plan and every bot on it go again.
    paid = H.shift(later, seconds=login_holds.PAYMENT_WAIT + 1)
    at(monkeypatch, paid, api)
    probe = claim(api, m)
    assert probe and probe["job_id"] == turn["job_id"]
    worked(api, m, probe)
    with api.app.state.store.read() as c:
        assert not login_holds.get(c, "", sub_login, "claude")
    assert plan(api)["problem"] == ""
    assert items(api)[0]["status"] != "open"
    other = claim(api, n)
    assert other and other["bot"] == "cpo" and other["subscriptions"][0]["id"] == sid


def test_only_a_billing_refusal_reads_as_a_payment_past_due():
    """`payment` is only the billing refusal: a usage limit, a busy model and a sign-in are not."""
    assert login_holds.payment(PAST_DUE)
    assert run_failures.is_limit(PAST_DUE)
    assert login_holds.window(PAST_DUE) == login_holds.PAYMENT_KIND
    for other in (
        "You've hit your session limit · resets 2:40am (UTC)",
        "Selected model is at capacity. Please try a different model.",
        "OAuth token has expired",
    ):
        assert not login_holds.payment(other)
    assert (
        login_holds.failure_line({"limit_source": {"runtime": "claude", "kind": login_holds.PAYMENT_KIND}})
        == "failed: payment past due (Claude, primary)"
    )


def past_due_plan(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, name="brooke@acme.example")["id"]
    m = machine(api)
    pick(api, "ops", "claude", sid)
    post(api, "chat/ops", {"text": "Bug?"})
    return sid, m, claim(api, m)


def test_a_refusal_said_only_in_the_turns_summary_is_still_the_plans_payment(api):
    sid, m, turn = past_due_plan(api)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-summary"}, m["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "failed", "last_seq": 0, "text": PAST_DUE}, m["token"])
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, "", login_holds.subscription_login(sid), "claude")
        assert login_holds.payment(hold["error"]) and run_failures.get(c, "ops") == {}
        assert H.status(c, "ops")["focus"].startswith("The Claude plan brooke@acme.example can't be used")
    assert len(items(api)) == 1


def test_removing_a_plan_past_due_closes_its_item(api):
    sid, m, turn = past_due_plan(api)
    refused(api, m, turn)
    assert items(api)[0]["status"] == "open"
    api.delete(f"/api/v2/model-subscriptions/{sid}?confirm=1", headers=headers()).raise_for_status()
    with api.app.state.store.read() as c:
        assert not login_holds.get(c, "", login_holds.subscription_login(sid), "claude")
    assert items(api)[0]["status"] != "open"
