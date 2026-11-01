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


def bot_id(api, slug="grok-designer"):
    """The id the store keys a bot by. A test names a bot by its slug; a bot a sync makes gets a UUID id."""
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, slug)["id"]


def revision(api, slug):
    return next(b for b in get(api, "bots") if b["slug"] == slug)["revision"]


def room_messages(api, slug):
    bid = bot_id(api, slug)
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(
            "SELECT m.* FROM messages m JOIN conversations v ON v.id=m.conversation_id "
            "WHERE v.scope='personal' AND v.owner_id='human:ana' AND v.room_key=? ORDER BY m.created_at", (bid,))]


def test_a_new_grok_bot_lands_under_the_person_with_its_history_and_instructions(api):
    out = sync(api, designer())
    [bot] = out["bots"]
    bid = bot_id(api)
    assert bot["created"] and bot["bot"] == bid and bot["messages_added"] == 2
    assert bot["synced_through"].startswith("2026-09-27T10:01:00")
    row = next(b for b in get(api, "bots") if b["slug"] == "grok-designer")
    assert row["reports_to"] == "human:ana" and row["owner_id"] == "ana"
    assert row["display_name"] == "Grok Designer"          # "Tico Designer" in Grok
    with api.app.state.store.read() as c:
        config = H._json(c.execute("SELECT config FROM bot_config WHERE bot=?", (bid,)).fetchone()[0], {})
        assert config["harness"] == "grokbot"
        assert config["grok"]["instructions"] == "You design Tico. Keep it plain."
        assert config["grok"]["source"] == "Groky"
        assert config["grok"]["name"] == "Tico Designer"      # its name in Grok, for rebuilding it
        # Copied history is read and quiet: no job for a runner, nothing unread.
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id=?", (bid,)).fetchone()[0] == 0
    messages = room_messages(api, "grok-designer")
    assert [(m["sender_id"], m["body"]) for m in messages] == [
        ("human:ana", "Make the org chart calmer."), ("bot:" + bid, "Softer lines, fewer badges.")]
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
    bid = bot_id(api, "dots")
    assert bot["created"] and bot["bot"] == bid and bot["messages_added"] == 1
    again = post(api, "external/sync", {"provider": "dots", "bots": [{"name": "Dots"}]}, token="cara-test")
    assert again["bots"][0]["bot"] == bid and not again["bots"][0]["created"]     # one Dots per person
    row = next(b for b in get(api, "bots") if b["slug"] == "dots")
    assert row["reports_to"] == "human:cara" and row["owner_id"] == "cara" and row["harness"] == "dots"
    post(api, "external/sync", {"provider": "dots", "bots": [{"name": "A"}, {"name": "B"}]},
         token="cara-test", expected=422)


