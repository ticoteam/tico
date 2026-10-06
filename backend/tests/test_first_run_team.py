"""First run builds a team: what the org builder saves, and a starter bot's life from Create to onboarded.

Creating uses the real support, Chief of Staff and Issue Triage templates.
"""

import json
import shutil
from pathlib import Path


from backend import releases
from backend.tests.test_api import claim, headers, post, ready
from backend.tests.test_onboarding import (ASSISTANT_AGENT, ASSISTANT_CARD, BOTOPS_CARD, as_person, draft,  # noqa: F401
                                           environment, machine, signed_in)

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"


def real_starters(api, *names):
    """The support and Chief of Staff templates as shipped: their card, playbooks and paused routine."""
    for name in names:
        shutil.copytree(CATALOG / name, Path(api.app.state.store.settings.catalog_dir) / name, dirs_exist_ok=True)


def test_a_starter_is_created_parked_and_leaves_that_state_only_when_it_says_its_setup_is_done(environment):
    """Create, parked, woken only by a person, onboarded by the bot itself; and the member limit ignores parked bots."""
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, "")])
    real_starters(api, "support", "chief-of-staff", "issue-triage")
    computer = machine(api)
    who = signed_in()
    selected = {"chief-of-staff": {"template": "chief-of-staff", "display_name": "Chief of Staff", "instructions": ""},
                "support": {"template": "support", "display_name": "Help desk", "instructions": "",
                            "reports_to": "chief-of-staff"}}
    assert draft(api, selected=selected).status_code == 200
    record = api.post("/api/v2/onboarding/complete", json={}, headers=who).json()
    rows = {row["slug"]: row for row in record["bots"]}
    assert rows["support"]["onboarding_state"] == rows["chief-of-staff"]["onboarding_state"] == "needs_setup"
    assert rows["botops"]["onboarding_state"] == rows["coo"]["onboarding_state"] == ""          # built in: they work at once
    assert rows["support"]["setup_task_id"] is None and rows["support"]["reports_to"] == "chief-of-staff"
    assert rows["chief-of-staff"]["reports_to"] == "human:morgan"                              # the owner, by default

    # Exposed with the template and its version; placed on the computer and active, its routine seeded off.
    listed = {b["slug"]: b for b in api.get("/api/v2/bots", headers=who).json()}
    assert listed["support"]["onboarding_state"] == "needs_setup" and listed["coo"]["onboarding_state"] == ""
    detail = api.get("/api/v2/bots/support", headers=who).json()
    assert (detail["template"], detail["template_version"], detail["state"]) == ("support", releases.version(), "active")
    assert api.get("/api/v2/tasks", params={"owner": "botops"}, headers=who).json()["tasks"] == []   # no BotOps task
    with api.app.state.store.read() as c:
        assert c.execute("SELECT enabled FROM schedule_config sc JOIN schedules s ON s.id=sc.schedule_id "
                         "WHERE s.bot='support'").fetchone()[0] == 0
        assert json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='support'").fetchone()[0])["materialize"] is True

    # Nothing wakes it but a person: a task notice waits, the person's own message is claimed.
    ready(api, computer, ["support"])
    api.post("/api/v2/tasks", json={"owner": "support", "title": "Look at the queue", "body": "x"}, headers=signed_in())
    assert claim(api, computer, "support") is None
    started = post(api, "chat/support", {"text": "Let's set you up."}, token="local-owner-secret-token-0123456789")
    with api.app.state.store.read() as c:            # starting the setup switched its first routine on: nobody approves it separately
        assert c.execute("SELECT enabled FROM schedule_config sc JOIN schedules s ON s.id=sc.schedule_id "
                         "WHERE s.bot='support'").fetchone()[0] == 1
    attempt = claim(api, computer, "support")
    assert attempt and attempt["bot"] == "support"
    assert attempt["onboarding"] == "needs_setup"           # the runner's prompt follows the template's flow

    # Start setup is a message in the person's own chat with the bot: the conversation the bot page's Chat tab lists.
    chats = api.get("/api/v2/conversations", params={"chat_with": "support"}, headers=who).json()["conversations"]
    assert [c["id"] for c in chats] == [started["conversation_id"]] == [attempt["conversation"]["id"]]
    assert chats[0]["scope"] == "personal" and chats[0]["kind"] == "chat"
    shown = api.get(f"/api/v2/conversations/{chats[0]['id']}/messages", headers=who).json()["messages"]
    assert "Let's set you up." in [m["body"] for m in shown]           # beside the task notice the room already held

    # Only the bot itself (or its manager) says it is onboarded, and saying it twice changes nothing.
    def call(token):
        return api.post("/api/v2/bots/support/onboarded", json={}, headers=headers(token))
    assert call(computer["token"]).status_code == 403                 # the computer is not the bot
    said = call(attempt["token"])                                      # the bot, in its own turn
    assert said.status_code == 200 and said.json() == {"bot": "support", "onboarding_state": "onboarded", "changed": True}
    assert call(attempt["token"]).json()["changed"] is False
    assert api.get("/api/v2/bots/support", headers=who).json()["onboarding_state"] == "onboarded"
    assert api.get("/api/v2/bots/chief-of-staff", headers=who).json()["onboarding_state"] == "needs_setup"

    # Parked bots do not count toward a member's limit until they are onboarded.
    real_starters(api, "issue-triage")
    assert api.put("/api/v2/access/limits", json={"member_bot_limit": 1}, headers=signed_in()).status_code == 200
    as_person(api, "quinn")
    model = api.get("/api/v2/models", headers=signed_in()).json()["default"]

    def quinn():
        return signed_in("quinn")

    def create(slug, template=""):
        return api.post("/api/v2/bots", headers=quinn(), json={
            "slug": slug, "display_name": slug, "model": model["model"], "effort": "medium", "template": template})

    for slug, template in (("help", "support"), ("closer", "issue-triage"), ("chief-two", "chief-of-staff")):
        assert create(slug, template).status_code == 200, slug           # parked: three of them, with a limit of one
    assert create("plain").status_code == 200                              # and the one that counts still fits
    over = create("plain-two")
    assert over.status_code == 409 and over.json()["error"]["code"] == "bot_limit"

    # Onboarding one of them makes it count, so it is refused while the limit is full.
    refused = api.post("/api/v2/bots/help/onboarded", json={}, headers=quinn())
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "bot_limit"
    plain = api.get("/api/v2/bots/plain", headers=quinn()).json()
    assert api.post("/api/v2/bots/plain/archive", headers=quinn(), json={"expected_revision": plain["revision"]}).status_code == 200
    assert api.post("/api/v2/bots/help/onboarded", json={}, headers=quinn()).status_code == 200
    assert api.post("/api/v2/bots/closer/onboarded", json={}, headers=quinn()).status_code == 409


def test_go_live_turns_the_first_routine_on_once_and_a_strangers_message_does_not(environment):
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, "")])
    real_starters(api, "support")
    machine(api)
    selected = {"support": {"template": "support", "display_name": "Help desk", "instructions": ""}}
    assert draft(api, selected=selected).status_code == 200
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())

    def enabled():
        with api.app.state.store.read() as c:
            return c.execute("SELECT enabled FROM schedule_config sc JOIN schedules s ON s.id=sc.schedule_id "
                             "WHERE s.bot='support'").fetchone()[0]
    as_person(api, "quinn")                                          # a member who does not manage it
    assert api.post("/api/v2/chat/support", json={"text": "hello"}, headers=signed_in("quinn")).status_code == 200
    assert enabled() == 0
    live = api.post("/api/v2/bots/support/go-live", json={}, headers=signed_in())
    assert live.status_code == 200 and live.json()["routine_armed"] and enabled() == 1
    with api.app.state.store.transaction() as c:                     # a person turns it off: going live again leaves it off
        c.execute("UPDATE schedule_config SET enabled=0 WHERE schedule_id LIKE 'support:%'")
    again = api.post("/api/v2/bots/support/go-live", json={}, headers=signed_in())
    assert again.status_code == 200 and enabled() == 0

