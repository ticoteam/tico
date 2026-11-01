"""A bot with Create product repositories makes a private product repository: every bot has the right unless the
Owner or an admin switches it off, the bot gets write to what it made, the Owner is told, and connecting GitHub stays
the Owner's."""
import json
import threading

import httpx
import pytest

from backend import github_app as G, hubdb as H
from backend.auth import Identity
from backend.repositories import PRODUCT_GRANTS, metadata
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
        c.execute("INSERT INTO messages(id,from_actor,to_actor,kind,body,created_at) "
                  "VALUES('m-cmo','keeper','bot:cmo','request','Make the services repository',?)", (H.now(),))
        job = c.execute("SELECT id FROM jobs WHERE bot='cmo'").fetchone()["id"]
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created_at) "
                  "VALUES('a1',?,'cmo','r1',1,'x-cmo','running',?,?)", (job, H.shift(H.now(), hours=1), H.now()))


def grant(api, on=True, who="owner-test", mode="own"):
    return api.put(ACCESS, json={"mode": mode, "create_product_repositories": on}, headers=auth(who))


def creates(gh):
    return [call for call in gh.calls if call[:2] == ("POST", "/orgs/Acme/repos")]


def test_a_bot_with_no_setting_creates_a_private_repo_gets_write_and_the_owner_is_told(api, gh):
    ready(api, gh)
    # Nobody chose anything for cmo: on by default (Chris, Oct 17), with nothing written for it.
    with api.app_state.store.read() as c:
        assert "cmo" not in metadata(c, PRODUCT_GRANTS)
    assert api.get(ACCESS, headers=auth()).json()["create_product_repositories"] is True

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
        event = c.execute("SELECT actor,detail FROM events WHERE action='github.product_repo_created'").fetchone()
        assert event["actor"] == "bot:cmo"
        assert json.loads(event["detail"])["bot_access"] == "write"
        assert json.loads(event["detail"])["owner_notified"] is True
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


@pytest.fixture
def refresh_after_grant(api, gh, monkeypatch):
    """`hold()` holds the inventory refresh that creating a repository queues; `finish()`, once the create has
    answered, lets it write after the bot's grant (the order that lost a repository in Dev 119) and checks it synced."""
    service, listing, release, answers = api.app_state.github_app, threading.Event(), threading.Event(), []

    def held(request):
        if request.url.path == "/installation/repositories" and threading.current_thread() is service.repository_worker:
            listing.set()
            released = release.wait(10)
            response = gh(request)
            answers.append((released, response.status_code))
            return response
        return gh(request)

    class Refresh:
        def hold(self):
            if service.repository_worker:
                service.repository_worker.join(10)               # the refresh connecting queued, unheld
            monkeypatch.setattr(G, "TRANSPORT", httpx.MockTransport(held))

        def finish(self):
            assert listing.wait(10), "creating the repository queued a refresh"
            release.set()
            service.repository_worker.join(10)
            assert not service.repository_worker.is_alive()
            assert answers == [(True, 200)] and service.repository_sync_failures == 0, "the held refresh synced"
    yield Refresh()
    release.set()


def test_the_inventory_refresh_creation_queues_keeps_the_new_repo_when_it_lands_after_the_grant(api, gh,
                                                                                                 refresh_after_grant):
    """Creation queues a background refresh of the repository list. When its write lands after the bot's grant, the
    new repository is listed, so it is not marked missing and the bot keeps write to it."""
    ready(api, gh)
    refresh_after_grant.hold()
    assert grant(api).status_code == 200
    made = product_repo_request(api, BODY, actor="bot-test", key="bot-services")
    assert made.status_code == 200 and made.json()["bot_access"] == "write", made.text
    refresh_after_grant.finish()
    with api.app_state.store.read() as c:
        assert c.execute("SELECT reachable FROM repositories WHERE full_name='Acme/tico-services'").fetchone()[0] == 1
    assert {"full_name": "Acme/tico-services", "access": "write"} in api.get(ACCESS, headers=auth()).json()["effective"]