def waiting(api):
    bid = bot_id(api)
    issue = next(i for i in api.get("/api/status", headers=headers()).json()["health_issues"] if i["bot"] == bid)
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
        c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('coo','hermes','x',?,'human:ana')",
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
    rows = {b["id"]: b for b in get(api, "bots")}
    assert rows[ana["bot"]]["reports_to"] == "human:ana" and rows[cara["bot"]]["reports_to"] == "human:cara"
    with api.app.state.store.read() as c:
        rooms = c.execute("SELECT owner_id, room_key FROM conversations WHERE scope='personal' AND room_key IN (?,?)",
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
    assert again["bot"] == bot_id(api) and again["messages_added"] == 0


def test_tico_fetches_only_public_https_images():
    from backend import external_sync as G
    assert G.fetch_image("http://imagine.example/a.png") is None
    assert G.fetch_image("file:///etc/passwd") is None
    assert not G.public_host("localhost") and not G.public_host("127.0.0.1") and not G.public_host("169.254.169.254")
    assert G.fetch_image("https://127.0.0.1/a.png") is None


def test_an_image_is_fetched_from_the_address_that_was_checked_not_a_second_lookup(monkeypatch):
    import socket

    import httpx
    from backend import external_sync as G
    # A rebinding name: public when Tico checks it, private on any later lookup.
    answers = iter(["93.184.216.34", "10.0.0.5", "10.0.0.5"])
    lookups = []

    def resolve(host, port, *args, **kwargs):
        lookups.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]
    monkeypatch.setattr(G.socket, "getaddrinfo", resolve)
    seen = []

    def connect(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png")
    assert G.fetch_image("https://rebind.example:8443/a.png?v=1", httpx.MockTransport(connect)) == (b"png", "image/png")
    [request] = seen
    assert lookups == ["rebind.example"]
    assert request.url.host == "93.184.216.34" and request.url.port == 8443 and request.url.raw_path == b"/a.png?v=1"
    assert request.headers["host"] == "rebind.example:8443"
    assert request.extensions["sni_hostname"] == "rebind.example"     # TLS still checks the name's certificate
    # A redirect is checked and pinned again: here the next lookup is private, so nothing is fetched there.
    answers = iter(["93.184.216.34", "10.0.0.5"])
    seen.clear()
    monkeypatch.setattr(G.socket, "getaddrinfo", resolve)
    hop = lambda request: (seen.append(request), httpx.Response(302, headers={"location": "https://inside.example/a.png"}))[1]
    assert G.fetch_image("https://rebind.example/a.png", httpx.MockTransport(hop)) is None
    assert [r.url.host for r in seen] == ["93.184.216.34"]


def test_stale_import_warns_without_claiming_a_runtime_failure_or_changing_history(api, monkeypatch):
    sync(api, designer())
    old = H.now()
    bid = bot_id(api)
    issues = lambda: [i for i in api.get('/api/status', headers=headers()).json()['health_issues']
                      if i['bot'] == bid]
    assert issues() == []
    later = H.shift(old, hours=27)
    monkeypatch.setattr(H, 'now', staticmethod(lambda: later))
    [issue] = issues()
    assert issue['severity'] == 'warning' and not issue['needs_person']
    with api.app.state.store.read() as c:
        config = H._json(c.execute("SELECT config FROM bot_config WHERE bot=?", (bid,)).fetchone()[0], {})
        assert issue['since'] == config['grok']['last_sync'] < later
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id=?", (bid,)).fetchone()[0] == 0
    assert len(room_messages(api, 'grok-designer')) == 2


def test_account_sync_preserves_moved_bots_and_history(api):
    group = post(api, "groups", {"name": "Leadership", "add": {"people": ["ana"]}})
    gid = group.get("id") or group["group"]["id"]
    sync(api, designer(section="Pinned"), designer(grok_id="scout-id", name="Scout", messages=[]))
    history_ids = [m["id"] for m in room_messages(api, "grok-designer")]
    team = lambda: next(b for b in get(api, "bots") if b["slug"] == "grok-designer")["team"]
    assert team() == gid                      # under Ana on the chart, not loose at the top
    response = api.patch(f"/api/v2/groups/{gid}", json={"remove": {"bots": ["grok-designer"]}}, headers=headers())
    assert response.status_code == 200
    post(api, "bots/grok-designer/definition", {"reports_to": "coo", "expected_revision": revision(api, "grok-designer")})
    post(api, "bots/scout/definition", {"reports_to": "human:cara", "expected_revision": revision(api, "scout")})
    again = sync(api, designer(section="Design"), designer(grok_id="scout-id", name="Scout", messages=[]))
    assert all(not bot["created"] and bot["messages_added"] == 0 for bot in again["bots"])
    rows = {b["slug"]: b for b in get(api, "bots")}
    assert rows["grok-designer"]["reports_to"] == "coo"
    assert rows["scout"]["reports_to"] == "human:cara"
    assert rows["grok-designer"]["agent"]["section"] == "Design"  # source metadata still syncs
    assert team() != gid                      # moved out by a person: a later sync leaves it there
    assert [m["id"] for m in room_messages(api, "grok-designer")] == history_ids

    other = post(api, "groups", {"name": "Design", "add": {"bots": ["grok-designer"]}})
    other_id = other.get("id") or other["group"]["id"]
    sync(api, designer(section="Pinned"), designer(grok_id="scout-id", name="Scout", messages=[]))
    assert team() == other_id                 # explicit department wins over the account owner's group


def test_muse_links_as_one_bot_beside_dots(api):
    post(api, "external/sync", {"provider": "dots", "bots": [{"name": "Dots"}]}, token="cara-test")
    out = post(api, "external/sync", {"provider": "muse", "bots": [{"name": "Muse", "messages": [
        {"role": "bot", "text": "Drafted three Instagram captions.", "at": "2026-10-10T08:00:00Z"}]}]}, token="cara-test")
    bid = bot_id(api, "muse")
    assert out["bots"][0]["bot"] == bid and out["bots"][0]["messages_added"] == 1
    again = post(api, "external/sync", {"provider": "muse", "bots": [{"name": "Muse"}]}, token="cara-test")
    assert again["bots"][0]["bot"] == bid and not again["bots"][0]["created"]      # one Muse per person
    rows = {b["slug"]: b for b in get(api, "bots") if b["slug"] in ("dots", "muse")}
    assert rows["muse"]["harness"] == "muse" and rows["muse"]["agent"]["label"] == "Muse"
    assert rows["muse"]["reports_to"] == rows["dots"]["reports_to"] == "human:cara"


def test_a_task_for_a_synced_bot_is_refused_with_what_to_do_instead(api):
    sync(api, designer())
    r = api.post("/api/v2/tasks", json={"owner_id": "grok-designer", "title": "Redo the logo", "body": "x"},
                 headers=headers())
    assert r.status_code == 422 and "only syncs its chat here" in r.text
    task = post(api, "tasks", {"owner_id": "ops", "title": "Redo the logo", "body": "x"})
    post(api, "tasks/" + task["id"], {"version": task["version"], "owner_id": "grok-designer"}, expected=422)
    # Its routines would be tasks too: none can be set, and one left from before is passed over by an event.
    from backend import routines
    from backend.store import H
    bid = bot_id(api)
    with api.app.state.store.transaction() as c:
        try:
            routines.create(c, "human:ana", bid, {"title": "Notes", "on": "meeting.ready"})
            raise AssertionError("a synced bot was given a routine")
        except H.Refused as exc:
            assert exc.rule == "external-bot"
        c.execute("INSERT INTO schedules(id,bot,event_name,title,playbook) VALUES('old',?,'meeting.ready','Notes','x')", (bid,))
        H.hook(c, "meeting.ready", "m1", lambda: {}, "human:ana")
        assert c.execute("SELECT count(*) FROM tasks WHERE owner_id LIKE ?", ("%" + bid,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM automation_runs").fetchone()[0] == 0



# ----------------------------------------------------------------------------- delivered through the Grok app
def mac(api, operator="cara", label="Cara's Mac", grok_ready=True, bots=()):
    """A computer of `operator`'s whose runner reports the Grok app ready (or signed out) and `bots` ready."""
    from backend.tests.test_api import runner
    machine = runner(api, operator, label)
    beat(api, machine, grok_ready, bots)
    return machine


def beat(api, machine, grok_ready=True, bots=()):
    grok = {"installed": True, "running": True, "signed_in": grok_ready, "background_ok": True, "ready": grok_ready,
            "state": "ready" if grok_ready else "not_signed_in", "detail": "" if grok_ready else "Grok shows its sign-in"}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {
        "schema_version": 1, "runtimes": {}, "bots": {bot_id(api, slug): {"ready": True} for slug in bots},
        "appdriver": {"trusted": True, "macos": "15.1", "apps": {"grok": grok}}}}, token=machine["token"])


def deliver(api, computer, token="cara-test", expected=200, slug="grok-designer"):
    revision = next(b for b in get(api, "bots", token=token) if b["slug"] == slug)["revision"]
    value = {"computer": computer, "app": "grok"} if computer else None
    return api.post(f"/api/v2/bots/{slug}/definition", headers=headers(token),
                    json={"app_delivery": value, "expected_revision": revision})


def jobs(api, slug="grok-designer"):
    bid = bot_id(api, slug)
    with api.app.state.store.read() as c:
        return {r["message_id"]: r["state"] for r in c.execute("SELECT message_id,state FROM jobs WHERE bot_id=?", (bid,))}


def inbox(api, token="cara-test"):
    return [m["text"] for m in post(api, "external/sync", {"provider": "grokbot", "inbox": True, "bots": [
        designer(id=GROK_ID, grok_id="", messages=[])]}, token=token)["bots"][0]["inbox"]]


def test_only_the_bots_person_delivers_it_through_their_own_ready_computer_and_an_admin_may_turn_it_off(api):
    sync(api, designer(), token="cara-test")
    signed_out, bens = mac(api, grok_ready=False), mac(api, "ben", "Ben's Mac")
    refused = deliver(api, signed_out["runner_id"])
    assert refused.status_code == 422 and "Grok isn't ready on Cara's Mac: Grok shows its sign-in" in refused.text
    assert deliver(api, bens["runner_id"]).status_code == 422          # signed in to Grok as Ben there, not Cara
    ready = mac(api)
    refused = deliver(api, ready["runner_id"], token="ben-test")       # an admin, not the person who synced it
    assert refused.status_code == 422 and "Only the person who synced this Grok Bot" in refused.text
    assert deliver(api, ready["runner_id"]).status_code == 200
    row = next(b for b in get(api, "bots", token="cara-test") if b["slug"] == "grok-designer")
    assert row["app_delivery"]["computer_label"] == "Cara's Mac" and row["assignment"]["runner_id"] == ready["runner_id"]
    assert row["app_delivery_app"] == "grok"
    employee = next(e for e in api.get("/api/employees", headers=headers("cara-test")).json() if e["name"] == "grok-designer")
    assert employee["app_delivery"]["computer_label"] == "Cara's Mac" and employee["app_delivery_app"] == "grok"
    assert deliver(api, None, token="ben-test").status_code == 200     # the way back is an admin's too
    row = next(b for b in get(api, "bots", token="cara-test") if b["slug"] == "grok-designer")
    assert row["app_delivery"] is None and row["assignment"] is None


def test_a_message_goes_to_the_computer_while_the_app_delivers_and_to_the_sync_otherwise_never_both(api):
    from backend.tests.test_api import claim
    sync(api, designer(), token="cara-test")
    before = post(api, "chat/grok-designer", {"text": "Before, for the sync."}, token="cara-test")
    assert jobs(api) == {}                                             # off: exactly as before
    computer = mac(api, bots=["grok-designer"])
    assert deliver(api, computer["runner_id"]).status_code == 200
    taken = post(api, "chat/grok-designer", {"text": "For the app."}, token="cara-test")
    waiting = post(api, "chat/grok-designer", {"text": "Also for the app."}, token="cara-test")
    assert jobs(api) == {taken["id"]: "queued", waiting["id"]: "queued"}
    assert inbox(api) == ["Before, for the sync."]                     # the computer's messages are not handed over
    attempt = claim(api, computer)
    assert attempt["message"]["id"] == taken["id"]
    assert attempt["config"]["app_delivery"] == {"computer": computer["runner_id"], "app": "grok"}
    assert attempt["config"]["app_bot"] == {"id": GROK_ID, "name": "Tico Designer"}
    assert deliver(api, None).status_code == 200                       # off again: what no runner took goes to the sync
    assert jobs(api) == {taken["id"]: "leased", waiting["id"]: "canceled"}
    assert inbox(api) == ["Also for the app."] and inbox(api) == []
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE id=? AND delivered_at IS NULL", (before["id"],)).fetchone()


def test_what_the_app_shows_is_posted_once_without_echoes_and_a_question_reaches_needs_you(api):
    from backend.tests.test_api import claim
    sync(api, designer(messages=[]), token="cara-test")
    computer = mac(api, bots=["grok-designer"])
    deliver(api, computer["runner_id"])
    asked = post(api, "chat/grok-designer", {"text": "Try a warmer palette."}, token="cara-test")
    attempt = claim(api, computer)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "grok"}, token=computer["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Warmer: sand and rust.",
                                                     "last_seq": 0}, token=computer["token"])
    bid = bot_id(api)
    assert get(api, "runners/me/app-delivery", token=computer["token"])["bots"] == [
        {"id": bid, "slug": "grok-designer", "app": "grok", "bot": {"id": GROK_ID, "name": "Tico Designer"}, "after": None,
         "error": False}]
    report = lambda *messages, error=None: post(api, "runners/me/app-delivery/grok-designer", {
        "app": "grok", "messages": list(messages), "error": error}, token=computer["token"])
    page = [{"key": "k1", "role": "user", "text": " Try a  warmer palette."},     # Tico delivered it
            {"key": "k2", "role": "bot", "text": "Warmer: sand and rust."},       # the turn posted it
            {"key": "k3", "role": "user", "text": "And the logo?"},               # typed in the app itself
            {"key": "k4", "role": "bot", "text": "Logo: keep it."}]               # the bot on its own
    assert report(*page) == {"after": "k4", "posted": 2}
    assert report(*page) == {"after": "k4", "posted": 0}                          # each key once
    bodies = [(m["sender_id"], m["body"], bool(m["read_at"])) for m in room_messages_of(api, "cara", "grok-designer")]
    assert bodies == [("human:cara", asked["body"], False), ("bot:" + bid, "Warmer: sand and rust.", False),
                      ("human:cara", "And the logo?", True), ("bot:" + bid, "Logo: keep it.", False)]
    assert get(api, "runners/me/app-delivery", token=computer["token"])["bots"][0]["after"] == "k4"
    report(error={"code": "needs_human", "detail": "Grok asks to approve", "text": "Approve the purchase in Grok?"})
    needs = get(api, "needs-you", token="cara-test")
    assert any(i.get("kind") == "ask" and i["body"] == "Approve the purchase in Grok?" for i in needs["items"])
    other = mac(api, "cara", "Cara's other Mac")
    refused = api.post("/api/v2/runners/me/app-delivery/grok-designer", headers=headers(other["token"]),
                       json={"app": "grok", "messages": [{"key": "k9", "role": "bot", "text": "Not mine."}]})
    assert refused.status_code == 404


