"""The GitHub App: manifest, state nonce, secret storage, installation tokens, runner scope."""
import json
import sqlite3
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from backend import github_app as G
from backend import hubdb as H
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import Problem, encode

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
        self.clock = time.time
        self.generate_status = 201
        self.delete_status = 204
        self.refuse = None                       # (status, message) for every token request
        self.permissions = None                  # the installation's live permissions; None leaves them out
        self.missing, self.selection, self.forbidden = set(), "all", False   # repositories GitHub answers 404 for
        self.selected_repositories = set()
        self.list_status = 200
        self.created_repositories = set()
        self.create_response_message = ""
        self.history = set()                     # repositories that hold commits; any other answers "empty"
        self.lose_next_product_create_response = False
        self.refuse_actions_write = False        # refuse Actions: write whatever the live permissions say
        self.refuse_workflows_write = False      # refuse Workflows: write whatever the live permissions say
        self.on_token = None                     # called with each token request's body before it is answered

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body, request.headers.get("authorization", "")))
        path = request.url.path
        if path == "/installation/repositories":
            page = int(request.url.params.get("page", 1))
            # An installation on all repositories lists one created through it, as GitHub does.
            listed = {r["full_name"] for r in self.repositories}
            made = [{"full_name": name} for name in sorted(self.created_repositories)
                    if self.selection == "all" and name not in listed]
            seen = [r for r in self.repositories + made if r["full_name"].split("/")[1] not in self.missing]
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
        if path.startswith("/repos/") and path.endswith("/commits"):
            name = path.split("/")[2] + "/" + path.split("/")[3]
            if name in self.history:
                return httpx.Response(200, json=[{"sha": "abc123"}])
            return httpx.Response(409, json={"message": "Git Repository is empty."})
        if path.startswith("/repos/") and path.count("/") == 3:
            if request.method == "DELETE":
                return httpx.Response(self.delete_status)
            return httpx.Response(404 if path.split("/")[3] in self.missing else 200, json={})
        if path.endswith("/access_tokens"):
            if self.on_token:
                self.on_token(body)
            if self.refuse:
                return httpx.Response(self.refuse[0], json={"message": self.refuse[1]})
            if self.refuse_actions_write and body.get("permissions", {}).get("actions") == "write" or (
                    self.refuse_workflows_write and body.get("permissions", {}).get("workflows") == "write") or any(
                    name in body.get("permissions", {}) and (name not in (self.permissions or {}) or
                   body["permissions"][name] == "write" and self.permissions[name] != "write")
                   for name in ("actions", "checks", "statuses", "deployments", "workflows")):
                return httpx.Response(422, json={"message": "The permissions requested are not granted to this installation."})
            if body and any(name in self.missing for name in body.get("repositories", [])):
                return httpx.Response(403 if self.forbidden else 422, json={"message": "Validation Failed"})
            if body and self.selection == "selected" and any(
                    name not in self.selected_repositories for name in body.get("repositories", [])):
                return httpx.Response(422, json={"message": "Repository is not in this selected installation"})
            exp = datetime.fromtimestamp(self.clock(), timezone.utc) + timedelta(seconds=self.ttl)
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
                c.execute("INSERT INTO bot_config(bot,config,team,owner_id,repo) VALUES(?,?,?,?,?)",
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
        c.execute("UPDATE messages SET sender_id='human:ana',conversation_id=?,kind='say',body='Delete the QA repository' WHERE id='m-botops'",
                  (conversation["id"],))
        c.execute("INSERT INTO attempt_conversations VALUES('a2',?)", (conversation["id"],))
    delegated = api.delete("/api/v2/github/repos/Acme/bot-qa-delegated", headers={**auth("botops-test"),
                           "X-Tico-On-Behalf-Of": "turn", "Idempotency-Key": str(uuid.uuid4())})
    assert delegated.status_code == 200 and delegated.json()["deleted"] and "needs_confirm" not in delegated.json(), delegated.text
    with api.app.state.store.read() as c:
        event = c.execute("SELECT actor,detail FROM events WHERE action='github.repo_deleted' "
                          "AND target='Acme/bot-qa-delegated'").fetchone()
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


def test_missing_repo_permission_raises_one_needs_you_item_and_finishes_when_granted(api, gh, monkeypatch):
    """BotOps' create without Administration: a refusal naming a person, one owner task however often it is
    retried, a Health alert, and the repository made (and the task closed) once GitHub reports the permission."""
    from backend import repo_waits
    connect(api, administration="false")
    gh.permissions = {"administration": "read", "contents": "write", "metadata": "read"}
    gh.missing = {"bot-newbie"}
    botops_turn(api)
    create = lambda: api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test"))
    # Nobody on the roster can change the app's permissions yet: no task, only the refusal and Health.
    assert create().json()["error"]["needs_you_task"] is None
    with api.app_state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)", (encode({"email": "ana@acme.example"}),))
    first, again = create(), create()
    for refused in (first, again):
        error = refused.json()["error"]
        assert refused.status_code == 409 and error["code"] == "github_permission_missing", refused.text
        assert error["detail"].startswith("A person must act")
        assert error["create_url"] == "https://github.com/new?owner=Acme&visibility=private&name=bot-newbie"
        assert error["permissions_url"] == "https://github.com/organizations/Acme/settings/apps/acme-tico/permissions"
    assert first.json()["error"]["needs_you_task"] == again.json()["error"]["needs_you_task"]
    with api.app_state.store.read() as c:
        tasks = c.execute("SELECT id,owner_id,status FROM tasks WHERE title LIKE 'Create Acme/bot-newbie%'").fetchall()
        assert [tuple(t)[1:] for t in tasks] == [("human:ana", "open")]
        assert H.bot(c, "newbie")["state"] == "planned"
    needs = api.get("/api/v2/needs-you", headers=auth()).json()
    assert [i["id"] for i in needs["items"] if i["id"] == tasks[0]["id"]] == [tasks[0]["id"]]
    health = {x["id"]: x for x in api.get("/api/v2/health", headers=auth()).json()["checks"]}
    alert = health["github_repo_create"]
    assert alert["label"] == "GitHub can't create bot repositories" and "Acme/bot-newbie" in alert["summary"]
    assert {f["href"] for f in alert["fixes"]} >= {error["create_url"], error["permissions_url"]}
    assert "github_repo_create" not in {x["id"] for x in api.get("/api/v2/health", headers=auth("person-test")).json()["checks"]}

    assert repo_waits.resume(api.app_state.github_app) == {}          # nothing changed on GitHub yet
    assert not gh.created_repositories
    # Another waiting bot whose check fails unexpectedly does not hold this one up.
    with api.app_state.store.transaction() as c:
        every = repo_waits.waits(c)
        repo_waits._save(c, repo_waits.KEY, {**every, "cpo": {"repository": "Acme/bot-cpo", "task_id": None}})
    service, mint = api.app_state.github_app, api.app_state.github_app.mint
    def flaky(repos, *a, **k):
        if repos == ["Acme/bot-cpo"]:
            raise httpx.ConnectError("synthetic")
        return mint(repos, *a, **k)
    monkeypatch.setattr(service, "mint", flaky)
    gh.permissions = {"administration": "write", "contents": "write", "metadata": "read"}
    checked = api.post("/api/v2/github/app/check-permissions", json={}, headers=auth())
    assert checked.status_code == 200 and checked.json()["administration"] is True, checked.text
    assert list(checked.json()["resumed"]) == ["newbie"] and [w["bot"] for w in checked.json()["waiting"]] == ["cpo"]
    assert gh.created_repositories == {"Acme/bot-newbie"}
    with api.app_state.store.read() as c:
        assert c.execute("SELECT status FROM tasks WHERE id=?", (tasks[0]["id"],)).fetchone()[0] == "closed"
    assert "github_repo_create" not in {x["id"] for x in api.get("/api/v2/health", headers=auth()).json()["checks"]}


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
    method, path, body, header = gh.of("/access_tokens")[-1]
    assert path == "/app/installations/77/access_tokens"
    assert body == {"repositories": ["emp-cpo"], "permissions": {"contents": "write", "pull_requests": "write",
                                                                   "issues": "write", "metadata": "read"}}
    claims = jwt.decode(header.removeprefix("Bearer "), KEY.public_key(), algorithms=["RS256"], options={"verify_exp": False})
    assert claims["iss"] == "4242" and claims["exp"] - claims["iat"] <= 660 and claims["iat"] <= time.time() - 59
    assert claims["exp"] <= time.time() + 600