def test_a_chosen_bot_keeps_its_grants_and_bots_on_every_ticked_repo_are_named(api, gh):
    ready(api, gh)
    gh.repositories.append({"full_name": "Acme/tico"})            # the installation sees the ticked repository
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES('r-tico','Acme/tico',1,?)", (H.now(),))
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='cpo'").fetchone()[0])
        c.execute("UPDATE bot_config SET config=? WHERE bot='cpo'",
                  (json.dumps({**config, "repo_access_mode": "all"}),))
    assert api.put(ACCESS, json={"mode": "chosen", "chosen": [{"full_name": "Acme/tico", "access": "read"}],
                                 "create_product_repositories": True}, headers=auth()).status_code == 200
    made = product_repo_request(api, BODY, actor="bot-test", key="chosen-bot")
    assert made.status_code == 200, made.text
    chosen = {r["full_name"]: r["access"] for r in api.get(ACCESS, headers=auth()).json()["chosen"]}
    assert chosen == {"Acme/tico": "read", "Acme/tico-services": "write"}
    assert "Bots set to every ticked repository reach it too: cpo." in made.json()["note"]


def test_a_read_only_all_repos_creator_gets_write_to_the_new_repo_and_keeps_read_elsewhere(api, gh,
                                                                                          refresh_after_grant):
    ready(api, gh)
    gh.repositories.append({"full_name": "Acme/tico"})            # the installation sees the ticked repository
    refresh_after_grant.hold()
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES('r-tico','Acme/tico',1,?)", (H.now(),))
    assert api.put(ACCESS, json={"mode": "all", "all_access": "read", "create_product_repositories": True},
                   headers=auth()).status_code == 200
    assert {"full_name": "Acme/tico", "access": "read"} in api.get(ACCESS, headers=auth()).json()["effective"]
    made = product_repo_request(api, BODY, actor="bot-test", key="all-read-bot")
    assert made.status_code == 200, made.text
    refresh_after_grant.finish()
    assert made.json()["bot_access"] == "write"
    view = api.get(ACCESS, headers=auth()).json()
    effective = {r["full_name"]: r["access"] for r in view["effective"]}
    assert view["mode"] == "chosen"
    assert effective == {"Acme/emp-cmo": "write", "Acme/tico": "read", "Acme/tico-services": "write"}
    with api.app_state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM events WHERE action='github.product_repo_created'").fetchone()[0])
        assert detail["bot_access"] == "write" and detail["moved_to_chosen"] is True
        notice = c.execute("SELECT body FROM messages WHERE to_actor='human:ana' AND kind='notice'").fetchone()[0]
    assert ("cmo has write access to it. It was on All ticked repos with read access, so it is now on Chosen repos: "
            "read on the 1 repository it reached and write on this one.") in notice
    assert notice.endswith(made.json()["note"].removeprefix("Created as a private empty repository. "))


def test_a_bot_switched_off_is_refused_and_told_which_right(api, gh):
    ready(api, gh)
    botops_turn(api)
    # Every bot has it by default, BotOps too; the Owner switches it off for both.
    for bot in ("cmo", "botops"):
        path = f"/api/v2/bots/{bot}/repositories"
        assert api.get(path, headers=auth()).json()["create_product_repositories"] is True, bot
        off = api.put(path, json={"mode": api.get(path, headers=auth()).json()["mode"],
                                  "create_product_repositories": False}, headers=auth())
        assert off.status_code == 200 and off.json()["create_product_repositories"] is False, off.text
    for actor in ("bot-test", "botops-test", "person-test", "runner-test"):
        refused = product_repo_request(api, BODY, actor=actor, key="no-right-" + actor)
        assert refused.status_code == 403, (actor, refused.text)
        assert RIGHT in refused.json()["error"]["detail"]
    assert not creates(gh)


