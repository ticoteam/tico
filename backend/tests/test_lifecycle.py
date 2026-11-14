"""A bot's stages, Recruiting -> Onboarding -> Active (backend/lifecycle.py): bot-lifecycle-spec.md section 3,
acceptance tests 5 and 10 to 14."""

from backend import automations
from backend.store import H
from backend.tests.helpers.api import as_member, get, headers, post  # noqa: F401
from backend.tests.helpers.strategy_bot import as_bot as bot_headers
from backend.tests.helpers.lifecycle import debrief


def as_bot(api, slug):
    return bot_headers(api, slug)["Authorization"].split()[-1]


def meeting(api, aid, rid):
    """A meeting shared: the debrief tasks ops got for it."""
    with api.app.state.store.transaction() as c:
        automations.on_event(c, "meeting.ready", rid, {"id": rid, "title": "Planning"}, "keeper")
    api.app.state.store.automations.drain()
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM tasks WHERE owner_id='bot:ops' AND title='Meeting debrief'").fetchone()[
            0
        ]


def onboarding(api, *slugs, manager="human:ben"):
    with api.app.state.store.transaction() as c:
        for slug in slugs:
            c.execute("UPDATE bot_config SET onboarding_state='needs_setup',reports_to=? WHERE bot=?", (manager, slug))


def test_a_recruiting_seat_holds_no_work_which_stays_with_its_manager(api):
    seat = post(
        api, "seats", {"title": "Head of Support", "description": "Answers customers.", "reports_to": "assistant"}
    )
    assert (seat["kind"], seat["stage"], seat["reports_to"]) == ("seat", "recruiting", "assistant")
    assert [s["id"] for s in get(api, "seats")["seats"]] == [seat["id"]]
    shown = {s["id"]: s for s in get(api, "org")["seats"]}
    assert shown[seat["id"]]["stage"] == "recruiting" and shown[seat["id"]]["reports_to"] == "assistant"

    refused = api.post(
        "/api/v2/tasks",
        json={"owner": "seat:" + seat["id"], "title": "Answer the backlog", "body": "x"},
        headers=headers(),
    )
    assert refused.status_code >= 400 and "still being recruited" in refused.text and "assistant" in refused.text, (
        refused.text
    )
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner_id LIKE 'seat:%'").fetchone()
        assert not c.execute("SELECT 1 FROM jobs WHERE bot_id LIKE 'seat:%'").fetchone()

    # A person places a seat under themselves or below them; the owner anywhere.
    as_member(api, "ben@acme.example")
    post(api, "seats", {"title": "Analyst", "reports_to": "assistant"}, token="cara-test", expected=403)
    mine = post(api, "seats", {"title": "Analyst"}, token="cara-test")
    assert mine["reports_to"] == "human:cara"
    assert (
        api.patch(
            f"/api/v2/seats/{seat['id']}", json={"title": "Support lead"}, headers=headers("cara-test")
        ).status_code
        == 403
    )
    post(api, f"seats/{mine['id']}/close", {}, token="cara-test")
    assert [s["id"] for s in get(api, "seats")["seats"]] == [seat["id"]]


def test_seat_batch_checks_each_reporting_line_and_rolls_back_as_a_unit(api):
    as_member(api, "ben@acme.example")
    plan = {"seats": [{"title": "My analyst"}, {"title": "Outside seat", "reports_to": "assistant"}]}
    post(api, "seats/batch", plan, token="ben-test", expected=403)
    assert get(api, "seats")["seats"] == []  # first row rolled back with the refused second

    own = post(
        api,
        "seats/batch",
        {"seats": [{"title": "My analyst"}, {"title": "My coordinator", "reports_to": "human:ben"}]},
        token="ben-test",
    )
    assert [s["title"] for s in own["seats"]] == ["My analyst", "My coordinator"]
    assert {s["reports_to"] for s in own["seats"]} == {"human:ben"}
    anywhere = post(api, "seats/batch", {"seats": [{"title": "Owner analyst", "reports_to": "assistant"}]})
    assert anywhere["seats"][0]["reports_to"] == "assistant"


