"""Health when Tico stops cloud machines because the company is out of credit (task #423): one Out of credit card
with Add credit, never restart advice, and a machine that drops on its own keeps its own one-line card."""

from backend import cloud_access
from backend.store import H
from backend.tests.helpers.cloud_access import BILLING_URL, signals
from backend.tests.helpers.getting_started import SIGNED_IN, add_bot, enrolled, heartbeat  # noqa: F401
from backend.tests.helpers.health import health_of
from backend.tests.helpers.onboarding import as_person, environment, signed_in  # noqa: F401
from backend.enqueue import enqueue_stored


def silent_computer(api, seconds_ago):
    """A computer enrolled for riley, holding one queued request for its bot, last heard from `seconds_ago`."""
    runner = enrolled(api)
    add_bot(api, "helper")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET owner_id='riley' WHERE id=?", (runner,))
        c.execute(
            "INSERT INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES('helper',?,1,?,'t')",
            (runner, H.now()),
        )
        # A chat message in a conversation, as the product writes them: it queues its job, and Pipeline counts it.
        c.execute("INSERT INTO conversations(id,kind,participants,created_at) VALUES('cv1','chat','[]',?)", (H.now(),))
        c.execute(
            "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at) "
            "VALUES('m1','cv1','human:ana','bot:helper','chat','hi',?)",
            (H.now(),),
        )
        enqueue_stored(c, 'm1')
    heartbeat(api, runner, seconds_ago=seconds_ago, runtimes=SIGNED_IN)
    return runner


def freeze(api, access="frozen"):
    cloud_access.apply(
        api.app.state.store, cloud_access.parse(signals(access, None if access == "open" else "out_of_credit"))
    )


def stopped_for_credit(api, runner, bot="helper"):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO cloud_machines(bot,runner_id,state,problem,updated_at) VALUES(?,?,'stopped','out_of_credit',?)",
            (bot, runner, H.now()),
        )


def second_silent(api, label, bot, seconds_ago=1900):
    """Another computer, `label`, holding one queued request for `bot`, silent for `seconds_ago`."""
    with api.app.state.store.transaction() as c:
        owner = c.execute("SELECT owner_id,created_at FROM runners LIMIT 1").fetchone()
        rid = "r-" + bot
        c.execute(
            "INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES(?,?,?,?,?)",
            (rid, label, owner["owner_id"], "h-" + bot, owner["created_at"]),
        )
        c.execute(
            "UPDATE runner_states SET last_seen_at=? WHERE runner_id=?", (H.shift(H.now(), seconds=-seconds_ago), rid)
        )
    add_bot(api, bot)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES(?,?,1,?,'t')",
            (bot, rid, H.now()),
        )
        c.execute(
            "INSERT INTO conversations(id,kind,participants,created_at) VALUES(?,'chat','[]',?)", ("cv-" + bot, H.now())
        )
        c.execute(
            "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at) "
            "VALUES(?,?,'human:ana',?,'chat','hi',?)",
            ("m-" + bot, "cv-" + bot, "bot:" + bot, H.now()),
        )
        enqueue_stored(c, "m-" + bot)
    return rid


def issues(api, headers):
    return api.get("/api/status", headers=headers).json()["health_issues"]


def test_one_out_of_credit_card_for_every_frozen_machine_and_no_restart_advice(environment):
    api = environment()
    first = silent_computer(api, 1900)
    other = second_silent(api, "Builder (cloud)", "builder")
    stopped_for_credit(api, first)
    stopped_for_credit(api, other, "builder")
    freeze(api)
    owner = signed_in()
    found = issues(api, owner)
    [card] = [i for i in found if i["kind"] == "cloud_credit"]
    assert card["title"] == "Out of credit" and card["detail"] == "2 cloud machines stopped · 2 bots waiting"
    assert card["needs_person"] and sorted(card["machines"]) == sorted([first, other])
    assert "Builder (cloud)" in card["more"] and "out of credit" in card["more"] and "saved request" in card["more"]
    # No machine card, and nothing tells the owner to restart a machine Tico stopped for credit.
    assert not [i for i in found if i["kind"] == "machine"]
    assert not any("restart" in str(i.get("action") or "") for i in found)
    # Health's lines say the cause, with Add credit for the owner.
    body, checks = health_of(api)
    assert checks["computers"]["summary"] == "Stopped: out of credit."
    assert checks["waiting"]["summary"] == "2 bots waiting: out of credit."
    assert {"label": "Add credit", "href": BILLING_URL, "tab": "", "click": ""} in checks["computers"]["fixes"]
    assert "checked in" not in checks["waiting"]["summary"]


