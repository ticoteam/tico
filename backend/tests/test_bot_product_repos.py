"""A bot with Create product repositories makes a private product repository: the Owner turns the right on, the bot
gets write to what it made, the Owner is told, and connecting GitHub stays the Owner's."""
import json
import threading

import httpx

from backend import github_app as G, hubdb as H
from backend.auth import Identity
from backend.store import encode
from backend.tests.test_github_app import api, gh, auth, botops_turn, connect, product_repo_request, runner_token  # noqa: F401

PREVIEW = "/api/v2/github/product-repos/preview"
ACCESS = "/api/v2/bots/cmo/repositories"
BODY = {"org": "Acme", "name": "tico-services", "visibility": "private", "auto_init": False, "confirmed": True}
RIGHT = "Create product repositories"


def ready(api, gh, administration="write"):
    """The Owner on the roster, GitHub connected, and bot cmo in a running turn (the `bot-test` credential)."""
    connect(api, administration="true")
    gh.permissions = {"administration": administration, "metadata": "read"}
    runner_token(api, "cmo")
    with api.app_state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)", (encode({"email": "ana@acme.example"}),))
        c.execute("INSERT INTO messages(id,from_actor,to_actor,kind,body,created) "
                  "VALUES('m-cmo','keeper','bot:cmo','request','Make the services repository',?)", (H.now(),))
        job = c.execute("SELECT id FROM jobs WHERE bot='cmo'").fetchone()["id"]
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created) "
                  "VALUES('a1',?,'cmo','r1',1,'x-cmo','running',?,?)", (job, H.shift(H.now(), hours=1), H.now()))


def grant(api, on=True, who="owner-test", mode="own"):
    return api.put(ACCESS, json={"mode": mode, "create_product_repositories": on}, headers=auth(who))


def creates(gh):
    return [call for call in gh.calls if call[:2] == ("POST", "/orgs/Acme/repos")]


def test_bot_with_the_right_creates_a_private_repo_gets_write_and_the_owner_is_told(api, gh):
    ready(api, gh)
    # Before the Owner turns it on, the bot is refused and told which right it needs (the 403 this ticket removes).
    refused = product_repo_request(api, BODY, actor="bot-test", key="bot-services")
    assert refused.status_code == 403 and RIGHT in refused.json()["error"]["detail"]
    assert api.get(PREVIEW, params={"name": "tico-services"}, headers=auth("bot-test")).status_code == 403
    assert not creates(gh)

    assert api.get(ACCESS, headers=auth()).json()["create_product_repositories"] is False
    on = grant(api)
    assert on.status_code == 200 and on.json()["create_product_repositories"] is True, on.text

    preview = api.get(PREVIEW, params={"name": "tico-services"}, headers=auth("bot-test"))
    assert preview.status_code == 200 and preview.json()["capability"] == "available", preview.text
    made = product_repo_request(api, BODY, actor="bot-test", key="bot-services")
    assert made.status_code == 200, made.text
    result = made.json()
    assert result["repository"] == "Acme/tico-services" and result["visibility"] == "private"
    assert result["bot_access"] == "write" and "cmo has write access to it." in result["note"]
    assert [call[2] for call in creates(gh)] == [{"name": "tico-services", "private": True, "auto_init": False,
                                                  "description": "Tico product repository"}]

    view = api.get(ACCESS, headers=auth()).json()
    assert view["mode"] == "chosen"
    assert {"full_name": "Acme/tico-services", "access": "write"} in view["effective"]
    with api.app_state.store.read() as c:
        event = c.execute("SELECT actor,detail_json FROM events WHERE action='github.product_repo_created'").fetchone()
        assert event["actor"] == "bot:cmo"
        assert json.loads(event["detail_json"])["bot_access"] == "write"
        assert json.loads(event["detail_json"])["owner_notified"] is True
        notices = c.execute("SELECT from_actor,body FROM messages WHERE to_actor='human:ana' AND kind='notice'").fetchall()
        assert [n["body"] for n in notices] == [
            "cmo created private repo tico-services (Acme/tico-services). cmo has write access to it."]

    # The same key replays the receipt: no second create, grant or notice.
    replay = product_repo_request(api, BODY, actor="bot-test", key="bot-services")
    assert replay.status_code == 200 and replay.json() == result
    assert len(creates(gh)) == 1
    with api.app_state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM messages WHERE to_actor='human:ana' AND kind='notice'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='github.product_repo_created'").fetchone()[0] == 1


