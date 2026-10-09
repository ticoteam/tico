"""A person's external agents synced in (Grok Bot, Dots): placed under them, transcripts copied once, never dispatched to."""

from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401

GROK_ID = "4a7e880c-eb8e-49df-9789-3abfff190aa7"


def designer(**overrides):
    return {"grok_id": GROK_ID, "name": "Tico Designer", "description": "Designs Tico's pages.",
            "instructions": "You design Tico. Keep it plain.",
            "messages": [{"role": "user", "text": "Make the org chart calmer.", "at": "2026-09-27T10:00:00Z"},
                         {"role": "bot", "text": "Softer lines, fewer badges.", "at": "2026-09-27T10:01:00Z"}],
            **overrides}


def sync(api, *bots, token="ana-test", expected=200, source="Groky"):
    return post(api, "grokbot/sync", {"bots": list(bots), "source": source}, token=token, expected=expected)


def room_messages(api, slug):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(
            "SELECT m.* FROM messages m JOIN conversations v ON v.id=m.conversation_id "
            "WHERE v.scope='personal' AND v.owner_actor='human:ana' AND v.room_key=? ORDER BY m.created", (slug,))]


def test_a_new_grok_bot_lands_under_the_person_with_its_history_and_instructions(api):
    out = sync(api, designer())
    [bot] = out["bots"]
    assert bot["created"] and bot["bot"] == "grok-designer" and bot["messages_added"] == 2
    assert bot["synced_through"].startswith("2026-09-27T10:01:00")
    row = next(b for b in get(api, "bots") if b["slug"] == "grok-designer")
    assert row["reports_to"] == "human:ana" and row["operator"] == "ana"
    assert row["display_name"] == "Grok Designer"          # "Tico Designer" in Grok
    with api.app.state.store.read() as c:
        config = H._json(c.execute("SELECT config_json FROM bot_config WHERE bot='grok-designer'").fetchone()[0], {})
        assert config["harness"] == "grokbot"
        assert config["grok"]["instructions"] == "You design Tico. Keep it plain."
        assert config["grok"]["source"] == "Groky"
        assert config["grok"]["name"] == "Tico Designer"      # its name in Grok, for rebuilding it
        # Copied history is read and quiet: no job for a runner, nothing unread.
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='grok-designer'").fetchone()[0] == 0
    messages = room_messages(api, "grok-designer")
    assert [(m["from_actor"], m["body"]) for m in messages] == [
        ("human:ana", "Make the org chart calmer."), ("bot:grok-designer", "Softer lines, fewer badges.")]
    assert all(m["read_at"] for m in messages)


def test_the_same_name_from_two_grok_bots_gets_two_slugs_and_sections_are_kept(api):
    sync(api, designer(section="Pinned"), designer(grok_id="other-grok-id", messages=[]))
    slugs = {b["slug"] for b in get(api, "bots")}
    assert {"grok-designer", "grok-designer-2"} <= slugs
    agents = {b["slug"]: b["agent"] for b in get(api, "bots") if b["slug"].startswith("grok-designer")}
    assert agents["grok-designer"]["section"] == "Pinned" and agents["grok-designer"]["label"] == "Grok Bot"
    assert agents["grok-designer-2"]["section"] == ""


def test_a_member_links_their_own_dots_as_one_bot_under_them(api):
    out = post(api, "external/sync", {"provider": "dots", "bots": [
        {"name": "Dots", "messages": [{"role": "bot", "text": "Morning brief ready.", "at": "2026-10-09T07:00:00Z"}]}]},
        token="cara-test")
    [bot] = out["bots"]
    assert bot["created"] and bot["bot"] == "dots" and bot["messages_added"] == 1
    again = post(api, "external/sync", {"provider": "dots", "bots": [{"name": "Dots"}]}, token="cara-test")
    assert again["bots"][0]["bot"] == "dots" and not again["bots"][0]["created"]     # one Dots per person
    row = next(b for b in get(api, "bots") if b["slug"] == "dots")
    assert row["reports_to"] == "human:cara" and row["operator"] == "cara" and row["harness"] == "dots"
    post(api, "external/sync", {"provider": "dots", "bots": [{"name": "A"}, {"name": "B"}]},
         token="cara-test", expected=422)


def test_what_the_person_wrote_in_tico_comes_back_once_in_the_next_sync(api):
    sync(api, designer())
    post(api, "chat/grok-designer", {"text": "Try a warmer palette."})
    [bot] = sync(api, designer(messages=[]))["bots"]
    assert [m["text"] for m in bot["inbox"]] == ["Try a warmer palette."]
    assert sync(api, designer(messages=[]))["bots"][0]["inbox"] == []


def test_tico_fetches_only_public_https_images():
    from backend import external_sync as G
    assert G.fetch_image("http://imagine.example/a.png") is None
    assert G.fetch_image("file:///etc/passwd") is None
    assert not G.public_host("localhost") and not G.public_host("127.0.0.1") and not G.public_host("169.254.169.254")
    assert G.fetch_image("https://127.0.0.1/a.png") is None


def test_stale_import_warns_without_claiming_a_runtime_failure_or_changing_history(api, monkeypatch):
    sync(api, designer())
    old = H.now()
    issues = lambda: [i for i in api.get('/api/status', headers=headers()).json()['health_issues']
                      if i['bot'] == 'grok-designer']
    assert issues() == []
    later = H.shift(old, hours=27)
    monkeypatch.setattr(H, 'now', staticmethod(lambda: later))
    [issue] = issues()
    assert issue['severity'] == 'warning' and not issue['needs_person']
    with api.app.state.store.read() as c:
        config = H._json(c.execute("SELECT config_json FROM bot_config WHERE bot='grok-designer'").fetchone()[0], {})
        assert issue['since'] == config['grok']['last_sync'] < later
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='grok-designer'").fetchone()[0] == 0
    assert len(room_messages(api, 'grok-designer')) == 2