def test_a_removed_computer_puts_the_bot_back_on_the_daily_sync(api):
    sync(api, designer(), token="cara-test")
    computer = mac(api, bots=["grok-designer"])
    deliver(api, computer["runner_id"])
    report = {"app": "grok", "error": {"code": "not_signed_in", "detail": "Grok shows its sign-in"}}
    post(api, "runners/me/app-delivery/grok-designer", report, token=computer["token"])
    issues = api.get("/api/v2/health", headers=headers()).json()
    assert any("Grok on Cara's Mac: not signed in" in check["summary"] for check in issues["checks"])
    post(api, f"runners/{computer['runner_id']}/revoke", {}, token="cara-test")
    row = next(b for b in get(api, "bots", token="cara-test") if b["slug"] == "grok-designer")
    assert row["app_delivery"] is None and row["assignment"] is None
    post(api, "chat/grok-designer", {"text": "Back to the sync."}, token="cara-test")
    assert jobs(api) == {} and inbox(api) == ["Back to the sync."]


def room_messages_of(api, person, slug):
    bid = bot_id(api, slug)
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(
            "SELECT m.* FROM messages m JOIN conversations v ON v.id=m.conversation_id "
            "WHERE v.scope='personal' AND v.owner_id=? AND v.room_key=? ORDER BY m.created_at,m.rowid",
            ("human:" + person, bid))]