def test_an_admin_switches_it_off_the_off_sticks_and_only_the_owner_switches_it_back_on(api, gh):
    ready(api, gh)
    with api.app_state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('access',?)", (encode({"admins": ["riley@acme.example"]}),))
    botops_turn(api)
    # A save that leaves the right as it is still works, for anyone who may save the bot's repositories.
    assert grant(api, who="person-test").status_code == 200
    for actor in ("bot-test", "botops-test", "runner-test"):
        assert grant(api, on=False, who=actor).status_code == 403, actor
    off = grant(api, on=False, who="person-test")
    assert off.status_code == 200 and off.json()["create_product_repositories"] is False
    assert off.json()["product_creation_stopped"] is True
    refused = product_repo_request(api, BODY, actor="bot-test", key="after-revoke")
    assert refused.status_code == 403 and RIGHT in refused.json()["error"]["detail"]
    assert "switched it off" in refused.json()["error"]["detail"]
    assert api.get(PREVIEW, params={"name": "tico-services"}, headers=auth("bot-test")).status_code == 403
    # The off is recorded as a choice, so running the migrations again, or the default changing, doesn't undo it.
    with api.app_state.store.read() as c:
        assert metadata(c, PRODUCT_GRANTS)["cmo"] is False
    api.app_state.store.initialize(seed_market=False)
    assert api.get(ACCESS, headers=auth()).json()["create_product_repositories"] is False
    assert product_repo_request(api, BODY, actor="bot-test", key="after-migrations").status_code == 403
    assert not creates(gh)
    # Switching it back on widens the bot again: the Owner's own click only, not an admin's or a bot's.
    for actor in ("person-test", "bot-test", "botops-test", "runner-test"):
        refused = grant(api, who=actor)
        assert refused.status_code == 403, (actor, refused.text)
    assert api.get(ACCESS, headers=auth()).json()["create_product_repositories"] is False
    assert grant(api).json()["create_product_repositories"] is True
    assert product_repo_request(api, BODY, actor="bot-test", key="back-on").status_code == 200
    assert len(creates(gh)) == 1


def test_a_bot_still_cannot_create_a_public_repo(api, gh):
    ready(api, gh)
    for i, body in enumerate(({**BODY, "visibility": "public"}, {**BODY, "auto_init": True}, {**BODY, "confirmed": False})):
        refused = product_repo_request(api, body, actor="bot-test", key=f"public-{i}")
        assert refused.status_code == 422, refused.text      # the request model takes only private, empty, confirmed
    nothing_made(api, gh)


def test_the_owners_request_carried_by_a_bot_turn_cannot_turn_the_right_on(api, gh):
    ready(api, gh)
    botops_turn(api)
    assert grant(api, on=False).status_code == 200            # the Owner switched cmo off
    carried = {"via-botops": Identity("human:ana", "owner", "ana@acme.example", runner_id="r1", attempt_id="a2",
                                      via="botops", task_actor="bot:botops", confirmed=True),
               "via-self": Identity("human:ana", "owner", "ana@acme.example", runner_id="r1", attempt_id="a1",
                                    task_actor="bot:cmo", confirmed=True)}
    api.app_state.store.settings.test_identities.update(carried)
    # The bot's own turn is refused; BotOps acting for her puts it on a Confirm card that only her own tap runs.
    refused = grant(api, who="via-self")
    assert refused.status_code == 403 and "Only the Owner turns on" in refused.text, refused.text
    card = grant(api, who="via-botops")
    assert card.status_code == 200 and card.json()["needs_confirm"] is True, card.text
    assert api.get(ACCESS, headers=auth()).json()["create_product_repositories"] is False


def test_an_external_agent_of_a_granted_bot_cannot_create(api, gh):
    ready(api, gh)
    assert grant(api).status_code == 200
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('cmo','hermes','agent-hash',?,'human:ana')",
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
