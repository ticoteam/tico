"""The GitHub App: manifest, state nonce, secret storage, installation tokens, runner scope."""

import json
import sqlite3
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, parse_qsl, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend import github_app as G
from backend import hubdb as H
from backend.store import Problem, encode
from backend.enqueue import enqueue_stored
from backend import bot_settings as BS
from backend.tests.helpers.github_app import (
    EVENT_PERMISSIONS,
    FakeGitHub,
    KEY,
    PEM,
    PUBLIC,
    _box,
    api,
    auth,
    botops_turn,
    connect,
    gh,
    manifest,
    manifest_refusal,
    product_repo_request,
    put_extras,
    runner_token,
    token_health,
    turn_token,
)


def test_repository_delete_is_owner_only_scoped_audited_and_never_treats_404_as_success(api, gh):
    botops_turn(api)
    connect(api, administration="true")
    path = "/api/v2/github/repos/Acme/bot-qa-delete"
    head = {**auth(), "Idempotency-Key": str(uuid.uuid4())}
    assert api.delete(path, headers={**head, **auth("person-test")}).status_code == 403
    assert api.delete(path, headers={**head, **auth("botops-test")}).status_code == 403
    assert api.delete("/api/v2/github/repos/elsewhere/bot-qa-delete", headers=head).status_code == 422
    assert not any(c[0] == "DELETE" for c in gh.calls)
    response = api.delete(path, headers=head)
    assert response.status_code == 200 and response.json() == {"repository": "Acme/bot-qa-delete", "deleted": True}
    token = gh.of("/access_tokens")[-1][2]
    assert token == {"repositories": ["bot-qa-delete"], "permissions": {"administration": "write", "metadata": "read"}}
    with api.app.state.store.read() as c:
        event = c.execute("SELECT actor,target FROM events WHERE action='github.repo_deleted'").fetchone()
        assert tuple(event) == ("human:ana", "Acme/bot-qa-delete")
    from backend import rooms

    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)", (encode({"email": "ana@acme.example"}),)
        )
        conversation = rooms.personal_room(c, "human:ana", "botops")
        c.execute(
            "UPDATE messages SET sender_id='human:ana',conversation_id=?,kind='say',body='Delete the QA repository' WHERE id='m-botops'",
            (conversation["id"],),
        )
        c.execute("INSERT INTO attempt_conversations VALUES('a2',?)", (conversation["id"],))
    delegated = api.delete(
        "/api/v2/github/repos/Acme/bot-qa-delegated",
        headers={**auth("botops-test"), "X-Tico-On-Behalf-Of": "turn", "Idempotency-Key": str(uuid.uuid4())},
    )
    assert delegated.status_code == 200 and delegated.json()["deleted"] and "needs_confirm" not in delegated.json(), (
        delegated.text
    )
    with api.app.state.store.read() as c:
        event = c.execute(
            "SELECT actor,detail FROM events WHERE action='github.repo_deleted' AND target='Acme/bot-qa-delegated'"
        ).fetchone()
        assert event["actor"] == "human:ana" and '"via": "botops"' in event["detail"]
    gh.delete_status = 404
    response = api.delete(path, headers={**auth(), "Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 409 and "did not delete" in response.json()["error"]["detail"]
    gh.permissions = {"administration": "read"}
    response = api.delete(path, headers={**auth(), "Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 403 and "Administration" in response.json()["error"]["detail"]


def test_manifest_asks_for_administration_unless_the_owner_unticks_it(api, gh):
    assert manifest(api)["manifest"]["default_permissions"]["administration"] == "write"
    assert "administration" not in manifest(api, administration="false")["manifest"]["default_permissions"]


@pytest.mark.parametrize("administration", [True, False])
@pytest.mark.parametrize("personal", [True, False])
def test_every_event_the_manifest_subscribes_to_is_covered_by_a_permission_it_asks_for(administration, personal):
    """GitHub refuses the whole manifest otherwise, and nobody can connect (v0.6.0-rc.7: `merge_group` without
    `merge_queues`). EVENT_PERMISSIONS is GitHub's rule per event, from its webhook events page."""
    body = G.manifest(SimpleNamespace(public_url=PUBLIC), "Acme Tico", administration, personal=personal)
    assert body["default_events"], "the manifest subscribes to no event"
    for event in body["default_events"]:
        assert event in EVENT_PERMISSIONS, f"add {event} and the permission GitHub requires for it to EVENT_PERMISSIONS"
        assert body["default_permissions"].get(EVENT_PERMISSIONS[event]) in ("read", "write"), event
    assert manifest_refusal(body) == ""


def test_fake_github_refuses_a_manifest_as_github_does_so_connecting_fails_in_tests(api, gh, monkeypatch):
    real = G.manifest

    def rc7(*args, **kw):  # the manifest v0.6.0-rc.7 shipped
        body = real(*args, **kw)
        del body["default_permissions"]["merge_queues"]
        return body

    monkeypatch.setattr(G, "manifest", rc7)
    with pytest.raises(AssertionError, match="Default events are not supported by permissions: merge_group"):
        connect(api)
    assert not gh.of("/conversions")  # GitHub made no App, so nothing came back to Tico
    monkeypatch.setattr(G, "manifest", lambda *a, **kw: {**real(*a, **kw), "default_events": ["push", "deployment"]})
    with pytest.raises(AssertionError, match="unknown to the fake GitHub.*deployment"):
        connect(api)


def test_no_token_names_merge_queues_so_an_app_made_before_it_keeps_working(api, gh):
    """An App made before the manifest asked for Merge queues: its installation reports none, and GitHub refuses a token
    naming a permission the installation lacks. Turn, read and check tokens never name it."""
    connect(api)
    gh.permissions = {name: level for name, level in manifest_permissions().items() if name != "merge_queues"}
    asked = []
    gh.on_token = lambda body: asked.append((body or {}).get("permissions") or {})
    runner_token(api, "cpo")
    assert turn_token(api).status_code == 200
    assert api.app_state.github_app.mint_checks("Acme/emp-cpo")
    assert len(asked) >= 2 and not any("merge_queues" in permissions for permissions in asked)
    assert "merge_queues" not in {**G.TURN_READS, **G.TURN_PERMISSIONS, **G.READ_PERMISSIONS, **G.CHECKS_PERMISSIONS}
    assert not token_health(api)


def manifest_permissions():
    return G.manifest(SimpleNamespace(public_url=PUBLIC), "Acme Tico", True)["default_permissions"]


def test_missing_repo_permission_is_refused_with_both_fixes_and_botops_creates_it_once_granted(api, gh):
    """BotOps' create without Administration: a refusal naming a person and both fixes, no task filed, the bot kept as
    planned; once GitHub reports the permission, BotOps's next try makes the repository."""
    connect(api, administration="false")
    gh.permissions = {"administration": "read", "contents": "write", "metadata": "read"}
    gh.missing = {"bot-newbie"}
    botops_turn(api)
    create = lambda: api.post(
        "/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test")
    )
    with api.app_state.store.read() as c:
        tasks = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    for refused in (create(), create()):
        error = refused.json()["error"]
        assert refused.status_code == 409 and error["code"] == "github_permission_missing", refused.text
        assert error["detail"].startswith("A person must act")
        assert error["create_url"] == "https://github.com/new?owner=Acme&visibility=private&name=bot-newbie"
        assert error["permissions_url"] == "https://github.com/organizations/Acme/settings/apps/acme-tico/permissions"
    with api.app_state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == tasks
        assert H.bot(c, "newbie")["state"] == "planned"
    assert not gh.created_repositories
    gh.permissions = {"administration": "write", "contents": "write", "metadata": "read"}
    checked = api.post("/api/v2/github/app/check-permissions", json={}, headers=auth())
    assert checked.status_code == 200 and checked.json()["administration"] is True, checked.text
    made = create()
    assert made.status_code == 200 and gh.created_repositories == {"Acme/bot-newbie"}, made.text


def test_a_first_repository_list_that_fails_is_named_by_its_code_on_the_install_event(api, gh, monkeypatch):
    from backend import repositories

    connect(api)
    secret = "ghs_PLANTEDsecret0123456789abcdef"

    def refused(service):
        raise Problem("github_repositories", "GitHub answered " + secret, 502)

    monkeypatch.setattr(repositories, "sync", refused)
    assert api.get("/api/v2/github/app/installed", headers=auth()).status_code == 302
    with api.app_state.store.read() as c:
        rows = [r["detail"] for r in c.execute("SELECT detail FROM events WHERE action='github.app_installed'")]
    assert json.loads(rows[-1]) == {"installed": True, "sync_failed": "github_repositories"}
    assert secret not in "".join(rows)


def test_state_is_single_use_and_bound_to_session(api, gh):
    state = manifest(api)["state"]
    bad = api.get("/api/v2/github/app/callback", params={"code": "c", "state": "forged"}, headers=auth())
    assert bad.status_code == 400
    # Another session's return parks its code and exchanges nothing; the state stays for the owner's own return.
    other = api.get("/api/v2/github/app/callback", params={"code": "c", "state": state}, headers=auth("other-test"))
    assert other.status_code == 200 and other.json() == {"parked": True, "finish": "/api/v2/github/app/finish"}
    assert not gh.of("/conversions")
    ok = api.get("/api/v2/github/app/callback", params={"code": "c", "state": state}, headers=auth())
    assert ok.status_code == 302 and len(gh.of("/conversions")) == 1
    replay = api.get("/api/v2/github/app/callback", params={"code": "c", "state": state}, headers=auth())
    assert replay.status_code == 400


def test_callback_stores_secrets_and_never_exposes_pem(api, gh, caplog):
    caplog.set_level("DEBUG")
    r = connect(api)
    assert r.headers["location"].startswith("https://github.com/apps/acme-tico/installations/new?state=")
    assert gh.of("/conversions")[0][1] == "/app-manifests/the-code/conversions"
    status = api.get("/api/v2/github/app", headers=auth())
    body = status.text
    assert (
        status.json()["slug"] == "acme-tico" and status.json()["org"] == "Acme" and status.json()["installed"] is False
    )
    for secret in ("BEGIN", "whs", "PRIVATE KEY", "\"cs\""):
        assert secret not in body
    assert "BEGIN" not in caplog.text
    # At rest: encrypted, not the PEM.
    raw = sqlite3.connect(api.app_state.store.settings.db_path).execute("SELECT * FROM github_app").fetchall()
    assert raw and PEM.encode() not in b"".join(v for v in raw[0] if isinstance(v, bytes))
    assert "PRIVATE KEY" not in json.dumps([str(v) for v in raw[0]])
    assert api.get("/api/v2/github/app/manifest", params={"org": "Acme"}, headers=auth()).status_code == 409


def test_jwt_claims_and_repo_scoped_token(api, gh):
    connect(api)
    r = api.post("/api/v2/github/token", json={"bot": "cpo"}, headers=auth("owner-test"))
    # An owner is neither the bot nor its runner.
    assert r.status_code == 403
    runner_token(api, "cpo")
    r = api.post("/api/v2/github/token", json={"bot": "cpo"}, headers=auth("runner-test"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["repository"] == "Acme/emp-cpo" and data["token"].startswith("ghs_")
    method, path, body, header = gh.of("/access_tokens")[-1]
    assert path == "/app/installations/77/access_tokens"
    assert body == {
        "repositories": ["emp-cpo"],
        "permissions": {"contents": "write", "pull_requests": "write", "issues": "write", "metadata": "read"},
    }
    claims = jwt.decode(
        header.removeprefix("Bearer "), KEY.public_key(), algorithms=["RS256"], options={"verify_exp": False}
    )
    assert claims["iss"] == "4242" and claims["exp"] - claims["iat"] <= 660 and claims["iat"] <= time.time() - 59
    assert claims["exp"] <= time.time() + 600


def test_turn_token_reads_workflow_runs_once_the_installation_allows_it(api, gh):
    # The App asks for Actions: write, for Tico's own release dispatch; a bot's turn token only ever reads Actions.
    assert manifest(api)["manifest"]["default_permissions"]["actions"] == "write"
    connect(api)
    runner_token(api, "cpo")
    # An installation that has not accepted Actions yet still gets its token, without it.
    assert turn_token(api).status_code == 200
    assert "actions" not in gh.of("/access_tokens")[-1][2]["permissions"]
    gh.permissions = {"actions": "read"}
    assert api.post("/api/v2/github/app/check-permissions", headers=auth()).status_code == 200
    assert turn_token(api).status_code == 200
    assert gh.of("/access_tokens")[-1][2]["permissions"]["actions"] == "read"
    gh.permissions = {"actions": "write"}  # accepted for the release dispatch: a turn still only reads
    assert api.post("/api/v2/github/app/check-permissions", headers=auth()).status_code == 200
    assert turn_token(api).status_code == 200
    assert gh.of("/access_tokens")[-1][2]["permissions"]["actions"] == "read"


def test_what_github_reports_of_the_installation_is_kept_for_health_and_forgotten_with_the_app(api, gh):
    from backend.github_app import PERMISSIONS_SEEN, permission_seen

    def seen(name):
        with api.app_state.store.read() as c:
            return permission_seen(c, name)

    connect(api)
    gh.permissions = {"checks": "read"}
    assert api.post("/api/v2/github/app/check-permissions", headers=auth()).status_code == 200
    assert seen("checks") == "read" and seen("deployments") == ""
    gh.permissions = {"checks": "write"}
    assert api.post("/api/v2/github/app/check-permissions", headers=auth()).status_code == 200
    assert seen("checks") == "write"
    with api.app_state.store.transaction() as c:
        api.app_state.github_app.forget(c)
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (PERMISSIONS_SEEN,)).fetchone()


def test_a_private_meetings_turn_gets_a_read_only_token_and_a_team_meetings_a_write_one(api, gh):
    connect(api)
    runner_token(api, "cmo")
    with api.app_state.store.transaction() as c:
        for rid, visibility in (("m-private", "private"), ("m-team", "team")):
            c.execute(
                "INSERT INTO live_meetings(id,owner_actor,title,state,window_ms,created_at,updated_at,started_at,visibility) "
                "VALUES(?,'human:ana','Plan','live',30000,?,?,?,?)",
                (rid, H.now(), H.now(), H.now(), visibility),
            )
        conv = H.open_conversation(c, H.KEEPER, ["bot:cmo"], room_key="live-meeting:m-private")
        H.feed(
            c, "bot:cmo", "A meeting turn", conv, refs={"live_meeting": {"meeting_id": "m-private", "turn_id": "t1"}}
        )
        job = c.execute("SELECT id FROM jobs WHERE bot_id='cmo'").fetchone()["id"]
        c.execute(
            "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
            "VALUES('a1',?,'cmo','r1',1,'x','running',?,?)",
            (job, H.shift(H.now(), hours=1), H.now()),
        )
    r = api.post("/api/v2/github/token", json={"bot": "cmo"}, headers=auth("bot-test"))
    assert r.status_code == 200, r.text
    assert gh.of("/access_tokens")[-1][2]["permissions"]["contents"] == "read"
    assert {t["access"] for t in r.json()["tokens"]} == {"read"}
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE live_meetings SET visibility='team' WHERE id='m-private'")
    r = api.post("/api/v2/github/token", json={"bot": "cmo"}, headers=auth("bot-test"))
    assert r.status_code == 200 and gh.of("/access_tokens")[-1][2]["permissions"]["contents"] == "write"


def test_turn_token_reads_pull_request_checks_and_statuses(api, gh):
    # `gh pr checks` reads check runs and commit statuses; a private repository refuses both without them.
    asked = manifest(api)["manifest"]["default_permissions"]
    assert {"checks", "statuses", "deployments"} <= asked.keys()
    assert asked["pull_requests"] == asked["issues"] == asked["contents"] == "write"  # a bot still opens PRs and issues
    connect(api)
    runner_token(api, "cpo")
    gh.permissions = {"actions": "read", "checks": "read", "statuses": "read", "deployments": "read"}
    assert turn_token(api).status_code == 200
    asked = gh.of("/access_tokens")[-1][2]["permissions"]
    assert asked == {
        "contents": "write",
        "pull_requests": "write",
        "issues": "write",
        "metadata": "read",
        "actions": "read",
        "checks": "read",
        "statuses": "read",
        "deployments": "read",
    }


def test_turn_token_asks_only_for_the_reads_the_installation_accepted(api, gh):
    connect(api)
    runner_token(api, "cpo")
    gh.permissions = {"checks": "read", "statuses": "write"}  # accepted the manifest before Actions was added
    assert turn_token(api).status_code == 200
    asked = gh.of("/access_tokens")[-1][2]["permissions"]
    assert asked["checks"] == asked["statuses"] == "read" and "actions" not in asked
    calls = len(gh.of("/access_tokens"))
    api.app_state.github_app.cache.clear()
    assert turn_token(api).status_code == 200
    assert len(gh.of("/access_tokens")) == calls + 1  # remembered: no refused ask first


def test_turn_token_stops_when_github_refuses_even_the_base(api, gh):
    connect(api)
    runner_token(api, "cpo")
    gh.permissions = {
        "actions": "read",
        "checks": "read",
        "statuses": "read",
        "pull_requests": "write",
        "issues": "write",
        "deployments": "read",
    }
    gh.refuse = (422, "The permissions requested are not granted to this installation.")
    assert turn_token(api).status_code == 409
    assert len(gh.of("/access_tokens")) == 2


def test_runner_refuses_bot_it_does_not_run_and_repo_outside_config(api, gh):
    connect(api)
    runner_token(api, "cpo")
    r = api.post("/api/v2/github/token", json={"bot": "cmo"}, headers=auth("runner-test"))
    assert r.status_code == 403 and not gh.of("/access_tokens")
    runner_token(api, "cfo")  # runs it, but its repo belongs to another org
    r = api.post("/api/v2/github/token", json={"bot": "cfo"}, headers=auth("runner-test"))
    assert r.status_code == 403 and "connected organization" in r.text and not gh.of("/access_tokens")
    # A bot's own turn token names only itself.
    assert api.post("/api/v2/github/token", json={"bot": "cpo"}, headers=auth("bot-test")).status_code in (403, 409)


def test_per_call_token_reuses_cache_without_writes_but_turn_start_keeps_45_minutes(api, gh, monkeypatch):
    connect(api)
    runner_token(api, "cpo")
    clock = [time.time()]
    gh.clock = lambda: clock[0]
    monkeypatch.setattr(G.time, "time", lambda: clock[0])

    started = turn_token(api)
    assert started.status_code == 200, started.text
    first = started.json()
    remaining = G._iso(first["expires_at"]) - clock[0]
    assert remaining >= G.TURN_START_REFRESH_MARGIN
    github_mints = len(gh.of("/access_tokens"))
    with api.app_state.store.read() as c:
        events_before = c.execute("SELECT COUNT(*) FROM events WHERE action='github.token'").fetchone()[0]
    clock[0] += 49 * 60  # the first 49 minutes of the old 45-minute-margin policy are the slow path

    store = api.app_state.store
    original_transaction = store.transaction
    writes = []

    def counted_transaction():
        writes.append(True)
        return original_transaction()

    monkeypatch.setattr(store, "transaction", counted_transaction)

    timings = []
    for _ in range(40):
        started_at = time.perf_counter()
        refreshed = turn_token(api, purpose="git")
        timings.append((time.perf_counter() - started_at) * 1000)
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["token"] == first["token"]
    assert len(gh.of("/access_tokens")) == github_mints
    assert not writes, "a valid per-call cache hit must not open a write transaction"
    with store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='github.token'").fetchone()[0] == events_before
    print(f"CACHED_GITHUB_TOKEN_P50_MS={statistics.median(timings):.2f}")

    # Below ten minutes remaining, a per-call refresh mints again.
    clock[0] += 2 * 60
    next_git = turn_token(api, purpose="git")
    assert next_git.status_code == 200, next_git.text
    assert len(gh.of("/access_tokens")) == github_mints + 1
    assert G._iso(next_git.json()["expires_at"]) - clock[0] >= G.GIT_CALL_REFRESH_MARGIN

    # A token with only 44 minutes left is fine for a one-off git request but not a new turn seed.
    clock[0] += 16 * 60
    next_turn = turn_token(api)
    assert next_turn.status_code == 200, next_turn.text
    next_body = next_turn.json()
    assert len(gh.of("/access_tokens")) == github_mints + 2
    assert G._iso(next_body["expires_at"]) - clock[0] >= G.TURN_START_REFRESH_MARGIN
    assert next_body["token"] != first["token"]

    # An expired cached token still goes to GitHub, and mint errors keep their existing diagnosis.
    clock[0] += 61 * 60
    gh.refuse = (500, "ghs_SYNTHETIC_DO_NOT_EXPOSE")
    mints_before_error = len(gh.of("/access_tokens"))
    failed = turn_token(api, purpose="git")
    assert failed.status_code == 502 and failed.json()["error"]["code"] == "github_token"
    assert "HTTP 500" in failed.json()["error"]["detail"]
    assert len(gh.of("/access_tokens")) == mints_before_error + 1
    assert token_health(api)


def test_concurrent_misses_share_one_mint_without_a_write_lock(api, gh, monkeypatch):
    import threading

    connect(api)
    service = api.app_state.github_app
    service.mint(None, {"metadata": "read"})  # learns the installation and the app's JWT
    store, writes = api.app_state.store, []
    original_transaction = store.transaction
    monkeypatch.setattr(store, "transaction", lambda: writes.append(True) or original_transaction())
    release, answer = threading.Event(), gh.__call__

    def slow(request):
        if request.url.path.endswith("/access_tokens"):
            release.wait(5)
        return answer(request)

    monkeypatch.setattr(G, "TRANSPORT", httpx.MockTransport(slow))
    mints_before, tokens = len(gh.of("/access_tokens")), []
    threads = [
        threading.Thread(target=lambda: tokens.append(service.mint(["Acme/emp-cpo"], G.TURN_PERMISSIONS)[0]))
        for _ in range(6)
    ]
    for thread in threads:
        thread.start()
    deadline = time.time() + 5
    while len(gh.of("/access_tokens")) == mints_before and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)
    release.set()
    for thread in threads:
        thread.join(5)
    assert len(tokens) == 6 and len(set(tokens)) == 1
    assert len(gh.of("/access_tokens")) == mints_before + 1
    assert not writes, "a mint reads the installation and reuses the JWT without taking the write lock"


def test_extra_repositories_join_the_turn_token_with_the_same_permissions(api, gh):
    connect(api)
    assert (
        api.put('/api/v2/bots/cpo/repositories', json={'mode': 'chosen', 'chosen': []}, headers=auth()).status_code
        == 200
    )
    runner_token(api, "cpo")
    r = put_extras(
        api,
        "cpo",
        ["shared-docs", "Acme/design-system", "https://github.com/Acme/infra.git", "Acme/emp-cpo", "shared-docs"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["repositories"] == ["Acme/shared-docs", "Acme/design-system", "Acme/infra"]
    data = turn_token(api).json()
    assert data["repository"] == "Acme/emp-cpo"
    assert set(data["repositories"]) == {"Acme/emp-cpo", "Acme/shared-docs", "Acme/design-system", "Acme/infra"}
    _, _, body, _ = gh.of("/access_tokens")[-1]
    assert body["repositories"] == ["design-system", "emp-cpo", "infra", "shared-docs"]
    assert body["permissions"] == {"contents": "write", "pull_requests": "write", "issues": "write", "metadata": "read"}
    assert set(api.get("/api/v2/bots/cpo/github-repos", headers=auth()).json()["repositories"]) == set(
        data["repositories"]
    ) - {"Acme/emp-cpo"}


def test_missing_own_repository_is_found_by_one_listing_not_a_probe_per_grant(api, gh):
    connect(api)
    assert (
        api.put('/api/v2/bots/cpo/repositories', json={'mode': 'chosen', 'chosen': []}, headers=auth()).status_code
        == 200
    )
    runner_token(api, "cpo")
    extras = [f"docs-{n}" for n in range(5)]
    assert put_extras(api, "cpo", extras).status_code == 200
    gh.repositories = [{"full_name": "Acme/" + name} for name in extras]
    gh.missing = {"emp-cpo"}
    data = turn_token(api).json()
    assert set(data["repositories"]) == {"Acme/" + name for name in extras}
    assert len(gh.of("/installation/repositories")) == 1 and not gh.of("/repos/Acme/docs-0")
    # Within the missing mark, the own repository no longer fails a first mint on every call.
    calls = len(gh.calls)
    assert turn_token(api).status_code == 200 and len(gh.calls) == calls
    # When GitHub will not list, each repository is probed as before.
    with api.app_state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='repositories-confirmed-missing'")
    gh.list_status = 500
    assert set(turn_token(api).json()["repositories"]) == {"Acme/" + name for name in extras}
    assert gh.of("/repos/Acme/emp-cpo") and gh.of("/repos/Acme/docs-0")


def test_only_the_owner_manages_extra_repositories(api, gh):
    connect(api)
    runner_token(api, "cpo")
    for who in ("person-test", "runner-test", "bot-test"):
        # A bot without a live attempt is refused earlier still (409).
        assert put_extras(api, "cpo", ["shared-docs"], who).status_code in (403, 409), who
        assert api.get("/api/v2/bots/cpo/github-repos", headers=auth(who)).status_code in (403, 409), who
    assert put_extras(api, "cpo", ["shared-docs"], "nobody").status_code == 401
    assert turn_token(api).json()["repositories"] == ["Acme/emp-cpo"]


def test_product_repository_preview_is_owner_only_and_rejects_paths_before_github(api, gh):
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}
    path = "/api/v2/github/product-repos/preview"
    botops_turn(api)
    # Every bot may create product repositories unless switched off (test_bot_product_repos.py): BotOps is, here.
    assert (
        api.put(
            "/api/v2/bots/botops/repositories",
            json={"mode": "own", "create_product_repositories": False},
            headers=auth(),
        ).status_code
        == 200
    )
    before = len(gh.calls)
    body = {"org": "Acme", "name": "tico-recorder", "visibility": "private", "auto_init": False, "confirmed": True}
    for actor in ("person-test", "botops-test"):
        assert api.get(path, params={"name": "tico-recorder"}, headers=auth(actor)).status_code == 403
        assert product_repo_request(api, body, actor=actor, key="non-owner-" + actor).status_code == 403
    assert len(gh.calls) == before, "non-Owner credentials cannot preview or create a product repository"
    for name in ("Acme/tico-recorder", "https://github.com/Acme/tico-recorder", "..", "../tico"):
        assert api.get(path, params={"name": name}, headers=auth()).status_code == 422
    assert len(gh.calls) == before, "authorization and exact-name validation happen before GitHub discovery"
    preview = api.get(path, params={"name": "tico-recorder"}, headers=auth())
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "org": "Acme",
        "name": "tico-recorder",
        "repository": "Acme/tico-recorder",
        "visibility": "private",
        "auto_init": False,
        "capability": "available",
        "capability_detail": "The connected installation currently has Administration: write.",
    }
    assert not any(call[0] == "POST" and call[1].endswith("/repos") for call in gh.calls)


def test_owner_product_creation_uses_exact_name_private_empty_and_idempotent_receipt(api, gh, monkeypatch):
    from backend import repositories

    refreshes = []
    monkeypatch.setattr(repositories, "queue_sync", lambda service, refresh=False: refreshes.append(refresh))
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}
    preview = api.get("/api/v2/github/product-repos/preview", params={"name": "tico-recorder"}, headers=auth()).json()
    body = {
        "org": preview["org"],
        "name": preview["name"],
        "visibility": preview["visibility"],
        "auto_init": preview["auto_init"],
        "confirmed": True,
    }
    created = product_repo_request(api, body, key="product-create-same")
    assert created.status_code == 200, created.text
    assert created.json() == {
        "repository": "Acme/tico-recorder",
        "html_url": "https://github.com/Acme/tico-recorder",
        "visibility": "private",
        "auto_init": False,
        "installation_access": "available",
        "note": "Created as a private empty repository. Tico will refresh its repository inventory; no bot access was granted.",
    }
    creation = next(call for call in gh.calls if call[0] == "POST" and call[1] == "/orgs/Acme/repos")
    assert creation[2] == {
        "name": "tico-recorder",
        "private": True,
        "auto_init": False,
        "description": "Tico product repository",
    }
    assert refreshes == [True]
    assert gh.created_repositories == {"Acme/tico-recorder"}
    count = len([call for call in gh.calls if call[0] == "POST" and call[1] == "/orgs/Acme/repos"])
    replay = product_repo_request(api, body, key="product-create-same")
    assert replay.status_code == 200 and replay.json() == created.json()
    assert len([call for call in gh.calls if call[0] == "POST" and call[1] == "/orgs/Acme/repos"]) == count
    changed_retry = product_repo_request(api, {**body, "name": "another-name"}, key="product-create-same")
    assert changed_retry.status_code == 409 and changed_retry.json()["error"]["code"] == "idempotency_conflict"
    duplicate = product_repo_request(api, body, key="product-create-different")
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "github_repo_exists"
    assert gh.created_repositories == {"Acme/tico-recorder"}, "a retry never replaces or duplicates existing history"
    with api.app_state.store.read() as c:
        event = c.execute(
            "SELECT action,target,detail FROM events WHERE action='github.product_repo_created'"
        ).fetchone()
        assert event and event["target"] == "Acme/tico-recorder"
        assert c.execute("SELECT COUNT(*) FROM bot_repo_access").fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT COUNT(*) FROM idempotency WHERE operation='/api/v2/github/product-repos' AND key='product-create-same'"
            ).fetchone()[0]
            == 1
        )
        operation = c.execute(
            "SELECT state,subject FROM idempotency WHERE binding='github_product_repo' "
            "AND operation='/api/v2/github/product-repos' AND key='product-create-same'"
        ).fetchone()
        assert tuple(operation) == ("completed", "Acme/tico-recorder")
    calls_before_durable_replay = len(
        [call for call in gh.calls if call[0] == "POST" and call[1] == "/orgs/Acme/repos"]
    )
    with api.app_state.store.transaction() as c:
        c.execute(
            "DELETE FROM idempotency WHERE actor='human:ana' AND operation=? AND key=? AND binding IS NULL",
            ("/api/v2/github/product-repos", "product-create-same"),
        )  # the answer only; the binding stays
    durable_replay = product_repo_request(api, body, key="product-create-same")
    assert durable_replay.status_code == 200 and durable_replay.json() == created.json()
    assert (
        len([call for call in gh.calls if call[0] == "POST" and call[1] == "/orgs/Acme/repos"])
        == calls_before_durable_replay
    )


