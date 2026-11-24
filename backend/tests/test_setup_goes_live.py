"""A company a setup file made still owes its owner the first run in the app (connect a plan, go live), once."""

from backend import company_file as C
from backend.tests.helpers.fresh_install import booted, environment, rows, signed_in  # noqa: F401


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