def test_an_existing_database_gets_the_queue_trigger_that_queues_for_app_delivery(api):
    from backend import store as S
    from backend.config import Settings
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("DROP TRIGGER queue_bot_message")
        c.execute(S.QUEUE_TRIGGER.replace(" AND NOT EXISTS (SELECT 1 FROM runners r", " AND NOT EXISTS (SELECT 2 FROM runners r"))
    S.Store(Settings(db_path=store.settings.db_path, registry_dir=store.settings.registry_dir)).initialize(seed_market=False)
    with store.read() as c:
        stored = c.execute("SELECT sql FROM sqlite_master WHERE name='queue_bot_message'").fetchone()[0]
    assert stored == S.QUEUE_TRIGGER.replace(" IF NOT EXISTS", "", 1)


def delivering(api):
    """Cara's Grok Bot, delivered through her ready Mac: (computer, report(messages, error))."""
    sync(api, designer(messages=[]), token="cara-test")
    computer = mac(api, bots=["grok-designer"])
    assert deliver(api, computer["runner_id"]).status_code == 200

    def report(*messages, error=None):
        return post(api, "runners/me/app-delivery/grok-designer", {"app": "grok", "messages": list(messages),
                                                                  "error": error}, token=computer["token"])
    return computer, report


def turn(api, computer, text, reply):
    """Cara says `text`; the computer's turn completes with `reply`."""
    from backend.tests.test_api import claim
    post(api, "chat/grok-designer", {"text": text}, token="cara-test")
    attempt = claim(api, computer)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "grok"}, token=computer["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": reply, "last_seq": 0},
         token=computer["token"])