def test_the_inventory_refresh_creation_queues_keeps_the_new_repo_when_it_lands_after_the_grant(api, gh, monkeypatch):
    """Creation queues a background refresh of the repository list. When its write lands after the bot's grant (held
    here until then), the new repository is listed, so it is not marked missing and the bot keeps write to it."""
    ready(api, gh)
    service, listing, release = api.app_state.github_app, threading.Event(), threading.Event()

    def held(request):
        if request.url.path == "/installation/repositories" and threading.current_thread() is service.repository_worker:
            listing.set()
            assert release.wait(10)
        return gh(request)
    monkeypatch.setattr(G, "TRANSPORT", httpx.MockTransport(held))

    assert grant(api).status_code == 200
    made = product_repo_request(api, BODY, actor="bot-test", key="bot-services")
    assert made.status_code == 200 and made.json()["bot_access"] == "write", made.text
    assert listing.wait(10), "creating the repository queued a refresh"
    release.set()
    service.repository_worker.join(10)
    assert not service.repository_worker.is_alive()
    with api.app_state.store.read() as c:
        assert c.execute("SELECT reachable FROM repositories WHERE full_name='Acme/tico-services'").fetchone()[0] == 1
    assert {"full_name": "Acme/tico-services", "access": "write"} in api.get(ACCESS, headers=auth()).json()["effective"]


def test_a_chosen_bot_keeps_its_grants_and_bots_on_every_ticked_repo_are_named(api, gh):
    ready(api, gh)
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO repositories(id,full_name,enabled,updated) VALUES('r-tico','Acme/tico',1,?)", (H.now(),))
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='cpo'").fetchone()[0])
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='cpo'",
                  (json.dumps({**config, "repo_access_mode": "all"}),))
    assert api.put(ACCESS, json={"mode": "chosen", "chosen": [{"full_name": "Acme/tico", "access": "read"}],
                                 "create_product_repositories": True}, headers=auth()).status_code == 200
    made = product_repo_request(api, BODY, actor="bot-test", key="chosen-bot")
    assert made.status_code == 200, made.text
    chosen = {r["full_name"]: r["access"] for r in api.get(ACCESS, headers=auth()).json()["chosen"]}
    assert chosen == {"Acme/tico": "read", "Acme/tico-services": "write"}
    assert "Bots set to every ticked repository reach it too: cpo." in made.json()["note"]


def test_a_read_only_all_repos_creator_gets_write_to_the_new_repo_and_keeps_read_elsewhere(api, gh):
    ready(api, gh)
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO repositories(id,full_name,enabled,updated) VALUES('r-tico','Acme/tico',1,?)", (H.now(),))
    assert api.put(ACCESS, json={"mode": "all", "all_access": "read", "create_product_repositories": True},
                   headers=auth()).status_code == 200
    assert {"full_name": "Acme/tico", "access": "read"} in api.get(ACCESS, headers=auth()).json()["effective"]
    made = product_repo_request(api, BODY, actor="bot-test", key="all-read-bot")
    assert made.status_code == 200, made.text
    assert made.json()["bot_access"] == "write"
    view = api.get(ACCESS, headers=auth()).json()
    effective = {r["full_name"]: r["access"] for r in view["effective"]}
    assert view["mode"] == "chosen"
    assert effective == {"Acme/emp-cmo": "write", "Acme/tico": "read", "Acme/tico-services": "write"}
    with api.app_state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail_json FROM events WHERE action='github.product_repo_created'").fetchone()[0])
        assert detail["bot_access"] == "write" and detail["moved_to_chosen"] is True
        notice = c.execute("SELECT body FROM messages WHERE to_actor='human:ana' AND kind='notice'").fetchone()[0]
    assert ("cmo has write access to it. It was on All ticked repos with read access, so it is now on Chosen repos: "
            "read on the 1 repository it reached and write on this one.") in notice
    assert notice.endswith(made.json()["note"].removeprefix("Created as a private empty repository. "))


