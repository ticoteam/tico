"""The GitHub App: manifest, state nonce, secret storage, installation tokens, runner scope."""
import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend import github_app as G
from backend import hubdb as H
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import encode

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                        serialization.NoEncryption()).decode()
PUBLIC = "https://tico.acme.example"


class FakeGitHub:
    def __init__(self):
        self.calls, self.installations = [], [{"id": 77, "account": {"login": "Acme"}}]
        self.repositories = []
        self.setup_files = {}
        self.ttl = 3600
        self.generate_status = 201
        self.delete_status = 204
        self.refuse = None                       # (status, message) for every token request
        self.permissions = None                  # the installation's live permissions; None leaves them out
        self.missing, self.selection, self.forbidden = set(), "all", False   # repositories GitHub answers 404 for
        self.selected_repositories = set()
        self.list_status = 200
        self.created_repositories = set()
        self.create_response_message = ""
        self.lose_next_product_create_response = False

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body, request.headers.get("authorization", "")))
        path = request.url.path
        if path == "/installation/repositories":
            page = int(request.url.params.get("page", 1))
            seen = [r for r in self.repositories if r["full_name"].split("/")[1] not in self.missing]
            if self.list_status != 200:
                return httpx.Response(self.list_status)
            return httpx.Response(200, json={"total_count": len(seen), "repositories": seen[(page-1)*100:page*100]})
        if "/contents/" in path:
            config = self.setup_files.get(path)
            if config is None:
                return httpx.Response(404)
            import base64
            return httpx.Response(200, json={"content": base64.b64encode(json.dumps(config).encode()).decode()})
        if path.startswith("/app-manifests/"):
            return httpx.Response(201, json={"id": 4242, "slug": "acme-tico", "client_id": "Iv1.abc", "client_secret": "cs",
                                             "webhook_secret": "whs", "pem": PEM, "html_url": "https://github.com/apps/acme-tico"})
        if path == "/app/installations":
            return httpx.Response(200, json=self.installations)
        if path.startswith("/app/installations/") and not path.endswith("/access_tokens"):
            info = {"id": 77, "repository_selection": self.selection}
            if self.permissions is not None:
                info["permissions"] = self.permissions
            return httpx.Response(200, json=info)
        if path.startswith("/repos/") and path.count("/") == 3:
            if request.method == "DELETE":
                return httpx.Response(self.delete_status)
            return httpx.Response(404 if path.split("/")[3] in self.missing else 200, json={})
        if path.endswith("/access_tokens"):
            if self.refuse:
                return httpx.Response(self.refuse[0], json={"message": self.refuse[1]})
            if body and any(name in self.missing for name in body.get("repositories", [])):
                return httpx.Response(403 if self.forbidden else 422, json={"message": "Validation Failed"})
            if body and self.selection == "selected" and any(
                    name not in self.selected_repositories for name in body.get("repositories", [])):
                return httpx.Response(422, json={"message": "Repository is not in this selected installation"})
            exp = datetime.now(timezone.utc) + timedelta(seconds=self.ttl)
            return httpx.Response(201, json={"token": "ghs_" + uuid.uuid4().hex, "expires_at": exp.strftime("%Y-%m-%dT%H:%M:%SZ")})
        if path.startswith("/orgs/") and path.endswith("/repos"):
            repository = path.split("/")[2] + "/" + body["name"]
            if self.generate_status >= 300:
                return httpx.Response(self.generate_status, json={"message": self.create_response_message})
            if body.get("description") == "Tico product repository" and repository in self.created_repositories:
                return httpx.Response(422, json={"message": "name already exists"})
            self.created_repositories.add(repository)
            if body.get("description") == "Tico product repository" and self.lose_next_product_create_response:
                self.lose_next_product_create_response = False
                raise httpx.ReadTimeout("synthetic response timeout", request=request)
            return httpx.Response(self.generate_status, json={"full_name": repository,
                                                              "html_url": "https://github.com/" + repository})
        if path.endswith("/generate"):
            return httpx.Response(self.generate_status, json={"full_name": body["owner"] + "/" + body["name"],
                                                            "html_url": "https://github.com/" + body["owner"] + "/" + body["name"]})
        return httpx.Response(404)

    def of(self, suffix):
        return [c for c in self.calls if c[1].endswith(suffix)]


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(G, "TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def api(tmp_path, gh):
    ids = {"owner-test": Identity("human:ana", "owner", "ana@acme.example"),
           "other-test": Identity("human:ben", "owner", "ben@acme.example"),
           "runner-test": Identity("runner:r1", "runner", runner_id="r1"),
           "bot-test": Identity("bot:cmo", "bot", runner_id="r1", attempt_id="a1"),
           "botops-test": Identity("bot:botops", "bot", runner_id="r1", attempt_id="a2"),
           "person-test": Identity("human:riley", "human", "riley@acme.example")}
    app = create_app(Settings(db_path=tmp_path / "hub.db", public_url=PUBLIC, company_name="Acme", github_owner="Acme",
                              test_identities=ids))
    with TestClient(app, follow_redirects=False) as client:
        with app.state.store.transaction() as c:
            bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("cpo", "cmo", "cfo", "botops")}
            bots["newbie"] = {"name": "newbie", "runtime": "fake", "status": "planned"}
            bots["oldie"] = {"name": "oldie", "runtime": "fake", "status": "archived"}
            H.sync_registry(c, bots, {"people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership"}, {"id": "ben", "email": "ben@acme.example", "team": "leadership"}, {"id": "riley", "email": "riley@acme.example", "team": "leadership"}]})
            repos = {"botops": "emp-botops", "newbie": "emp-newbie", "oldie": "emp-oldie", "cpo": "emp-cpo", "cmo": "https://github.com/Acme/emp-cmo.git", "cfo": "elsewhere/emp-cfo"}
            for slug, config in bots.items():
                c.execute("INSERT INTO bot_config(bot,config_json,team,operator,repo) VALUES(?,?,?,?,?)",
                          (slug, encode(config), "t", "ana", repos[slug]))
            c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),))
        client.app_state = app.state
        yield client


