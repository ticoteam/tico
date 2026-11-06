"""The Assistant's daily sweep on the Automations page: a routine the Assistant gets on an install is an automation at once
(redesign_migration.adopt), so the owner lists it with its controls, a person who is not above the Assistant does not
see it, and Run now creates a run. Same rules as every bot's routine (backend/automations.py may_change)."""

from backend.tests.test_builtin_routines import COO, _ensure, _row, _write_assistant_routines  # noqa: F401  (helpers)
from backend.tests.test_onboarding import TOKEN, as_person, environment, signed_in  # noqa: F401  (the fixture)

SWEEP_TITLE = "Daily sweep of each person's queue"


def _installed(environment):
    """An install whose Assistant has its template routines, started as a fresh boot starts it: returns the client and
    the automation the sweep moved to."""
    api = environment(seed=COO)
    _write_assistant_routines(api)
    assert _ensure(api) == [("coo", "daily-sweep")]
    return api, _row(api)["automation"]


def _call(api, method, path, token=TOKEN, body=None, expected=200):
    r = api.request(method, "/api/v2/" + path, json=body, headers=signed_in(token))
    assert r.status_code == expected, r.text
    return r.json()


def _listed_ids(api, token=TOKEN):
    return [a["id"] for a in _call(api, "GET", "automations", token)["automations"]]


def test_the_sweep_is_listed_edited_paused_run_by_the_owner_and_hidden_from_quinn(environment):
    api, aid = _installed(environment)
    # The owner lists the sweep with its controls.
    assert aid.startswith("auto_")
    listed = next(a for a in _call(api, "GET", "automations")["automations"] if a["id"] == aid)
    assert listed["name"] == SWEEP_TITLE and listed["owner_id"] == "bot:coo" and listed["enabled"] is True
    assert listed["trigger"]["type"] == "schedule" and listed["trigger"]["cron"] == "0 8 * * *"
    assert listed["next_run"] and listed["can_edit"] is True and listed["can_pause"] is True
    shown = _call(api, "GET", f"automations/{aid}")
    assert shown["steps"][0]["type"] == "bot_turn" and shown["steps"][0]["bot"] == "bot:coo"
    assert shown["can_edit"] is True and shown["can_pause"] is True

    # A person who is not above the Assistant does not see the sweep, and changes nothing about it.
    as_person(api, "quinn")                    # registers the identity; its token is "quinn"
    quinn = "quinn"
    assert aid not in _listed_ids(api, quinn)
    assert _call(api, "GET", f"automations/{aid}", quinn, expected=403)
    assert _call(api, "PATCH", f"automations/{aid}", quinn, {"enabled": False}, expected=403)
    assert _call(api, "POST", f"automations/{aid}/run", quinn, {}, expected=403)
    assert _call(api, "GET", f"automations/{aid}/runs", quinn, expected=403)
    still = _call(api, "GET", f"automations/{aid}")
    assert still["enabled"] is True and still["version"] == 1

    # The owner changes its schedule and switch.
    edited = _call(api, "PATCH", f"automations/{aid}",
                   body={"trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}})
    assert edited["version"] == 2 and edited["trigger"]["cron"] == "0 9 * * *"
    paused = _call(api, "PATCH", f"automations/{aid}", body={"enabled": False})
    assert paused["enabled"] is False and paused["version"] == 2     # a pause is not a new version
    resumed = _call(api, "PATCH", f"automations/{aid}", body={"enabled": True})
    assert resumed["enabled"] is True

    # Run now creates a run.
    run = _call(api, "POST", f"automations/{aid}/run", body={})["run"]
    assert run["id"]
    runs = _call(api, "GET", f"automations/{aid}/runs")["runs"]
    assert [r["id"] for r in runs] == [run["id"]]
    assert _call(api, "GET", f"automations/{aid}")["last_run"]["id"] == run["id"]