def test_owner_mcp_product_repository_tool_previews_confirms_and_replays(api, gh):
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}

    def call(arguments):
        response = api.post(
            "/api/v2/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "tico_repo_product_create", "arguments": arguments},
            },
            headers=auth(),
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        return result["isError"], result["structuredContent"]

    operation_id = "mcp-product-create-stable"
    error, preview = call({"name": "tico-recorder", "operation_id": operation_id})
    assert not error and preview["confirmation_required"] is True and preview["created"] is False
    assert preview["preview"]["repository"] == "Acme/tico-recorder"
    assert not gh.of("/orgs/Acme/repos"), "the preview call does not create a repository"

    error, refused = call(
        {"name": "tico-recorder", "operation_id": operation_id, "confirm_repository": "Acme/other-product"}
    )
    assert error and refused["error"] == "confirmation_required"
    assert not gh.of("/orgs/Acme/repos"), "a mismatched confirmation does not create a repository"

    arguments = {"name": "tico-recorder", "operation_id": operation_id, "confirm_repository": "Acme/tico-recorder"}
    error, created = call(arguments)
    assert not error and created["repository"] == "Acme/tico-recorder"
    creates = gh.of("/orgs/Acme/repos")
    assert len(creates) == 1
    assert creates[0][2] == {
        "name": "tico-recorder",
        "private": True,
        "auto_init": False,
        "description": "Tico product repository",
    }

    error, replay = call(arguments)
    assert not error and replay == created
    assert len(gh.of("/orgs/Acme/repos")) == 1, "the same MCP operation id replays its saved receipt"

    gh.permissions = {"contents": "write", "metadata": "read"}
    before = len(gh.of("/orgs/Acme/repos"))
    error, unavailable = call({"name": "tico-recorder", "operation_id": "mcp-product-create-no-admin"})
    assert not error and unavailable["preview"]["capability"] == "missing"
    assert "Administration: write" in unavailable["preview"]["capability_detail"]
    assert unavailable["created"] is False and unavailable["confirmation_required"] is False
    assert len(gh.of("/orgs/Acme/repos")) == before, "missing Administration does not create a repository"


