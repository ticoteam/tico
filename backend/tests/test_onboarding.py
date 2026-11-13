"""First-run onboarding: pick templates, name things, and hand BotOps the setup backlog.

The catalog is written per test, so these assertions never depend on the cards a release
happens to ship. The company is Acme, its app is Atlas, its assistant Morgan.
"""

import json
from pathlib import Path

import pytest
import yaml

from backend import providers
from backend.config import Settings
from backend.store import H
from backend import bot_settings as BS
from backend.tests.helpers.onboarding import (
    ASSISTANT_AGENT,
    ASSISTANT_CARD,
    BOTOPS_CARD,
    LIBRARIAN_CARD,
    OWNER_EMAIL,
    SEED,
    SUPPORT_AGENT,
    SUPPORT_CARD,
    as_person,
    build_team,
    draft,
    environment,
    machine,
    signed_in,
)


def seats(api):
    with api.app.state.store.read() as c:
        return [
            dict(row) for row in c.execute("SELECT id,title,description,reports_to FROM seats WHERE closed_at IS NULL")
        ]


def test_setup_lays_out_each_picked_role_once_as_a_recruiting_seat_and_creates_no_bot_for_it(environment):
    """bot-lifecycle-spec.md acceptance tests 1 and 20: a picked role is a Recruiting seat with its job description and
    reporting line; no bot, repository, computer slot or BotOps setup task. Completing twice changes nothing."""
    api = environment()
    machine(api)
    # The owner's edits to the instructions are the duties and limits recruiting must see; the template's text is not.
    edited = "# Help\n\nYou answer Acme's support inbox.\n\nRefund up to $40 without asking.\nNever phone a customer.\n"
    draft(api, selected={"support": {"template": "support-owner", "display_name": "Help", "instructions": edited}})
    first = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).json()
    again = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert again.status_code == 200, again.text
    record = again.json()
    assert record["completed"] == first["completed"] and record["seats"] == first["seats"]
    assert sorted(row["slug"] for row in record["bots"]) == ["assistant", "botops"]
    (seat,) = seats(api)
    assert record["seats"] == {"support": seat["id"]}
    assert seat["title"] == "Help" and seat["reports_to"] == "human:morgan"
    assert (
        "Refund up to $40 without asking." in seat["description"] and "Never phone a customer." in seat["description"]
    )
    assert "Answers the support inbox." in seat["description"] and "You answer Acme" not in seat["description"]
    opened = api.get(f"/api/v2/seats/{seat['id']}", headers=signed_in())
    assert opened.status_code == 200 and opened.json()["description_by"] == "person"
    assert api.get("/api/v2/tasks", params={"owner": "botops"}, headers=signed_in()).json()["tasks"] == []
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM bots WHERE slug='support'").fetchone()
        assert not c.execute("SELECT 1 FROM assignments WHERE bot NOT IN ('assistant','botops')").fetchone()
    assert _states(api)["botops"] == _states(api)["assistant"] == "active"  # the built-ins work from day one