def auth(token="owner-test"):
    return {"Authorization": "Bearer " + token}


def product_repo_request(api, body, *, key="product-create-1", actor="owner-test"):
    return api.post("/api/v2/github/product-repos", json=body,
                    headers={**auth(actor), "Idempotency-Key": key})


def manifest(api, **params):
    r = api.get("/api/v2/github/app/manifest", params={"org": "Acme", **params}, headers=auth())
    assert r.status_code == 200, r.text
    return r.json()


def connect(api, **params):
    state = manifest(api, **params)["state"]
    r = api.get("/api/v2/github/app/callback", params={"code": "the-code", "state": state}, headers=auth())
    assert r.status_code == 302, r.text
    return r


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
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)", (encode({"email": "ana@acme.example"}),))
        conversation = rooms.personal_room(c, "human:ana", "botops")
        c.execute("UPDATE messages SET from_actor='human:ana',conversation_id=?,kind='say',body='Delete the QA repository' WHERE id='m-botops'",
                  (conversation["id"],))
        c.execute("INSERT INTO attempt_conversations VALUES('a2',?)", (conversation["id"],))
    delegated = api.delete("/api/v2/github/repos/Acme/bot-qa-delegated", headers={**auth("botops-test"),
                           "X-Tico-On-Behalf-Of": "turn", "Idempotency-Key": str(uuid.uuid4())})
    assert delegated.status_code == 200 and delegated.json()["deleted"] and "needs_confirm" not in delegated.json(), delegated.text
    with api.app.state.store.read() as c:
        event = c.execute("SELECT actor,detail_json FROM events WHERE action='github.repo_deleted' "
                          "AND target='Acme/bot-qa-delegated'").fetchone()
        assert event["actor"] == "human:ana" and '"via": "botops"' in event["detail_json"]
    gh.delete_status = 404
    response = api.delete(path, headers={**auth(), "Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 409 and "did not delete" in response.json()["error"]["detail"]
    gh.permissions = {"administration": "read"}
    response = api.delete(path, headers={**auth(), "Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 403 and "Administration" in response.json()["error"]["detail"]


def test_state_is_single_use_and_bound_to_session(api, gh):
    state = manifest(api)["state"]
    bad = api.get("/api/v2/github/app/callback", params={"code": "c", "state": "forged"}, headers=auth())
    assert bad.status_code == 400
    other = api.get("/api/v2/github/app/callback", params={"code": "c", "state": state}, headers=auth("other-test"))
    assert other.status_code == 400 and not gh.of("/conversions")
    # Presenting it from another session burned it.
    assert api.get("/api/v2/github/app/callback", params={"code": "c", "state": state}, headers=auth()).status_code == 400
    state = manifest(api)["state"]
    ok = api.get("/api/v2/github/app/callback", params={"code": "c", "state": state}, headers=auth())
    assert ok.status_code == 302
    replay = api.get("/api/v2/github/app/callback", params={"code": "c", "state": state}, headers=auth())
    assert replay.status_code == 400


def test_callback_stores_secrets_and_never_exposes_pem(api, gh, caplog):
    caplog.set_level("DEBUG")
    r = connect(api)
    assert r.headers["location"] == "https://github.com/apps/acme-tico/installations/new"
    assert gh.of("/conversions")[0][1] == "/app-manifests/the-code/conversions"
    status = api.get("/api/v2/github/app", headers=auth())
    body = status.text
    assert status.json()["slug"] == "acme-tico" and status.json()["org"] == "Acme" and status.json()["installed"] is False
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
    method, path, body, header = gh.of("/access_tokens")[0]
    assert path == "/app/installations/77/access_tokens"
    assert body == {"repositories": ["emp-cpo"], "permissions": {"contents": "write", "pull_requests": "write",
                                                                   "issues": "write", "metadata": "read"}}
    claims = jwt.decode(header.removeprefix("Bearer "), KEY.public_key(), algorithms=["RS256"], options={"verify_exp": False})
    assert claims["iss"] == "4242" and claims["exp"] - claims["iat"] <= 660 and claims["iat"] <= time.time() - 59
    assert claims["exp"] <= time.time() + 600


def runner_token(api, bot):
    with api.app_state.store.transaction() as c:
        if not c.execute("SELECT 1 FROM runners WHERE id='r1'").fetchone():
            cols = [r[1] for r in c.execute("PRAGMA table_info(runners)")]
            values = {"id": "r1", "operator": "ana", "label": "mac", "token_hash": "x", "created": H.now(), "created_by": "ana",
                      "last_seen": H.now(), "hostname": "h", "version": "1", "state": "active"}
            use = {k: v for k, v in values.items() if k in cols}
            c.execute(f"INSERT INTO runners({','.join(use)}) VALUES({','.join('?' * len(use))})", tuple(use.values()))
        c.execute("INSERT OR REPLACE INTO assignments VALUES(?,?,?,?,?)", (bot, "r1", 1, H.now(), "ana"))


def test_runner_refuses_bot_it_does_not_run_and_repo_outside_config(api, gh):
    connect(api)
    runner_token(api, "cpo")
    r = api.post("/api/v2/github/token", json={"bot": "cmo"}, headers=auth("runner-test"))
    assert r.status_code == 403 and not gh.of("/access_tokens")
    runner_token(api, "cfo")           # runs it, but its repo belongs to another org
    r = api.post("/api/v2/github/token", json={"bot": "cfo"}, headers=auth("runner-test"))
    assert r.status_code == 403 and "connected organization" in r.text and not gh.of("/access_tokens")
    # A bot's own turn token names only itself.
    assert api.post("/api/v2/github/token", json={"bot": "cpo"}, headers=auth("bot-test")).status_code in (403, 409)


def put_extras(api, bot, repos, who="owner-test"):
    return api.put(f"/api/v2/bots/{bot}/github-repos", json={"repositories": repos}, headers=auth(who))


def turn_token(api, bot="cpo"):
    return api.post("/api/v2/github/token", json={"bot": bot}, headers=auth("runner-test"))


def test_extra_repositories_join_the_turn_token_with_the_same_permissions(api, gh):
    connect(api)
    assert api.put('/api/v2/bots/cpo/repositories', json={'mode': 'chosen', 'chosen': []}, headers=auth()).status_code == 200
    runner_token(api, "cpo")
    r = put_extras(api, "cpo", ["shared-docs", "Acme/design-system", "https://github.com/Acme/infra.git", "Acme/emp-cpo", "shared-docs"])
    assert r.status_code == 200, r.text
    assert r.json()["repositories"] == ["Acme/shared-docs", "Acme/design-system", "Acme/infra"]
    data = turn_token(api).json()
    assert data["repository"] == "Acme/emp-cpo"
    assert set(data["repositories"]) == {"Acme/emp-cpo", "Acme/shared-docs", "Acme/design-system", "Acme/infra"}
    _, _, body, _ = gh.of("/access_tokens")[-1]
    assert body["repositories"] == ["design-system", "emp-cpo", "infra", "shared-docs"]
    assert body["permissions"] == {"contents": "write", "pull_requests": "write", "issues": "write", "metadata": "read"}
    assert set(api.get("/api/v2/bots/cpo/github-repos", headers=auth()).json()["repositories"]) == set(data["repositories"]) - {"Acme/emp-cpo"}


def test_missing_own_repository_is_found_by_one_listing_not_a_probe_per_grant(api, gh):
    connect(api)
    assert api.put('/api/v2/bots/cpo/repositories', json={'mode': 'chosen', 'chosen': []}, headers=auth()).status_code == 200
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


def botops_turn(api):
    """An unattended BotOps turn: the runner, its assignment, and a leased attempt for `botops-test`."""
    runner_token(api, "botops")
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO messages(id,from_actor,to_actor,kind,body,created) VALUES('m-botops','keeper','bot:botops','request','hi',?)",
                  (H.now(),))
        job = c.execute("SELECT id FROM jobs WHERE bot='botops'").fetchone()["id"]
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created) "
                  "VALUES('a2',?,'botops','r1',1,'x','running',?,?)", (job, H.shift(H.now(), hours=1), H.now()))


def token_health(api):
    with api.app_state.store.read() as c:
        row = c.execute("SELECT last_error FROM service_health WHERE service='github:token'").fetchone()
        return bool(row and row["last_error"])


def test_product_repository_preview_is_owner_only_and_rejects_paths_before_github(api, gh):
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}
    path = "/api/v2/github/product-repos/preview"
    botops_turn(api)
    before = len(gh.calls)
    body = {"org": "Acme", "name": "tico-recorder", "visibility": "private",
            "auto_init": False, "confirmed": True}
    for actor in ("person-test", "botops-test"):
        assert api.get(path, params={"name": "tico-recorder"}, headers=auth(actor)).status_code == 403
        assert product_repo_request(api, body, actor=actor, key="non-owner-" + actor).status_code == 403
    assert len(gh.calls) == before, "non-Owner credentials cannot preview or create a product repository"
    for name in ("Acme/tico-recorder", "https://github.com/Acme/tico-recorder", "..", "../tico"):
        assert api.get(path, params={"name": name}, headers=auth()).status_code == 422
    assert len(gh.calls) == before, "authorization and exact-name validation happen before GitHub discovery"
    preview = api.get(path, params={"name": "tico-recorder"}, headers=auth())
    assert preview.status_code == 200, preview.text
    assert preview.json() == {"org": "Acme", "name": "tico-recorder", "repository": "Acme/tico-recorder",
                              "visibility": "private", "auto_init": False, "capability": "available",
                              "capability_detail": "The connected installation currently has Administration: write."}
    assert not any(call[0] == "POST" and call[1].endswith("/repos") for call in gh.calls)