def test_lost_github_create_response_keeps_durable_key_binding_and_never_retries_create(api, gh):
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}
    preview = api.get("/api/v2/github/product-repos/preview", params={"name": "tico-recorder"}, headers=auth()).json()
    body = {
        "org": preview["org"],
        "name": preview["name"],
        "visibility": preview["visibility"],
        "auto_init": preview["auto_init"],
        "confirmed": True,
    }
    gh.lose_next_product_create_response = True

    lost = product_repo_request(api, body, key="lost-create-response")
    assert lost.status_code == 409 and lost.json()["error"]["code"] == "github_create_outcome_unknown"
    assert gh.created_repositories == {"Acme/tico-recorder"}

    changed = product_repo_request(api, {**body, "name": "another-product"}, key="lost-create-response")
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_conflict"
    same = product_repo_request(api, body, key="lost-create-response")
    assert same.status_code == 409 and same.json()["error"]["code"] == "github_create_outcome_unknown"

    create_calls = [call for call in gh.calls if call[0] == "POST" and call[1] == "/orgs/Acme/repos"]
    assert len(create_calls) == 1
    assert gh.created_repositories == {"Acme/tico-recorder"}
    with api.app_state.store.read() as c:
        operation = c.execute(
            "SELECT actor,operation,key,request_hash,subject,state,response "
            "FROM idempotency WHERE binding='github_product_repo' AND actor='human:ana' AND key=?",
            ("lost-create-response",),
        ).fetchone()
        assert operation["actor"] == "human:ana"
        assert operation["operation"] == "/api/v2/github/product-repos"
        assert operation["key"] == "lost-create-response"
        assert operation["request_hash"] == G.digest(encode(body))
        assert operation["subject"] == "Acme/tico-recorder"
        assert operation["state"] == "pending" and operation["response"] is None