def test_the_default_marketing_chart_hires_the_writer_first_and_moves_it_under_the_head_when_the_head_is_hired(
    environment,
):
    """ticket e9c84fc5: setup's Marketing chart is Content Owner under Head of Marketing, which reports to the owner.
    While the head's seat is still Recruiting, hiring the writer used to fail with "Reports-to bot was not found". Now the
    writer is hired under the owner and says so until the head is hired; hiring the head moves it under the new bot."""
    api = environment(catalog_dir=Settings.catalog_dir)
    machine(api)
    picks = {
        "head-of-marketing": {
            "template": "growth-manager",
            "display_name": "Head of Marketing",
            "reports_to": "human:morgan",
        },
        "content-marketer": {
            "template": "content-owner",
            "display_name": "Content Owner",
            "reports_to": "head-of-marketing",
        },
    }
    assert draft(api, selected=picks).status_code == 200
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    (head,) = [seat for seat in seats(api) if seat["title"] == "Head of Marketing"]
    (writer,) = [seat for seat in seats(api) if seat["title"] == "Content Owner"]
    assert head["reports_to"] == "human:morgan" and writer["reports_to"] == "seat:" + head["id"]

    def recruit_and_hire(seat, template):
        offered = api.post(f"/api/v2/seats/{seat['id']}/recruit", json={}, headers=signed_in())
        assert offered.status_code == 200, offered.text
        assert template in [candidate["template"] for candidate in offered.json()["candidates"]]
        hired = api.post(f"/api/v2/seats/{seat['id']}/hire", json={"template": template}, headers=signed_in())
        assert hired.status_code == 200, hired.text
        return hired.json()

    def definition_reports_to(hired):
        with api.app.state.store.read() as c:
            return c.execute("SELECT reports_to FROM bot_config WHERE bot=?", (hired["bot"],)).fetchone()[0]

    assert (
        api.get(f"/api/v2/seats/{writer['id']}", headers=signed_in()).json()["reports_now"]["until"]
        == "Head of Marketing"
    )
    writing = recruit_and_hire(writer, "content-owner")
    assert writing["reports_to"] == "human:morgan" and definition_reports_to(writing) == "human:morgan"
    leading = recruit_and_hire(head, "growth-manager")
    assert definition_reports_to(leading) == "human:morgan"
    assert definition_reports_to(writing) == leading["bot"]  # moved when the head was hired, as placed
    assert seats(api) == []


def test_the_inbox_manager_turned_on_in_setup_is_a_message_bot_linked_to_the_owner_not_a_seat(environment):
    """A message bot (`kind: helper`) serves one person and is never on the chart: setup makes the bot, reporting to
    the owner and linked to their mailbox (docs/starter-bots.md), while a role picked beside it is still a seat."""
    from backend import routines, views

    inbox = {
        "template": "inbox-manager",
        "slug": "inbox",
        "name": "Inbox Manager",
        "required": False,
        "bootstrap": False,
        "kind": "helper",
        "summary": "Runs one human's mailbox.",
        "owns": ["the morning brief"],
        "never": ["sends"],
        "reasoning_effort": "high",
        "recommend_when": [],
    }
    api = environment(
        cards=[
            (ASSISTANT_CARD, ASSISTANT_AGENT),
            (BOTOPS_CARD, ""),
            (SUPPORT_CARD, SUPPORT_AGENT),
            (inbox, "# {{bot_name}}\n\nYou run one human's mailbox.\n"),
        ]
    )
    machine(api)
    # The wizard sends a message bot under the owner (docs/onboarding.md, Finish).
    draft(
        api,
        selected={
            "inbox": {"template": "inbox-manager", "display_name": "Inbox Manager", "reports_to": "human:morgan"},
            "support": {"template": "support-owner", "display_name": "Help"},
        },
    )
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    assert sorted(row["slug"] for row in done.json()["bots"]) == ["assistant", "botops", "inbox"]
    assert set(done.json()["seats"]) == {"support"}
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT c.reports_to FROM bot_config c JOIN bots b ON b.id=c.bot WHERE b.slug='inbox'"
            ).fetchone()[0]
            == "human:morgan"
        )
        owner = next(p for p in views.roster(c)["people"] if p["id"] == "morgan")
        config = BS.read(c, H.resolve_bot(c, "inbox")["id"])
        inbox_id = c.execute("SELECT id FROM bots WHERE slug='inbox'").fetchone()[0]
    assert owner["inbox_bot"] == inbox_id and routines.declared_mailbox(config) == OWNER_EMAIL


def test_direct_signup_with_no_chosen_chart_is_the_built_ins_and_an_empty_chart(environment):
    """bot-lifecycle-spec.md acceptance test 4: no company file and nothing picked still finishes setup, with only the
    built-in bots as bots; the chart is the default chart's seats, still to recruit into."""
    api = environment()
    machine(api)
    draft(api)
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    assert done.json()["completed"] and done.json()["company_import_failed"] is None
    assert sorted(row["slug"] for row in done.json()["bots"]) == ["assistant", "botops"]
    # Nothing picked is the default chart (company_import.default_chart): its seats whose template this catalog has.
    assert sorted(seat["title"] for seat in seats(api)) == ["Inbound Sales Owner", "Support Owner"]
    assert _states(api)["botops"] == _states(api)["assistant"] == "active"
    added = api.post("/api/v2/seats", json={"title": "Head of Support"}, headers=signed_in())
    assert added.status_code == 200 and added.json()["stage"] == "recruiting"


