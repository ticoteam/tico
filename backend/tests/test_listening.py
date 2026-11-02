"""Listening's observation store and the shared intake (backend/listening.py): save what a sweep
saw, score it with the judge, route it, and let each receiver accept or reject what it was sent."""

import json

from backend import listening as L
from backend.store import H
from backend.tests.test_api import api, get, post, put, setup_attempt  # noqa: F401

RECEIVERS = ("listening", "librarian", "content-social", "influencer", "sales-ops", "doc-updater", "recruiting")

# The destinations a company writes in registry/listening.yaml; the hub ships none.
DESTINATIONS = """
destinations:
  market:   {category: market, threshold: 0.70, receiver: bot:librarian, what: a concrete fact about a company in the market}
  content:  {category: content, threshold: 0.75, receiver: content-social, readers: [bot:doc-updater], what: a question a post could answer}
  creators: {category: creator, threshold: 0.75, receiver: bot:influencer, what: a creator the company may want to reach}
  partners: {category: partner, threshold: 0.75, receiver: bot:recruiting, what: a consultancy or trainer, a partner lead}
  leads:    {category: lead, threshold: 0.75, receiver: bot:sales-ops, unless: {category: vendor_pitch, threshold: 0.70}, what: a team lead who might use the product}
"""


def _bots(api):
    (api.app.state.store.settings.registry_dir / L.LISTENING_FILE).write_text(DESTINATIONS)
    with api.app.state.store.transaction() as c:
        for slug in RECEIVERS:
            if not H.bot(c, slug):
                c.execute("INSERT INTO bots (id, slug, display_name, effort, state, created_at) VALUES (?,?,?,?,?,?)",
                          (slug, slug, slug, "low", "active", H.now()))
            if not c.execute("SELECT 1 FROM bot_config WHERE bot=?", (slug,)).fetchone():
                c.execute("INSERT INTO bot_config (bot, config, team, operator, description, reports_to, repo) "
                          "VALUES (?,?,?,?,?,?,?)",
                          (slug, '{"runtime":"fake","model":"fake","host":"keeper"}', "marketing", "ana", slug, "cmo",
                           "emp-" + slug))


def _token(api, slug):
    return setup_attempt(api, slug)[2]["token"]


def _post(native_id, text, author="@host1"):
    return {"native_id": native_id, "url": f"https://x.com/{author.strip('@')}/status/{native_id}",
            "author": author, "author_url": f"https://x.com/{author.strip('@')}", "content": text,
            "published_at": "2026-09-23T10:00:00Z"}


class FakeJudge:
    """Scores by keyword so a test says which categories a post should clear."""

    def __init__(self):
        self.calls = []

    def __call__(self, state, questions, label=None):
        self.calls.append((state, label))
        text = state["content"].lower()
        answers = {qid: {"type": "noul", "noul": 0.05} for qid in questions}
        for qid in questions:
            if f"[{qid}]" in text:
                answers[qid]["noul"] = 0.9
        return {"answers": answers, "ms": 1, "model": "fake-judge", "usage": {}}


def test_only_listening_and_the_owner_save_runs_and_judgments(api):
    _bots(api)
    coo = _token(api, "coo")
    post(api, "listening/runs", {"source": "x", "query": "q", "status": "ok"}, token=coo, expected=403)
    post(api, "listening/runs", {"source": "X Bookmarks", "query": "q", "status": "ok"}, expected=422)
    saved = post(api, "listening/runs", {"source": "x", "query": "q", "status": "ok", "items": [_post("9", "hi")]})
    post(api, "listening/judgments", {"judgments": [{"item_id": saved["items"][0]["id"], "question_set": "x@1",
                                                     "scores": {"lead": 0.9}}]}, token=coo, expected=403)
    post(api, "listening/judgments", {"judgments": [{"item_id": saved["items"][0]["id"], "question_set": "x@1",
                                                     "scores": {"lead": 1.5}}]}, expected=422)