def test_product_create_does_not_expose_github_error_bodies(api, gh):
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}
    gh.refuse = (500, "ghs_SYNTHETIC_DO_NOT_EXPOSE")
    preview = api.get("/api/v2/github/product-repos/preview", params={"name": "tico-recorder"}, headers=auth()).json()
    body = {
        "org": preview["org"],
        "name": preview["name"],
        "visibility": preview["visibility"],
        "auto_init": preview["auto_init"],
        "confirmed": True,
    }
    token_failed = product_repo_request(api, body, key="safe-token-error")
    assert token_failed.status_code == 409
    assert "ghs_SYNTHETIC_DO_NOT_EXPOSE" not in token_failed.text
    assert not any(call[0] == "POST" and call[1] == "/orgs/Acme/repos" for call in gh.calls)

    gh.refuse = None
    gh.generate_status = 500
    gh.create_response_message = "ghs_SYNTHETIC_DO_NOT_EXPOSE"
    failed = product_repo_request(api, body, key="safe-create-error")
    assert failed.status_code == 409 and failed.json()["error"]["code"] == "github_create_outcome_unknown"
    assert "ghs_SYNTHETIC_DO_NOT_EXPOSE" not in failed.text


def test_selected_bot_repository_creation_grant_and_revocation(api, gh):
    connect(api, administration='true')
    runner_token(api, 'cmo')
    with api.app_state.store.transaction() as c:
        c.execute(
            "INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('m-cmo','keeper','bot:cmo','request','Prepare the repository',?)",
            (H.now(),),
        )
        enqueue_stored(c, 'm-cmo')
        job = c.execute("SELECT id FROM jobs WHERE bot_id='cmo'").fetchone()['id']
        c.execute(
            "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
            "VALUES('a1',?,'cmo','r1',1,'x','running',?,?)",
            (job, H.shift(H.now(), hours=1), H.now()),
        )
    path = '/api/v2/bots/cmo/repositories'
    create = lambda body: api.post('/api/v2/github/repos', json=body, headers=auth('bot-test'))
    assert api.get(path, headers=auth()).json()['create_repositories'] is False
    denied = create({'slug': 'newbie'})
    assert denied.status_code == 403 and 'Ask BotOps' in denied.text
    for actor in ('bot-test', 'person-test', 'runner-test'):
        assert api.put(path, json={'mode': 'own', 'create_repositories': True}, headers=auth(actor)).status_code == 403
    saved = api.put(path, json={'mode': 'own', 'create_repositories': True}, headers=auth())
    assert saved.status_code == 200 and saved.json()['create_repositories'] is True
    # Legacy access writes preserve the separate human-managed creation grant.
    assert api.put(path, json={'mode': 'own'}, headers=auth()).json()['create_repositories'] is True
    for body in ({'slug': 'oldie'}, {'slug': 'nobody'}, {'slug': 'newbie', 'template': 'elsewhere/template'}):
        assert create(body).status_code == 403
    made = create({'slug': 'newbie', 'empty': True})
    assert made.status_code == 200, made.text
    assert made.json()['repository'] == 'Acme/bot-newbie'
    assert gh.created_repositories == {"Acme/bot-newbie"}, "the existing bot creator keeps its bot- name contract"
    with api.app_state.store.read() as c:
        assert c.execute("SELECT actor FROM events WHERE action='github.repo_created'").fetchone()[0] == 'bot:cmo'
        assert BS.read(c, 'cmo').get('create_repositories') is None
    # A local grant never replaces GitHub's own Administration permission.
    gh.permissions = {'administration': 'read', 'contents': 'write'}
    missing = create({'slug': 'newbie', 'empty': True})
    assert missing.status_code == 409 and missing.json()['error']['code'] == 'github_permission_missing'
    assert (
        api.put(path, json={'mode': 'own', 'create_repositories': False}, headers=auth()).json()['create_repositories']
        is False
    )
    assert create({'slug': 'newbie'}).status_code == 403
    assert api.get('/api/v2/bots/botops/repositories', headers=auth()).json()['create_repositories'] is True


def test_botops_token_writes_every_bot_in_its_own_run_the_persons_bots_for_them_and_none_for_a_bot(api, gh):
    from backend import team_rules

    connect(api)
    botops_turn(api)
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE jobs SET message_id='m-botops' WHERE bot_id='botops'")
        # The daily update: BotOps' own schedule.
        c.execute("UPDATE messages SET kind='notice',refs='{\"wake\": \"update\"}' WHERE id='m-botops'")
    wide = ["Acme/emp-botops", "Acme/emp-cmo", "Acme/emp-cpo", "Acme/emp-newbie"]
    assert sorted(turn_token(api, "botops").json()["repositories"]) == wide
    with api.app_state.store.read() as c:
        # The computer's git helper asked: the run in progress on it decided the scope.
        audit = json.loads(
            c.execute(
                "SELECT detail FROM events WHERE action='github.token' AND target='botops' "
                "ORDER BY created_at DESC,rowid DESC LIMIT 1"
            ).fetchone()[0]
        )
    assert audit["runs"] == [{"attempt": "a2", "rights": "bot:botops", "own_run": True}]
    assert (
        sorted(
            api.post("/api/v2/github/token", json={"bot": "botops"}, headers=auth("botops-test")).json()["repositories"]
        )
        == wide
    )
    with api.app_state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"botops_manages_bots": False})
    assert turn_token(api, "botops").json()["repositories"] == ["Acme/emp-botops"]
    with api.app_state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"botops_manages_bots": True})
        c.execute("UPDATE messages SET sender_id='bot:cmo' WHERE id='m-botops'")
    assert turn_token(api, "botops").json()["repositories"] == ["Acme/emp-botops"]
    # A person asking: the bots they manage (Riley runs cpo), whatever team rule; nothing once no run is in progress.
    from backend import rooms

    with api.app_state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"botops_manages_bots": False})
        c.execute("UPDATE bot_config SET owner_id='riley' WHERE bot='cpo'")
        room = rooms.personal_room(c, "human:riley", "botops")
        c.execute(
            "UPDATE messages SET sender_id='human:riley',conversation_id=?,kind='say' WHERE id='m-botops'",
            (room["id"],),
        )
        c.execute("INSERT INTO attempt_conversations VALUES('a2',?)", (room["id"],))
    assert sorted(turn_token(api, "botops").json()["repositories"]) == ["Acme/emp-botops", "Acme/emp-cpo"]
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id='a2'")
    assert turn_token(api, "botops").json()["repositories"] == ["Acme/emp-botops"]


def lose_key(api, *sources):
    """Seal directory credentials for `sources` with github-app.key, then lose the file with no copy anywhere."""
    state = api.app_state
    with state.store.transaction() as c:
        for source in sources:
            state.directory.put_credentials(c, "human:ana", source, {"client_secret": source + "-synthetic"}, source)
    state.github_app.key_file().unlink()


def directory_put(api, **more):
    revision = api.get("/api/v2/directory", headers=auth()).json()["revision"]
    body = {
        "source": "entra",
        "filter": {},
        "expected_revision": revision,
        "credentials": {"tenant": "t", "client_id": "c", "client_secret": "entra-new"},
        **more,
    }
    return api.put("/api/v2/directory", json=body, headers={**auth(), "Idempotency-Key": str(uuid.uuid4())})