def test_completing_twice_duplicates_neither_a_bot_nor_a_task(environment):
    api = environment()
    # The owner's edits to the instructions are the duties and limits recruiting must see; the template's text is not.
    edited = "# Help\n\nYou answer Acme's support inbox.\n\nRefund up to $40 without asking.\nNever phone a customer.\n"
    draft(api, selected={"support": {"template": "support-owner", "display_name": "Help", "instructions": edited}})
    first = build_team(api)
    again = build_team(api)
    assert again["completed"] == first["completed"]
    assert [row["slug"] for row in again["bots"]] == [row["slug"] for row in first["bots"]]
    assert {row["slug"]: row["setup_task_id"] for row in again["bots"]} == {
        row["slug"]: row["setup_task_id"] for row in first["bots"]
    }
    tasks = api.get("/api/v2/tasks", params={"owner": "botops"}, headers=signed_in()).json()["tasks"]
    assert len(tasks) == 1


def test_a_new_company_starts_with_nothing_new_in_whats_new(environment):
    api = environment()

    def unread(headers):
        return api.get("/api/v2/changelog", headers=headers).json()["unread_count"]

    riley = as_person(api, "riley")
    for title, bullet in (("Search tasks", "Search your tasks."), ("Browse the team", "Browse the team.")):
        assert (
            api.post("/api/changelog", json={"title": title, "bullets": [bullet]}, headers=signed_in()).status_code
            == 200
        )
    assert unread(signed_in()) == 2 and unread(riley) == 2
    draft(api)
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    assert unread(signed_in()) == 0
    assert api.get("/api/v2/config", headers=signed_in()).json()["changelog"] == {"unread_count": 0}
    # Only the owner who set it up was caught up, and a later update is still news to them.
    assert unread(riley) == 2
    assert (
        api.post(
            "/api/changelog", json={"title": "Pipelines", "bullets": ["Pin a pipeline."]}, headers=signed_in()
        ).status_code
        == 200
    )
    assert unread(signed_in()) == 1
    # Completing again catches up nothing more.
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    assert unread(signed_in()) == 1


def test_only_the_owner_sets_the_company_up(environment):
    api = environment()
    riley = as_person(api, "riley")  # a bot administrator, not the owner
    assert api.get("/api/v2/onboarding", headers=riley).status_code == 200
    assert (
        api.put("/api/v2/onboarding", json={"names": {}, "answers": {}, "selected": {}}, headers=riley).status_code
        == 403
    )
    assert api.post("/api/v2/onboarding/complete", json={}, headers=riley).status_code == 403

    quinn = as_person(api, "quinn")  # neither the owner nor a bot administrator
    assert api.get("/api/v2/onboarding", headers=quinn).status_code == 403
    # The catalog itself is readable by anyone who is signed in; it names no company records.
    assert api.get("/api/v2/catalog", headers=quinn).status_code == 200


