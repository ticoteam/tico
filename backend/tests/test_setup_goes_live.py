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


def test_the_apps_first_run_hands_the_setup_to_tico_once_it_runs_with_no_seat_live(environment):
    """No seat starts on a guess (first_job none). The owner's "Set up my company." is owed by the finish itself and
    said once the Assistant runs (here: when the first computer enrolls), once: a reload, a retried finish or a restart
    cannot lose it or say it twice."""
    api = environment()
    saved = api.put("/api/v2/setup", json={"answers": {"first_job": "none"}}, headers=signed_in())
    assert saved.status_code == 200, saved.text
    done = finish(api, {"hand_off": True})
    assert not {r["slug"] for r in rows(api, "SELECT slug FROM bots")} & JOB_SEATS
    assert not rows(api, "SELECT 1 FROM tasks WHERE title LIKE 'Set up %' AND title NOT LIKE 'Set up the market%'")
    # No computer yet: the Assistant is not running, so it is kept, not refused or lost.
    assert done["hand_off_owed"] is True and not hand_offs(api)
    finish(api, {"hand_off": True})
    machine(api)
    said = hand_offs(api)
    assert len(said) == 1 and said[0]["sender_id"].startswith("human:") and said[0]["recipient_id"].startswith("bot:")
    assert api.get("/api/v2/setup", headers=signed_in()).json()["hand_off_owed"] is False
    finish(api, {"hand_off": True})
    machine(api)
    assert len(hand_offs(api)) == 1
