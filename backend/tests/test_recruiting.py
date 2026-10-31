"""Recruiting a seat (backend/recruiting.py): bot-lifecycle-spec.md section 3, acceptance tests 6 to 8. Test 9 (Team >
Add bot makes a seat first) is the page's: ui/tests/team-groups.cjs."""
import json

import pytest

from backend import recruiting
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.test_api import api, as_member, get, headers, post  # noqa: F401
from backend.tests.test_strategy_bot import as_bot


def strategy_bot(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES('goal-manager','Strategy Bot','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT OR IGNORE INTO bot_config(bot,config_json,operator) VALUES('goal-manager','{}','ana')")
    return as_bot(api, "goal-manager")


def providers(api, *enabled):
    current = get(api, "providers")
    assert api.put("/api/v2/providers", headers=headers(), json={
        "enabled": list(enabled), "expected_revision": current["revision"]}).status_code == 200


def test_org_model_recommendations_are_only_the_published_starter_catalog(api):
    models = get(api, "org-models")["models"]
    software = next(model for model in models if model["model"] == "software")
    assert software["name"] == "Software company"
    assert {seat["team"] for seat in software["seats"]} >= {"engineering", "product"}
    assert any(seat["template"] == "engineering-lead" for seat in software["seats"])
    assert all(set(model) == {"model", "name", "about", "seats"} for model in models)
    assert all("bots" not in model and "people" not in model and "tasks" not in model for model in models)
    from clients import hubtools
    offered = {tool["name"] for tool in hubtools.listing(kind="assistant")}
    assert "hub_org_models" in offered and "hub_assistant_propose" in offered


def test_post_a_job_drafts_a_description_from_the_title_at_once_and_redraft_rewrites_it_once(api, monkeypatch):
    import time
    started = time.monotonic()
    drafted = post(api, "seats/draft", {"title": "Customer support lead", "team": "Support"})
    assert time.monotonic() - started < 1
    assert drafted["source"] == "catalog"
    text = drafted["description"]
    assert "\n\nWhat you'll do\n- " in text and "\n\nWorks in: " in text and not text.startswith("What")
    assert post(api, "seats/draft", {"title": "Qwxz vrrb"}) == {"description": "", "source": "none"}
    # Nothing is stored: drafting is not posting.
    assert get(api, "seats")["seats"] == []

    # Redraft asks the model once, for this title and boss; a failure, or an answer too short to use, keeps the
    # composed text. The first draft never asks.
    asked = []
    def rewrite(composed, title, boss):
        asked.append((title, boss))
        return "Leads our support desk.\n\nWhat you'll do\n- Answer every ticket the same day"
    monkeypatch.setattr(recruiting, "_rewrite_call", rewrite)
    post(api, "seats/draft", {"title": "Customer support lead"})
    assert asked == []
    again = post(api, "seats/draft", {"title": "Customer support lead", "reports_to": "coo", "redraft": True})
    assert again["source"] == "model" and again["description"].startswith("Leads our support desk.")
    assert asked == [("Customer support lead", get(api, "bots/coo")["display_name"])]
    for broken in (lambda *_: (_ for _ in ()).throw(TimeoutError("slow")), lambda *_: "Too short."):
        monkeypatch.setattr(recruiting, "_rewrite_call", broken)
        assert post(api, "seats/draft", {"title": "Customer support lead", "redraft": True}) == drafted
    post(api, "seats/draft", {"title": ""}, expected=422)

    # Only a person drafts, and the model hears only a manager the caller could post under: Cara, a member, gets her
    # own name sent but not the COO's, which is outside her chain; a name nobody has is dropped too.
    asked.clear()
    monkeypatch.setattr(recruiting, "_rewrite_call", rewrite)
    as_member(api, "ben@acme.example")
    for reports_to in ("coo", "nobody-here", "human:ana"):
        post(api, "seats/draft", {"title": "Customer support lead", "reports_to": reports_to, "redraft": True}, token="cara-test")
    post(api, "seats/draft", {"title": "Customer support lead", "reports_to": "human:cara", "redraft": True}, token="cara-test")
    with api.app.state.store.read() as c:
        cara = H.human(c, "cara")["name"]
    assert asked == [("Customer support lead", "")] * 3 + [("Customer support lead", cara)]
    bot = as_bot(api, "coo")
    assert api.post("/api/v2/seats/draft", headers=bot, json={"title": "Customer support lead"}).status_code == 403


def test_redraft_is_bounded_in_all_and_never_leaves_a_rehearsal(api, monkeypatch):
    import time
    from clients import docs_qa
    composed = post(api, "seats/draft", {"title": "Customer support lead"})
    body = {"title": "Customer support lead", "redraft": True}
    monkeypatch.setattr(recruiting, "REWRITE_SECONDS", 0.3)

    def timed():
        started = time.monotonic()
        out = post(api, "seats/draft", body)
        return out, time.monotonic() - started

    # The bound covers finding the key: a secret store that hangs does not hold the draft.
    monkeypatch.setattr(docs_qa, "key", lambda: time.sleep(3) or "k")
    out, took = timed()
    assert out == composed and took < 1.5

    # And reading the answer: a server that trickles it is cut off at the same bound.
    class Answer:
        def __init__(self, read):
            self._read = read
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, n):
            return self._read(n)
    monkeypatch.setattr(docs_qa, "key", lambda: "k")
    monkeypatch.setattr(recruiting, "urlopen", lambda *a, **k: Answer(lambda n: time.sleep(3) or b"{}"))
    out, took = timed()
    assert out == composed and took < 1.5

    # An answer longer than the cap is not read past it, and is not used.
    reads = []
    huge = json.dumps({"candidates": [{"content": {"parts": [{"text": "Leads support. " * 20000}]}}]}).encode()
    monkeypatch.setattr(recruiting, "urlopen", lambda *a, **k: Answer(lambda n: reads.append(n) or huge[:n]))
    assert post(api, "seats/draft", body) == composed and reads == [recruiting.REWRITE_BYTES + 1]
    fine = json.dumps({"candidates": [{"content": {"parts": [{"text": "Leads our support desk, every day of the week."}]}}]}).encode()
    monkeypatch.setattr(recruiting, "urlopen", lambda *a, **k: Answer(lambda n: fine[:n]))
    assert post(api, "seats/draft", body)["source"] == "model"

    # A rehearsal or a demo never reads a key or asks a model.
    touched = []
    monkeypatch.setattr(docs_qa, "key", lambda: touched.append("key") or "k")
    monkeypatch.setattr(recruiting, "urlopen", lambda *a, **k: touched.append("model") or Answer(lambda n: fine[:n]))
    for flag in ("rehearsal", "demo"):
        monkeypatch.setattr(api.app.state.onboarding.settings, flag, True)
        assert post(api, "seats/draft", body) == composed
        monkeypatch.setattr(api.app.state.onboarding.settings, flag, False)
    assert touched == []


def test_the_strategy_bot_no_longer_drafts_and_people_s_words_stay_theirs(api):
    from backend import lifecycle
    with api.app.state.store.transaction() as c:
        # The seats table as the release before recruiting left it, with a person's text: an upgrade keeps it theirs.
        c.execute("DROP TABLE seats")
        c.execute(lifecycle.SCHEMA)
        c.execute("INSERT INTO seats(id,title,description,reports_to,created,created_by,updated,updated_by) "
                  "VALUES('s-old','Role','Keeps the books.','coo',?,'human:ana',?,'human:ben')", (H.now(), H.now()))
        lifecycle.ensure(c)
        owner = Identity("human:ana", "owner")
        chart = lifecycle.create_seat(api.app.state.auth, c, owner, "Analyst", "From the chart model.", "coo",
                                      description_by=recruiting.CHART)
    mine = post(api, "seats", {"title": "Support lead", "reports_to": "coo", "description": "Answers email."})
    assert get(api, f"seats/{mine['id']}")["description_by"] == "person"
    assert get(api, "seats/s-old")["description_by"] == "person"
    shown = get(api, f"seats/{chart['id']}")
    assert shown["description_by"] == "chart" and "draft" not in shown and shown["can_edit"] is True
    for route in (f"seats/{mine['id']}/open", f"seats/{mine['id']}/draft"):
        assert api.post("/api/v2/" + route, headers=headers(), json={}).status_code in (404, 405)


def test_recruit_answers_at_once_with_up_to_eight_applicants_best_first_each_with_a_match_and_a_model(api):
    providers(api, "anthropic", "openai")
    seat = post(api, "seats", {"title": "Customer support lead", "reports_to": "coo",
                               "description": "Answers customer email and chat and keeps the help center current."})
    found = post(api, f"seats/{seat['id']}/recruit", {})
    candidates = found["candidates"]
    assert 3 < len(candidates) <= recruiting.CANDIDATES == 8
    assert "support" in candidates[0]["template"] and candidates[0]["match"] == "strong"
    tiers = [c["match"] for c in candidates]
    assert tiers == sorted(tiers, key=["strong", "good", ""].index)        # best first
    choices = {m["id"] for m in found["models"]}
    for c in candidates:
        assert {"template", "name", "icon", "department", "line", "tags", "owns", "prerequisites", "match",
                "source"} <= set(c) and "reason" not in c and c["source"] == "template"
        assert c["line"] and "\n" not in c["line"] and len(c["line"]) <= 90
        assert len(c["tags"]) <= 6 and all(n["state"] in ("connected", "on_hire", "not_set") for n in c["prerequisites"])
        assert c["model"] in choices                                 # suggested, from a provider the team turned on
    assert {m["provider"] for m in found["models"]} <= {"anthropic", "openai"}

    # Recruiting is for whoever may fill the seat.
    as_member(api, "ben@acme.example")
    post(api, f"seats/{seat['id']}/recruit", {}, token="cara-test", expected=403)


def test_picking_a_candidate_creates_the_bot_in_onboarding_in_the_seat_and_only_then_its_repo(api):
    providers(api, "anthropic", "openai")
    seat = post(api, "seats", {"title": "Support lead", "reports_to": "coo",
                               "description": "Runs customer support and reports weekly on response times."})
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

    hired = post(api, f"seats/{seat['id']}/hire", {"template": pick["template"], "model": other, "name": "Support Desk"})
    slug = hired["slug"]
    bot = get(api, f"bots/{slug}")
    assert (bot["stage"], bot["reports_to"], bot["display_name"], hired["name"]) == (
        "onboarding", "coo", "Support Desk", "Support Desk")
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
    again = api.post(f"/api/v2/seats/{seat['id']}/hire", json={"template": pick["template"]}, headers=headers())
    assert again.status_code == 409 and "Support Desk" in again.text


def test_a_bot_or_an_unconfirmed_assistant_never_picks_and_a_refused_pick_leaves_nothing(api, monkeypatch):
    providers(api, "anthropic", "openai")
    strategy_token = strategy_bot(api)
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
    for caller in (strategy_token, headers("ana-assistant")):          # a bot; the Assistant's own turn for the owner
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