def test_the_owner_saves_a_revisioned_choice_and_a_stale_editor_is_refused(environment):
    api = environment(seed={}, enabled_providers=())
    read = api.get("/api/v2/providers", headers=signed_in()).json()
    assert not read["configured"] and read["revision"] == 0
    # No model to sign in to before a provider is chosen: the config carries no runtime until then.
    assert api.get("/api/v2/config", headers=signed_in()).json()["default_runtime"] == ""
    saved = api.put(
        "/api/v2/providers", headers=signed_in(), json={"enabled": ["anthropic", "google"], "expected_revision": 0}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["default"] == {"runtime": "claude", "model": "claude-opus-5-5"}
    assert saved.json()["revision"] == 1
    stale = api.put("/api/v2/providers", headers=signed_in(), json={"enabled": ["openai"], "expected_revision": 0})
    assert stale.status_code == 409
    config = api.get("/api/v2/config", headers=signed_in()).json()
    assert config["providers_configured"] is True and config["default_runtime"] == "claude"
    models = api.get("/api/v2/models", headers=signed_in()).json()
    assert models["enabled_providers"] == ["anthropic", "google"]
    assert models["default"]["model"] == "claude-opus-5-5"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='providers.updated'").fetchone()[0] == 1


def test_every_shipped_template_saves_as_the_wizard_sends_it(environment):
    """The wizard sends each picked bot's instructions back as the catalog rendered them (ui/app/welcome.ts). A shipped
    AGENTS.md longer than the setup save accepts stopped every owner on the team chart (BotOps, 24,048 characters,
    against 20,000). The limit is the API's own: the save itself, and the model "Add from catalog" posts."""
    from backend.models import BotDefinitionCreate

    api = environment(catalog_dir=Settings.catalog_dir)
    cards = api.get("/api/v2/templates", headers=signed_in()).json()["cards"]
    assert any(card["template"] == "botops" for card in cards)
    saved = api.put(
        "/api/v2/setup",
        headers=signed_in(),
        json={
            "names": {"company_name": "Acme", "app_name": "Atlas", "assistant_name": "Morgan"},
            "answers": {},
            "selected": {
                card["slug"]: {
                    "template": card["template"],
                    "display_name": card["name"],
                    "instructions": card["instructions"],
                }
                for card in cards
            },
        },
    )
    assert saved.status_code == 200, saved.text[:500]
    assert len(saved.json()["selected"]) == len(cards)
    for card in cards:
        BotDefinitionCreate(display_name=card["name"], template=card["template"], instructions=card["instructions"])


def test_the_team_is_created_with_no_provider_and_no_computer_and_starts_once_they_exist(environment):
    api = environment(seed={}, enabled_providers=())
    draft(
        api,
        selected={"support-owner": {"template": "support-owner", "display_name": "Support Owner", "instructions": ""}},
    )
    done = build_team(api)
    assert {"botops", "support-owner"} <= {row["slug"] for row in done["bots"]}
    with api.app.state.store.read() as c:
        config = BS.read(c, 'support-owner')
        assert not c.execute("SELECT 1 FROM assignments").fetchone()
    assert not {"model", "runtime"} & set(config)  # it follows the company default, once there is one
    assert _states(api)["botops"] == "planned"  # nothing is placed or running yet
    machine(api)  # a computer appears: the bots are placed
    assert _states(api)["botops"] == "active"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM assignments WHERE bot='support-owner'").fetchone()
    api.put("/api/v2/providers", headers=signed_in(), json={"enabled": ["openai"], "expected_revision": 0})
    with api.app.state.store.read() as c:  # a provider is added: the bot resolves to it
        assert providers.bot_choice(c, api.app.state.store.settings, config)[0] == "codex"


def _states(api):
    with api.app.state.store.read() as c:
        return {row["slug"]: row["state"] for row in c.execute("SELECT slug,state FROM bots")}


def test_completing_onboarding_activates_the_bootstrap_bots_once_a_machine_hosts_them(environment):
    api = environment(seed={})
    machine(api)
    draft(
        api,
        selected={
            "support": {"template": "support-owner", "display_name": "Support", "instructions": ""},
            "assistant": {"template": "assistant", "display_name": "Morgan", "instructions": ""},
        },
    )
    build_team(api)
    states = _states(api)
    assert states["botops"] == "active" and states["assistant"] == "active"
    assert states["support"] == "planned"  # activating what BotOps builds stays a person's call
    task = api.post(
        "/api/v2/tasks", json={"owner": "botops", "title": "Look at this", "body": "x"}, headers=signed_in()
    )
    assert task.status_code == 200, task.text


LIBRARIAN_MANIFEST = {
    "name": "librarian",
    "schedules": [
        {
            "id": "refresh-the-map",
            "title": "Refresh the map",
            "cron": "30 3 * * *",
            "timezone": "America/Los_Angeles",
            "template": "playbooks/refresh-the-map.md",
        }
    ],
}


def _routines(api):
    with api.app.state.store.read() as c:
        return [
            dict(row) for row in c.execute("SELECT id,cron,playbook,deleted_at FROM schedules WHERE bot='librarian'")
        ]


def test_the_librarian_is_always_built_with_its_daily_routine_seeded_once(environment):
    api = environment(seed={}, cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, ""), (LIBRARIAN_CARD, "# L\n")])
    machine(api)
    folder = Path(api.app.state.store.settings.catalog_dir) / "librarian"
    (folder / "playbooks").mkdir()
    (folder / "employee.yaml").write_text(yaml.safe_dump(LIBRARIAN_MANIFEST))
    (folder / "playbooks/refresh-the-map.md").write_text("Refresh {{company_name}}'s map of the docs.\n")
    draft(api)
    record = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).json()
    assert sorted(row["slug"] for row in record["bots"]) == ["assistant", "botops", "librarian"]
    assert all(row["setup_task_id"] is None for row in record["bots"])  # the runner sets it up, not BotOps
    assert _states(api)["librarian"] == "active"
    (routine,) = _routines(api)
    assert routine["id"] == "librarian:refresh-the-map" and routine["cron"] == "30 3 * * *"
    assert routine["playbook"].strip() == "Refresh Acme's map of the docs."  # the company's words, filled in
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert len(_routines(api)) == 1  # twice is still one
    with api.app.state.store.transaction() as c:  # a person deleted it
        c.execute("UPDATE schedules SET deleted_at=? WHERE bot='librarian'", (H.now(),))
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert [r["deleted_at"] is not None for r in _routines(api)] == [True]  # and it stays deleted