def test_owner_product_creation_uses_exact_name_private_empty_and_idempotent_receipt(api, gh, monkeypatch):
    from backend import repositories
    refreshes = []
    monkeypatch.setattr(repositories, "queue_sync", lambda service, refresh=False: refreshes.append(refresh))
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}
    preview = api.get("/api/v2/github/product-repos/preview", params={"name": "tico-recorder"}, headers=auth()).json()
    body = {"org": preview["org"], "name": preview["name"], "visibility": preview["visibility"],
            "auto_init": preview["auto_init"], "confirmed": True}
    created = product_repo_request(api, body, key="product-create-same")
    assert created.status_code == 200, created.text
    assert created.json() == {"repository": "Acme/tico-recorder", "html_url": "https://github.com/Acme/tico-recorder",
                              "visibility": "private", "auto_init": False, "installation_access": "available",
                              "note": "Created as a private empty repository. Tico will refresh its repository inventory; no bot access was granted."}
    creation = next(call for call in gh.calls if call[0] == "POST" and call[1] == "/orgs/Acme/repos")
    assert creation[2] == {"name": "tico-recorder", "private": True, "auto_init": False,
                           "description": "Tico product repository"}
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
        event = c.execute("SELECT action,target,detail_json FROM events WHERE action='github.product_repo_created'").fetchone()
        assert event and event["target"] == "Acme/tico-recorder"
        assert c.execute("SELECT COUNT(*) FROM bot_repo_access").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM idempotency WHERE operation='/api/v2/github/product-repos' AND key='product-create-same'").fetchone()[0] == 1
        operation = c.execute("SELECT state,target_org,target_name FROM github_product_repo_operations "
                              "WHERE operation='/api/v2/github/product-repos' AND key='product-create-same'").fetchone()
        assert tuple(operation) == ("completed", "Acme", "tico-recorder")
    calls_before_durable_replay = len([call for call in gh.calls if call[0] == "POST" and call[1] == "/orgs/Acme/repos"])
    with api.app_state.store.transaction() as c:
        c.execute("DELETE FROM idempotency WHERE actor='human:ana' AND operation=? AND key=?",
                  ("/api/v2/github/product-repos", "product-create-same"))
    durable_replay = product_repo_request(api, body, key="product-create-same")
    assert durable_replay.status_code == 200 and durable_replay.json() == created.json()
    assert len([call for call in gh.calls if call[0] == "POST" and call[1] == "/orgs/Acme/repos"]) == calls_before_durable_replay


