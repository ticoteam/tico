"""The Assistant changes settings with the same catalogue and classes as BotOps (backend/settings_catalog.py,
backend/assistant.py): a direct one on the person's words in their own chat, a confirm one as a Confirm card there, a
person-only one never; the owner's "Assistant acts without asking" off makes every change a card.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture).
"""

import re

import pytest

from backend import settings_catalog
from backend.assistant import valid_operation
from backend.auth import Identity
from backend.settings_entries import ENTRIES
from backend.store import Problem
from backend.tests.helpers.api import as_member, get, headers, post, put  # noqa: F401  (fixture)
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.botops_repositories import FLAG, effective, repos  # noqa: F401  (fixture)
from backend.tests.helpers.member_bots import botops  # noqa: F401  (fixture)
from clients import hubtools
from backend.tests.helpers.settings_assistant import mcp


def release_times(api):
    return get(api, "release-schedule")["times"]


def test_a_person_asks_their_assistant_for_a_direct_setting_and_it_changes(api):
    before = release_times(api)
    assert before != ["09:30"], before
    r, attempt = assistant_turn(api, "ana-test", "Set the release to 09:30")
    token = attempt["token"]
    assert "tico_settings_set" in {t["name"] for t in hubtools.listing(kind="assistant")}
    # On the person's own turn, the message that started it is the citation: it changes at once, no header needed.
    err, done = mcp(api, "tico_settings_set", {"key": "release.times", "value": "09:30"}, token)
    assert not err and done["value"] == ["09:30"], done
    assert release_times(api) == ["09:30"]
    with api.app.state.store.read() as c:
        row = c.execute(
            "SELECT actor,detail FROM events WHERE detail LIKE '%\"via\": \"assistant\"%' "
            "AND detail LIKE '%\"cited\"%' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert row and row["actor"] == "human:ana", row


def test_a_member_changes_a_direct_setting_anyone_may_as_themselves(api):
    as_member(api, "cara@acme.example")
    r, attempt = assistant_turn(api, "cara-test", "Make me an incoming trigger called Orders")
    err, done = mcp(
        api,
        "tico_settings_set",
        {"key": "webhooks.incoming.create", "value": {"name": "Orders"}, "on_behalf_of": "turn"},
        attempt["token"],
    )
    assert not err and done["value"] == {"name": "Orders"}, done
    with api.app.state.store.read() as c:
        assert c.execute("SELECT created_by FROM triggers WHERE name='Orders'").fetchone()[0] == "human:cara"


def test_a_confirm_setting_is_a_card_in_the_assistant_chat_and_the_tap_changes_it(api):
    r, attempt = assistant_turn(api, "ana-test", "Stop admins seeing SQL")
    err, card = mcp(
        api,
        "tico_settings_set",
        {"key": "team.rules.admin_sql", "value": "off", "on_behalf_of": "turn"},
        attempt["token"],
    )
    assert not err and card["needs_confirm"] is True, card
    assert get(api, "access")["rules"]["admin_sql"] is True
    action = card["action"]
    assert action["owner"] == "human:ana" and action["status"] == "pending"
    with api.app.state.store.read() as c:
        row = c.execute("SELECT proposer,conversation_id FROM assistant_actions WHERE id=?", (action["id"],)).fetchone()
        assert row["proposer"] == "assistant"
        room = c.execute("SELECT owner_id FROM conversations WHERE id=?", (row["conversation_id"],)).fetchone()
        assert room["owner_id"] == "human:ana"  # the person's own chat, not BotOps'
    # The Assistant can't tap it; the person's own tap runs it, as them.
    post(api, f"assistant/actions/{action['id']}/confirm", {}, token=attempt["token"], expected=403)
    done = post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ana-test")["action"]
    assert done["status"] == "done", done
    assert get(api, "access")["rules"]["admin_sql"] is False


def test_with_assistant_acts_without_asking_off_a_direct_setting_is_a_card(api):
    put(api, "access/rules", {"assistant_direct": False})
    before = release_times(api)
    r, attempt = assistant_turn(api, "ana-test", "Set the release to 09:30")
    token = attempt["token"]
    err, card = mcp(api, "tico_settings_set", {"key": "release.times", "value": "09:30", "on_behalf_of": "turn"}, token)
    assert not err and card["needs_confirm"] is True, card
    assert release_times(api) == before
    # Nor does the write itself run on the person's words while the rule is off.
    refused = api.patch(
        "/api/v2/release-schedule", json={"times": ["09:30"]}, headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"}
    )
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "confirm_required", refused.text
    post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token="ana-test")
    assert release_times(api) == ["09:30"]