def _botops_goals(api):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute("SELECT * FROM goals WHERE owner_id='bot:botops'")]


def test_existing_builtin_goals_survive_upgrade(environment):
    api = environment()
    assert _botops_goals(api) == []
    made = api.post("/api/v2/goals", json={"title": "Own goal", "owner": "botops"}, headers=signed_in())
    assert made.status_code == 200, made.text
    api.app.state.store.initialize(seed_market=False)
    assert [g["title"] for g in _botops_goals(api)] == ["Own goal"]


@pytest.mark.parametrize("state,draining", [("paused", 1)])
def test_enrollment_places_paused_and_draining_bots_but_skips_archived(environment, state, draining):
    from backend import onboarding

    api = environment(
        seed={
            "ana": {"name": "Ana", "status": "active", "template": "assistant"},
            "sam": {"name": "Sam", "status": "archived", "template": "assistant"},
            "waiting": {"name": "Waiting", "status": state, "template": "assistant"},
        }
    )
    placement = api.app.state.execution.runner_enrolled.__self__
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_control(bot,draining) VALUES('waiting',?)", (draining,))
        if draining:
            H.event(c, "system:deploy", "bot.drain", "waiting", {"release": "test-release"})
        record = onboarding.load(c)
        record["completed"] = H.now()
        placement._store(c, record, placement.auth.owner_identity(c).actor)
    computer = machine(api)
    with api.app.state.store.transaction() as c:
        assert placement.assign_pending(c, computer["runner_id"]) == []
        rows = c.execute("SELECT bot,runner_id FROM assignments ORDER BY bot").fetchall()
        assert [(row["bot"], row["runner_id"]) for row in rows] == [
            ("ana", computer["runner_id"]),
            ("waiting", computer["runner_id"]),
        ]
        assert c.execute("SELECT state FROM bots WHERE slug='sam'").fetchone()[0] == "archived"
        assert c.execute("SELECT state FROM bots WHERE slug='waiting'").fetchone()[0] == state
        assert c.execute("SELECT draining FROM bot_control WHERE bot='waiting'").fetchone()[0] == draining


COUNSEL_CARD = {
    "template": "rules-owner",
    "slug": "rules-owner",
    "name": "Counsel",
    "required": True,
    "bootstrap": True,
    "kind": "helper",
    "summary": "Reads documents for Rules.",
    "owns": ["suggested rules"],
    "never": ["advises"],
    "reasoning_effort": "high",
    "recommend_when": ["never"],
    "private_tasks_default": True,
}