def test_a_lost_github_app_key_is_replaced_only_when_the_owner_confirms(api, gh):
    connect(api)
    lose_key(api, "entra")
    assert api.post("/api/v2/github/app/disconnect", headers=auth()).status_code == 200
    state = api.app_state
    for params in ({}, {"replace_lost_key": "true"}):  # a GET, even a link carrying the flag, never drops anything
        refused = api.get("/api/v2/github/app/manifest", params={"org": "Acme", **params}, headers=auth())
        assert refused.status_code == 409 and "replace-lost-key" in refused.text
        with state.store.read() as c:
            assert c.execute("SELECT count(*) FROM directory_credentials").fetchone()[0] == 1
    assert api.post("/api/v2/github/app/replace-lost-key", headers=auth("person-test")).status_code == 403
    assert api.post("/api/v2/github/app/replace-lost-key", headers=auth()).json() == {"replaced": True}
    connect(api)
    with state.store.read() as c:
        assert c.execute("SELECT count(*) FROM directory_credentials").fetchone()[0] == 0  # to be entered again
        assert state.github_app.private_key(c, state.github_app.row(c))
        (event,) = c.execute("SELECT detail FROM events WHERE action='github.lost_key_replaced'").fetchall()
    assert json.loads(event[0]) == {"github_app": False, "directory_sources": ["entra"]}


def test_two_directory_sources_sealed_with_a_lost_key_do_not_deadlock(api, gh):
    lose_key(api, "google", "entra")
    refused = directory_put(api)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "github_key_missing", refused.text
    assert directory_put(api, replace_lost_key=True).status_code == 200, "the confirmed save goes through"
    state = api.app_state
    with state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT source FROM directory_credentials")] == ["entra"]
        assert state.directory.credentials(c, "entra")["client_secret"] == "entra-new"
    # With the key present the flag drops nothing.
    assert directory_put(api, replace_lost_key=True).status_code == 200
    with state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='github.lost_key_replaced'").fetchone()[0] == 1


def test_a_proposal_cannot_carry_the_lost_key_replacement():
    """PUT /api/v2/directory is a route BotOps may propose; replacing the lost key is a person's own confirm, not a card's."""
    from types import SimpleNamespace

    from backend import assistant

    settings = SimpleNamespace(assistant_bot="assistant")
    body = {"source": "entra", "replace_lost_key": True}
    with pytest.raises(Problem, match="person directly"):
        assistant.create_proposal(None, settings, None, None, "botops", "Save Entra", "PUT", "/api/v2/directory", body)


def enable_kms(api):
    """TICO_CREDENTIAL_KMS_KEY set after the install already sealed secrets with github-app.key."""
    from backend.credentials import CredentialCipher
    from backend.tests.helpers.credentials import FakeKMS

    state = api.app_state
    state.store.settings.credential_kms_key = "test-key"
    state.vault.cipher = CredentialCipher("test-key", FakeKMS())


def test_secrets_sealed_before_kms_was_set_open_with_github_app_key_and_are_resealed_once(api, gh, caplog):
    connect(api)
    state = api.app_state
    with state.store.transaction() as c:
        state.directory.put_credentials(c, "human:ana", "entra", {"client_secret": "entra-synthetic"}, "app c in t")
    legacy = state.github_app.key_file().read_bytes()
    with state.store.read() as c:
        before = {
            "app": bytes(state.github_app.row(c)["ciphertext"]),
            "entra": bytes(c.execute("SELECT ciphertext FROM directory_credentials").fetchone()[0]),
        }
    from backend import health, slack_app

    slack_app.save(
        state.store, state.store.settings, "human:ana", "xoxb-synthetic-0000000000", "xapp-synthetic-0000000000"
    )
    enable_kms(api)
    with state.store.read() as c:
        state.vault.cipher.key(c)  # the vault's KMS data key, as the first save under KMS makes it
        # Nothing is lost (github-app.key and slack.key still open them), but Health says which files are still needed.
        check = health._sealed_secrets(c, state.store.settings, state.github_app)
        assert check["status"] == "info", check
        assert (
            "2 secrets still need github-app.key" in check["summary"] and "1 still needs slack.key" in check["summary"]
        )
        assert "keep" in check["summary"].lower()
        assert bytes(state.github_app.row(c)["ciphertext"]) == before["app"]  # Health only looks
    caplog.set_level("INFO")
    assert state.github_app.webhook_secret() == "whs"
    with state.store.read() as c:
        assert state.github_app.private_key(c, state.github_app.row(c)) == PEM
        assert state.directory.credentials(c, "entra")["client_secret"] == "entra-synthetic"
        app_row, source = state.github_app.row(c), c.execute("SELECT * FROM directory_credentials").fetchone()
        vault_key = state.vault.cipher.key(c)
    # Re-sealed with the vault key, once: the legacy key no longer opens them, the vault key does.
    assert bytes(app_row["ciphertext"]) != before["app"] and bytes(source["ciphertext"]) != before["entra"]
    AESGCM(vault_key).decrypt(app_row["nonce"], app_row["ciphertext"], b"tico-github-app:v1")
    with pytest.raises(Exception):
        AESGCM(legacy).decrypt(app_row["nonce"], app_row["ciphertext"], b"tico-github-app:v1")
    resealed = [r.getMessage() for r in caplog.records if "re-sealed" in r.getMessage().lower()]
    assert len(resealed) == 2 and not any("whs" in m or "entra-synthetic" in m or "BEGIN" in m for m in resealed)
    caplog.clear()
    with state.store.read() as c:
        assert state.github_app.private_key(c, state.github_app.row(c)) == PEM
    assert not [r for r in caplog.records if "re-sealed" in r.getMessage().lower()]
    assert state.github_app.key_file().read_bytes() == legacy  # left as it was, for an older backup's rows
    with state.store.read() as c:
        check = health._sealed_secrets(c, state.store.settings, state.github_app)
    assert (
        check["status"] == "info"
        and "github-app.key" not in check["summary"]
        and "1 secret still needs slack.key" in check["summary"]
    )
    assert slack_app.load(state.store, state.store.settings, state.vault)["bot_token"] == "xoxb-synthetic-0000000000"
    with state.store.read() as c:
        assert health._sealed_secrets(c, state.store.settings, state.github_app) is None  # every one re-sealed: clear


def test_without_github_app_key_a_pre_kms_secret_still_fails_and_no_key_file_is_made(api, gh):
    connect(api)
    state = api.app_state
    state.github_app.key_file().unlink()
    enable_kms(api)
    with state.store.read() as c:
        with pytest.raises(Problem, match="could not be decrypted"):
            state.github_app.private_key(c, state.github_app.row(c))
    assert state.github_app.webhook_secret() == ""
    assert not state.github_app.key_file().exists()


def _built_by_botops(api, gh):
    """BotOps builds newbie in its workspace on "mac" (r1) and asks for its empty GitHub repository."""
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "contents": "write", "metadata": "read"}
    botops_turn(api)
    _box(api)
    made = api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test"))
    assert made.status_code == 200, made.text
    with api.app_state.store.read() as c:
        assert c.execute("SELECT value FROM registry_metadata WHERE key='bot-built-on:newbie'").fetchone()


def _go_live(api, **body):
    return api.post(
        "/api/v2/bots/newbie/go-live",
        json={"setup": False, **body},
        headers={**auth(), "Idempotency-Key": str(uuid.uuid4())},
    )


def _placed(api):
    with api.app_state.store.read() as c:
        row = c.execute("SELECT runner_id FROM assignments WHERE bot='newbie'").fetchone()
        return row and row[0]


def test_a_new_bot_goes_live_first_on_the_computer_that_built_it(api, gh):
    """Only the building computer can publish a new bot's history, so another computer is refused while GitHub's
    repository is empty, and a go-live without a computer goes there and waits for its report."""
    _built_by_botops(api, gh)
    refused = _go_live(api, computer="box")
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "history_elsewhere"
    assert "built on mac" in error["detail"] and 'tico bot go-live newbie --computer "mac"' in error["detail"]
    assert not _placed(api)
    waiting = _go_live(api)
    assert waiting.status_code == 202 and waiting.json()["computer"] == "mac", waiting.text
    assert _placed(api) == "r1"
    with api.app_state.store.read() as c:
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key='bot-built-on:newbie'").fetchone()


def test_a_new_bot_whose_history_is_on_github_goes_anywhere(api, gh):
    _built_by_botops(api, gh)
    gh.history.add("Acme/emp-newbie")
    waiting = _go_live(api, computer="box")
    assert waiting.status_code == 202 and waiting.json()["computer"] == "box", waiting.text
    assert _placed(api) == "r2"


def _built_on(api, bot="newbie"):
    with api.app_state.store.read() as c:
        row = c.execute("SELECT value FROM registry_metadata WHERE key=?", ("bot-built-on:" + bot,)).fetchone()
        return row and json.loads(row[0])["runner_id"]


def test_only_botops_records_where_a_bot_was_built_and_never_over_another_record(api, gh):
    """Another bot allowed to create repositories cannot steer a new bot's first placement, a failed create
    records nothing, and a second call never moves the first record."""
    from backend.repositories import save_metadata

    connect(api, administration="true")
    gh.permissions = {"administration": "write", "contents": "write", "metadata": "read"}
    runner_token(api, "cmo")
    with api.app_state.store.transaction() as c:
        save_metadata(c, "repository-creation-grants", {"cmo": True})
        c.execute(
            "INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('m-cmo','keeper','bot:cmo','request','hi',?)",
            (H.now(),),
        )
        enqueue_stored(c, 'm-cmo')
        job = c.execute("SELECT id FROM jobs WHERE bot_id='cmo'").fetchone()["id"]
        c.execute(
            "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
            "VALUES('a1',?,'cmo','r1',1,'cmo-turn','running',?,?)",
            (job, H.shift(H.now(), hours=1), H.now()),
        )
    made = api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("bot-test"))
    assert made.status_code == 200, made.text
    assert _built_on(api) is None  # cmo's call is not a build
    botops_turn(api)
    _box(api)
    gh.generate_status = 500  # (a 422 is "already exists": cmo's repository, this company's own, is the answer)
    failed = api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test"))
    assert failed.status_code >= 400 and _built_on(api) is None  # GitHub refused: nothing recorded
    gh.generate_status = 201
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES('bot-built-on:newbie',?)", (json.dumps({"runner_id": "r2"}),))
    assert (
        api.post(
            "/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test")
        ).status_code
        == 200
    )
    assert _built_on(api) == "r2"  # the first record stands


def test_a_refusal_says_when_github_could_not_be_checked(api, gh, monkeypatch):
    _built_by_botops(api, gh)
    monkeypatch.setattr(api.app_state.github_app, "repository_history", lambda name: None)
    refused = _go_live(api, computer="box")
    assert refused.status_code == 409 and "Tico could not check GitHub's copy" in refused.json()["error"]["detail"]