def test_a_member_asking_for_an_admin_setting_changes_nothing_and_is_told_who_can(api):
    as_member(api, "cara@acme.example")
    before = release_times(api)
    r, attempt = assistant_turn(api, "cara-test", "Set the release to 09:30")
    token = attempt["token"]
    err, refused = mcp(
        api, "tico_settings_set", {"key": "release.times", "value": "09:30", "on_behalf_of": "turn"}, token
    )
    assert err and "owner or an admin" in str(refused), refused
    # The same write sent without asking for its plan: the route's own check, as the person, refuses it.
    raw = api.patch(
        "/api/v2/release-schedule", json={"times": ["09:30"]}, headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"}
    )
    assert raw.status_code == 403 and "owner or an admin" in raw.json()["error"]["detail"], raw.text
    assert release_times(api) == before
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM assistant_actions WHERE owner='human:cara'").fetchone()


def test_the_assistant_never_runs_two_settings_at_once_nor_cards_a_person_only_one(api):
    r, attempt = assistant_turn(api, "ana-test", "Set the release to 09:30 and turn it off")
    token = attempt["token"]
    # A list or a bare value is no setting's body (New incoming trigger takes any object): never read as `{}`.
    for body in ([], [{"times": ["09:30"]}], "09:30"):
        listed = api.post("/api/v2/triggers", json=body, headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"})
        assert listed.status_code == 403 and listed.json()["error"]["code"] == "confirm_required", listed.text
    both = api.patch(
        "/api/v2/release-schedule",
        json={"times": ["09:30"], "on": False},
        headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"},
    )
    assert both.status_code == 403 and both.json()["error"]["code"] == "confirm_required", both.text
    rules = api.put(
        "/api/v2/access/rules", json={"admin_sql": False}, headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"}
    )  # a confirm one: a card
    assert rules.status_code == 403 and rules.json()["error"]["code"] == "confirm_required", rules.text
    # A card holds one catalogue setting that is not the person's own to change in Settings.
    post(
        api,
        "assistant/actions",
        {"summary": "Gateway key", "method": "PUT", "path": "/api/v2/providers/gateway", "body": {"key": "x"}},
        token=token,
        expected=422,
    )
    post(
        api,
        "assistant/actions",
        {
            "summary": "Two rules",
            "method": "PUT",
            "path": "/api/v2/access/rules",
            "body": {"admin_sql": False, "assistant_direct": False},
        },
        token=token,
        expected=422,
    )
    card = post(
        api,
        "assistant/actions",
        {"summary": "Admins see SQL", "method": "PUT", "path": "/api/v2/access/rules", "body": {"admin_sql": False}},
        token=token,
    )["action"]
    assert card["status"] == "pending"


def outbound_automation(api):
    """A stored automation that sends outside, off: synthetic rows, no delivery worker runs here. Wake rules that sent
    outside became automations; an automation is no catalogue setting, so it is never the Assistant's to change."""
    hook = post(api, "webhooks", {"name": "Synthetic", "url": "https://example.com/hook", "events": ["*"]})["webhook"]
    return post(
        api,
        "automations",
        {
            "name": "Send out",
            "owner": "person:ana",
            "enabled": False,
            "trigger": {"type": "event", "name": "task.created"},
            "steps": [
                {
                    "id": "send",
                    "type": "action",
                    "do": "call_webhook",
                    "args": {"webhook": hook["id"], "type": "custom.settings_test"},
                }
            ],
        },
    )


def test_the_assistant_cannot_turn_on_or_edit_an_automation_that_sends_outside_nor_card_it(api):
    made = outbound_automation(api)
    r, attempt = assistant_turn(api, "ana-test", "Turn my automations on")
    token = attempt["token"]
    cited = {**headers(token), "X-Tico-On-Behalf-Of": "turn"}
    for value in ({"enabled": True}, {"name": "Renamed"}, {"steps": []}):
        # Sent on the person's words, the write itself is refused: it waits for the person's own yes.
        raw = api.patch(f"/api/v2/automations/{made['id']}", json=value, headers=cited)
        assert raw.status_code == 403 and raw.json()["error"]["code"] == "confirm_required", raw.text
        # Nor may it hold one on a Confirm card.
        post(
            api,
            "assistant/actions",
            {"summary": "Automation on", "method": "PATCH", "path": f"/api/v2/automations/{made['id']}", "body": value},
            token=token,
            expected=422,
        )
    after = get(api, f"automations/{made['id']}")
    assert (after["enabled"], after["name"], after["version"], len(after["steps"])) == (False, "Send out", 1, 1)


def test_with_the_rule_off_a_repository_setting_is_a_card_the_tap_runs(api):
    # A repository's id is owner/name, two segments of its route: the card names the route the Settings page uses.
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES(?,?,1,?)",
            ("repo-acme", "Acme/product", "2026-10-27T00:00:00Z"),
        )
    put(api, "access/rules", {"assistant_direct": False})
    r, attempt = assistant_turn(api, "ana-test", "Switch off the product repository")
    err, card = mcp(
        api,
        "tico_settings_set",
        {"key": "repo.enabled", "id": "Acme/product", "value": "off", "on_behalf_of": "turn"},
        attempt["token"],
    )
    assert not err and card["needs_confirm"] is True, card
    assert card["action"]["path"] == "/api/v2/repositories/Acme/product", card["action"]
    post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token="ana-test")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT enabled FROM repositories WHERE full_name='Acme/product'").fetchone()[0] == 0


