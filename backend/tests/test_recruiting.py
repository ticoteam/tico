"""Recruiting a seat (backend/recruiting.py): bot-lifecycle-spec.md section 3, acceptance tests 6 to 8. Test 9 (Team >
Add bot makes a seat first) is the page's: ui/tests/team-groups.cjs."""
import json

import pytest

from backend import recruiting
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.test_api import api, as_member, get, headers, post  # noqa: F401
from backend.tests.test_ceo_bot import as_bot


def ceo_bot(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES('goal-manager','CEO Bot','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT OR IGNORE INTO bot_config(bot,config_json,operator) VALUES('goal-manager','{}','ana')")
    return as_bot(api, "goal-manager")


def providers(api, *enabled):
    current = get(api, "providers")
    assert api.put("/api/v2/providers", headers=headers(), json={
        "enabled": list(enabled), "expected_revision": current["revision"]}).status_code == 200


def draft_tasks(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,status,owner FROM tasks WHERE owner='bot:goal-manager' "
                                           "AND title LIKE 'Draft the job description:%'")]


def test_the_owner_opening_a_seat_gets_the_ceo_bots_draft_and_a_person_s_words_are_never_drafted_over(api):
    ceo = ceo_bot(api)
    seat = post(api, "seats", {"title": "Support lead", "reports_to": "human:ben"})
    # Someone else opening it asks the CEO Bot nothing: it works for the owner alone.
    assert post(api, f"seats/{seat['id']}/open", {}, token="ben-test")["draft"]["state"] == "none"
    assert draft_tasks(api) == []
    opened = post(api, f"seats/{seat['id']}/open", {})
    assert opened["draft"]["state"] == "drafting" and opened["can_edit"] is True
    post(api, f"seats/{seat['id']}/open", {})                       # opening again asks once
    assert [t["status"] for t in draft_tasks(api)] == ["open"]

    # Only the CEO Bot drafts, on its own credential.
    post(api, f"seats/{seat['id']}/draft", {"text": "Owns the support queue end to end."}, token="ben-test", expected=403)
    drafted = api.post(f"/api/v2/seats/{seat['id']}/draft", headers=ceo,
                       json={"text": "Owns the support queue end to end and keeps answers kind and fast."})
    assert drafted.status_code == 200, drafted.text
    shown = get(api, f"seats/{seat['id']}")
    assert shown["draft"]["state"] == "drafted" and shown["description_by"] == "ceo_bot"
    assert shown["description"].startswith("Owns the support queue")
    assert [t["status"] for t in draft_tasks(api)] == ["done"]

    # Anyone who may change the seat edits it; from then on the words are theirs.
    edited = api.patch(f"/api/v2/seats/{seat['id']}", json={"description": "Runs support; reports weekly."},
                       headers=headers("ben-test"))
    assert edited.status_code == 200 and get(api, f"seats/{seat['id']}")["description_by"] == "person"

    # A seat whose description a person wrote, or one they edit while the CEO Bot drafts, keeps their words.
    mine = post(api, "seats", {"title": "Bookkeeper", "description": "Keeps the books.", "reports_to": "coo"})
    assert post(api, f"seats/{mine['id']}/open", {})["draft"]["state"] == "none"
    chart = post(api, "seats", {"title": "Analyst", "reports_to": "coo"})
    post(api, f"seats/{chart['id']}/open", {})
    assert api.patch(f"/api/v2/seats/{chart['id']}", json={"description": "Mine."}, headers=headers()).status_code == 200
    late = api.post(f"/api/v2/seats/{chart['id']}/draft", headers=as_bot(api, "goal-manager"),
                    json={"text": "An analyst who digs into the numbers every week."})
    assert late.status_code == 409 and "theirs stays" in late.text
    assert get(api, f"seats/{chart['id']}")["description"] == "Mine."
    assert sorted(t["status"] for t in draft_tasks(api)) == ["declined", "done"]


def test_an_upgrade_keeps_job_descriptions_people_wrote_before_authorship_was_recorded(api):
    from backend import lifecycle
    ceo = ceo_bot(api)
    with api.app.state.store.transaction() as c:
        # The seats table as the release before recruiting left it, with a person's text, an edited one and an empty one.
        c.execute("DROP TABLE seats")
        c.execute(lifecycle.SCHEMA)
        for sid, text in (("s-old", "Keeps the books."), ("s-edited", "Runs payroll; edited."), ("s-empty", "")):
            c.execute("INSERT INTO seats(id,title,description,reports_to,created,created_by,updated,updated_by) "
                      "VALUES(?,?,?,'coo',?,'human:ana',?,'human:ben')", (sid, "Role " + sid, text, H.now(), H.now()))
        lifecycle.ensure(c)
        lifecycle.ensure(c)
        owner = Identity("human:ana", "owner")
        chart = lifecycle.create_seat(api.app.state.auth, c, owner, "Analyst", "From the chart model.", "coo",
                                      description_by=recruiting.CHART)
    for sid in ("s-old", "s-edited"):
        opened = post(api, f"seats/{sid}/open", {})
        assert opened["draft"]["state"] == "none" and opened["description_by"] == "person"
        assert get(api, f"seats/{sid}")["description"] in ("Keeps the books.", "Runs payroll; edited.")
    assert post(api, "seats/s-empty/open", {})["draft"]["state"] == "drafting"
    assert post(api, f"seats/{chart['id']}/open", {})["draft"]["state"] == "drafting"
    assert len(draft_tasks(api)) == 2
    with api.app.state.store.transaction() as c:      # even with a stray draft task, the old words stay
        c.execute("UPDATE seats SET draft_task=(SELECT draft_task FROM seats WHERE id='s-empty') WHERE id='s-old'")
    refused = api.post("/api/v2/seats/s-old/draft", headers=ceo, json={"text": "A bookkeeper who closes every month on time."})
    assert refused.status_code == 409 and get(api, "seats/s-old")["description"] == "Keeps the books."


def test_recruit_answers_at_once_with_one_recommended_candidate_each_with_a_reason_and_a_model(api):
    providers(api, "anthropic", "openai")
    seat = post(api, "seats", {"title": "Customer support lead", "reports_to": "coo",
                               "description": "Answers customer email and chat and keeps the help center current."})
    found = post(api, f"seats/{seat['id']}/recruit", {})
    candidates = found["candidates"]
    assert 1 <= len(candidates) <= 3 and sum(c["recommended"] for c in candidates) == 1
    assert candidates[0]["recommended"] and "support" in candidates[0]["template"]
    choices = {m["id"] for m in found["models"]}
    for c in candidates:
        assert c["reason"] and "\n" not in c["reason"] and len(c["reason"]) <= 140
        assert c["model"] in choices                                 # suggested, from a provider the team turned on
    assert {m["provider"] for m in found["models"]} <= {"anthropic", "openai"}

    # Recruiting is for whoever may fill the seat.
    as_member(api, "ben@acme.example")
    post(api, f"seats/{seat['id']}/recruit", {}, token="cara-test", expected=403)


def test_picking_a_candidate_creates_the_bot_in_onboarding_in_the_seat_and_only_then_its_repo(api):
    providers(api, "anthropic", "openai")
    ceo = ceo_bot(api)
    seat = post(api, "seats", {"title": "Support lead", "reports_to": "coo"})
    assert post(api, f"seats/{seat['id']}/open", {})["draft"]["state"] == "drafting"
    assert api.post(f"/api/v2/seats/{seat['id']}/draft", headers=ceo, json={
        "text": "Runs customer support and reports weekly on response times."}).status_code == 200
    found = post(api, f"seats/{seat['id']}/recruit", {})
    pick = found["candidates"][0]
    other = next(m["id"] for m in found["models"] if m["id"] != pick["model"])
    with api.app.state.store.read() as c:
        before = {r["slug"] for r in c.execute("SELECT slug FROM bots")}

    # Nobody outside the seat's chain picks, and a model the team has not turned on is refused; nothing is created.
    as_member(api, "ben@acme.example")
    post(api, f"seats/{seat['id']}/hire", {"template": pick["template"]}, token="cara-test", expected=403)
    post(api, f"seats/{seat['id']}/hire", {"template": pick["template"], "model": "gemini-3-pro"}, expected=422)
    post(api, f"seats/{seat['id']}/hire", {"template": "botops"}, expected=422)       # a built-in is never a candidate
    with api.app.state.store.read() as c:
        assert {r["slug"] for r in c.execute("SELECT slug FROM bots")} == before

    below = post(api, "seats", {"title": "Support agent", "reports_to": "coo"})
    with api.app.state.store.transaction() as c:   # a seat may report to another seat (the chart import makes these)
        c.execute("UPDATE seats SET reports_to=? WHERE id=?", ("seat:" + seat["id"], below["id"]))

    hired = post(api, f"seats/{seat['id']}/hire", {"template": pick["template"], "model": other})
    slug = hired["slug"]
    bot = get(api, f"bots/{slug}")
    assert (bot["stage"], bot["reports_to"], bot["display_name"]) == ("onboarding", "coo", "Support lead")
    with api.app.state.store.read() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()[0])
        assert H.bot(c, slug)["model"] == other
        # The repository is built from this definition once the bot exists (the computer's `materialize`).
        assert config["materialize"] is True and config["template"] == pick["template"]
        assert "## Job description\nRuns customer support" in config["instructions"]
        kinds = [r["kind"] for r in c.execute("SELECT action AS kind FROM events WHERE target IN (?,?) ORDER BY rowid",
                                              (slug, seat["id"]))]
    assert kinds.index("bot.definition_created") < kinds.index("bot.needs_onboarding")
    # The seat is filled: off the Recruiting list, and a second pick is refused.
    open_seats = {s["id"]: s for s in get(api, "seats")["seats"]}
    assert seat["id"] not in open_seats and open_seats[below["id"]]["reports_to"] == slug
    assert [t["status"] for t in draft_tasks(api)] == ["done"]
    again = api.post(f"/api/v2/seats/{seat['id']}/hire", json={"template": pick["template"]}, headers=headers())
    assert again.status_code == 409 and "Support lead" in again.text