def asks_in_needs_you(api, body):
    return [i for i in get(api, "needs-you", token="cara-test")["items"] if i.get("kind") == "ask" and i["body"] == body]


def test_a_question_the_app_needs_its_person_for_is_one_ask_whichever_arrives_first(api):
    from backend.tests.test_api import claim
    computer, report = delivering(api)
    needs = lambda text: {"code": "needs_human", "detail": "the bot asked", "text": text}
    # The turn's reply first, then the error report: the reply becomes the question.
    turn(api, computer, "Send the invoice.", "Which account should I bill?")
    report(error=needs("Which account should I bill?"))
    # The error report first, then the turn's reply: the reply isn't posted again.
    post(api, "chat/grok-designer", {"text": "And the receipt?"}, token="cara-test")
    attempt = claim(api, computer)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "grok"}, token=computer["token"])
    report(error=needs("Email or paper?"))
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Email or paper?", "last_seq": 0},
         token=computer["token"])
    # The watch then reads both questions in the conversation: neither is posted again.
    assert report({"key": "q1", "role": "bot", "text": "Which account should I bill?"},
                  {"key": "q2", "role": "bot", "text": "Email or paper?"})["posted"] == 0
    for question in ("Which account should I bill?", "Email or paper?"):
        rows = [m for m in room_messages_of(api, "cara", "grok-designer") if m["body"] == question]
        assert [(m["sender_id"], m["kind"]) for m in rows] == [("bot:" + bot_id(api), "ask")], question
        assert len(asks_in_needs_you(api, question)) == 1, question