def test_run_workflows_is_a_card_in_the_assistant_chat_and_a_cited_write_never_skips_it(api, botops, repos):
    """Criterion 10, and the route's own rule at the Assistant gate: a kept Run workflows tick coming back as
    `{"mode": "chosen"}`, or taking access away, holds no tick in the body but is still the person's card."""
    r, attempt = assistant_turn(api, "ana-test", "Let ops run workflows in product")
    token = attempt["token"]
    cited = {**headers(token), "X-Tico-On-Behalf-Of": "turn"}
    before = effective(api)  # write on product, no workflows
    err, card = mcp(
        api,
        "tico_settings_set",
        {
            "key": "bot.repositories",
            "bot": "ops",
            "on_behalf_of": "turn",
            "value": {"mode": "chosen", "chosen": [FLAG]},
        },
        token,
    )
    assert not err and card["needs_confirm"] is True and effective(api) == before != [FLAG], card
    assert card["action"]["description"].startswith("ops: may run workflows in product: it can start CI"), card
    with api.app.state.store.read() as c:
        row = c.execute("SELECT proposer FROM assistant_actions WHERE id=?", (card["action"]["id"],)).fetchone()
    assert row["proposer"] == "assistant"
    post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token="ana-test")
    assert effective(api) == [FLAG]
    # The tick is kept, then comes back with a body that holds none: the route would card it, so the gate does.
    put(api, "bots/ops/repositories", {"mode": "own"}, token="ana-test")
    for value in ({"mode": "chosen"}, {"mode": "chosen", "chosen": [FLAG]}):
        raw = api.put("/api/v2/bots/ops/repositories", json=value, headers=cited)
        assert raw.status_code == 403 and raw.json()["error"]["code"] == "confirm_required", raw.text
        assert effective(api) == [], value
    put(api, "bots/ops/repositories", {"mode": "chosen", "chosen": [FLAG]}, token="ana-test")
    # Taking access away is the route's card too; sending again a tick that is on runs, as the route runs it.
    raw = api.put("/api/v2/bots/ops/repositories", json={"mode": "own"}, headers=cited)
    assert raw.status_code == 403 and effective(api) == [FLAG], raw.text
    raw = api.put("/api/v2/bots/ops/repositories", json={"mode": "chosen", "chosen": [FLAG]}, headers=cited)
    assert raw.status_code == 200 and effective(api) == [FLAG], raw.text