def test_an_onboarding_bot_learns_only_and_every_other_attempt_is_refused_plainly(api):
    onboarding(api, "ops")
    as_member(api, "ben@acme.example")
    token = as_bot(api, "ops")

    given = api.post("/api/v2/tasks", json={"owner": "ops", "title": "Audit the queue", "body": "x"}, headers=headers())
    assert given.status_code >= 400 and "still onboarding" in given.text, given.text
    filed = api.post(
        "/api/v2/tasks", json={"owner": "finance", "title": "Help me", "body": "x"}, headers=headers(token)
    )
    assert filed.status_code >= 400 and "still onboarding" in filed.text, filed.text

    # It talks with its manager, and nobody else.
    assert (
        api.post(
            "/api/v2/messages", json={"to": "human:ben", "text": "What matters most?"}, headers=headers(token)
        ).status_code
        == 200
    )
    for to in ("human:cara", "finance"):
        sent = api.post("/api/v2/messages", json={"to": to, "text": "Hello"}, headers=headers(token))
        assert sent.status_code == 409 and "still onboarding" in sent.text, (to, sent.text)

    task = post(api, "tasks", {"owner": "finance", "title": "Close the month", "body": "x"})
    said = api.post(f"/api/v2/tasks/{task['id']}/comments", json={"text": "I can help"}, headers=headers(token))
    assert said.status_code >= 400 and "still onboarding" in said.text, said.text

    # Its mail connector refuses every message while it learns, approval or not (connectors/mail/policy.py).
    mail = get(api, "bots/ops/mail-settings", token=token)
    assert mail["outbound_send"] is False and mail["onboarding"] is True


def test_a_person_above_marks_onboarding_complete_and_its_automation_turns_start(api):
    aid = debrief(api)
    onboarding(api, "ops")
    as_member(api, "ben@acme.example")
    token = as_bot(api, "ops")

    # Nobody outside its chain of command sees or uses Mark onboarding complete, the bot included.
    assert get(api, "bots/ops", token="cara-test")["can_complete_onboarding"] is False
    post(api, "bots/ops/onboarded", {}, token="cara-test", expected=403)
    ready = post(api, "bots/ops/onboarded", {"summary": "I know the audit checklist."}, token=token)
    assert ready["stage"] == "onboarding" and ready["ready"]["summary"] == "I know the audit checklist."
    assert meeting(api, aid, "rec-1") == 0

    shown = get(api, "bots/ops", token="ben-test")
    assert (
        shown["can_complete_onboarding"] is True
        and shown["onboarding_ready"]["summary"] == "I know the audit checklist."
    )
    assert get(api, "bots/ops")["can_complete_onboarding"] is True  # the owner, above Ben
    done = post(api, "bots/ops/onboarded", {}, token="ben-test")
    assert done["stage"] == "active" and done["changed"] is True
    assert get(api, "bots/ops")["stage"] == "active"
    assert meeting(api, aid, "rec-2") == 1


def test_any_number_onboard_at_once_and_an_upgrade_maps_set_up_to_onboarding(api):
    onboarding(api, "ops", "finance", "cpo")
    with api.app.state.store.transaction() as c:
        # A row an older release wrote, before bot_config.onboarding_state had its CHECK: readers still park it.
        c.execute("PRAGMA ignore_check_constraints=ON")
        c.execute("UPDATE bot_config SET onboarding_state='needs_onboarding' WHERE bot='cpo'")
        c.execute("PRAGMA ignore_check_constraints=OFF")
        c.execute("UPDATE bot_config SET onboarding_state='onboarded' WHERE bot='assistant'")
        c.execute("UPDATE bots SET state='planned' WHERE slug='doc-updater'")
    stages = {b["slug"]: (b["stage"], b["state"]) for b in get(api, "bots")}
    assert {slug for slug, (stage, _) in stages.items() if stage == "onboarding"} == {"ops", "finance", "cpo"}
    assert stages["assistant"] == ("active", "active") and stages["product-design"] == ("active", "active")
    assert stages["doc-updater"] == ("active", "planned")  # a planned bot keeps working as today
    with api.app.state.store.read() as c:
        assert H.parked_bots(c) == {"ops", "finance", "cpo"}