def test_a_receiver_sees_and_resolves_only_its_own_inbox(api):
    _bots(api)
    listening = _token(api, "listening")
    api.app.state.judge = FakeJudge()
    saved = post(api, "listening/runs", {"source": "x", "query": "q", "status": "ok", "items": [
        _post("1", "Creator asks how teams handle standups [creator] [content]")]}, token=listening)
    post(api, "listening/judge", {}, token=listening)
    sales = _token(api, "influencer")
    inbox = get(api, "intake", token=sales)["items"]
    assert [i["destination"] for i in inbox] == ["creators"]
    assert inbox[0]["post"]["author"] == "@host1" and inbox[0]["routed_because"] == "creator >= 0.75"
    get(api, "intake?destination=content", token=sales, expected=403)
    content_id = get(api, "intake?destination=content", token=listening)["items"][0]["id"]
    post(api, f"intake/{content_id}/resolve", {"status": "accepted", "receiver_ref": "x"}, token=sales, expected=403)
    sid = inbox[0]["id"]
    post(api, f"intake/{sid}/resolve", {"status": "accepted"}, token=sales, expected=422)
    post(api, f"intake/{sid}/resolve", {"status": "rejected"}, token=sales, expected=422)
    done = post(api, f"intake/{sid}/resolve", {"status": "accepted", "receiver_ref": "instagram:@host1"}, token=sales)
    assert done["intake"]["status"] == "accepted" and done["intake"]["decided_at"]
    # Saying it again is harmless; changing the verdict is refused.
    assert post(api, f"intake/{sid}/resolve", {"status": "accepted", "receiver_ref": "instagram:@host1"},
                token=sales)["intake"]["id"] == sid
    post(api, f"intake/{sid}/resolve", {"status": "rejected", "reason": "a travel account"}, token=sales, expected=409)
    assert get(api, "intake", token=sales)["items"] == []
    assert get(api, "intake?status=accepted", token=sales)["items"][0]["receiver_ref"] == "instagram:@host1"
    # A receiver traces what it was sent; another bot cannot open the post at all.
    assert get(api, f"listening/items/{saved['items'][0]['id']}", token=sales)["intake"][0]["destination"] == "creators"
    get(api, f"listening/items/{saved['items'][0]['id']}", token=_token(api, "finance"), expected=403)
    stats = get(api, "listening/stats", token=listening)
    assert stats["destinations"]["creators"]["precision"] == 1.0 and stats["destinations"]["content"]["new"] == 1
    assert stats["coverage"]["x"]["ok"]["runs"] == 1


def test_hub_sql_shows_posts_only_to_listening_the_owner_and_their_receivers(api):
    _bots(api)
    listening = _token(api, "listening")
    api.app.state.judge = FakeJudge()
    post(api, "listening/runs", {"source": "x", "query": "q", "status": "ok", "items": [
        _post("1", "A creator for team leads [creator]"), _post("2", "Atlia news [market]")]}, token=listening)
    post(api, "listening/judge", {}, token=listening)

    def rows(sql, token):
        return post(api, "sql", {"sql": sql}, token=token)["rows"]

    finance, sales = _token(api, "finance"), _token(api, "influencer")
    assert rows("SELECT count(*) FROM listen_runs", finance)[0][0] == 1
    assert rows("SELECT count(*) FROM listen_items", finance)[0][0] == 0
    assert rows("SELECT count(*) FROM intake_items", finance)[0][0] == 0
    assert rows("SELECT native_id FROM listen_items", sales) == [["1"]]
    assert rows("SELECT destination FROM intake_items", sales) == [["creators"]]
    assert rows("SELECT count(*) FROM listen_judgments", sales)[0][0] == 1
    assert rows("SELECT count(*) FROM listen_items", listening)[0][0] == 2
    assert rows("SELECT count(*) FROM listen_items", "ana-test")[0][0] == 2


