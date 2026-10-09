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


def waiting(api):
    issue = next(i for i in api.get("/api/status", headers=headers()).json()["health_issues"] if i["bot"] == "grok-designer")
    return issue["detail"]


def test_what_the_person_wrote_in_tico_comes_back_once_to_a_sync_that_takes_the_inbox(api, monkeypatch):
    sync(api, designer())
    post(api, "chat/grok-designer", {"text": "Try a warmer palette."})
    # A routine from before the inbox ignores it: its sync leaves the message waiting.
    assert sync(api, designer(messages=[]))["bots"][0]["inbox"] == []
    later = H.shift(H.now(), hours=27)
    monkeypatch.setattr(H, "now", staticmethod(lambda: later))
    assert "1 message waits for the next sync" in waiting(api)
    take = lambda: post(api, "external/sync", {"provider": "grokbot", "inbox": True,
                                               "bots": [designer(id=GROK_ID, grok_id="", messages=[])]})["bots"][0]
    assert [m["text"] for m in take()["inbox"]] == ["Try a warmer palette."]
    assert take()["inbox"] == []
    later = H.shift(later, hours=27)
    assert "wait" not in waiting(api)               # handed over: no longer counted as waiting


def test_a_bot_may_not_sync_and_nothing_is_fetched_for_it(api, monkeypatch):
    from backend import external_sync as G
    from backend.auth import Identity
    fetched = []
    monkeypatch.setattr(G, "fetch_image", lambda url, transport=None: fetched.append(url))
    with api.app.state.store.transaction() as c:      # a signed-in agent, as a Hermes bot is
        c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) VALUES('coo','hermes','x',?,'human:ana')",
                  (H.now(),))
    api.app.state.store.settings.test_identities["coo-test"] = Identity("bot:coo", "bot", agent="hermes")
    image = {"role": "bot", "text": "See", "at": "2026-09-27T10:00:00Z", "images": [{"url": "https://imagine.example/a.png"}]}
    assert sync(api, designer(messages=[image]), token="coo-test", expected=403)["error"]["code"] == "forbidden"
    assert fetched == []


def test_a_member_past_their_bot_limit_is_refused_before_any_image_is_fetched(api, monkeypatch):
    from backend import external_sync as G
    fetched = []
    monkeypatch.setattr(G, "fetch_image", lambda url, transport=None: fetched.append(url))
    assert api.put("/api/v2/access/limits", json={"member_bot_limit": 1}, headers=headers("ben-test")).status_code == 200
    image = lambda n: {"role": "bot", "text": f"See {n}", "at": "2026-09-27T10:00:00Z",
                       "images": [{"url": f"https://imagine.example/{n}.png"}]}
    two = [designer(messages=[image(1)]), designer(grok_id="other-grok-id", messages=[image(2)])]
    assert sync(api, *two, token="cara-test", expected=409)["error"]["code"] == "bot_limit"
    assert fetched == [] and not [b for b in get(api, "bots") if b["slug"].startswith("grok-")]
    sync(api, designer(messages=[image(1)]), token="cara-test")             # one fits
    assert fetched == ["https://imagine.example/1.png"]
    assert sync(api, designer(grok_id="other-grok-id", messages=[image(2)]), token="cara-test",
                expected=409)["error"]["code"] == "bot_limit"
    assert len(fetched) == 1
    sync(api, designer(messages=[image(3)]), token="cara-test")             # a bot they have already is no new bot
    assert len(fetched) == 2


def test_one_sync_fetches_at_most_its_share_of_images_and_keeps_the_rest_as_links(api, monkeypatch):
    from backend import external_sync as G
    fetched = []
    monkeypatch.setattr(G, "fetch_image", lambda url, transport=None: fetched.append(url))
    many = [{"role": "bot", "text": f"Image {n}", "at": f"2026-09-27T10:{n // 60:02d}:{n % 60:02d}Z",
             "images": [{"url": f"https://imagine.example/{n}.png"}] * 2} for n in range(G.IMAGE_FETCHES)]
    sync(api, designer(messages=many))
    assert len(fetched) == G.IMAGE_FETCHES and len(room_messages(api, "grok-designer")) == G.IMAGE_FETCHES


def test_two_people_syncing_the_same_grok_bot_get_their_own_bot_and_chat(api):
    ana = sync(api, designer())["bots"][0]
    cara = sync(api, designer(), token="cara-test")["bots"][0]
    assert ana["bot"] != cara["bot"] and ana["created"] and cara["created"] and cara["messages_added"] == 2
    rows = {b["slug"]: b for b in get(api, "bots")}
    assert rows[ana["bot"]]["reports_to"] == "human:ana" and rows[cara["bot"]]["reports_to"] == "human:cara"
    with api.app.state.store.read() as c:
        rooms = c.execute("SELECT owner_actor, room_key FROM conversations WHERE scope='personal' AND room_key IN (?,?)",
                          (ana["bot"], cara["bot"])).fetchall()
    assert sorted(map(tuple, rooms)) == sorted([("human:ana", ana["bot"]), ("human:cara", cara["bot"])])
    post(api, "chat/" + ana["bot"], {"text": "Only for Ana's."})
    take = lambda token, bot: post(api, "external/sync", {"provider": "grokbot", "inbox": True, "bots": [
        designer(messages=[])]}, token=token)["bots"][0]["inbox"]
    assert take("cara-test", cara["bot"]) == [] and [m["text"] for m in take("ana-test", ana["bot"])] == ["Only for Ana's."]


def test_an_old_routine_sending_grok_id_keeps_the_message_ids_it_had_before_other_providers(api):
    import hashlib
    import json
    import uuid
    from backend import external_sync as G
    sync(api, designer())
    first = designer()["messages"][0]
    key = hashlib.sha256(json.dumps([first["role"], first["at"], first["text"], []], sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()
    before = "grok-" + str(uuid.uuid5(G.NAMESPACE, json.dumps(["ana", GROK_ID, key])))     # 0.3.35's id
    assert room_messages(api, "grok-designer")[0]["id"] == before
    again = post(api, "external/sync", {"provider": "grokbot", "bots": [designer(id=GROK_ID, grok_id="")]})["bots"][0]
    assert again["bot"] == "grok-designer" and again["messages_added"] == 0


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


def test_a_synced_bot_joins_its_humans_group_until_moved(api):
    group = post(api, "groups", {"name": "Leadership", "add": {"people": ["ana"]}})
    gid = group.get("id") or group["group"]["id"]
    sync(api, designer())
    team = lambda: next(b for b in get(api, "bots") if b["slug"] == "grok-designer")["team"]
    assert team() == gid                      # under Ana on the chart, not loose at the top
    api.patch(f"/api/v2/groups/{gid}", json={"remove": {"bots": ["grok-designer"]}}, headers=headers())
    sync(api, designer(messages=[]))
    assert team() != gid                      # moved out by a person: a later sync leaves it there