def test_a_refused_actions_ask_is_remembered_until_the_installation_webhook_says_it_changed(api, gh):
    import hashlib, hmac
    from backend import github as GH

    connect(api)
    runner_token(api, "cpo")
    # The webhook also queues a repository sync that mints its own token, so read only the turn's asks.
    turns = lambda: [
        call[2]["permissions"] for call in gh.of("/access_tokens") if call[2].get("repositories") == ["emp-cpo"]
    ]
    assert turn_token(api).status_code == 200
    before = len(turns())
    api.app_state.github_app.cache.clear()  # past the token cache: the next turn mints again
    assert turn_token(api).status_code == 200
    asked = turns()[before:]
    assert len(asked) == 1 and "actions" not in asked[0]  # no second refused ask while it is remembered
    gh.permissions = {"actions": "read"}  # the owner accepts; GitHub says so by webhook
    body = json.dumps({"action": "new_permissions_accepted", "installation": {"id": 77}}).encode()
    sig = "sha256=" + hmac.new(b"whs", body, hashlib.sha256).hexdigest()
    r = api.post(
        GH.PATH,
        content=body,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "installation", "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert turn_token(api).status_code == 200
    assert turns()[-1]["actions"] == "read"


def personal(gh, login="ana-dev"):
    """The signed-in person's personal account: the one GitHub makes the App under, with the installation there."""
    gh.accounts[login] = "User"
    gh.conversion_owner = {"login": login, "type": "User", "id": gh.user_id}
    gh.installations = [{"id": 77, "account": {"login": login}}]
    gh.installation_account_id = gh.user_id


def test_the_setup_link_goes_to_the_settings_of_the_kind_of_account_the_name_is(api, gh):
    personal(gh)
    user = manifest(api, org="ana-dev")
    assert user["action"].startswith("https://github.com/settings/apps/new?state=")
    # A personal account always asks for Administration, so one acceptance covers repository creation once that lands.
    assert user["manifest"]["default_permissions"]["administration"] == "write"
    left_out = manifest(api, org="ana-dev", administration="false")
    assert left_out["manifest"]["default_permissions"]["administration"] == "write"
    org = manifest(api)
    assert org["action"].startswith("https://github.com/organizations/Acme/settings/apps/new?state=")
    with api.app_state.store.read() as c:
        kinds = {r["org"]: r["account_type"] for r in c.execute("SELECT org,account_type FROM github_app_states")}
    assert kinds == {"ana-dev": "User", "Acme": "Organization"}


def test_an_unknown_name_or_a_github_that_cannot_say_is_a_clear_problem_not_a_guess(api, gh):
    def refused():
        return api.get("/api/v2/github/app/manifest", params={"org": "nobody-here"}, headers=auth())

    missing = refused()
    assert missing.status_code == 404
    assert missing.json()["error"]["detail"] == "No GitHub account or organization named nobody-here"
    gh.account_status = 503
    failing = refused()
    assert failing.status_code == 502 and failing.json()["error"]["code"] == "github_account"
    gh.account_status = (403, {"x-ratelimit-remaining": "0"})
    assert refused().status_code == 502
    gh.account_status, gh.account_down = None, True
    assert refused().status_code == 502
    with api.app_state.store.read() as c:
        assert c.execute("SELECT count(*) FROM github_app_states").fetchone()[0] == 0


def test_an_account_github_says_is_neither_a_user_nor_an_organization_is_a_clear_problem_with_no_manifest(api, gh):
    # GitHub answers 200 but with a type Tico does not connect (a bot account): no state row, no setup link, no manifest.
    gh.accounts["some-bot"] = "Bot"
    r = api.get("/api/v2/github/app/manifest", params={"org": "some-bot"}, headers=auth())
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "github_account"
    assert "not a user or an organization" in r.json()["error"]["detail"]
    assert "action" not in r.json() and "manifest" not in r.json()
    with api.app_state.store.read() as c:
        assert c.execute("SELECT count(*) FROM github_app_states").fetchone()[0] == 0


def test_a_personal_app_is_saved_under_the_login_github_made_it_for_not_the_name_typed(api, gh):
    personal(gh, "ana-dev")
    gh.accounts["typed-name"] = "User"
    state = manifest(api, org="typed-name")["state"]
    r = api.get("/api/v2/github/app/callback", params={"code": "the-code", "state": state}, headers=auth())
    assert r.status_code == 302, r.text
    status = api.get("/api/v2/github/app", headers=auth()).json()
    assert status["org"] == "ana-dev" and status["account_type"] == "User"
    assert status["settings_url"] == "https://github.com/settings/apps/acme-tico"
    assert status["permissions_url"] == "https://github.com/settings/apps/acme-tico/permissions"
    assert status["uninstall_url"] == "https://github.com/settings/installations"
    assert status["install_url"] == "/api/v2/github/app/install"
    with api.app_state.store.read() as c:
        assert tuple(c.execute("SELECT org,account_type FROM github_app").fetchone()) == ("ana-dev", "User")
    # The installation is found by that login, so a run token can be minted for the account's repositories.
    assert api.app_state.github_app.installation(refresh=True) == 77


def authorize(api):
    """The owner authorizes Tico for the personal account: the authorize link's state, then GitHub's return with a code."""
    link = api.get("/api/v2/github/app/authorize-link", headers=auth())
    assert link.status_code == 200, link.text
    state = parse_qs(urlparse(link.json()["url"]).query)["state"][0]
    back = api.get("/api/v2/github/app/authorized", params={"code": "the-code", "state": state}, headers=auth())
    assert back.status_code == 302, back.text
    return back


def expire(api):
    """Make the stored user access token past its end, so the next use refreshes it."""
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE github_user_token SET access_expires_at=?", ("2000-01-01T00:00:00.000Z",))


def test_a_personal_manifest_asks_for_the_authorization_callback_and_an_organization_does_not(api, gh):
    personal(gh, "ana-dev")
    body = manifest(api, org="ana-dev")["manifest"]
    assert body["callback_urls"] == [PUBLIC + "/api/v2/github/app/authorized"]
    assert body["request_oauth_on_install"] is True and body["default_permissions"]["administration"] == "write"
    org = manifest(api)["manifest"]
    assert "callback_urls" not in org and "request_oauth_on_install" not in org


def test_the_owner_authorizes_tico_for_the_personal_account_once_and_only_that_account(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    assert api.get("/api/v2/github/app", headers=auth()).json()["authorized"] is False
    link = api.get("/api/v2/github/app/authorize-link", headers=auth())
    assert link.status_code == 200, link.text
    url = urlparse(link.json()["url"])
    query = parse_qs(url.query)
    assert url.netloc == "github.com" and query["client_id"] == ["Iv1.abc"]
    assert query["redirect_uri"] == [PUBLIC + "/api/v2/github/app/authorized"]
    assert api.get("/api/v2/github/app/authorize-link", headers=auth("person-test")).status_code == 403
    # Another login's sign-in is refused, nothing is kept, and GitHub's token for it is revoked.
    gh.token_login = "someone-else"
    other = api.get(
        "/api/v2/github/app/authorized", params={"code": "the-code", "state": query["state"][0]}, headers=auth()
    )
    assert other.status_code == 403 and other.json()["error"]["code"] == "github_user_login", other.text
    assert gh.revoked == {"gho_1"}
    gh.token_login = "ana-dev"
    with api.app_state.store.read() as c:
        installs = c.execute("SELECT COUNT(*) FROM events WHERE action='github.app_installed'").fetchone()[0]
    assert authorize(api).headers["location"] == "/#/tools/github?github=authorized"
    with api.app_state.store.read() as c:  # a Tools sign-in is not an install: the install step does not run on it
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='github.app_installed'").fetchone()[0] == installs
    status = api.get("/api/v2/github/app", headers=auth()).json()
    # The manifest named the callback, so the card has no callback hint to show.
    assert status["authorized"] is True and status["callback_url"] == ""
    assert "gho_" not in json.dumps(status) and "ghr_" not in json.dumps(status)
    with api.app_state.store.read() as c:
        sealed = bytes(c.execute("SELECT ciphertext FROM github_user_token").fetchone()[0])
    assert b"gho_" not in sealed and b"ghr_" not in sealed, "the token is sealed, never in the clear"
    # A state is single use: the same return again is refused.
    again = api.get(
        "/api/v2/github/app/authorized", params={"code": "the-code", "state": query["state"][0]}, headers=auth()
    )
    assert again.status_code == 400 and again.json()["error"]["code"] == "github_state"


def test_an_empty_personal_repository_is_made_with_the_owners_token_and_a_template_one_with_the_app(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    authorize(api)
    gh.permissions = {"administration": "write", "contents": "write", "metadata": "read"}
    botops_turn(api)
    empty = api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test"))
    assert empty.status_code == 200, empty.text
    assert empty.json()["repository"] == "ana-dev/bot-newbie" and empty.json()["empty"] is True
    posts = [c for c in gh.calls if c[0] == "POST" and c[1] == "/user/repos"]
    assert posts and posts[-1][3] == "Bearer gho_1" and posts[-1][2]["auto_init"] is False
    assert not [c for c in gh.calls if c[0] == "POST" and c[1].startswith("/orgs/")]
    templated = api.post("/api/v2/github/repos", json={"slug": "cpo"}, headers=auth("botops-test"))
    assert templated.status_code == 200, templated.text
    generated = [c for c in gh.calls if c[1].endswith("/generate")]
    assert generated and generated[-1][3].startswith("Bearer ghs_"), (
        "a template repository stays with the installation token"
    )


def test_an_expired_user_token_is_refreshed_once_and_its_rotated_refresh_token_is_kept(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    authorize(api)
    service = api.app_state.github_app
    expire(api)
    assert service.user_token() == "gho_2" and gh.refreshes == 1
    assert service.user_token() == "gho_2" and gh.refreshes == 1, "a fresh token is served without a refresh"
    with api.app_state.store.read() as c:
        assert service._open_user(c, service._user_token_row(c)) == {"access_token": "gho_2", "refresh_token": "ghr_2"}


def test_a_refused_refresh_forgets_the_authorization_and_the_next_authorization_lets_botops_create_it(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    authorize(api)
    gh.permissions = {"administration": "write", "contents": "write", "metadata": "read"}
    gh.missing = {"bot-newbie"}  # not made yet: the bot waits
    expire(api)
    gh.refuse_refresh = True
    botops_turn(api)
    created = api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test"))
    error = created.json()["error"]
    assert created.status_code == 409 and error["code"] == "github_permission_missing", created.text
    assert error["detail"].startswith("A person must act: Tico is not authorized to create repositories in ana-dev yet")
    assert api.get("/api/v2/github/app", headers=auth()).json()["authorized"] is False
    with api.app_state.store.read() as c:
        assert c.execute("SELECT count(*) FROM github_user_token").fetchone()[0] == 0
    gh.refuse_refresh = False
    authorize(api)  # the owner authorizes again: BotOps's next try makes the empty repository in the account
    again = api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test"))
    assert again.status_code == 200 and "ana-dev/bot-newbie" in gh.created_repositories, again.text


def test_a_personal_product_repository_needs_the_authorization_and_is_made_with_the_owners_token(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    gh.permissions = {"administration": "write", "metadata": "read"}

    def preview():
        return api.get("/api/v2/github/product-repos/preview", params={"name": "tico-recorder"}, headers=auth()).json()

    assert preview()["capability"] == "missing"
    body = {"org": "ana-dev", "name": "tico-recorder", "visibility": "private", "auto_init": False, "confirmed": True}
    refused = product_repo_request(api, body)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "github_permission_missing", refused.text
    authorize(api)
    assert preview()["capability"] == "available"
    made = product_repo_request(api, body, key="product-personal-1")
    assert made.status_code == 200, made.text
    assert made.json()["repository"] == "ana-dev/tico-recorder"
    posts = [c for c in gh.calls if c[0] == "POST" and c[1] == "/user/repos" and c[2]["name"] == "tico-recorder"]
    assert posts and posts[-1][3] == "Bearer gho_1"
    assert not [c for c in gh.calls if c[0] == "POST" and c[1].startswith("/orgs/ana-dev")]


def test_a_personal_account_deletes_its_repository_with_the_app_token(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    gh.permissions = {"administration": "write", "metadata": "read"}
    deleted = api.delete("/api/v2/github/repos/ana-dev/bot-old", headers=auth())
    assert deleted.status_code == 200 and deleted.json() == {"repository": "ana-dev/bot-old", "deleted": True}, (
        deleted.text
    )
    calls = [c for c in gh.calls if c[0] == "DELETE"]
    assert calls and calls[-1][3].startswith("Bearer ghs_"), "deleting uses the installation token, never the user's"


def test_disconnect_revokes_the_owners_user_token_on_github_and_forgets_it(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    authorize(api)
    assert api.post("/api/v2/github/app/disconnect", headers=auth()).status_code == 200
    revoked = [c for c in gh.calls if c[0] == "DELETE" and c[1] == "/applications/Iv1.abc/grant"]
    assert revoked and revoked[0][2] == {"access_token": "gho_1"} and revoked[0][3].startswith("Basic ")
    assert gh.revoked == {"gho_1"}
    with api.app_state.store.read() as c:
        assert c.execute("SELECT count(*) FROM github_user_token").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM github_user_states").fetchone()[0] == 0


def test_health_warns_a_personal_account_until_the_owner_authorizes_it(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    gh.permissions = {"administration": "write", "metadata": "read"}

    def alerts():
        return {x["id"]: x for x in api.get("/api/v2/health", headers=auth()).json()["checks"]}

    warn = alerts()["github_repo_create"]
    assert warn["status"] == "warn"
    assert warn["summary"] == "Tico can't create bot repositories on ana-dev until you authorize it."
    assert warn["fixes"][0]["href"] == "#/tools/github"
    authorize(api)
    assert "github_repo_create" not in alerts()


def link_state(api, actor="owner-test"):
    """A fresh single-use state from the Authorize link, as that person's session gets it."""
    link = api.get("/api/v2/github/app/authorize-link", headers=auth(actor))
    assert link.status_code == 200, link.text
    return parse_qs(urlparse(link.json()["url"]).query)["state"][0]


def test_the_authorization_return_needs_a_state_its_own_actor_made_recently(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    stateless = api.get("/api/v2/github/app/authorized", params={"code": "the-code"}, headers=auth())
    assert stateless.status_code == 400 and stateless.json()["error"]["code"] == "github_state"
    state = link_state(api)
    someone = api.get(
        "/api/v2/github/app/authorized", params={"code": "the-code", "state": state}, headers=auth("other-test")
    )
    assert someone.status_code == 400 and someone.json()["error"]["code"] == "github_state"
    state = link_state(api)
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE github_user_states SET created_at=0 WHERE nonce=?", (state,))
    late = api.get("/api/v2/github/app/authorized", params={"code": "the-code", "state": state}, headers=auth())
    assert late.status_code == 400 and late.json()["error"]["code"] == "github_state"
    assert gh.issued == 0, "no code is exchanged for a return without a valid state"
    assert api.get("/api/v2/github/app", headers=auth()).json()["authorized"] is False


def test_a_sign_in_as_the_same_login_with_another_account_id_is_refused_and_revoked(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    gh.user_id = 9002  # the same login, now another numeric account
    back = api.get(
        "/api/v2/github/app/authorized", params={"code": "the-code", "state": link_state(api)}, headers=auth()
    )
    assert back.status_code == 403 and back.json()["error"]["code"] == "github_user_login", back.text
    assert gh.revoked == {"gho_1"}
    assert api.get("/api/v2/github/app", headers=auth()).json()["authorized"] is False
    with api.app_state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM github_user_token").fetchone()[0] == 0


def test_tokens_never_reach_events_logs_or_the_status(api, gh, caplog):
    caplog.set_level("DEBUG")
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    authorize(api)
    service = api.app_state.github_app
    expire(api)
    assert service.user_token() == "gho_2"
    seen = api.get("/api/v2/github/app", headers=auth()).text
    expire(api)
    gh.refuse_refresh = True
    with pytest.raises(Problem) as lost:
        service.user_token()
    assert lost.value.code == "github_user_token"
    with api.app_state.store.read() as c:
        recorded = " ".join(str(part) for row in c.execute("SELECT action, target, detail FROM events") for part in row)
    assert "github.user_authorization_lost" in recorded and "refresh_refused" in recorded
    for token in ("gho_", "ghr_"):
        assert token not in seen + recorded + caplog.text


def test_a_refresh_github_cannot_answer_is_retryable_and_forgets_nothing(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    authorize(api)
    gh.permissions = {"administration": "write", "contents": "write", "metadata": "read"}
    expire(api)
    gh.refresh_status = 503
    botops_turn(api)
    with api.app_state.store.read() as c:
        tasks = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

    def create():
        return api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test"))

    down = create()
    assert down.status_code == 502 and down.json()["error"]["code"] == "github_unreachable", down.text
    assert api.get("/api/v2/github/app", headers=auth()).json()["authorized"] is True, "an outage forgets nothing"
    with api.app_state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == tasks, "no task for an outage"
        assert not c.execute("SELECT 1 FROM events WHERE action='github.user_authorization_lost'").fetchone()
    gh.refresh_status = None
    made = create()
    assert made.status_code == 200 and "ana-dev/bot-newbie" in gh.created_repositories, made.text


def test_a_new_app_saved_over_an_old_one_starts_with_no_owner_authorization(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    authorize(api)
    newer = {
        "id": 4243,
        "slug": "acme-tico-two",
        "client_id": "Iv1.new",
        "client_secret": "cs2",
        "webhook_secret": "whs2",
        "pem": PEM,
        "html_url": "https://github.com/apps/acme-tico-two",
        "owner": {"login": "ana-dev", "type": "User", "id": 9001},
    }
    with api.app_state.store.transaction() as c:
        api.app_state.github_app.save(c, "human:ana", "ana-dev", True, newer, "User")
    assert api.get("/api/v2/github/app", headers=auth()).json()["authorized"] is False
    with api.app_state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM github_user_token").fetchone()[0] == 0


def test_an_install_return_runs_the_install_step_and_a_decline_goes_back_to_tools(api, gh):
    personal(gh, "ana-dev")
    manifest_state = manifest(api, org="ana-dev")["state"]
    setup = api.get("/api/v2/github/app/callback", params={"code": "the-code", "state": manifest_state}, headers=auth())
    assert setup.status_code == 302, setup.text
    install_state = parse_qs(urlparse(setup.headers["location"]).query)["state"][0]
    with api.app_state.store.read() as c:
        installs = c.execute("SELECT COUNT(*) FROM events WHERE action='github.app_installed'").fetchone()[0]
    back = api.get(
        "/api/v2/github/app/authorized",
        params={"code": "the-code", "state": install_state, "installation_id": "77", "setup_action": "install"},
        headers=auth(),
    )
    assert back.status_code == 302 and back.headers["location"] == "/#/tools/github?github=authorized", back.text
    with api.app_state.store.read() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM events WHERE action='github.app_installed'").fetchone()[0] == installs + 1
        )
    assert api.get("/api/v2/github/app", headers=auth()).json()["authorized"] is True
    wrong = api.get(
        "/api/v2/github/app/authorized",
        params={"code": "the-code", "state": link_state(api), "installation_id": "99"},
        headers=auth(),
    )
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "github_installation", wrong.text
    declined = api.get(
        "/api/v2/github/app/authorized", params={"error": "access_denied", "state": link_state(api)}, headers=auth()
    )
    assert declined.status_code == 302 and declined.headers["location"] == "/#/tools/github?github=declined"


def test_the_status_install_link_mints_a_state_its_return_takes_once(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    status = api.get("/api/v2/github/app", headers=auth()).json()
    assert status["install_url"] == "/api/v2/github/app/install"
    link = api.get(status["install_url"], headers=auth())
    assert link.status_code == 302, link.text
    assert link.headers["location"].startswith("https://github.com/apps/acme-tico/installations/new?state=")
    state = parse_qs(urlparse(link.headers["location"]).query)["state"][0]
    params = {"state": state, "installation_id": "77", "setup_action": "install"}
    back = api.get("/api/v2/github/app/authorized", params=params, headers=auth())
    assert back.status_code == 302 and back.headers["location"] == "/#/tools/github?github=installed", back.text
    assert api.get("/api/v2/github/app", headers=auth()).json()["installed"] is True
    again = api.get("/api/v2/github/app/authorized", params=params, headers=auth())
    assert again.status_code == 400 and again.json()["error"]["code"] == "github_state", again.text


def test_an_install_return_with_no_state_is_taken_only_for_an_installation_on_the_owners_account(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    params = {"installation_id": "77", "setup_action": "install"}
    gh.installation_account_id = 1234  # the App's installation is on another account
    refused = api.get("/api/v2/github/app/authorized", params=params, headers=auth())
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "github_installation", refused.text
    gh.installation_account_id = gh.user_id
    back = api.get("/api/v2/github/app/authorized", params=params, headers=auth())
    assert back.status_code == 302 and back.headers["location"] == "/#/tools/github?github=installed", back.text
    assert api.get("/api/v2/github/app", headers=auth()).json()["installed"] is True
    # A code that comes with a state Tico did not make stays strict, and so does a code with no installation id.
    forged = api.get(
        "/api/v2/github/app/authorized", params={**params, "code": "the-code", "state": "not-ours"}, headers=auth()
    )
    assert forged.status_code == 400 and forged.json()["error"]["code"] == "github_state", forged.text
    bare = api.get("/api/v2/github/app/authorized", params={"code": "the-code"}, headers=auth())
    assert bare.status_code == 400 and bare.json()["error"]["code"] == "github_state", bare.text


def test_an_install_return_with_a_code_and_no_state_is_taken_for_the_owners_installation_and_user(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    params = {"installation_id": "77", "setup_action": "install", "code": "the-code"}
    back = api.get("/api/v2/github/app/authorized", params=params, headers=auth())
    assert back.status_code == 302 and back.headers["location"] == "/#/tools/github?github=authorized", back.text
    status = api.get("/api/v2/github/app", headers=auth()).json()
    assert status["installed"] is True and status["authorized"] is True
    assert "gho_" not in json.dumps(status) and "ghr_" not in json.dumps(status)


def test_an_install_return_with_a_code_and_no_state_is_refused_for_another_installation(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    gh.installation_account_id = 1234  # the App's installation is on another account
    refused = api.get(
        "/api/v2/github/app/authorized",
        params={"installation_id": "77", "setup_action": "install", "code": "the-code"},
        headers=auth(),
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "github_installation", refused.text
    assert gh.revoked == set()  # the code was never exchanged
    assert api.get("/api/v2/github/app", headers=auth()).json()["authorized"] is False


def test_an_install_return_with_a_code_and_no_state_is_refused_and_revoked_for_another_user(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    gh.user_id = 5555  # the sign-in is another GitHub user
    refused = api.get(
        "/api/v2/github/app/authorized",
        params={"installation_id": "77", "setup_action": "install", "code": "the-code"},
        headers=auth(),
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "github_user_login", refused.text
    assert gh.revoked == {"gho_1"}  # the token is revoked and was never saved
    assert api.get("/api/v2/github/app", headers=auth()).json()["authorized"] is False


def test_an_install_return_declined_with_no_state_goes_back_to_tools_and_changes_nothing(api, gh):
    personal(gh, "ana-dev")
    connect(api, org="ana-dev")
    declined = api.get(
        "/api/v2/github/app/authorized",
        params={"installation_id": "77", "setup_action": "install", "error": "access_denied"},
        headers=auth(),
    )
    assert declined.status_code == 302 and declined.headers["location"] == "/#/tools/github?github=declined", (
        declined.text
    )
    assert gh.issued == 0  # nothing was exchanged
    assert api.get("/api/v2/github/app", headers=auth()).json()["authorized"] is False


def test_an_install_return_with_no_state_is_refused_for_an_app_that_recorded_no_owner_id(api, gh):
    personal(gh, "ana-dev")
    gh.conversion_owner = {"login": "ana-dev", "type": "User"}  # GitHub did not say whose account the App was made for
    connect(api, org="ana-dev")
    refused = api.get(
        "/api/v2/github/app/authorized", params={"installation_id": "77", "setup_action": "install"}, headers=auth()
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "github_installation", refused.text


def test_links_follow_the_kind_of_account_everywhere_they_are_built():
    organization = {"slug": "acme-tico", "org": "Acme", "account_type": "Organization"}
    personal_row = {"slug": "acme-tico", "org": "ana-dev", "account_type": "User"}
    assert G.links(organization) == {
        "settings_url": "https://github.com/organizations/Acme/settings/apps/acme-tico",
        "permissions_url": "https://github.com/organizations/Acme/settings/apps/acme-tico/permissions",
        "install_url": "/api/v2/github/app/install",
        "uninstall_url": "https://github.com/organizations/Acme/settings/installations",
    }
    assert G.links(personal_row) == {
        "settings_url": "https://github.com/settings/apps/acme-tico",
        "permissions_url": "https://github.com/settings/apps/acme-tico/permissions",
        "install_url": "/api/v2/github/app/install",
        "uninstall_url": "https://github.com/settings/installations",
    }
    assert G.fixes(None, personal_row)["permissions_url"] == G.links(personal_row)["permissions_url"]
    assert G.fixes(None, {**personal_row, "account_type": "Organization"})["permissions_url"] == (
        "https://github.com/organizations/ana-dev/settings/apps/acme-tico/permissions"
    )


def test_an_app_connected_before_account_types_is_an_organization():
    """The migration adds the column with the organization default, to both tables, once; a fresh database gets none
    of its own tables from it (the module code makes them after the migrations)."""
    import importlib.util
    from pathlib import Path

    path = Path(G.__file__).parent / "migrations" / "20261107145006_github_account_type.py"
    spec = importlib.util.spec_from_file_location("github_account_type", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sqlite3.connect(":memory:") as c:
        migration.up(c)
        assert c.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
        c.execute("CREATE TABLE github_app(id TEXT PRIMARY KEY, org TEXT NOT NULL)")
        c.execute("CREATE TABLE github_app_states(nonce TEXT PRIMARY KEY, org TEXT NOT NULL)")
        c.execute("INSERT INTO github_app VALUES('app','Acme')")
        migration.up(c)
        migration.up(c)
        assert c.execute("SELECT org,account_type FROM github_app").fetchone() == ("Acme", "Organization")
        assert {row[1] for row in c.execute("PRAGMA table_info(github_app_states)")} == {"nonce", "org", "account_type"}


CALLBACK = "/api/v2/github/app/callback"
FINISH = "/api/v2/github/app/finish"


def parked_code(api, state):
    with sqlite3.connect(api.app_state.store.settings.db_path) as c:
        return c.execute("SELECT code FROM github_app_states WHERE nonce=?", (state,)).fetchone()


def finish(api, state, actor="owner-test"):
    return api.post(FINISH, json={"state": state}, headers=auth(actor))


def test_a_return_with_no_owner_session_parks_its_code_and_exchanges_nothing(api, gh):
    state = manifest(api)["state"]
    browser = api.get(CALLBACK, params={"code": "the-code", "state": state}, headers={"Accept": "text/html"})
    assert browser.status_code == 302 and browser.headers["location"] == "/#/tools/github?github=finish"
    assert parked_code(api, state) == ("the-code",)
    assert not gh.of("/conversions")


def test_a_return_with_no_owner_session_gets_json_from_a_non_browser(api, gh):
    state = manifest(api)["state"]
    r = api.get(CALLBACK, params={"code": "the-code", "state": state})
    assert r.status_code == 200 and r.json() == {"parked": True, "finish": FINISH}
    assert not gh.of("/conversions")


def test_the_first_parked_code_is_kept_and_a_second_return_does_not_overwrite_it(api, gh):
    state = manifest(api)["state"]
    assert api.get(CALLBACK, params={"code": "first", "state": state}).status_code == 200
    again = api.get(CALLBACK, params={"code": "second", "state": state})
    assert again.status_code == 200 and again.json()["parked"] is True
    assert parked_code(api, state) == ("first",)
    assert not gh.of("/conversions")


def test_the_owner_finishes_a_parked_setup_once_and_gets_the_install_page(api, gh):
    state = manifest(api)["state"]
    api.get(CALLBACK, params={"code": "the-code", "state": state})
    done = finish(api, state)
    assert done.status_code == 200, done.text
    assert done.json()["install_url"].startswith("https://github.com/apps/acme-tico/installations/new?state=")
    assert len(gh.of("/conversions")) == 1
    assert api.get("/api/v2/github/app", headers=auth()).json()["slug"] == "acme-tico"
    again = finish(api, state)
    assert again.status_code == 400 and again.json()["error"]["code"] == "github_state"
    assert len(gh.of("/conversions")) == 1


def test_finish_before_github_has_returned_is_a_409_and_keeps_the_state(api, gh):
    state = manifest(api)["state"]
    early = finish(api, state)
    assert early.status_code == 409 and early.json()["error"]["code"] == "github_not_returned"
    api.get(CALLBACK, params={"code": "the-code", "state": state})
    assert finish(api, state).status_code == 200


def test_finish_by_another_actor_or_after_expiry_is_github_state(api, gh):
    state = manifest(api)["state"]
    api.get(CALLBACK, params={"code": "the-code", "state": state})
    foreign = finish(api, state, actor="other-test")
    assert foreign.status_code == 400 and foreign.json()["error"]["code"] == "github_state"
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE github_app_states SET created_at=0 WHERE nonce=?", (state,))
    late = finish(api, state)
    assert late.status_code == 400 and late.json()["error"]["code"] == "github_state"
    assert not gh.of("/conversions")


def test_a_return_with_no_session_and_an_unknown_state_is_refused_and_parks_nothing(api, gh):
    manifest(api)
    r = api.get(CALLBACK, params={"code": "the-code", "state": "forged"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "github_state"
    with sqlite3.connect(api.app_state.store.settings.db_path) as c:
        assert c.execute("SELECT count(*) FROM github_app_states WHERE code IS NOT NULL").fetchone() == (0,)
    assert not gh.of("/conversions")


def test_the_no_session_exemption_is_the_callback_alone(api, gh):
    state = manifest(api)["state"]
    for method, path in (
        ("get", "/api/v2/github/app"),
        ("get", "/api/v2/github/app/installed"),
        ("get", "/api/v2/github/app/manifest?org=Acme"),
        ("post", FINISH),
    ):
        r = getattr(api, method)(path, json={"state": state}) if method == "post" else api.get(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "identity", path


def test_a_browser_return_with_no_session_goes_to_the_owner_link_and_back_to_the_return(api, gh, monkeypatch):
    state = manifest(api)["state"]
    proxy = SimpleNamespace(name="owner-link")
    monkeypatch.setattr(api.app_state, "auth", SimpleNamespace(proxy=proxy), raising=False)
    r = api.get(CALLBACK, params={"code": "the-code", "state": state}, headers={"Accept": "text/html"})
    assert r.status_code == 302
    target = urlparse(r.headers["location"])
    assert target.path == "/auth/owner-link"
    back = urlparse(parse_qs(target.query)["next"][0])
    assert back.path == CALLBACK and parse_qs(back.query) == {"state": [state]}  # the code stays parked, out of the URL
    assert parked_code(api, state) == ("the-code",)
    # Signed in, the owner comes back with the state alone and the parked code finishes the setup.
    done = api.get(CALLBACK, params={"state": state}, headers=auth(), follow_redirects=False)
    assert done.status_code == 302, done.text
    assert done.headers["location"].startswith("https://github.com/apps/acme-tico/installations/new?state=")
    assert len(gh.of("/conversions")) == 1
