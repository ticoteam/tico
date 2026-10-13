"""An inbox bot gets a computer to itself: assignment is refused both ways, and Health warns about
installs that already mix them."""

import uuid

from backend.store import encode
from backend.tests.test_getting_started import add_bot, enrolled, SIGNED_IN, heartbeat  # noqa: F401
from backend.tests.test_onboarding import (PEOPLE, ASSISTANT_AGENT, ASSISTANT_CARD, BOTOPS_CARD, LIBRARIAN_CARD,  # noqa: F401
                                           environment, machine, signed_in)


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


def test_turning_on_a_helper_skips_the_computer_an_inbox_bot_keeps(environment):
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, ""), (LIBRARIAN_CARD, "# L\n")])
    first = setup(api)
    assert place(api, "mail", first).status_code == 200
    turn_on = lambda: api.post("/api/v2/librarian/turn-on", json={},
                               headers={**signed_in(), "Idempotency-Key": str(uuid.uuid4())})
    refused = turn_on()
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "inbox_isolation"   # no other computer
    code = api.post("/api/v2/enrollments", json={"operator": "quinn"}, headers=signed_in()).json()["code"]
    quinns = api.post("/api/v2/runners/enroll", json={"code": code, "label": "Quinn's Mac", "platform": "test"},
                      headers={"Idempotency-Key": str(uuid.uuid4())})                # a member's: not for the owner's bots
    assert quinns.status_code == 200, quinns.text
    other = machine(api, "Spare Mac")["runner_id"]
    turned = turn_on()
    assert turned.status_code == 200 and turned.json()["placed"], turned.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT runner_id FROM assignments WHERE bot='librarian'").fetchone()[0] == other
