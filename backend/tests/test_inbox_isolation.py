"""An inbox bot gets a computer to itself: assignment is refused both ways, and Health warns about
installs that already mix them."""

from backend.store import encode
from backend.tests.test_getting_started import add_bot, enrolled, SIGNED_IN, heartbeat  # noqa: F401
from backend.tests.test_onboarding import PEOPLE, environment, machine, signed_in  # noqa: F401


def setup(api):
    """Riley's inbox bot `mail` and an ordinary bot `helper`, and one computer."""
    add_bot(api, "mail")
    add_bot(api, "helper")
    add_bot(api, "mail2")
    people = {**PEOPLE, "people": [{**p, **({"inbox_bot": "mail"} if p["id"] == "riley" else
                                             {"inbox_bot": "mail2"} if p["id"] == "quinn" else {})}
                                   for p in PEOPLE["people"]]}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(people),))
        for slug in ("mail", "helper", "mail2"):
            c.execute("INSERT OR IGNORE INTO bot_config(bot,config_json,operator,repo) VALUES(?,?,?,?)",
                      (slug, "{}", "morgan", "emp-" + slug))
    return enrolled(api)


def place(api, bot, runner, generation=0):
    return api.post(f"/api/v2/bots/{bot}/assignment", json={"runner_id": runner, "expected_generation": generation},
                    headers=signed_in())


def test_an_inbox_bot_and_another_bot_never_share_a_computer(environment):
    api = environment()
    runner = setup(api)
    assert place(api, "helper", runner).status_code == 200
    refused = place(api, "mail", runner)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "inbox_isolation"
    assert "Add a computer" in refused.json()["error"]["detail"]
    other = machine(api, "Mail Mac")["runner_id"]
    assert place(api, "mail", other).status_code == 200
    again = place(api, "helper", other, 1)
    assert again.status_code == 409 and again.json()["error"]["code"] == "inbox_isolation"