def test_invalid_registry_questions_refuse_decisions_without_private_details(api):
    settings = api.app.state.store.settings
    directory = settings.registry_dir / "questions"
    directory.mkdir()
    (directory / "listening-item.json").write_text('{"private-question-fixture-value": [}')
    api.app.state.judge = engine = FakeJudge()
    response = post(api, "listening/judge", {}, expected=422)
    assert response["error"]["code"] == "judge_questions"
    assert "private-question-fixture-value" not in json.dumps(response)
    assert engine.calls == []


# ----------------------------------------------------------------------------- Settings > Listening
def _settings(api):
    return api.app.state.store.settings


def _merged(api):
    with api.app.state.store.read() as c:
        return L.destinations(_settings(api), c, check_questions=False)


def _save(api, key, token="ana-test", expected=200, **fields):
    body = {"category": "lead", "threshold": 0.8, "receiver": "bot:sales-ops", **fields}
    return put(api, f"listening/destinations/{key}", body, token=token, expected=expected)


def test_a_destination_saved_in_settings_wins_over_the_file_on_the_same_key_and_routes_a_post(api):
    _bots(api)
    api.app.state.judge = FakeJudge()
    _save(api, "leads", threshold=0.5, receiver="bot:influencer")
    _save(api, "extra", category="content", receiver="librarian")
    merged = _merged(api)
    assert merged["leads"]["receiver"] == "bot:influencer" and merged["leads"]["threshold"] == 0.5
    assert "unless" not in merged["leads"], "the saved row replaces the file's whole entry"
    assert merged["extra"]["receiver"] == "bot:librarian" and merged["content"]["receiver"] == "bot:content-social"
    listing = get(api, "listening/destinations")
    assert {d["key"]: d["source"] for d in listing["destinations"]}["leads"] == "settings"
    assert {d["key"]: d["source"] for d in listing["destinations"]}["market"] == "yaml"
    assert [d for d in listing["destinations"] if d["key"] == "leads"][0]["overrides_yaml"] is True
    listening = _token(api, "listening")
    post(api, "listening/runs", {"source": "x", "query": "q", "status": "ok", "items": [
        _post("1", "A team lead asks for a tool [lead]")]}, token=listening)
    post(api, "listening/judge", {}, token=listening)
    assert [i["destination"] for i in get(api, "intake?destination=leads", token=_token(api, "influencer"))["items"]] == ["leads"]


def test_a_destination_the_file_would_refuse_is_refused_when_it_is_saved(api):
    _bots(api)
    for bad in ({"category": "vendor_pitch_typo"}, {"category": "lead", "threshold": 0}, {"threshold": 1.5},
                {"receiver": "bot:nobody"}, {"receiver": "human:ana"}, {"readers": ["bot:nobody"]},
                {"unless": {"category": "nope", "threshold": 0.5}}, {"unless": {"category": "vendor_pitch", "threshold": 2}}):
        assert _save(api, "bad", expected=422, **bad)["error"]["code"] == "invalid", bad
    _save(api, "Not A Key", expected=422)
    assert "bad" not in _merged(api)
    ok = _save(api, "good", readers=["bot:doc-updater", "bot:doc-updater"], unless={"category": "vendor_pitch", "threshold": 0.7})
    assert ok["destination"]["readers"] == ["bot:doc-updater"] and ok["destination"]["unless"]["threshold"] == 0.7


def test_only_the_owner_writes_destinations_and_only_owner_and_admins_read_them(api):
    _bots(api)
    for token in ("ben-test", "cara-test", _token(api, "librarian")):
        _save(api, "mine", token=token, expected=403)
        post(api, "listening/destinations/market/remove", {}, token=token, expected=403)
    assert "mine" not in _merged(api)
    get(api, "listening/destinations", token="ben-test")
    get(api, "listening/destinations", token="cara-test", expected=403)
    get(api, "listening/destinations", token=_token(api, "listening"), expected=403)