def test_an_onboarding_bot_leaves_no_note_or_word_on_a_task_kept_from_before(api):
    # A task it held, and a question it put to a person, from before it went back to onboarding: the fence on new
    # work does not cover them, and asking again on the same task would join that question rather than file one.
    ops, finance, cpo = (as_bot(api, slug) for slug in ("ops", "finance", "cpo"))
    as_member(api, "ben@acme.example")
    kept = post(api, "tasks", {"owner": "ops", "title": "Audit the queue", "body": "x"})
    question = {
        "to": "ben",
        "line": "Which queue first?",
        "version": kept["version"],
        "why": "It decides what ships next.",
    }
    post(api, f"tasks/{kept['id']}/asks", question, token=ops)
    onboarding(api, "ops", manager="assistant")
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM messages WHERE sender_id='bot:ops'").fetchone()[0]

    # A quiet note goes to its manager only; an Active bot still leaves one for the same peer.
    assert (
        api.post(
            "/api/v2/notes", json={"to": "assistant", "text": "Read the runbook."}, headers=headers(ops)
        ).status_code
        == 200
    )
    refused = api.post("/api/v2/notes", json={"to": "cpo", "text": "Read the runbook."}, headers=headers(ops))
    assert refused.status_code == 409 and "still onboarding" in refused.text, refused.text
    assert (
        api.post("/api/v2/notes", json={"to": "cpo", "text": "Read the runbook."}, headers=headers(finance)).status_code
        == 200
    )
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT to_actor FROM notes WHERE from_actor='bot:ops'")] == ["bot:assistant"]

    # Nothing on the kept task either: no comment, no question put to a person.
    for path, body in ((f"tasks/{kept['id']}/comments", {"text": "Started"}), (f"tasks/{kept['id']}/asks", question)):
        said = api.post("/api/v2/" + path, json=body, headers=headers(ops))
        assert said.status_code == 409 and "still onboarding" in said.text, (path, said.text)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE sender_id='bot:ops'").fetchone()[0] == before


def test_a_seat_may_report_to_an_open_seat_and_hangs_under_it_until_that_seat_closes(api):
    """A chosen chart lays out roles under roles (bot-lifecycle-spec.md acceptance test 1): a seat reports to
    `seat:<id>`, never in a circle, and who may place it is decided by the person or bot at the top of its branch."""
    lead = post(api, "seats", {"title": "Head of Support", "reports_to": "assistant"})
    agent = post(api, "seats", {"title": "Support agent", "reports_to": "seat:" + lead["id"]})
    assert agent["reports_to"] == "seat:" + lead["id"]
    shown = {s["id"]: s for s in get(api, "org")["seats"]}
    assert shown[agent["id"]]["org_parent"] == "s:" + lead["id"] and shown[lead["id"]]["org_parent"] == "b:assistant"
    people = {
        s["id"]: s for s in api.get("/api/people", headers=headers()).json()["seats"]
    }  # the sidebar's team chart reads these
    assert people[agent["id"]]["org_parent"] == "s:" + lead["id"] and people[agent["id"]]["stage"] == "recruiting"

    circle = api.patch(f"/api/v2/seats/{lead['id']}", json={"reports_to": "seat:" + agent["id"]}, headers=headers())
    assert circle.status_code == 422 and circle.json()["error"]["code"] == "hierarchy", circle.text
    assert (
        api.patch(f"/api/v2/seats/{lead['id']}", json={"reports_to": "seat:nope"}, headers=headers()).status_code == 404
    )

    # Cara is not above the COO, so not above a seat under the COO's seat either.
    as_member(api, "ben@acme.example")
    post(api, "seats", {"title": "Night shift", "reports_to": "seat:" + agent["id"]}, token="cara-test", expected=403)

    # A closed seat's reports hang where it did.
    post(api, f"seats/{lead['id']}/close", {})
    assert {s["id"]: s for s in get(api, "org")["seats"]}[agent["id"]]["org_parent"] == "b:assistant"
    # The sidebar refreshes its seats from GET /api/v2/seats (ui/app/refresh.ts): those rows hang the same way.
    listed = {s["id"]: s["org_parent"] for s in get(api, "seats")["seats"]}
    people = {s["id"]: s["org_parent"] for s in api.get("/api/people", headers=headers()).json()["seats"]}
    assert listed == people and listed[agent["id"]] == "b:assistant"

    # A line through a closed seat still counts: B <- A (closed) <- C, so B cannot move under C.
    b = post(api, "seats", {"title": "B", "reports_to": "assistant"})
    a = post(api, "seats", {"title": "A", "reports_to": "seat:" + b["id"]})
    c_seat = post(api, "seats", {"title": "C", "reports_to": "seat:" + a["id"]})
    post(api, f"seats/{a['id']}/close", {})
    circle = api.patch(f"/api/v2/seats/{b['id']}", json={"reports_to": "seat:" + c_seat["id"]}, headers=headers())
    assert circle.status_code == 422 and circle.json()["error"]["code"] == "hierarchy", circle.text
    assert {s["id"]: s for s in get(api, "org")["seats"]}[b["id"]]["reports_to"] == "assistant"
    d = post(api, "seats", {"title": "D", "reports_to": "assistant"})
    moved = api.patch(f"/api/v2/seats/{d['id']}", json={"reports_to": "seat:" + c_seat["id"]}, headers=headers())
    assert moved.status_code == 200, moved.text
    # A circle stored before this check answers to nobody: placing under it ends, and only the owner may.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE seats SET reports_to=? WHERE id=?", ("seat:" + c_seat["id"], b["id"]))
    post(api, "seats", {"title": "E", "reports_to": "seat:" + d["id"]}, token="cara-test", expected=403)