def test_owner_mcp_product_repository_tool_previews_confirms_and_replays(api, gh):
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}

    def call(arguments):
        response = api.post("/api/v2/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "hub_repo_product_create", "arguments": arguments},
        }, headers=auth())
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        return result["isError"], result["structuredContent"]

    operation_id = "mcp-product-create-stable"
    error, preview = call({"name": "tico-recorder", "operation_id": operation_id})
    assert not error and preview["confirmation_required"] is True and preview["created"] is False
    assert preview["preview"]["repository"] == "Acme/tico-recorder"
    assert not gh.of("/orgs/Acme/repos"), "the preview call does not create a repository"

    error, refused = call({"name": "tico-recorder", "operation_id": operation_id,
                           "confirm_repository": "Acme/other-product"})
    assert error and refused["error"] == "confirmation_required"
    assert not gh.of("/orgs/Acme/repos"), "a mismatched confirmation does not create a repository"

    arguments = {"name": "tico-recorder", "operation_id": operation_id,
                 "confirm_repository": "Acme/tico-recorder"}
    error, created = call(arguments)
    assert not error and created["repository"] == "Acme/tico-recorder"
    creates = gh.of("/orgs/Acme/repos")
    assert len(creates) == 1
    assert creates[0][2] == {"name": "tico-recorder", "private": True, "auto_init": False,
                             "description": "Tico product repository"}

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
    body = {"org": preview["org"], "name": preview["name"], "visibility": preview["visibility"],
            "auto_init": preview["auto_init"], "confirmed": True}
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
        operation = c.execute("SELECT actor,operation,key,request_hash,target_org,target_name,state,response_json "
                              "FROM github_product_repo_operations WHERE actor='human:ana' AND key=?",
                              ("lost-create-response",)).fetchone()
        assert operation["actor"] == "human:ana"
        assert operation["operation"] == "/api/v2/github/product-repos"
        assert operation["key"] == "lost-create-response"
        assert operation["request_hash"] == G.digest(encode(body))
        assert operation["target_org"] == "Acme" and operation["target_name"] == "tico-recorder"
        assert operation["state"] == "pending" and operation["response_json"] is None