def test_a_bot_or_an_unconfirmed_assistant_never_picks_and_a_refused_pick_leaves_nothing(api, monkeypatch):
    providers(api, "anthropic", "openai")
    ceo = ceo_bot(api)
    seat = post(api, "seats", {"title": "Support lead", "reports_to": "coo", "description": "Answers customer email."})
    pick = post(api, f"seats/{seat['id']}/recruit", {})["candidates"][0]
    # A template that is not a starter: BotOps builds its repository, so a team with no BotOps cannot hire it.
    real = recruiting._recruitable
    def no_starters(onboarding):
        value, cards = real(onboarding)
        return value, [{**card, "raw": {**card["raw"], "starter": False}} for card in cards]
    monkeypatch.setattr(recruiting, "_recruitable", no_starters)
    api.app.state.store.settings.test_identities["ana-assistant"] = Identity("human:ana", "owner", via="assistant")

    def state():
        with api.app.state.store.read() as c:
            return ({r["slug"] for r in c.execute("SELECT slug FROM bots")},
                    dict(c.execute("SELECT filled_by,closed_at,reports_to FROM seats WHERE id=?", (seat["id"],)).fetchone()),
                    c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Set up %'").fetchone()[0])
    before = state()
    for caller in (ceo, headers("ana-assistant")):          # a bot; the Assistant's own turn for the owner
        assert api.post(f"/api/v2/seats/{seat['id']}/hire", headers=caller,
                        json={"template": pick["template"]}).status_code == 403
    # The pick's own rule, behind the route's: a bot, or anyone acting for a person without their confirmation.
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM seats WHERE id=?", (seat["id"],)).fetchone()
        for who in (Identity("bot:goal-manager", "bot"), Identity("human:ana", "owner", via="botops")):
            with pytest.raises(Problem, match="Only a person picks"):
                recruiting._may_hire(api.app.state.auth, c, who, row)
    refused = api.post(f"/api/v2/seats/{seat['id']}/hire", headers=headers(), json={"template": pick["template"]})
    assert refused.status_code == 409 and "BotOps" in refused.text
    assert state() == before