def test_a_bots_own_setup_done_keeps_the_summary_it_gave_when_ready(api):
    onboarding(api, "ops")
    as_member(api, "ben@acme.example")
    token = as_bot(api, "ops")
    first = post(api, "bots/ops/onboarded", {"summary": "I know the audit checklist."}, token=token)

    # `tico bot setup-done` by the bot itself sends no summary: the one it gave with `tico bot ready` stays.
    done = post(api, "bots/ops/onboarded", {}, token=token)
    assert done["stage"] == "onboarding" and done["ready"]["summary"] == "I know the audit checklist."
    shown = get(api, "bots/ops", token="ben-test")
    assert shown["onboarding_ready"]["summary"] == "I know the audit checklist."
    # Nothing new is recorded: the summary keeps the time it was given.
    assert done["ready"]["at"] == shown["onboarding_ready"]["at"] == first["ready"]["at"]

    # A new summary said with the ready call replaces it.
    post(api, "bots/ops/onboarded", {"summary": "Now the queue too."}, token=token)
    assert get(api, "bots/ops", token="ben-test")["onboarding_ready"]["summary"] == "Now the queue too."


def test_renaming_a_seat_to_another_role_drops_the_template_it_was_set_up_with(api):
    seat = post(api, "seats", {"title": "Marketing lead", "description": "Runs marketing.", "reports_to": "assistant"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE seats SET template='growth-manager' WHERE id=?", (seat["id"],))

    def template():
        with api.app.state.store.read() as c:
            return c.execute("SELECT template FROM seats WHERE id=?", (seat["id"],)).fetchone()[0]

    # The same role said again, and a new description, keep the template the seat was set up with.
    same = api.patch(f"/api/v2/seats/{seat['id']}", json={"title": " Marketing lead "}, headers=headers())
    assert same.status_code == 200, same.text
    described = api.patch(
        f"/api/v2/seats/{seat['id']}", json={"description": "Runs all of marketing."}, headers=headers()
    )
    assert described.status_code == 200, described.text
    # Only the capitals change: the same role, so the template stays.
    recased = api.patch(f"/api/v2/seats/{seat['id']}", json={"title": "MARKETING LEAD"}, headers=headers())
    assert recased.status_code == 200, recased.text
    assert template() == "growth-manager"
    pinned = post(api, f"seats/{seat['id']}/recruit", {})["candidates"]
    assert [c["template"] for c in pinned] == ["growth-manager"]

    # A different role is a different job: the seat is no longer pinned to that template.
    renamed = api.patch(f"/api/v2/seats/{seat['id']}", json={"title": "Support lead"}, headers=headers())
    assert renamed.status_code == 200, renamed.text
    assert template() == ""
    assert len(post(api, f"seats/{seat['id']}/recruit", {})["candidates"]) > 1


def test_a_growth_pack_owner_goes_live_at_its_own_setup_done_and_may_file_tasks(api):
    """Initech, Nov 11: the SEO Owner, hired from a seat, ran its setup, said `tico bot setup-done` and stayed
    Onboarding, so its first ideas were refused until a person marked it complete. A card that goes live after setup
    (`live_after_setup`, the six growth channel packs) now goes live on the bot's own call, as a seat bot does."""
    onboarding(api, "ops")
    as_member(api, "ben@acme.example")
    token = as_bot(api, "ops")
    filed = {"owner": "ops", "title": "Publish the SEO playbook", "body": "x"}
    assert api.post("/api/v2/tasks", json=filed, headers=headers(token)).status_code >= 400  # still onboarding
    state = api.app.state.onboarding
    with api.app.state.store.transaction() as c:
        declared = state._declared(c, "ops")
        state._write_config(c, "ops", {**declared, "template": "seo-owner"})

    done = post(api, "bots/ops/onboarded", {"summary": "I know the pages that matter."}, token=token)
    assert done["stage"] == "active" and done["changed"] is True and done["onboarding_state"] == "onboarded"
    shown = get(api, "bots/ops", token="ben-test")
    assert shown["stage"] == "active" and "can_complete_onboarding" not in shown  # nothing left for a person to mark
    assert api.post("/api/v2/tasks", json=filed, headers=headers(token)).status_code == 200
    # Saying it again changes nothing.
    assert post(api, "bots/ops/onboarded", {}, token=token)["changed"] is False