def test_turn_token_reads_workflow_runs_once_the_installation_allows_it(api, gh):
    assert manifest(api)["manifest"]["default_permissions"]["actions"] == "read"
    connect(api)
    runner_token(api, "cpo")
    # An installation that has not accepted Actions yet still gets its token, without it.
    assert turn_token(api).status_code == 200
    assert "actions" not in gh.of("/access_tokens")[-1][2]["permissions"]
    gh.permissions = {"actions": "read"}
    assert api.post("/api/v2/github/app/check-permissions", headers=auth()).status_code == 200
    assert turn_token(api).status_code == 200
    assert gh.of("/access_tokens")[-1][2]["permissions"]["actions"] == "read"



def test_a_private_meetings_turn_gets_a_read_only_token_and_a_team_meetings_a_write_one(api, gh):
    connect(api)
    runner_token(api, "cmo")
    with api.app_state.store.transaction() as c:
        for rid, visibility in (("m-private", "private"), ("m-team", "team")):
            c.execute("INSERT INTO live_meetings(id,owner_actor,title,state,window_ms,created_at,updated_at,started_at,visibility) "
                      "VALUES(?,'human:ana','Plan','live',30000,?,?,?,?)", (rid, H.now(), H.now(), H.now(), visibility))
        conv = H.open_conversation(c, H.KEEPER, ["bot:cmo"], room_key="live-meeting:m-private")
        H.feed(c, "bot:cmo", "A meeting turn", conv, refs={"live_meeting": {"meeting_id": "m-private", "turn_id": "t1"}})
        job = c.execute("SELECT id FROM jobs WHERE bot_id='cmo'").fetchone()["id"]
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
                  "VALUES('a1',?,'cmo','r1',1,'x','running',?,?)", (job, H.shift(H.now(), hours=1), H.now()))
    r = api.post("/api/v2/github/token", json={"bot_id": "cmo"}, headers=auth("bot-test"))
    assert r.status_code == 200, r.text
    assert gh.of("/access_tokens")[-1][2]["permissions"]["contents"] == "read"
    assert {t["access"] for t in r.json()["tokens"]} == {"read"}
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE live_meetings SET visibility='team' WHERE id='m-private'")
    r = api.post("/api/v2/github/token", json={"bot_id": "cmo"}, headers=auth("bot-test"))
    assert r.status_code == 200 and gh.of("/access_tokens")[-1][2]["permissions"]["contents"] == "write"


