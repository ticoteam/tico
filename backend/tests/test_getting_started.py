"""Getting started: every tick is computed from live state, and each person keeps their own choices."""

from backend.tests.helpers.onboarding import as_person, environment, signed_in
from backend.tests.helpers import model_subscriptions as subs
from backend.tests.helpers.api import headers as bearer, post
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.getting_started import add_bot

OWNER = "ana-test"  # the fictional company's owner (backend/tests/helpers/api.py)


def items(api, headers=None):
    body = api.get("/api/v2/getting-started", headers=headers or signed_in()).json()
    return body, {row["id"]: row for row in body["items"]}


def body_of(api):
    return api.get("/api/v2/getting-started", headers=signed_in()).json()


def activate(api, *slugs):
    with api.app.state.store.transaction() as c:
        for slug in slugs:
            c.execute("UPDATE bots SET state='active' WHERE slug=?", (slug,))


def test_each_person_sees_only_what_they_can_act_on(environment):
    api = environment()
    riley = as_person(api, "riley")  # a bot administrator
    quinn = as_person(api, "quinn")  # neither
    assert [r["id"] for r in items(api)[0]["items"]] == [
        "signed_in",
        "computer",
        "model",
        "subscription",
        "github",
        "botops",
        "first_bot",
        "next_bot",
        "first_output",
        "first_update",
        "strategy",
    ]
    assert [r["id"] for r in items(api, riley)[0]["items"]] == [
        "signed_in",
        "botops",
        "first_bot",
        "next_bot",
        "first_output",
        "first_update",
    ]
    assert [r["id"] for r in items(api, quinn)[0]["items"]] == ["signed_in", "first_output", "first_update"]
    assert api.get("/api/v2/getting-started").status_code in (401, 403)


def test_publish_your_strategy_is_ticked_by_the_first_published_version(environment):
    api, owner = environment(), signed_in()
    step = items(api)[1]["strategy"]
    assert (step["label"], step["done"], step["href"]) == ("Publish your strategy", False, "#/strategy")
    revision = api.get("/api/v2/strategy", headers=owner).json()["draft"]["revision"]
    assert (
        api.put(
            "/api/v2/strategy/draft",
            headers=owner,
            json={"revision": revision, "sections": {"not": "No enterprise deals."}},
        ).status_code
        == 200
    )
    assert (
        api.post(
            "/api/v2/strategy/bets",
            headers=owner,
            json={
                "statement": "Small landlords pay for bots",
                "rules_out": "Enterprise sales",
                "status": "active",
                "wrong_if": {"kind": "text", "text": "Three landlords say no", "check_by": "2099-01-01"},
            },
        ).status_code
        == 200
    )
    assert items(api)[1]["strategy"]["done"] is False  # a draft is not a strategy yet
    revision = api.get("/api/v2/strategy", headers=owner).json()["draft"]["revision"]
    assert (
        api.post("/api/v2/strategy/publish", headers=owner, json={"note": "First", "revision": revision}).status_code
        == 200
    )
    step = items(api)[1]["strategy"]
    assert step["done"] is True and step["why"] == ""


def test_the_market_box_is_a_task_for_the_librarian(environment):
    api = environment()
    ask = {"text": "https://northwind.example\nOffice cleaning for property managers."}
    # The Librarian must be running: a task for a bot that is not would wait for nothing.
    refused = api.post("/api/v2/getting-started/market", json=ask, headers=signed_in())
    assert refused.status_code == 409
    add_bot(api, "librarian")
    made = api.post("/api/v2/getting-started/market", json=ask, headers=signed_in())
    assert made.status_code == 200, made.text
    assert made.json()["bot"] == "librarian"
    task = api.get("/api/v2/tasks", params={"owner": "librarian"}, headers=signed_in()).json()["tasks"][0]
    assert task["id"] == made.json()["task_id"] and "https://northwind.example" in task["body"]
    # Only the owner asks, and the box may not be empty.
    assert api.post("/api/v2/getting-started/market", json=ask, headers=as_person(api, "quinn")).status_code == 403


def plan_step(api):
    return items(api, bearer(OWNER))[1]["subscription"]


def report_claude(api, m, claude):
    """A computer's word on its Claude login, as its heartbeat sends it."""
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": [subs.MS.CAPABILITY],
            "readiness": {
                "schema_version": 1,
                "runtimes": {"claude": claude, "codex": {"installed": True, "authenticated": "missing"}},
                "bots": {},
            },
        },
        token=m["token"],
    )


def test_a_plan_signed_in_on_a_computer_ticks_the_step_and_signing_out_unticks_it(api):
    m = subs.machine(api)
    report_claude(api, m, {"installed": True, "authenticated": "ready"})
    assert plan_step(api)["done"] is True and plan_step(api)["why"] == ""
    report_claude(api, m, {"installed": True, "authenticated": "missing"})
    assert plan_step(api)["done"] is False


def test_an_api_key_on_a_computer_is_not_a_subscription(api):
    m = subs.machine(api)
    report_claude(api, m, {"installed": True, "authenticated": "ready", "account": {"kind": "api_key"}})
    assert plan_step(api)["done"] is False


def test_a_company_api_key_given_to_a_computer_does_not_count_but_a_company_plan_does(api):
    setup(api)
    subs.company(api)
    m = subs.machine(api)
    key = subs.add(api, "anthropic_key", "sk-ant-api-" + "c" * 24, name="Company API")["id"]
    subs.give(api, m, "claude", key)  # billed per use, not a subscription
    assert plan_step(api)["done"] is False
    plan = subs.add(api, name="Company Max")["id"]
    subs.give(api, m, "claude", plan)
    assert plan_step(api)["done"] is True


def test_the_step_never_blocks_setup_and_can_be_set_aside(api):
    before = items(api, bearer(OWNER))[0]["done"]
    post(api, "getting-started/state", {"skip": "subscription"}, token=OWNER)
    body, _ = items(api, bearer(OWNER))
    assert body["done"] == before + 1  # a skipped step is settled, like the GitHub one
    assert plan_step(api)["skipped"] is True and plan_step(api)["done"] is False
    post(api, "getting-started/state", {"skip": "model"}, token=OWNER, expected=422)  # required steps are not skippable


def test_on_tico_cloud_a_plan_or_key_is_required_and_a_saved_one_counts(api):
    from backend import cloud_access
    from backend.tests.helpers.cloud_access import signals

    cloud_access.apply(api.app.state.store, cloud_access.parse(signals("open")))
    setup(api)
    subs.company(api)
    row = plan_step(api)
    assert (row["done"], row["optional"]) == (False, False)
    post(api, "getting-started/state", {"skip": "subscription"}, token=OWNER)
    assert plan_step(api)["skipped"] is False  # not set aside on Tico Cloud
    subs.add(api, "anthropic_key", "sk-ant-api-" + "c" * 24, name="Company API")  # on no computer yet
    assert plan_step(api)["done"] is True
