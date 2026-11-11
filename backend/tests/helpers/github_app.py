"""Helpers that other tests share, moved out of test_github_app.py so they import no test file."""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl
import httpx
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
from backend.enqueue import enqueue_stored
from backend import bot_settings as BS


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
).decode()


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
        self.refuse = None  # (status, message) for every token request
        self.permissions = None  # the installation's live permissions; None leaves them out
        self.missing, self.selection, self.forbidden = set(), "all", False  # repositories GitHub answers 404 for
        self.selected_repositories = set()
        self.list_status = 200
        self.created_repositories = set()
        self.taken = set()  # owner/name already on GitHub, made by someone else: creating it answers 422
        self.create_response_message = ""
        self.history = set()  # repositories that hold commits; any other answers "empty"
        self.lose_next_product_create_response = False
        self.refuse_actions_write = False  # refuse Actions: write whatever the live permissions say
        self.refuse_workflows_write = False  # refuse Workflows: write whatever the live permissions say
        self.on_token = None  # called with each token request's body before it is answered
        self.accounts = {"acme": "Organization"}  # GET /users/{name}: lowercase login -> "User" or "Organization"
        self.account_status = None  # an int, or (int, headers): every account lookup answers that instead
        self.account_down = False  # every account lookup fails to connect
        self.conversion_owner = {"login": "Acme", "type": "Organization"}  # whose account GitHub made the App under
        self.token_login = "ana-dev"  # the login GitHub says an authorized user token is for
        self.user_id = 9001  # the numeric id of that login (GET /user)
        self.installation_account_id = 9001  # the account the installation is on (GET /app/installations/{id})
        self.refresh_status = None  # answer every refresh with this status, as GitHub failing does, when set
        self.refuse_refresh = False  # refuse every refresh (the refresh token is no longer good)
        self.refreshes, self.issued = 0, 0  # refreshes made; tokens issued (gho_<n> access, ghr_<n> refresh)
        self.access_tokens, self.live_refresh = set(), set()
        self.revoked = set()  # access tokens DELETE /applications/{client_id}/grant revoked
        self.user_repo_status = 201  # POST /user/repos answers this
        self.expires_in = 28800

    def __call__(self, request):
        raw = request.content.decode() if request.content else ""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = dict(parse_qsl(raw))  # the OAuth token endpoint takes form fields
        self.calls.append((request.method, request.url.path, body, request.headers.get("authorization", "")))
        path = request.url.path
        if path == "/login/oauth/access_token":
            return self._oauth(body)
        if path == "/user":
            return httpx.Response(200, json={"login": self.token_login, "id": self.user_id})
        if path == "/user/repos":
            return self._user_repos(request, body)
        if path.startswith("/applications/") and path.endswith("/grant"):
            self.revoked.add(body["access_token"])
            return httpx.Response(204)
        if path.startswith("/users/"):
            name = path.split("/")[2]
            if self.account_down:
                raise httpx.ConnectError("synthetic", request=request)
            if self.account_status is not None:
                status, headers = (
                    self.account_status if isinstance(self.account_status, tuple) else (self.account_status, {})
                )
                return httpx.Response(status, headers=headers, json={"message": "limited"})
            kind = self.accounts.get(name.lower())
            if kind is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"login": name, "type": kind})
        if path == "/installation/repositories":
            page = int(request.url.params.get("page", 1))
            # An installation on all repositories lists one created through it, as GitHub does.
            listed = {r["full_name"] for r in self.repositories}
            made = [
                {"full_name": name}
                for name in sorted(self.created_repositories)
                if self.selection == "all" and name not in listed
            ]
            seen = [r for r in self.repositories + made if r["full_name"].split("/")[1] not in self.missing]
            if self.list_status != 200:
                return httpx.Response(self.list_status)
            return httpx.Response(
                200,
                json={
                    "total_count": len(seen),
                    "repository_selection": self.selection,
                    "repositories": seen[(page - 1) * 100 : page * 100],
                },
            )
        if "/contents/" in path:
            config = self.setup_files.get(path)
            if config is None:
                return httpx.Response(404)
            import base64

            return httpx.Response(200, json={"content": base64.b64encode(json.dumps(config).encode()).decode()})
        if path.startswith("/app-manifests/"):
            return httpx.Response(
                201,
                json={
                    "id": 4242,
                    "slug": "acme-tico",
                    "client_id": "Iv1.abc",
                    "client_secret": "cs",
                    "webhook_secret": "whs",
                    "pem": PEM,
                    "html_url": "https://github.com/apps/acme-tico",
                    "owner": self.conversion_owner,
                },
            )
        if path == "/app/installations":
            return httpx.Response(200, json=self.installations)
        if path.startswith("/app/installations/") and not path.endswith("/access_tokens"):
            info = {"id": 77, "repository_selection": self.selection, "account": {"id": self.installation_account_id}}
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
            if (
                self.refuse_actions_write
                and body.get("permissions", {}).get("actions") == "write"
                or (self.refuse_workflows_write and body.get("permissions", {}).get("workflows") == "write")
                or any(
                    name in body.get("permissions", {})
                    and (
                        name not in (self.permissions or {})
                        or body["permissions"][name] == "write"
                        and self.permissions[name] != "write"
                    )
                    for name in ("actions", "checks", "statuses", "deployments", "workflows")
                )
            ):
                return httpx.Response(
                    422, json={"message": "The permissions requested are not granted to this installation."}
                )
            if body and any(name in self.missing for name in body.get("repositories", [])):
                return httpx.Response(403 if self.forbidden else 422, json={"message": "Validation Failed"})
            if (
                body
                and self.selection == "selected"
                and any(name not in self.selected_repositories for name in body.get("repositories", []))
            ):
                return httpx.Response(422, json={"message": "Repository is not in this selected installation"})
            exp = datetime.fromtimestamp(self.clock(), timezone.utc) + timedelta(seconds=self.ttl)
            return httpx.Response(
                201, json={"token": "ghs_" + uuid.uuid4().hex, "expires_at": exp.strftime("%Y-%m-%dT%H:%M:%SZ")}
            )
        if path.startswith("/orgs/") and path.endswith("/repos"):
            repository = path.split("/")[2] + "/" + body["name"]
            if self.generate_status >= 300:
                return httpx.Response(self.generate_status, json={"message": self.create_response_message})
            if repository in self.taken or (
                body.get("description") == "Tico product repository" and repository in self.created_repositories
            ):
                return httpx.Response(422, json={"message": "name already exists"})
            self.created_repositories.add(repository)
            if body.get("description") == "Tico product repository" and self.lose_next_product_create_response:
                self.lose_next_product_create_response = False
                raise httpx.ReadTimeout("synthetic response timeout", request=request)
            return httpx.Response(
                self.generate_status, json={"full_name": repository, "html_url": "https://github.com/" + repository}
            )
        if path.endswith("/generate"):
            if body["owner"] + "/" + body["name"] in self.taken:
                return httpx.Response(422, json={"message": "name already exists"})
            return httpx.Response(
                self.generate_status,
                json={
                    "full_name": body["owner"] + "/" + body["name"],
                    "html_url": "https://github.com/" + body["owner"] + "/" + body["name"],
                },
            )
        return httpx.Response(404)

    def _oauth(self, body):
        """GitHub's OAuth token endpoint: a code is exchanged once, and a refresh rotates the refresh token (the one given stops
        working). A refusal is a 200 with an error in the body, as GitHub answers it."""
        if body.get("grant_type") == "refresh_token":
            if self.refresh_status:
                return httpx.Response(self.refresh_status, json={"message": "GitHub is unavailable"})
            if self.refuse_refresh or body.get("refresh_token") not in self.live_refresh:
                return httpx.Response(200, json={"error": "bad_refresh_token"})
            self.live_refresh.discard(body["refresh_token"])
            self.refreshes += 1
        elif body.get("code") != "the-code":
            return httpx.Response(200, json={"error": "bad_verification_code"})
        self.issued += 1
        access, refresh = f"gho_{self.issued}", f"ghr_{self.issued}"
        self.access_tokens.add(access)
        self.live_refresh.add(refresh)
        return httpx.Response(
            200,
            json={
                "access_token": access,
                "token_type": "bearer",
                "expires_in": self.expires_in,
                "refresh_token": refresh,
                "refresh_token_expires_in": 15897600,
            },
        )

    def _user_repos(self, request, body):
        """POST /user/repos: an empty repository in the signed-in person's account, for a token GitHub still accepts."""
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.access_tokens or token in self.revoked:
            return httpx.Response(401, json={"message": "Bad credentials"})
        if self.user_repo_status >= 300:
            return httpx.Response(self.user_repo_status, json={"message": self.create_response_message})
        repository = f"{self.token_login}/{body['name']}"
        self.created_repositories.add(repository)
        return httpx.Response(201, json={"full_name": repository, "html_url": "https://github.com/" + repository})

    def of(self, suffix):
        return [c for c in self.calls if c[1].endswith(suffix)]


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(G, "TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def api(tmp_path, gh):
    ids = {
        "owner-test": Identity("human:ana", "owner", "ana@acme.example"),
        "other-test": Identity("human:ben", "owner", "ben@acme.example"),
        "runner-test": Identity("runner:r1", "runner", runner_id="r1"),
        "bot-test": Identity("bot:cmo", "bot", runner_id="r1", attempt_id="a1"),
        "botops-test": Identity("bot:botops", "bot", runner_id="r1", attempt_id="a2"),
        "person-test": Identity("human:riley", "human", "riley@acme.example"),
    }
    app = create_app(
        Settings(
            db_path=tmp_path / "hub.db",
            public_url=PUBLIC,
            company_name="Acme",
            github_owner="Acme",
            test_identities=ids,
        )
    )
    with TestClient(app, follow_redirects=False) as client:
        with app.state.store.transaction() as c:
            bots = {
                slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("cpo", "cmo", "cfo", "botops")
            }
            bots["newbie"] = {"name": "newbie", "runtime": "fake", "status": "planned"}
            bots["oldie"] = {"name": "oldie", "runtime": "fake", "status": "archived"}
            H.sync_registry(
                c,
                bots,
                {
                    "people": [
                        {"id": "ana", "email": "ana@acme.example", "team": "leadership"},
                        {"id": "ben", "email": "ben@acme.example", "team": "leadership"},
                        {"id": "riley", "email": "riley@acme.example", "team": "leadership"},
                    ]
                },
            )
            repos = {
                "botops": "emp-botops",
                "newbie": "emp-newbie",
                "oldie": "emp-oldie",
                "cpo": "emp-cpo",
                "cmo": "https://github.com/Acme/emp-cmo.git",
                "cfo": "elsewhere/emp-cfo",
            }
            for slug, config in bots.items():
                c.execute(
                    'INSERT INTO bot_config(bot,team,owner_id,repo) VALUES(?,?,?,?)', (slug, 't', 'ana', repos[slug])
                )
                BS.write(c, slug, config)
            c.execute(
                "INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),)
            )
        client.app_state = app.state
        yield client


def auth(token="owner-test"):
    return {"Authorization": "Bearer " + token}


def product_repo_request(api, body, *, key="product-create-1", actor="owner-test"):
    return api.post("/api/v2/github/product-repos", json=body, headers={**auth(actor), "Idempotency-Key": key})


def manifest(api, **params):
    r = api.get("/api/v2/github/app/manifest", params={"org": "Acme", **params}, headers=auth())
    assert r.status_code == 200, r.text
    return r.json()


def connect(api, **params):
    state = manifest(api, **params)["state"]
    r = api.get("/api/v2/github/app/callback", params={"code": "the-code", "state": state}, headers=auth())
    assert r.status_code == 302, r.text
    return r


def runner_token(api, bot):
    with api.app_state.store.transaction() as c:
        if not c.execute("SELECT 1 FROM runners WHERE id='r1'").fetchone():
            cols = [r[1] for r in c.execute("PRAGMA table_info(runners)")]
            values = {
                "id": "r1",
                "owner_id": "ana",
                "label": "mac",
                "token_hash": "x",
                "created_at": H.now(),
                "created_by": "ana",
                "hostname": "h",
                "state": "active",
            }
            use = {k: v for k, v in values.items() if k in cols}
            c.execute(f"INSERT INTO runners({','.join(use)}) VALUES({','.join('?' * len(use))})", tuple(use.values()))
            # Last contact and version are runner_states columns (migration 0062); the insert made the row.
            c.execute("UPDATE runner_states SET last_seen_at=?,version=? WHERE runner_id='r1'", (H.now(), "1"))
        c.execute("INSERT OR REPLACE INTO assignments VALUES(?,?,?,?,?)", (bot, "r1", 1, H.now(), "ana"))


def put_extras(api, bot, repos, who="owner-test"):
    return api.put(f"/api/v2/bots/{bot}/github-repos", json={"repositories": repos}, headers=auth(who))


def turn_token(api, bot="cpo", purpose="turn"):
    body = {"bot": bot}
    if purpose != "turn":
        body["purpose"] = purpose
    return api.post("/api/v2/github/token", json=body, headers=auth("runner-test"))


def botops_turn(api):
    """An unattended BotOps turn: the runner, its assignment, and a leased attempt for `botops-test`."""
    runner_token(api, "botops")
    with api.app_state.store.transaction() as c:
        c.execute(
            "INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('m-botops','keeper','bot:botops','request','hi',?)",
            (H.now(),),
        )
        enqueue_stored(c, 'm-botops')
        job = c.execute("SELECT id FROM jobs WHERE bot_id='botops'").fetchone()["id"]
        c.execute(
            "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
            "VALUES('a2',?,'botops','r1',1,'x','running',?,?)",
            (job, H.shift(H.now(), hours=1), H.now()),
        )


def token_health(api):
    with api.app_state.store.read() as c:
        row = c.execute("SELECT last_error FROM service_health WHERE service='github:token'").fetchone()
        return bool(row and row["last_error"])


# ------------------------------------------------------------------ a new bot's first placement follows its build
def _box(api):
    """A second computer, "box", that never held newbie."""
    with api.app_state.store.transaction() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(runners)")]
        values = {
            "id": "r2",
            "owner_id": "ana",
            "label": "box",
            "token_hash": "y",
            "created_at": H.now(),
            "created_by": "ana",
            "hostname": "box",
            "state": "active",
        }
        use = {k: v for k, v in values.items() if k in cols}
        c.execute(f"INSERT INTO runners({','.join(use)}) VALUES({','.join('?' * len(use))})", tuple(use.values()))
        # Last contact and version are runner_states columns (migration 0062); the insert made the row.
        c.execute("UPDATE runner_states SET last_seen_at=?,version=? WHERE runner_id='r2'", (H.now(), "1"))