def test_turn_token_reads_pull_request_checks_and_statuses(api, gh):
    # `gh pr checks` reads check runs and commit statuses; a private repository refuses both without them.
    asked = manifest(api)["manifest"]["default_permissions"]
    assert {"checks", "statuses", "deployments"} <= asked.keys()
    assert asked["pull_requests"] == asked["issues"] == asked["contents"] == "write"     # a bot still opens PRs and issues
    connect(api)
    runner_token(api, "cpo")
    gh.permissions = {"actions": "read", "checks": "read", "statuses": "read", "deployments": "read"}
    assert turn_token(api).status_code == 200
    asked = gh.of("/access_tokens")[-1][2]["permissions"]
    assert asked == {"contents": "write", "pull_requests": "write", "issues": "write", "metadata": "read",
                     "actions": "read", "checks": "read", "statuses": "read", "deployments": "read"}


def test_turn_token_asks_only_for_the_reads_the_installation_accepted(api, gh):
    connect(api)
    runner_token(api, "cpo")
    gh.permissions = {"checks": "read", "statuses": "write"}   # accepted the manifest before Actions was added
    assert turn_token(api).status_code == 200
    asked = gh.of("/access_tokens")[-1][2]["permissions"]
    assert asked["checks"] == asked["statuses"] == "read" and "actions" not in asked
    calls = len(gh.of("/access_tokens"))
    api.app_state.github_app.cache.clear()
    assert turn_token(api).status_code == 200
    assert len(gh.of("/access_tokens")) == calls + 1        # remembered: no refused ask first


