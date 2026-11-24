"""A company a setup file made still owes its owner the first run in the app (connect a plan, go live), once; one set up
in the app hands the rest to Tico in the finish itself."""

from backend import company_file as C
from backend.tests.helpers.fresh_install import booted, environment, machine, rows, signed_in  # noqa: F401


def imported(api):
    draft = C.Turn(C.blank()).finish()
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    draft["bots"] = [b for b in draft["bots"] if b["template"] in ("assistant", "agent-ops", "controller")]
    body = {"company_yaml": C.to_yaml(draft)}
    preview = api.post("/api/v2/setup/company/preview", json=body, headers=signed_in())
    assert preview.status_code == 200, preview.text
    body["fingerprint"] = preview.json()["fingerprint"]
    done = api.post("/api/v2/setup/company/import", json=body, headers=signed_in())
    assert done.status_code == 200, done.text
    return done.json()


# The default chart's roles a first job could make live (tests/test_company_import.py LIVE_DEFAULT).
JOB_SEATS = {"product-manager", "feature-owner", "growth-manager", "success-manager", "legal-owner", "qa-owner"}


def finish(api, body):
    done = api.post("/api/v2/setup/complete", json=body, headers=signed_in())
    assert done.status_code == 200, done.text
    return done.json()


def hand_offs(api):
    return rows(
        api,
        "SELECT m.sender_id,m.recipient_id FROM messages m JOIN conversations v ON v.id=m.conversation_id "
        "WHERE m.body='Set up my company.' AND v.scope='personal'",
    )


def owed(api):
    return api.get("/api/v2/config", headers=signed_in()).json()["onboarding_needed"]


def test_a_company_a_file_made_owes_the_first_run_until_the_owner_finishes_it_and_its_team_stays(environment):
    api = environment()
    record = imported(api)
    assert record["completed"] and record["live_owed"] and record["needed"]
    assert owed(api) is True
    team = (rows(api, "SELECT id,state FROM bots ORDER BY id"), rows(api, "SELECT id FROM seats ORDER BY id"))
    done = api.post("/api/v2/setup/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    assert done.json()["live_owed"] is False and done.json()["needed"] is False
    assert owed(api) is False
    # Going live lays nothing out again: the file's team is as it was.
    assert (rows(api, "SELECT id,state FROM bots ORDER BY id"), rows(api, "SELECT id FROM seats ORDER BY id")) == team
    assert rows(api, "SELECT action FROM events WHERE action='onboarding.live'")


def test_a_company_set_up_in_the_app_never_owes_it_again(environment):
    api = environment()
    assert owed(api) is True
    assert api.post("/api/v2/setup/complete", json={}, headers=signed_in()).status_code == 200
    assert owed(api) is False
    assert api.get("/api/v2/setup", headers=signed_in()).json()["live_owed"] is False
    assert not hand_offs(api)  # a finish without hand_off (the CLI's) asks Tico nothing


def test_a_finish_again_on_a_live_file_made_company_changes_nothing(environment):
    """A retried finish (its answer lost) on a company a file made returns the same record and lays out nothing."""
    api = environment()
    imported(api)
    first = finish(api, {})
    team = rows(api, "SELECT bot,revision,reports_to,definition_updated_at FROM bot_config ORDER BY bot")
    again = finish(api, {})
    assert again["completed"] == first["completed"] and again["selected"] == first["selected"]
    assert rows(api, "SELECT bot,revision,reports_to,definition_updated_at FROM bot_config ORDER BY bot") == team
    assert len(rows(api, "SELECT 1 FROM events WHERE action='onboarding.live'")) == 1


def connect_a_plan(api):
    body = {"kind": "anthropic_key", "secret": "sk-ant-api03-" + "a" * 24, "name": "Company key"}
    added = api.post("/api/v2/model-subscriptions", json=body, headers=signed_in())
    assert added.status_code == 200, added.text


def owes(api):
    return api.get("/api/v2/setup", headers=signed_in()).json()["hand_off_owed"]


def first_run_finished(api):
    saved = api.put("/api/v2/setup", json={"answers": {"first_job": "none"}}, headers=signed_in())
    assert saved.status_code == 200, saved.text
    return finish(api, {"hand_off": True})


def test_the_apps_first_run_starts_no_seat_and_waits_for_a_plan_before_it_asks_tico(environment):
    """No seat starts on a guess (first_job none). The finish owes the owner's "Set up my company.": with the Assistant
    running but no plan connected nothing is said (no turn could run, and never on Gateway); connecting one says it once,
    and a retried finish or a second computer does not say it again."""
    api = environment()
    machine(api)  # a computer first, so the Assistant runs from the finish
    done = first_run_finished(api)
    assert not {r["slug"] for r in rows(api, "SELECT slug FROM bots")} & JOB_SEATS
    assert not rows(api, "SELECT 1 FROM tasks WHERE title LIKE 'Set up %' AND title NOT LIKE 'Set up the market%'")
    assert rows(api, "SELECT state FROM bots WHERE slug='assistant'") == [{"state": "active"}]
    assert done["hand_off_owed"] is True and not hand_offs(api)
    connect_a_plan(api)
    said = hand_offs(api)
    assert len(said) == 1 and said[0]["sender_id"].startswith("human:") and said[0]["recipient_id"].startswith("bot:")
    assert owes(api) is False
    finish(api, {"hand_off": True})
    machine(api)
    connect_a_plan(api)
    assert len(hand_offs(api)) == 1 and owes(api) is False


def test_an_enrollment_with_the_assistant_running_and_the_hand_off_owed_says_it_once_and_it_stays_said(environment):
    """Finished with no computer, a plan connected while the Assistant could not run, the Assistant then turned on by
    hand: the computer's enrollment says it once, and the record it saves afterwards does not owe it again."""
    api = environment()
    first_run_finished(api)
    connect_a_plan(api)
    assert not hand_offs(api) and owes(api) is True  # the Assistant is not running yet
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug='assistant'")
    machine(api)
    assert len(hand_offs(api)) == 1 and owes(api) is False
    machine(api)
    assert len(hand_offs(api)) == 1 and owes(api) is False
