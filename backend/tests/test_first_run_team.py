"""First run builds a team: what the org builder saves, and a starter bot's life from Create to onboarded.

Creating uses the real support, Chief of Staff and Issue Triage templates.
"""

import json
import shutil
from pathlib import Path


from backend import releases, routines
from backend.tests.test_api import claim, headers, post, ready
from backend.tests.test_onboarding import (ASSISTANT_AGENT, ASSISTANT_CARD, BOTOPS_CARD, as_person, build_team,  # noqa: F401
                                           draft, environment, machine, signed_in)

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"


def real_starters(api, *names):
    """The support and Chief of Staff templates as shipped: their card, playbooks and paused routine."""
    for name in names:
        shutil.copytree(CATALOG / name, Path(api.app.state.store.settings.catalog_dir) / name, dirs_exist_ok=True)


def support_routine(api):
    """The support bot's first routine as it runs now: its automation (backend/routines.py `row`)."""
    with api.app.state.store.read() as c:
        sid = c.execute("SELECT id FROM schedules WHERE bot='support'").fetchone()[0]
        return routines.row(c, sid)


def enabled(api):
    return support_routine(api)["enabled"]


def test_a_starter_is_created_onboarding_and_leaves_it_only_when_a_person_above_marks_it_complete(environment):
    """Create, parked, woken only by a person, ready by the bot itself and completed by a person above it; and the member
    limit ignores parked bots."""
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, "")])
    real_starters(api, "support", "chief-of-staff", "issue-triage")
    computer = machine(api)
    who = signed_in()
    selected = {"chief-of-staff": {"template": "chief-of-staff", "display_name": "Chief of Staff", "instructions": ""},
                "support": {"template": "support", "display_name": "Help desk", "instructions": "",
                            "reports_to": "chief-of-staff"}}
    assert draft(api, selected=selected).status_code == 200
    record = build_team(api)          # what picking a candidate for each seat makes (bot-lifecycle-spec.md)
    rows = {row["slug"]: row for row in record["bots"]}
    assert rows["support"]["onboarding_state"] == rows["chief-of-staff"]["onboarding_state"] == "needs_setup"
    assert rows["botops"]["onboarding_state"] == rows["coo"]["onboarding_state"] == ""          # built in: they work at once
    assert rows["support"]["setup_task_id"] is None and rows["support"]["reports_to"] == rows["chief-of-staff"]["id"]   # stored by id
    assert rows["chief-of-staff"]["reports_to"] == "human:morgan"                              # the owner, by default

    # Exposed with the template and its version; placed on the computer and active, its routine seeded off.
    listed = {b["slug"]: b for b in api.get("/api/v2/bots", headers=who).json()}
    assert listed["support"]["onboarding_state"] == "needs_setup" and listed["coo"]["onboarding_state"] == ""
    detail = api.get("/api/v2/bots/support", headers=who).json()
    assert (detail["template"], detail["template_version"], detail["state"]) == ("support", releases.version(), "active")
    assert api.get("/api/v2/tasks", params={"owner_id": "botops"}, headers=who).json()["tasks"] == []   # no BotOps task
    assert support_routine(api)["automation"] and enabled(api) == 0
    with api.app.state.store.read() as c:
        assert json.loads(c.execute("SELECT config FROM bot_config WHERE bot='support'").fetchone()[0])["materialize"] is True

    # Nothing wakes it but a person: a task notice waits, the person's own message is claimed.
    ready(api, computer, ["support"])
    api.post("/api/v2/tasks", json={"owner_id": "support", "title": "Look at the queue", "body": "x"}, headers=signed_in())
    assert claim(api, computer, "support") is None
    started = post(api, "chat/support", {"text": "Let's set you up."}, token="local-owner-secret-token-0123456789")
    assert enabled(api) == 1          # starting the setup switched its first routine on: nobody approves it separately
    attempt = claim(api, computer, "support")
    assert attempt and attempt["bot"] == "support"
    assert attempt["onboarding"] == "needs_setup"           # the runner's prompt follows the template's flow

    # Start setup is a message in the person's own chat with the bot: the conversation the bot page's Chat tab lists.
    chats = api.get("/api/v2/conversations", params={"chat_with": "support"}, headers=who).json()["conversations"]
    assert [c["id"] for c in chats] == [started["conversation_id"]] == [attempt["conversation"]["id"]]
    assert chats[0]["scope"] == "personal" and chats[0]["kind"] == "chat"
    shown = api.get(f"/api/v2/conversations/{chats[0]['id']}/messages", headers=who).json()["messages"]
    assert "Let's set you up." in [m["body"] for m in shown]           # beside the task notice the room already held

    # The bot says it is ready, with what it learned; a person above it marks onboarding complete, once.
    def call(token, summary=""):
        return api.post("/api/v2/bots/support/onboarded", json={"summary": summary}, headers=headers(token))
    assert call(computer["token"]).status_code == 403                 # the computer is not the bot
    said = call(attempt["token"], "I know the queue and the refund rules.")      # the bot, in its own turn
    assert said.status_code == 200 and said.json()["stage"] == "onboarding" and said.json()["changed"] is False
    assert said.json()["ready"]["summary"] == "I know the queue and the refund rules."
    shown = api.get("/api/v2/bots/support", headers=who).json()
    assert shown["stage"] == "onboarding" and shown["can_complete_onboarding"] is True
    assert shown["onboarding_ready"]["summary"] == "I know the queue and the refund rules."
    done = api.post("/api/v2/bots/support/onboarded", json={}, headers=who)       # the owner, above its manager
    assert done.status_code == 200 and done.json()["onboarding_state"] == "onboarded" and done.json()["changed"] is True
    assert call(attempt["token"]).json() == {"bot": "support", "onboarding_state": "onboarded", "stage": "active",
                                             "changed": False}
    assert api.get("/api/v2/bots/support", headers=who).json()["stage"] == "active"
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