def test_turn_token_stops_when_github_refuses_even_the_base(api, gh):
    connect(api)
    runner_token(api, "cpo")
    gh.permissions = {"actions": "read", "checks": "read", "statuses": "read", "pull_requests": "write",
                      "issues": "write", "deployments": "read"}
    gh.refuse = (422, "The permissions requested are not granted to this installation.")
    assert turn_token(api).status_code == 409
    assert len(gh.of("/access_tokens")) == 2

def runner_token(api, bot):
    with api.app_state.store.transaction() as c:
        if not c.execute("SELECT 1 FROM runners WHERE id='r1'").fetchone():
            cols = [r[1] for r in c.execute("PRAGMA table_info(runners)")]
            values = {"id": "r1", "owner_id": "ana", "label": "mac", "token_hash": "x", "created_at": H.now(), "created_by": "ana",
                      "last_seen_at": H.now(), "hostname": "h", "version": "1", "state": "active"}
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


def turn_token(api, bot="cpo", purpose="turn"):
    body = {"bot": bot}
    if purpose != "turn":
        body["purpose"] = purpose
    return api.post("/api/v2/github/token", json=body, headers=auth("runner-test"))


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
    service.mint(None, {"metadata": "read"})          # learns the installation and the app's JWT
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
    threads = [threading.Thread(target=lambda: tokens.append(service.mint(["Acme/emp-cpo"], G.TURN_PERMISSIONS)[0]))
               for _ in range(6)]
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
        c.execute("INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('m-botops','keeper','bot:botops','request','hi',?)",
                  (H.now(),))
        job = c.execute("SELECT id FROM jobs WHERE bot_id='botops'").fetchone()["id"]
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
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
    # Every bot may create product repositories unless switched off (test_bot_product_repos.py): BotOps is, here.
    assert api.put("/api/v2/bots/botops/repositories", json={"mode": "own", "create_product_repositories": False},
                   headers=auth()).status_code == 200
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
        event = c.execute("SELECT action,target,detail FROM events WHERE action='github.product_repo_created'").fetchone()
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
        operation = c.execute("SELECT actor,operation,key,request_hash,target_org,target_name,state,response "
                              "FROM github_product_repo_operations WHERE actor='human:ana' AND key=?",
                              ("lost-create-response",)).fetchone()
        assert operation["actor"] == "human:ana"
        assert operation["operation"] == "/api/v2/github/product-repos"
        assert operation["key"] == "lost-create-response"
        assert operation["request_hash"] == G.digest(encode(body))
        assert operation["target_org"] == "Acme" and operation["target_name"] == "tico-recorder"
        assert operation["state"] == "pending" and operation["response"] is None


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
        c.execute("INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('m-cmo','keeper','bot:cmo','request','Prepare the repository',?)", (H.now(),))
        job = c.execute("SELECT id FROM jobs WHERE bot_id='cmo'").fetchone()['id']
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
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
        assert json.loads(c.execute("SELECT config FROM bot_config WHERE bot='cmo'").fetchone()[0]).get('create_repositories') is None
    # A local grant never replaces GitHub's own Administration permission.
    gh.permissions = {'administration': 'read', 'contents': 'write'}
    missing = create({'slug': 'newbie', 'empty': True})
    assert missing.status_code == 409 and missing.json()['error']['code'] == 'github_permission_missing'
    assert api.put(path, json={'mode': 'own', 'create_repositories': False}, headers=auth()).json()['create_repositories'] is False
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
        audit = json.loads(c.execute("SELECT detail FROM events WHERE action='github.token' AND target='botops' "
                                     "ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()[0])
    assert audit["runs"] == [{"attempt": "a2", "rights": "bot:botops", "own_run": True}]
    assert sorted(api.post("/api/v2/github/token", json={"bot": "botops"},
                           headers=auth("botops-test")).json()["repositories"]) == wide
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
        c.execute("UPDATE messages SET sender_id='human:riley',conversation_id=?,kind='say' WHERE id='m-botops'", (room["id"],))
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
    body = {"source": "entra", "filter": {}, "expected_revision": revision,
            "credentials": {"tenant": "t", "client_id": "c", "client_secret": "entra-new"}, **more}
    return api.put("/api/v2/directory", json=body, headers={**auth(), "Idempotency-Key": str(uuid.uuid4())})


def test_a_lost_github_app_key_is_replaced_only_when_the_owner_confirms(api, gh):
    connect(api)
    lose_key(api, "entra")
    assert api.post("/api/v2/github/app/disconnect", headers=auth()).status_code == 200
    state = api.app_state
    for params in ({}, {"replace_lost_key": "true"}):       # a GET, even a link carrying the flag, never drops anything
        refused = api.get("/api/v2/github/app/manifest", params={"org": "Acme", **params}, headers=auth())
        assert refused.status_code == 409 and "replace-lost-key" in refused.text
        with state.store.read() as c:
            assert c.execute("SELECT count(*) FROM directory_credentials").fetchone()[0] == 1
    assert api.post("/api/v2/github/app/replace-lost-key", headers=auth("person-test")).status_code == 403
    assert api.post("/api/v2/github/app/replace-lost-key", headers=auth()).json() == {"replaced": True}
    connect(api)
    with state.store.read() as c:
        assert c.execute("SELECT count(*) FROM directory_credentials").fetchone()[0] == 0   # to be entered again
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
    from backend.tests.test_credentials import FakeKMS
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
        before = {"app": bytes(state.github_app.row(c)["ciphertext"]),
                  "entra": bytes(c.execute("SELECT ciphertext FROM directory_credentials").fetchone()[0])}
    from backend import health, slack_app
    slack_app.save(state.store, state.store.settings, "human:ana", "xoxb-synthetic-0000000000", "xapp-synthetic-0000000000")
    enable_kms(api)
    with state.store.read() as c:
        state.vault.cipher.key(c)            # the vault's KMS data key, as the first save under KMS makes it
        # Nothing is lost (github-app.key and slack.key still open them), but Health says which files are still needed.
        check = health._sealed_secrets(c, state.store.settings, state.github_app)
        assert check["status"] == "info", check
        assert "2 secrets still need github-app.key" in check["summary"] and "1 still needs slack.key" in check["summary"]
        assert "keep" in check["summary"].lower()
        assert bytes(state.github_app.row(c)["ciphertext"]) == before["app"]       # Health only looks
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
    assert state.github_app.key_file().read_bytes() == legacy     # left as it was, for an older backup's rows
    with state.store.read() as c:
        check = health._sealed_secrets(c, state.store.settings, state.github_app)
    assert check["status"] == "info" and "github-app.key" not in check["summary"] and "1 secret still needs slack.key" in check["summary"]
    assert slack_app.load(state.store, state.store.settings, state.vault)["bot_token"] == "xoxb-synthetic-0000000000"
    with state.store.read() as c:
        assert health._sealed_secrets(c, state.store.settings, state.github_app) is None   # every one re-sealed: clear


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


# ------------------------------------------------------------------ a new bot's first placement follows its build
def _box(api):
    """A second computer, "box", that never held newbie."""
    with api.app_state.store.transaction() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(runners)")]
        values = {"id": "r2", "owner_id": "ana", "label": "box", "token_hash": "y", "created_at": H.now(), "created_by": "ana",
                  "last_seen_at": H.now(), "hostname": "box", "version": "1", "state": "active"}
        use = {k: v for k, v in values.items() if k in cols}
        c.execute(f"INSERT INTO runners({','.join(use)}) VALUES({','.join('?' * len(use))})", tuple(use.values()))


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
    return api.post("/api/v2/bots/newbie/go-live", json={"setup": False, **body},
                    headers={**auth(), "Idempotency-Key": str(uuid.uuid4())})


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
    assert "built on mac" in error["detail"] and 'hub bot go-live newbie --computer "mac"' in error["detail"]
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
        c.execute("INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('m-cmo','keeper','bot:cmo','request','hi',?)", (H.now(),))
        job = c.execute("SELECT id FROM jobs WHERE bot_id='cmo'").fetchone()["id"]
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
                  "VALUES('a1',?,'cmo','r1',1,'cmo-turn','running',?,?)", (job, H.shift(H.now(), hours=1), H.now()))
    made = api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("bot-test"))
    assert made.status_code == 200, made.text
    assert _built_on(api) is None                                  # cmo's call is not a build
    botops_turn(api)
    _box(api)
    gh.generate_status = 422
    failed = api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test"))
    assert failed.status_code >= 400 and _built_on(api) is None     # GitHub refused: nothing recorded
    gh.generate_status = 201
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES('bot-built-on:newbie',?)", (json.dumps({"runner_id": "r2"}),))
    assert api.post("/api/v2/github/repos", json={"slug": "newbie", "empty": True}, headers=auth("botops-test")).status_code == 200
    assert _built_on(api) == "r2"                                  # the first record stands


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
    turns = lambda: [call[2]["permissions"] for call in gh.of("/access_tokens")
                     if call[2].get("repositories") == ["emp-cpo"]]
    assert turn_token(api).status_code == 200
    before = len(turns())
    api.app_state.github_app.cache.clear()             # past the token cache: the next turn mints again
    assert turn_token(api).status_code == 200
    asked = turns()[before:]
    assert len(asked) == 1 and "actions" not in asked[0]  # no second refused ask while it is remembered
    gh.permissions = {"actions": "read"}                 # the owner accepts; GitHub says so by webhook
    body = json.dumps({"action": "new_permissions_accepted", "installation": {"id": 77}}).encode()
    sig = "sha256=" + hmac.new(b"whs", body, hashlib.sha256).hexdigest()
    r = api.post(GH.PATH, content=body, headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "installation",
                                                  "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    assert turn_token(api).status_code == 200
    assert turns()[-1]["actions"] == "read"


def test_the_fake_installation_lists_a_repository_created_through_it_only_when_it_has_every_repository():
    """The fake behaves like GitHub: an all-repositories installation lists what was created through it, once; a
    selected-repositories one lists only what was selected."""
    fake = FakeGitHub()
    fake.repositories = [{"full_name": "Acme/docs", "default_branch": "trunk"}]
    client = httpx.Client(transport=httpx.MockTransport(fake), base_url="https://api.github.com")
    for name in ("docs", "tico-services"):
        assert client.post("/orgs/Acme/repos", json={"name": name, "private": True}).status_code == 201

    def listed():
        return client.get("/installation/repositories").json()["repositories"]
    assert listed() == [{"full_name": "Acme/docs", "default_branch": "trunk"}, {"full_name": "Acme/tico-services"}]
    fake.missing = {"tico-services"}
    assert listed() == [{"full_name": "Acme/docs", "default_branch": "trunk"}]
    fake.missing, fake.selection = set(), "selected"
    assert listed() == [{"full_name": "Acme/docs", "default_branch": "trunk"}]