def test_a_destination_only_the_file_has_cannot_be_removed_and_a_saved_one_can(api):
    _bots(api)
    assert post(api, "listening/destinations/market/remove", {}, expected=409)["error"]["code"] == "yaml"
    post(api, "listening/destinations/nothing/remove", {}, expected=404)
    _save(api, "market", threshold=0.9)
    assert _merged(api)["market"]["threshold"] == 0.9
    post(api, "listening/destinations/market/remove", {})
    assert _merged(api)["market"]["threshold"] == 0.70, "removing the saved one brings the file's back"


def test_the_market_destination_is_offered_once_and_a_removal_sticks(api):
    settings = _settings(api)
    with api.app.state.store.transaction() as c:
        assert L.seed_market(c, settings) is False, "no Librarian, nothing to offer"
    _bots(api)
    (settings.registry_dir / L.LISTENING_FILE).write_text("destinations: {}")
    with api.app.state.store.transaction() as c:
        assert L.seed_market(c, settings) is True
    assert _merged(api)["market"] == {"category": "market", "threshold": 0.7, "receiver": "bot:librarian",
                                      "what": "a post about the market: competitors, buyers, prices, regulation"}
    with api.app.state.store.transaction() as c:
        assert L.seed_market(c, settings) is False
    post(api, "listening/destinations/market/remove", {})
    with api.app.state.store.transaction() as c:
        assert L.seed_market(c, settings) is False
    assert "market" not in _merged(api)


def test_the_market_destination_is_not_offered_when_one_already_routes_market(api):
    _bots(api)                                  # the file already sends category market to the Librarian
    with api.app.state.store.transaction() as c:
        assert L.seed_market(c, _settings(api)) is False
        assert c.execute("SELECT count(*) FROM listening_destinations").fetchone()[0] == 0


def test_health_reads_destinations_saved_in_settings(api):
    from backend import health
    _bots(api)
    (_settings(api).registry_dir / L.LISTENING_FILE).write_text("destinations: {}")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO listening_destinations (key, category, threshold, receiver, readers, unless, what) "
                  "VALUES ('typo','no_such_category',0.7,'bot:librarian','[]','null','')")
    with api.app.state.store.read() as c:
        check = health._listening(c, _settings(api))
    assert check["status"] == "warn" and "typo" in check["summary"]
    _save(api, "typo", category="market", receiver="bot:librarian")
    with api.app.state.store.read() as c:
        assert health._listening(c, _settings(api)) is None


def test_hub_sql_shows_saved_destinations_to_the_owner_and_admins_only(api):
    _bots(api)
    _save(api, "extra", category="content")
    assert post(api, "sql", {"sql": "SELECT key FROM listening_destinations"})["rows"] == [["extra"]]
    assert post(api, "sql", {"sql": "SELECT count(*) FROM listening_destinations"}, token=_token(api, "finance"))["rows"][0][0] == 0


def test_stats_carry_a_daily_chart_and_the_callers_recent_posts(api):
    """Market › Listening reads posts per day by source and the newest routed posts; a receiver sees only its own."""
    _bots(api)
    listening = _token(api, "listening")
    api.app.state.judge = FakeJudge()
    post(api, "listening/runs", {"source": "x", "query": "q", "status": "ok", "items": [
        _post("1", "Creator asks how teams handle standups [creator] [content]"), _post("2", "Nothing to see")]},
        token=listening)
    post(api, "listening/runs", {"source": "reddit", "query": "q", "status": "ok", "items": [
        _post("3", "A team lead asks for a hosted plan [lead]", author="@lead")]}, token=listening)
    post(api, "listening/judge", {}, token=listening)
    stats = get(api, "listening/stats")
    assert [d["sources"] for d in stats["daily"]] == [{"x": 2, "reddit": 1}]
    assert sorted(tuple(r["destinations"]) for r in stats["recent"]) == [("content", "creators"), ("leads",)]
    mine = get(api, "listening/stats", token=_token(api, "influencer"))["recent"]
    assert [(r["source"], r["destinations"]) for r in mine] == [("x", ["creators"])]
    assert mine[0]["text"].startswith("Creator asks") and mine[0]["author"] == "@host1"