def test_product_create_does_not_expose_github_error_bodies(api, gh):
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}
    gh.refuse = (500, "ghs_SYNTHETIC_DO_NOT_EXPOSE")
    preview = api.get("/api/v2/github/product-repos/preview", params={"name": "tico-recorder"}, headers=auth()).json()
    body = {"org": preview["org"], "name": preview["name"], "visibility": preview["visibility"],
            "auto_init": preview["auto_init"], "confirmed": True}
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
        c.execute("INSERT INTO messages(id,from_actor,to_actor,kind,body,created) VALUES('m-cmo','keeper','bot:cmo','request','Prepare the repository',?)", (H.now(),))
        job = c.execute("SELECT id FROM jobs WHERE bot='cmo'").fetchone()['id']
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created) "
                  "VALUES('a1',?,'cmo','r1',1,'x','running',?,?)", (job, H.shift(H.now(), hours=1), H.now()))
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
        assert json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='cmo'").fetchone()[0]).get('create_repositories') is None
    # A local grant never replaces GitHub's own Administration permission.
    gh.permissions = {'administration': 'read', 'contents': 'write'}
    assert create({'slug': 'newbie', 'empty': True}).status_code == 403
    assert api.put(path, json={'mode': 'own', 'create_repositories': False}, headers=auth()).json()['create_repositories'] is False
    assert create({'slug': 'newbie'}).status_code == 403
    assert api.get('/api/v2/bots/botops/repositories', headers=auth()).json()['create_repositories'] is True