def test_a_companys_own_bot_named_counsel_is_never_taken_for_the_built_in_counsel(environment):
    # A legal team's own bot, still being set up: planned and on no computer, which is the state a built-in is adopted in.
    own = {"name": "counsel", "status": "planned", "display_name": "Legal Desk"}
    api = environment(
        seed={**SEED, "counsel": own},
        cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, ""), (COUNSEL_CARD, "# Counsel\n")],
    )

    def theirs():
        with api.app.state.store.read() as c:
            return (
                dict(c.execute("SELECT * FROM bots WHERE slug='counsel'").fetchone()),
                dict(c.execute(BS.ROW_SQL + " WHERE bc.bot='counsel'").fetchone()),
                c.execute("SELECT COUNT(*) FROM assignments WHERE bot='counsel'").fetchone()[0],
            )

    before = theirs()
    machine(api)
    draft(api)
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    with api.app.state.store.transaction() as c:
        api.app.state.onboarding.ensure_counsel(c)
    assert theirs() == before
    assert _states(api)[H.RULES_OWNER] == "active"
    assert not api.app.state.auth.system_bot("counsel") and api.app.state.auth.system_bot(H.RULES_OWNER)
    # The built-in still works: a can-I question is its task, not theirs.
    asked = api.post("/api/v2/rules/ask", json={"question": "Can I text customers on Sunday?"}, headers=signed_in())
    assert asked.status_code == 200, asked.text
    task = api.get("/api/v2/tasks/" + asked.json()["task_id"], headers=signed_in()).json()
    assert (task.get("task") or task)["owner_id"] == "bot:" + H.RULES_OWNER


def test_a_restored_bot_keeps_the_model_its_config_names(environment):
    """A restore reads the bot's model from its config: bots has no model column (migration 0043), and a read of the
    dropped column gave the restore no model at all."""
    # Only the assistant is brought back this way (create_bot restores it; any other archived slug is a duplicate).
    api = environment(
        seed={
            "assistant": {
                "name": "Coo",
                "status": "archived",
                "template": "assistant",
                "model": "claude-opus-5-5",
                "runtime": "claude",
            }
        }
    )
    store = api.app.state.store
    with store.transaction() as c:
        who = api.app.state.auth.owner_identity(c)
        done = api.app.state.onboarding._turn_on(c, who, "assistant", "assistant", "Coo", "bot.turned_on")
    assert done["restored"] is True
    with store.read() as c:
        config = BS.read(c, "assistant")
    assert config["model"] == "claude-opus-5-5"


def test_a_save_that_names_no_places_keeps_the_saved_ones_and_an_empty_list_clears_them(environment):
    api = environment()
    machine(api)
    assert draft(api, answers={"what_we_do": "Kits.", "regions": ["Worldwide", "Texas"]}).status_code == 200
    assert draft(api, answers={"what_we_do": "Kits and parts."}).status_code == 200
    assert api.get("/api/v2/onboarding", headers=signed_in()).json()["answers"]["regions"] == ["Worldwide", "Texas"]
    assert draft(api, answers={"what_we_do": "Kits.", "regions": []}).status_code == 200
    assert api.get("/api/v2/onboarding", headers=signed_in()).json()["answers"]["regions"] == []


def test_the_places_a_company_serves_are_held_to_the_company_file_rule():
    from pydantic import ValidationError

    from backend import models as M

    assert M.OnboardingAnswers(regions=["  Texas ", "texas", "Worldwide\n"]).regions == ["Texas", "Worldwide"]
    assert M.OnboardingAnswers(regions=["Texas\nOhio"]).regions == ["Texas Ohio"]
    assert M.OnboardingAnswers().regions == []
    for bad in (["   "], ["x" * 61], [f"Place {i}" for i in range(13)]):
        with pytest.raises(ValidationError):
            M.OnboardingAnswers(regions=bad)
