"""Listening's observation store and the shared intake (backend/listening.py): save what a sweep
saw, score it with the judge, route it, and let each receiver accept or reject what it was sent."""

import json
from pathlib import Path
from types import SimpleNamespace

from backend import listening as L
from backend.store import H
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401

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
                c.execute("INSERT INTO bots (slug, display_name, runtime, model, effort, cwd, host, state, created) "
                          "VALUES (?,?,?,?,?,?,?,?,?)", (slug, slug, "fake", "fake", "low", "", "keeper", "active", H.now()))
            if not c.execute("SELECT 1 FROM bot_config WHERE bot=?", (slug,)).fetchone():
                c.execute("INSERT INTO bot_config (bot, config_json, team, operator, description, reports_to, repo) "
                          "VALUES (?,?,?,?,?,?,?)", (slug, "{}", "marketing", "ana", slug, "cmo", "emp-" + slug))


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


def test_registry_question_versions_rescore_recent_posts_and_keep_vetoes(api):
    _bots(api)
    settings = api.app.state.store.settings
    (settings.registry_dir / L.LISTENING_FILE).write_text("""
destinations:
  leads: {category: custom_lead, threshold: 0.75, receiver: sales-ops, unless: {category: custom_veto, threshold: 0.70}}
""")
    qset = {"id": "listening-item", "version": 9, "summary": "Company questions", "questions": {
        "custom_lead": {"type": "noul", "instructions": "A team lead wants help"},
        "custom_veto": {"type": "noul", "instructions": "A vendor is pitching"}}}
    directory = settings.registry_dir / "questions"
    directory.mkdir()
    path = directory / "listening-item.json"
    path.write_text(json.dumps(qset))
    api.app.state.judge = engine = FakeJudge()
    saved = post(api, "listening/runs", {"source": "x", "query": "q", "status": "ok", "items": [
        _post("custom-1", "[custom_lead]"), _post("custom-2", "[custom_lead] [custom_veto]")]})
    result = post(api, "listening/judge", {})
    assert (result["question_set"], result["judged"], result["routed"]) == ("listening-item@9", 2, 1)
    assert [i["post"]["id"] for i in get(api, "intake")["items"]] == [saved["items"][0]["id"]]
    assert all(label == "listening-item@9" for _, label in engine.calls)
    assert post(api, "listening/judge", {})["judged"] == 0
    qset["version"] = 10
    path.write_text(json.dumps(qset))
    result = post(api, "listening/judge", {})
    assert (result["question_set"], result["judged"]) == ("listening-item@10", 2)
    assert len(get(api, "intake")["items"]) == 1, "rescoring does not duplicate an inbox row"


def test_missing_destination_and_veto_categories_are_logged(api, caplog):
    settings = api.app.state.store.settings
    (settings.registry_dir / L.LISTENING_FILE).write_text("""
destinations:
  lead: {category: unknown, threshold: 0.75, receiver: sales-ops, unless: {category: wrong_type, threshold: 0.70}}
""")
    qset = {"questions": {"wrong_type": {"type": "choice"}}}
    dests = L.destinations(settings, qset=qset)
    assert dests["lead"]["receiver"] == "bot:sales-ops"
    assert len(caplog.records) == 2
    assert "unknown" in caplog.text and "unless category 'wrong_type'" in caplog.text


def test_configuration_warnings_repeat_only_after_changes_and_memory_is_bounded(api, caplog, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(L, "_warning_revisions", L.OrderedDict())
    settings = api.app.state.store.settings
    path = settings.registry_dir / L.LISTENING_FILE
    source = "destinations:\n  leads: {category: unknown, threshold: 0.75, receiver: sales-ops}\n"
    path.write_text(source)
    qset = {"version": 1, "questions": {}}
    for _ in range(5):
        L.destinations(settings, qset=qset)
        L.destinations(settings, check_questions=False)  # Health reads must not rearm category logs.
    assert len(caplog.records) == 1
    path.write_text(source + "# changed destination configuration\n")
    L.destinations(settings, qset=qset)
    assert len(caplog.records) == 2
    qset["version"] = 2
    L.destinations(settings, qset=qset)
    assert len(caplog.records) == 3
    qset["questions"]["unknown"] = {"type": "noul"}
    L.destinations(settings, qset=qset)
    qset["questions"].clear()
    L.destinations(settings, qset=qset)
    assert len(caplog.records) == 4, "a correction rearms the warning if the problem returns"
    path.unlink()
    L.destinations(settings)
    path.write_text(source)
    L.destinations(settings, qset=qset)
    assert len(caplog.records) == 5, "removed and restored config is a new revision"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: L.destinations(settings, qset=qset), range(8)))
    assert len(caplog.records) == 5, "concurrent loads also suppress an unchanged warning"
    for index in range(L.WARNING_CACHE_LIMIT + 1):
        other = SimpleNamespace(registry_dir=Path("/fictional-registry") / str(index))
        L._warn_configuration(other, "questions", source, [], qset)
    assert len(L._warning_revisions) == L.WARNING_CACHE_LIMIT
    assert all(len(key[0]) == len(value) == 32 for key, value in L._warning_revisions.items())


def test_invalid_question_warnings_rearm_on_changed_file_without_echoing_it(api, caplog, monkeypatch):
    monkeypatch.setattr(L, "_warning_revisions", L.OrderedDict())
    settings = api.app.state.store.settings
    (settings.registry_dir / L.LISTENING_FILE).write_text("destinations:\n  leads: {category: lead, threshold: 0.75, receiver: sales-ops}\n")
    directory = settings.registry_dir / "questions"
    directory.mkdir()
    path = directory / "listening-item.json"
    path.write_text("private-invalid-fixture-one")
    L.destinations(settings)
    L.destinations(settings)
    assert len(caplog.records) == 1
    path.write_text("private-invalid-fixture-two")
    L.destinations(settings)
    L.destinations(settings)
    assert len(caplog.records) == 2 and "private-invalid-fixture" not in caplog.text


def test_malformed_destination_warnings_are_fixed_and_deduplicated(api, caplog, monkeypatch):
    monkeypatch.setattr(L, "_warning_revisions", L.OrderedDict())
    settings = api.app.state.store.settings
    secret = "private-destination-fixture"
    path = settings.registry_dir / L.LISTENING_FILE
    path.write_text(f"destinations:\n  leads: {{category: lead, threshold: {secret}, receiver: sales-ops}}\n")
    for _ in range(3):
        assert L.destinations(settings) == L.destinations(settings, check_questions=False) == {}
    assert len(caplog.records) == 1
    assert caplog.records[0].message == "listening.yaml: destination 'leads' skipped (invalid destination configuration)"
    assert secret not in caplog.text
    path.write_text("destinations:\n  leads: {category: lead, threshold: 0.75, receiver: sales-ops}\n")
    L.destinations(settings)
    path.write_text(f"destinations:\n  leads: {{category: lead, threshold: {secret}, receiver: sales-ops}}\n")
    L.destinations(settings)
    assert len(caplog.records) == 2 and secret not in caplog.text


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