def test_a_machine_that_drops_on_its_own_keeps_its_one_line_card_beside_the_credit_card(environment):
    api = environment()
    first = silent_computer(api, 1900)
    second_silent(api, "Builder (cloud)", "builder")
    stopped_for_credit(api, first)
    freeze(api)
    found = issues(api, signed_in())
    assert [i["title"] for i in found if i["kind"] == "cloud_credit"] == ["Out of credit"]
    [down] = [i for i in found if i["kind"] == "machine"]
    assert down["title"] == "Builder (cloud) offline" and down["detail"].startswith("1 bot waiting · ")
    assert "tico-computer restart" in down["more"] and "tico-computer restart" in down["action"]
    body, checks = health_of(api)
    assert "Stopped, out of credit: Owner Mac." in checks["computers"]["summary"]
    assert "Offline: Builder (cloud)." in checks["computers"]["summary"]
    assert checks["waiting"]["summary"].startswith("1 bot waiting: out of credit.")
    assert "Builder (cloud) has not checked in" in checks["waiting"]["summary"]
    # One naming rule: a member reads both lists of computer names, as main's offline line showed them.
    member = health_of(api, as_person(api, "quinn"))[1]["computers"]["summary"]
    assert "Offline: Builder (cloud)." in member and "Stopped, out of credit: Owner Mac." in member


def test_without_a_known_freeze_a_stopped_machine_is_never_called_out_of_credit(environment):
    api = environment()
    runner = silent_computer(api, 1900)
    stopped_for_credit(api, runner)  # a stale problem: HQ says access is open again, or has not said
    for access in (None, "open"):
        if access:
            freeze(api, access)
        found = issues(api, signed_in())
        assert not [i for i in found if i["kind"] == "cloud_credit"]
        [down] = [i for i in found if i["kind"] == "machine"]
        assert down["title"] == "Owner Mac offline" and "tico-computer restart" in down["more"]
        assert "out of credit" not in health_of(api)[1]["computers"]["summary"]


def test_once_credit_is_added_the_card_is_gone(environment):
    api = environment()
    runner = silent_computer(api, 1900)
    stopped_for_credit(api, runner)
    freeze(api)
    assert [i for i in issues(api, signed_in()) if i["kind"] == "cloud_credit"]
    freeze(api, "open")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE cloud_machines SET problem='' WHERE runner_id=?", (runner,))
    assert not [i for i in issues(api, signed_in()) if i["kind"] == "cloud_credit"]


def test_members_see_the_cause_but_only_owners_are_asked_and_offered_add_credit(environment):
    api = environment()
    runner = silent_computer(api, 1900)
    stopped_for_credit(api, runner)
    freeze(api)
    [card] = [i for i in issues(api, as_person(api, "quinn")) if i["kind"] == "cloud_credit"]
    assert not card["needs_person"]


def test_agents_read_one_cloud_credit_item_with_the_full_text(environment):
    api = environment()
    first = silent_computer(api, 1900)
    other = second_silent(api, "Builder (cloud)", "builder")
    stopped_for_credit(api, first)
    stopped_for_credit(api, other, "builder")
    freeze(api)
    found = api.get("/api/v2/health/issues", headers=signed_in()).json()
    found = found.get("issues", found) if isinstance(found, dict) else found
    credit = [i for i in found if i["kind"] == "cloud_credit"]
    assert len(credit) == 1 and "Owner Mac" in credit[0]["text"] and "Builder (cloud)" in credit[0]["text"]
    assert "Add credit: " in credit[0]["fix"]


def test_the_computers_aggregate_names_the_credit_stopped_computers_it_lists(environment):
    """Its text lists the computers stopped for credit too, so its membership does: a stuck update on one of them then
    clears the aggregate instead of leaving text that names it (check_health `apply`)."""
    from backend.maintenance_steps import check_health

    api = environment()
    first = silent_computer(api, 1900)
    second_silent(api, "Builder (cloud)", "builder")
    stopped_for_credit(api, first)
    freeze(api)
    state, notes = api.app.state, {}
    with state.store.read_transaction() as c:
        check_health._page_findings(c, check_health.context(state.auth, state.store.settings), notes)
    # Builder is silent: its own computer_silent names it. Owner Mac is in the text as stopped for credit, so named too.
    assert notes["offline"]["named"]["page:computers"] == [first]