def test_finishing_setup_with_defaults_turns_the_first_routine_on_as_starting_it_would(environment):
    """Overview's "Finish with defaults" on a bot nobody started: its template's first routine is on, as Start setup
    would have left it, and only once."""
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, "")])
    real_starters(api, "support")
    machine(api)
    selected = {"support": {"template": "support", "display_name": "Help desk", "instructions": ""}}
    assert draft(api, selected=selected).status_code == 200
    build_team(api)

    def enabled():
        return support_routine(api)["enabled"]
    assert enabled() == 0
    finished = api.post("/api/v2/bots/support/onboarded", json={}, headers=signed_in()).json()
    assert finished["onboarding_state"] == "onboarded" and finished["routine"] and enabled() == 1
    again = api.post("/api/v2/bots/support/onboarded", json={}, headers=signed_in()).json()
    assert again["changed"] is False and enabled() == 1


def test_go_live_turns_the_first_routine_on_once_and_a_strangers_message_does_not(environment):
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, "")])
    real_starters(api, "support")
    machine(api)
    selected = {"support": {"template": "support", "display_name": "Help desk", "instructions": ""}}
    assert draft(api, selected=selected).status_code == 200
    build_team(api)

    def enabled():
        return support_routine(api)["enabled"]
    as_person(api, "quinn")                                          # a member who does not manage it
    assert api.post("/api/v2/chat/support", json={"text": "hello"}, headers=signed_in("quinn")).status_code == 200
    assert enabled() == 0
    live = api.post("/api/v2/bots/support/go-live", json={}, headers=signed_in())
    assert live.status_code == 200 and live.json()["routine_armed"] and enabled() == 1
    off = api.patch(f"/api/v2/automations/{support_routine(api)['automation']}", json={"enabled": False},
                    headers=signed_in())                             # a person turns it off: going live again leaves it off
    assert off.status_code == 200, off.text
    again = api.post("/api/v2/bots/support/go-live", json={}, headers=signed_in())
    assert again.status_code == 200 and enabled() == 0



def test_created_groups_keep_the_finished_wizard_chart_order_after_json_storage(environment):
    """Stored selected keys are alphabetical; groups still follow the chart, including on repeated Create."""
    api = environment(seed={}, catalog_dir=CATALOG)
    who = signed_in()
    departments = ["sales", "marketing", "support", "finance", "operations"]
    templates = ["sales-lead", "marketing-lead", "support-lead", "finance-lead", "ops-manager"]
    selected = {slug: {"template": slug, "display_name": slug, "instructions": ""} for slug in templates}
    body = {"names": {"company_name": "Fresh Synthetic Co"},
            "answers": {"departments": departments}, "selected": selected}
    assert api.put("/api/v2/setup", json=body, headers=who).status_code == 200
    # This fresh read exercises encode(sort_keys=True), the source of the original mismatch.
    saved = api.get("/api/v2/setup", headers=who).json()
    assert list(saved["selected"]) == sorted(templates)
    for _ in range(2):
        assert api.post("/api/v2/setup/complete", json={}, headers=signed_in()).status_code == 200
        groups = api.get("/api/v2/groups", headers=who).json()
        assert [group["id"] for group in groups] == departments

    # A manual group added after setup appends, even though stored keys sort it between wizard groups.
    added = api.post("/api/v2/groups", json={"name": "Legal"}, headers=signed_in())
    assert added.status_code == 200
    expected = departments + ["legal"]
    groups = api.get("/api/v2/groups", headers=who).json()
    chart = api.get("/api/v2/org", headers=who).json()["org_groups"]
    assert [group["id"] for group in groups] == expected
    assert [group["id"] for group in sorted(chart, key=lambda group: group["order"])] == expected
    with api.app.state.store.read() as c:
        saved = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
    assert list(saved["org_groups"]) == sorted(expected)
    assert {gid: group["order"] for gid, group in saved["org_groups"].items()} == dict(zip(expected, range(6)))


def test_created_groups_append_after_legacy_positions_across_reload_and_repeated_create(environment):
    """Adding a group preserves legacy positions and the chart/list agree after sorted JSON reloads."""
    api = environment(seed={}, catalog_dir=CATALOG)
    who = signed_in()
    for name in ["Marketing", "Operations"]:
        assert api.post("/api/v2/groups", json={"name": name}, headers=signed_in()).status_code == 200
    # Model a roster stored before group creation persisted positions.
    with api.app.state.store.transaction() as c:
        legacy = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for group in legacy["org_groups"].values():
            group.pop("order", None)
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(legacy, sort_keys=True),))
    assert all("order" not in group for group in legacy["org_groups"].values())
    selected = {"finance-lead": {"template": "finance-lead", "display_name": "Finance", "instructions": ""}}
    assert api.put("/api/v2/setup", json={"answers": {"departments": ["finance"]},
                                         "selected": selected}, headers=who).status_code == 200
    expected = ["marketing", "operations", "finance"]
    for _ in range(2):
        assert api.post("/api/v2/setup/complete", json={}, headers=signed_in()).status_code == 200
        # Separate requests reload the persisted roster, whose object keys are alphabetically sorted.
        groups = api.get("/api/v2/groups", headers=who).json()
        chart = api.get("/api/v2/org", headers=who).json()["org_groups"]
        assert [group["id"] for group in groups] == expected
        assert [group["id"] for group in sorted(chart, key=lambda group: group["order"])] == expected
        with api.app.state.store.read() as c:
            saved = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        assert list(saved["org_groups"]) == sorted(expected)
        assert {gid: group["order"] for gid, group in saved["org_groups"].items()} == dict(zip(expected, range(3)))