def test_a_bot_without_the_right_is_refused_and_told_which_right(api, gh):
    ready(api, gh)
    botops_turn(api)
    # BotOps creates bot repositories built in, but not product repositories.
    assert api.get("/api/v2/bots/botops/repositories", headers=auth()).json()["create_product_repositories"] is False
    for actor in ("bot-test", "botops-test", "person-test", "runner-test"):
        refused = product_repo_request(api, BODY, actor=actor, key="no-right-" + actor)
        assert refused.status_code == 403, (actor, refused.text)
        assert RIGHT in refused.json()["error"]["detail"]
    assert not creates(gh)


def test_only_the_owner_turns_the_right_on_and_an_admin_may_turn_it_off(api, gh):
    ready(api, gh)
    with api.app_state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('access',?)", (encode({"admins": ["riley@acme.example"]}),))
    botops_turn(api)
    for actor in ("person-test", "bot-test", "botops-test", "runner-test"):
        refused = grant(api, who=actor)
        assert refused.status_code == 403, (actor, refused.text)
    assert api.get(ACCESS, headers=auth()).json()["create_product_repositories"] is False
    # An admin's save that leaves the right as it is still works.
    assert grant(api, on=False, who="person-test").status_code == 200
    assert grant(api).json()["create_product_repositories"] is True
    off = grant(api, on=False, who="person-test")
    assert off.status_code == 200 and off.json()["create_product_repositories"] is False
    assert off.json()["product_creation_stopped"] is True
    refused = product_repo_request(api, BODY, actor="bot-test", key="after-revoke")
    assert refused.status_code == 403 and RIGHT in refused.text
    assert not creates(gh)


def test_the_owners_request_carried_by_a_bot_turn_cannot_turn_the_right_on(api, gh):
    ready(api, gh)
    botops_turn(api)
    carried = {"via-botops": Identity("human:ana", "owner", "ana@acme.example", runner_id="r1", attempt_id="a2",
                                      via="botops", task_actor="bot:botops", confirmed=True),
               "via-self": Identity("human:ana", "owner", "ana@acme.example", runner_id="r1", attempt_id="a1",
                                    task_actor="bot:cmo", confirmed=True)}
    api.app_state.store.settings.test_identities.update(carried)
    for token in carried:
        refused = grant(api, who=token)
        assert refused.status_code == 403 and "Only the Owner turns on" in refused.text, (token, refused.text)
        assert api.get(ACCESS, headers=auth()).json()["create_product_repositories"] is False, token


def test_an_external_agent_of_a_granted_bot_cannot_create(api, gh):
    ready(api, gh)
    assert grant(api).status_code == 200
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) VALUES('cmo','hermes','agent-hash',?,'human:ana')",
                  (H.now(),))
    api.app_state.store.settings.test_identities["agent-test"] = Identity("bot:cmo", "bot", agent="hermes")
    assert api.get(PREVIEW, params={"name": "tico-services"}, headers=auth("agent-test")).status_code == 403
    refused = product_repo_request(api, BODY, actor="agent-test", key="agent")
    assert refused.status_code == 403 and RIGHT in refused.text
    assert not creates(gh)


CARRIED = "acting for someone"


def nothing_made(api, gh):
    assert not creates(gh)
    with api.app_state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM github_product_repo_operations").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='github.product_repo_created'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM repositories WHERE full_name='Acme/tico-services'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM messages WHERE to_actor='human:ana' AND kind='notice'").fetchone()[0] == 0


def test_an_owner_origin_botops_turn_cannot_preview_or_create_a_product_repo(api, gh):
    from backend import rooms
    ready(api, gh)
    botops_turn(api)
    with api.app_state.store.transaction() as c:
        conversation = rooms.personal_room(c, "human:ana", "botops")
        c.execute("UPDATE messages SET from_actor='human:ana',conversation_id=?,kind='say',"
                  "body='Make the tico-services repository' WHERE id='m-botops'", (conversation["id"],))
        c.execute("INSERT INTO attempt_conversations VALUES('a2',?)", (conversation["id"],))
    turn = {**auth("botops-test"), "X-Tico-On-Behalf-Of": "turn"}
    preview = api.get(PREVIEW, params={"name": "tico-services"}, headers=turn)
    assert preview.status_code == 403 and CARRIED in preview.json()["error"]["detail"], preview.text
    made = api.post("/api/v2/github/product-repos", json=BODY, headers={**turn, "Idempotency-Key": "botops-turn"})
    assert made.status_code == 403 and CARRIED in made.json()["error"]["detail"], made.text
    nothing_made(api, gh)
    # The Owner's own click still previews and creates.
    assert api.get(PREVIEW, params={"name": "tico-services"}, headers=auth()).status_code == 200
    assert product_repo_request(api, BODY, key="owner-direct").status_code == 200
    assert len(creates(gh)) == 1