def test_old_history_is_one_message_whichever_of_the_watch_and_the_sync_brings_it_first(api):
    computer, report = delivering(api)
    old, older = "2026-09-20T09:00:00Z", "2026-09-19T09:00:00Z"
    # The watch reads days-old history first, then the daily sync brings the same.
    assert report({"key": "h1", "role": "bot", "text": "Old reply.", "at": old},
                  {"key": "h2", "role": "user", "text": "Old question.", "at": old})["posted"] == 2
    sync(api, designer(messages=[{"role": "bot", "text": "Old reply.", "at": old},
                                 {"role": "user", "text": "Old question.", "at": old},
                                 {"role": "bot", "text": "Older still.", "at": older}]), token="cara-test")
    # The sync first, then the watch.
    assert report({"key": "h3", "role": "bot", "text": "Older still.", "at": older})["posted"] == 0
    bodies = [m["body"] for m in room_messages_of(api, "cara", "grok-designer")]
    assert sorted(bodies) == ["Old question.", "Old reply.", "Older still."]
    # The window, against the synced "Older still.": 25 hours after it is another message, 23 hours after it the same.
    assert report({"key": "h4", "role": "bot", "text": "Older still.", "at": "2026-09-20T10:00:00Z"})["posted"] == 1
    assert report({"key": "h5", "role": "bot", "text": "Older still.", "at": "2026-09-20T08:00:00Z"})["posted"] == 0
    assert [m["body"] for m in room_messages_of(api, "cara", "grok-designer")].count("Older still.") == 2


def test_a_clean_read_clears_the_apps_error_from_health(api):
    computer, report = delivering(api)
    listed = lambda: get(api, "runners/me/app-delivery", token=computer["token"])["bots"][0]["error"]
    problems = lambda: [c["summary"] for c in api.get("/api/v2/health", headers=headers()).json()["checks"]
                        if "Grok on Cara's Mac" in c["summary"]]
    report(error={"code": "timeout", "detail": "no answer in 60s"})
    assert listed() is True and any("it did not answer in time" in p for p in problems())
    beat(api, computer, bots=["grok-designer"])               # a ready heartbeat alone doesn't clear a read's error
    assert problems()
    assert report() == {"after": None, "posted": 0}            # the computer's clean, empty read
    assert listed() is False and problems() == []


def test_a_thousand_and_one_keys_roll_over_and_a_resent_old_key_still_posts_nothing(api):
    from backend import app_delivery
    computer, report = delivering(api)
    for start in range(0, 1001, 50):
        report(*({"key": f"k{i}", "role": "bot", "text": f"Message {i}."} for i in range(start, min(start + 50, 1001))))
    bid = bot_id(api)
    with api.app.state.store.read() as c:
        seen = app_delivery._seen(c, bid)
    assert len(seen["keys"]) == app_delivery.SEEN_KEYS and seen["keys"][0] == "k1" and seen["after"] == "k1000"
    assert get(api, "runners/me/app-delivery", token=computer["token"])["bots"][0]["after"] == "k1000"
    # A computer that lost its cursor sends from the start: the key no longer in the list, and a recent one.
    assert report({"key": "k0", "role": "bot", "text": "Message 0."},
                  {"key": "k999", "role": "bot", "text": "Message 999."}) == {"after": "k999", "posted": 0}
    assert len([m for m in room_messages_of(api, "cara", "grok-designer") if m["sender_id"] == "bot:" + bid]) == 1001