# ------------------------------------------------------------------ the same request to BotOps and to the Assistant
SAMPLE = {"bool": "on", "int": "3", "number": "1", "list": "a", "text": "x", "json": {"name": "x"}, "upload": "x"}


def sample(e):
    return e.value.split(":", 1)[1].split("|")[0] if e.value.startswith("choice:") else SAMPLE[e.value]


def outcome(c, auth, who, e, card_direct=False):
    target = {"bot": "ops", "computer": "x", "person": who.actor.split(":", 1)[1], "id": "x"}
    proposer = "assistant" if who.via == "assistant" else "botops"
    try:
        made = settings_catalog.plan(
            c,
            auth,
            who,
            e,
            sample(e),
            target,
            propose=lambda *a: {"needs_confirm": True},
            card_direct=card_direct,
            can_card=lambda method, path: valid_operation(method, path, None, proposer),
        )
    except Problem as refused:
        return refused.code, refused.status, refused.detail
    return "run" if "run" in made else "card"


# Direct entries that must still come back as a card when the owner's rule is off (botops_act.DO), never refused.
CARDED = {"computer.label", "computer.turns_at_once"}


ASSISTANT_REFUSED = {"mail.intake"}


@pytest.mark.parametrize("role", ["owner", "admin", "member"])
def test_every_setting_does_for_the_assistant_what_it_does_for_botops(api, role):
    """Stage 1's sweep (test_settings_parity) asked of the person's Assistant on their cited words: every entry ran, was
    a card or was refused exactly as for BotOps, gaps included."""
    from backend.tests.helpers import settings_parity as parity
    from backend.tests.helpers.api import runner

    token, person = parity.PEOPLE[role]
    objects = parity._objects(api, runner(api))
    r, attempt = assistant_turn(api, token, "Change my settings")
    wrong, unproven = [], {}
    # bot.archive goes last: it archives the bot the other entries act on.
    for e in sorted(ENTRIES, key=lambda e: e.key == "bot.archive"):
        with api.app.state.store.read() as c:
            want = parity.KNOWN_GAPS.get((e.key, role)) or parity.expected(c, e, role, person)
        got = parity.ask(api, attempt, e, objects, person, token)
        if e.key in ASSISTANT_REFUSED:
            # Assistant-only divergence: postal intake is a NEVER route (it takes mail from outside, backend/assistant.py),
            # so no card can carry it on a cited turn and the planner refuses it as forbidden (403). BotOps runs it
            # directly as an admin.
            assert got == "refused" and parity.LAST[e.key].startswith("403") and '"forbidden"' in parity.LAST[e.key], (
                e.key,
                got,
                parity.LAST.get(e.key),
            )
            continue
        if got in ("ran", "card", "refused", "card, then refused"):
            # On the person's own cited turn the Assistant applies a confirm-class setting at once (the person's rights are
            # the ceiling; BotOps still cards it): the one divergence the Assistant is meant to have.
            if got != want and not (got == "ran" and want == "card" and e.kind == "confirm"):
                wrong.append(
                    f"{e.key}: BotOps {want} for the {role} ({e.kind}, {e.who}), the Assistant {got}"
                    f" [{parity.LAST.get(e.key, '')}]"
                )
        else:
            unproven[e.key] = got
    assert not wrong, "\n".join(wrong)
    new = {k: v for k, v in unproven.items() if k not in parity.NOT_PROVEN}
    assert not new, {k: f"{v} [{parity.LAST.get(k, '')}]" for k, v in new.items()}