def test_a_carried_owner_identity_cannot_preview_or_create_a_product_repo(api, gh):
    ready(api, gh)
    botops_turn(api)
    assert grant(api).status_code == 200                      # cmo has the right; carried as the Owner it still can't
    carried = {"owner-via-botops": Identity("human:ana", "owner", "ana@acme.example", runner_id="r1", attempt_id="a2",
                                            via="botops", task_actor="bot:botops", confirmed=True),
               "owner-via-cmo": Identity("human:ana", "owner", "ana@acme.example", runner_id="r1", attempt_id="a1",
                                         via="botops", task_actor="bot:cmo", confirmed=True)}
    api.app_state.store.settings.test_identities.update(carried)
    for token in carried:
        preview = api.get(PREVIEW, params={"name": "tico-services"}, headers=auth(token))
        assert preview.status_code == 403 and CARRIED in preview.text, (token, preview.text)
        made = product_repo_request(api, BODY, actor=token, key="carried-" + token)
        assert made.status_code == 403 and CARRIED in made.text, (token, made.text)
    nothing_made(api, gh)
    # The granted bot in its own turn still creates.
    assert product_repo_request(api, BODY, actor="bot-test", key="own-turn").status_code == 200
    assert len(creates(gh)) == 1


def test_connecting_and_disconnecting_github_stay_the_owners(api, gh):
    ready(api, gh)
    assert grant(api).status_code == 200
    for method, path in (("GET", "/api/v2/github/app"), ("GET", "/api/v2/github/app/manifest?org=Acme"),
                         ("POST", "/api/v2/github/app/disconnect"), ("DELETE", "/api/v2/github/repos/Acme/tico-services")):
        refused = api.request(method, path, headers=auth("bot-test"))
        assert refused.status_code == 403, (method, path, refused.text)
    assert api.get("/api/v2/github/app", headers=auth()).json()["connected"] is True


def test_a_bot_never_creates_a_bot_repository_name(api, gh):
    ready(api, gh)
    assert grant(api).status_code == 200
    # bot-/emp- names, and a name a bot's repository already uses (cmo's own emp-cmo, cpo's unprefixed handbook).
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo='Acme/handbook' WHERE bot='cpo'")
    for name in ("bot-newhire", "emp-newhire", "BOT-x", "emp-cmo", "handbook", "Handbook"):
        refused = product_repo_request(api, {**BODY, "name": name}, actor="bot-test", key="name-" + name)
        assert refused.status_code == 403 and "bot repository name" in refused.text, (name, refused.text)
        assert api.get(PREVIEW, params={"name": name}, headers=auth("bot-test")).status_code == 403
    assert not creates(gh)
    # The Owner's name rules are unchanged.
    assert api.get(PREVIEW, params={"name": "bot-newhire"}, headers=auth()).status_code == 200


def test_without_administration_the_refusal_says_so_and_what_the_owner_changes(api, gh):
    ready(api, gh, administration="read")
    assert grant(api).status_code == 200
    preview = api.get(PREVIEW, params={"name": "tico-services"}, headers=auth("bot-test")).json()
    assert preview["capability"] == "missing"
    fix = "https://github.com/organizations/Acme/settings/apps/acme-tico/permissions"
    assert "missing Administration: write" in preview["capability_detail"] and fix in preview["capability_detail"]
    refused = product_repo_request(api, BODY, actor="bot-test", key="no-admin")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "github_permission_missing"
    assert "set Administration to Read and write on " + fix in refused.json()["error"]["detail"]
    assert not creates(gh)
