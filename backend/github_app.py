"""Per-company GitHub access through a GitHub App the company creates in its own organization.

The owner registers the app with GitHub's manifest flow (nothing to copy by hand), installs it on the
org, and from then on the hub mints short-lived installation tokens scoped to one bot repository.
Runners fetch that token per turn (runner/git_credentials.py); no long-lived personal token is used.

The app's private key and secrets are encrypted at rest and are never returned by any endpoint or
written to a log. See docs/github-app.md.
"""
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from . import hubdb as H
from .auth import validate_identity
from .health import GITHUB_HEALTH, note_github_token
from .store import Problem

log = logging.getLogger("tico.github_app")

API = "https://api.github.com"
STATE_TTL = 3600
LIVE_TTL = 60                 # how long the installation's live permissions are trusted
REFRESH_MARGIN = 300          # a cached token is dropped this long before GitHub expires it
# What a bot's turn needs in its own repository, and nothing else.
TURN_PERMISSIONS = {"contents": "write", "pull_requests": "write", "issues": "write", "metadata": "read"}
CREATE_PERMISSIONS = {"administration": "write", "contents": "read"}
ORG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
REPO_PART = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
DEFAULT_TEMPLATE = "ticoteam/botops"
BOTOPS = "botops"
EXTRA_KEY = "github-extra-repos"   # registry_metadata: {bot: ["owner/name", ...]}
MAX_EXTRA_REPOS = 20
# A refusal about one bot's repository (not on GitHub yet, or not shared with the app), or a rehearsal that never calls GitHub. The bot's own readiness
# and Health already name it; it says nothing about whether the App works, so it never marks the token unhealthy.
BOT_SCOPED = ("github_repo_missing", "github_repo_not_accessible", "rehearsal")
RECONNECT = "Reconnect GitHub in Settings."

# Replaced in tests with an httpx.MockTransport so nothing reaches the network.
TRANSPORT = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS github_app(
 id TEXT PRIMARY KEY, app_id INTEGER NOT NULL, slug TEXT NOT NULL, client_id TEXT NOT NULL, org TEXT NOT NULL,
 administration INTEGER NOT NULL, installation_id INTEGER, html_url TEXT NOT NULL DEFAULT '',
 ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, created TEXT NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS github_app_states(
 nonce TEXT PRIMARY KEY, actor TEXT NOT NULL, org TEXT NOT NULL, administration INTEGER NOT NULL,
 created REAL NOT NULL);
"""


def _client():
    return httpx.Client(base_url=API, timeout=15, transport=TRANSPORT,
                        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                                 "User-Agent": "Tico-GitHub-App"})


def _iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def repo_of(repo, default_owner):
    """`owner/name` from a stored bot repo: a bare name, owner/name, or a github.com URL."""
    repo = str(repo or "").strip()
    if repo.startswith(("http://", "https://")):
        url = urlparse(repo)
        if (url.hostname or "").lower() != "github.com":
            return None
        repo = url.path.strip("/")
    repo = repo.removesuffix(".git")
    if "/" not in repo:
        repo = f"{default_owner}/{repo}" if default_owner and repo else ""
    parts = repo.split("/")
    if len(parts) != 2 or not all(REPO_PART.match(p) for p in parts):
        return None
    return repo


class GitHubApp:
    """The stored app, its at-rest encryption, and installation-token minting."""

    def __init__(self, settings, store, vault=None):
        self.settings, self.store, self.vault = settings, store, vault
        self.cache = {}
        self.live = {}
        self.lock = threading.Lock()
        self.repository_sync_attempt = 0
        self.repository_sync_failures = 0
        self.repository_queue_lock = threading.Lock()
        self.repository_sync_lock = threading.Lock()
        self.repository_sync_wanted = False
        self.repository_refresh_wanted = False
        self.repository_worker = None
        self.repository_running = False
        self.repository_stop = threading.Event()
        with store.transaction() as c:
            c.executescript(SCHEMA)

    # -- secret storage ---------------------------------------------------------------------
    def _key(self, c):
        """The vault's KMS-wrapped key when the deployment has one; otherwise a local key file kept
        beside the database, so a copy of the database alone does not carry the app's private key."""
        if self.settings.credential_kms_key and self.vault is not None:
            return self.vault.cipher.key(c)
        path = Path(self.settings.db_path).parent / "github-app.key"
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(os.urandom(32))
        return path.read_bytes()

    def secret_key(self, c):
        """The same at-rest key, for other stored secrets (directory sync credentials)."""
        return self._key(c)

    def save(self, c, actor, org, administration, conversion):
        secrets_blob = json.dumps({"pem": conversion["pem"], "webhook_secret": conversion.get("webhook_secret") or "",
                                   "client_secret": conversion.get("client_secret") or ""}).encode()
        nonce = os.urandom(12)
        sealed = AESGCM(self._key(c)).encrypt(nonce, secrets_blob, b"tico-github-app:v1")
        c.execute("DELETE FROM github_app")
        c.execute("INSERT INTO github_app VALUES('app',?,?,?,?,?,NULL,?,?,?,?,?)",
                  (int(conversion["id"]), conversion["slug"], conversion["client_id"], org, int(administration),
                   str(conversion.get("html_url") or ""), sealed, nonce, H.now(), actor))
        self.cache.clear()
        self.live.clear()

    def row(self, c=None):
        if c is not None:
            return c.execute("SELECT * FROM github_app WHERE id='app'").fetchone()
        with self.store.read() as c:
            return self.row(c)

    def private_key(self, c, row):
        try:
            blob = AESGCM(self._key(c)).decrypt(row["nonce"], row["ciphertext"], b"tico-github-app:v1")
            return json.loads(blob)["pem"]
        except Exception:
            raise Problem("github_unavailable", "The stored GitHub App key could not be decrypted", 503) from None

    def webhook_secret(self):
        with self.store.read() as c:
            row = self.row(c)
            if not row:
                return ""
            try:
                blob = AESGCM(self._key(c)).decrypt(row["nonce"], row["ciphertext"], b"tico-github-app:v1")
                return json.loads(blob).get("webhook_secret") or ""
            except Exception:
                return ""

    def forget(self, c):
        c.execute("DELETE FROM github_app")
        c.execute("UPDATE repositories SET reachable=0 WHERE reachable<>0")
        c.execute("DELETE FROM github_app_states")
        c.execute("DELETE FROM registry_metadata WHERE key IN ('repositories-reachability-verified','repositories-confirmed-missing')")
        c.execute("DELETE FROM service_health WHERE service=?", (GITHUB_HEALTH,))   # nothing left to be unhealthy
        self.cache.clear()
        self.live.clear()

    # -- GitHub calls -----------------------------------------------------------------------
    def app_jwt(self, c, row):
        now = int(time.time())
        # GitHub rejects an exp more than ten minutes out; iat is backdated for clock drift.
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": str(row["app_id"])},
                          self.private_key(c, row), algorithm="RS256")

    def _call(self, method, path, **kw):
        if self.settings.rehearsal:
            raise Problem("rehearsal", "GitHub is not contacted from a rehearsal", 409)
        try:
            with _client() as http:
                return http.request(method, path, **kw)
        except httpx.HTTPError:
            raise Problem("github_unreachable", "GitHub could not be reached; try again shortly", 502) from None

    def exchange(self, code):
        r = self._call("POST", f"/app-manifests/{quote(code, safe='')}/conversions")
        if r.status_code >= 300:
            raise Problem("github_exchange", "GitHub did not accept that setup code; start again from Tools", 400)
        data = r.json()
        if not all(data.get(k) for k in ("id", "slug", "client_id", "pem")):
            raise Problem("github_exchange", "GitHub's answer was missing the app's credentials", 502)
        return data

    def installation(self, refresh=False):
        """The org's installation id, discovered through the app and remembered."""
        with self.store.transaction() as c:
            row = self.row(c)
            if not row:
                return None
            if row["installation_id"] and not refresh:
                return int(row["installation_id"])
            token = self.app_jwt(c, row)
        r = self._call("GET", "/app/installations", params={"per_page": 100},
                       headers={"Authorization": "Bearer " + token})
        if r.status_code >= 300:
            raise Problem("github_installations", "GitHub would not list the app's installations", 502)
        found = next((i for i in r.json() if str((i.get("account") or {}).get("login", "")).lower() == row["org"].lower()), None)
        with self.store.transaction() as c:
            c.execute("UPDATE github_app SET installation_id=? WHERE id='app'", (found["id"] if found else None,))
        return int(found["id"]) if found else None

    def live_permissions(self, refresh=False):
        """The installation's permissions as GitHub reports them now, cached briefly; None when they
        cannot be learned (not installed, GitHub unreachable or silent), so callers keep what is stored."""
        with self.lock:
            hit = self.live.get("permissions")
            if hit and hit[0] > time.time() and not refresh:
                return hit[1]
        try:
            installation = self.installation()
            with self.store.transaction() as c:
                row = self.row(c)
                token = self.app_jwt(c, row) if row else None
            if not installation or not token:
                return None
            r = self._call("GET", f"/app/installations/{installation}", headers={"Authorization": "Bearer " + token})
            permissions = r.json().get("permissions") if r.status_code < 300 else None
        except (Problem, ValueError):
            return None
        if not isinstance(permissions, dict):
            return None
        with self.lock:
            self.live["permissions"] = (time.time() + LIVE_TTL, permissions)
        return permissions

    def can_create_repos(self, refresh=False):
        """Whether the app may create repositories: the installation's live Administration permission
        (write), falling back to the stored choice when GitHub cannot be asked. The stored flag follows
        the live value so status views stay right."""
        row = self.row()
        if not row:
            return False
        permissions = self.live_permissions(refresh)
        if permissions is None:
            return bool(row["administration"])
        allowed = permissions.get("administration") == "write"
        if allowed != bool(row["administration"]):
            with self.store.transaction() as c:
                c.execute("UPDATE github_app SET administration=? WHERE id='app'", (int(allowed),))
        return allowed

    def delete_repo(self, repository):
        row = self.row()
        if not row:
            raise Problem("github_not_connected", "Connect the GitHub App first", 409)
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or any(
                part in (".", "..") for part in repository.split("/")):
            raise Problem("github_repo", "Use an owner/name repository", 422)
        if repository.split("/")[0].lower() != row["org"].lower():
            raise Problem("github_repo", f"The repository must be in the connected organization ({row['org']})", 422)
        if not self.can_create_repos(refresh=True):
            raise Problem("github_app_permissions", "Deleting repositories needs Administration (write) on the GitHub App. "
                          "Enable it in GitHub and accept the updated installation permissions", 403)
        token, _ = self.mint([repository], {"administration": "write", "metadata": "read"})
        response = self._call("DELETE", f"/repos/{repository}", headers={"Authorization": "Bearer " + token})
        if response.status_code != 204:
            raise Problem("github_repo_delete", f"GitHub did not delete {repository} (HTTP {response.status_code}). "
                          "Check the App's repository access and Administration permission", 409)
        with self.lock:
            self.cache = {key: value for key, value in self.cache.items() if repository not in key[0]}
        return {"repository": repository, "deleted": True}

    def mint(self, repos, permissions, diagnose=True):
        """A token for the repositories (`owner/name` each), or the whole installation when there are none.
        Cached until shortly before it expires."""
        repos = sorted(set(repos or ()))
        key = (tuple(repos), tuple(sorted(permissions.items())))
        with self.lock:
            hit = self.cache.get(key)
            if hit and hit["exp"] - REFRESH_MARGIN > time.time():
                return hit["token"], hit["expires_at"]
        installation = self.installation()
        if not installation:
            raise Problem("github_not_installed", "The GitHub App is not installed on the organization yet", 409)
        with self.store.transaction() as c:
            token = self.app_jwt(c, self.row(c))
        body = {"permissions": permissions}
        if repos:
            body["repositories"] = [r.split("/", 1)[1] for r in repos]
        r = self._call("POST", f"/app/installations/{installation}/access_tokens", json=body,
                       headers={"Authorization": "Bearer " + token})
        said = _said(r)
        if r.status_code in (403, 422) and "permission" in said.lower():
            raise Problem("github_app_permissions",
                          "The GitHub App has not been granted the permissions bots need (contents, pull requests, issues). "
                          "Accept its updated permissions on the installation page in GitHub, or " + RECONNECT[0].lower() + RECONNECT[1:], 409)
        if r.status_code in (403, 404, 422) and repos:
            if diagnose:
                raise self._unreachable(installation, repos, r.status_code)
            raise Problem('github_repo_not_accessible', 'GitHub could not reach the requested repositories', 409)
        if r.status_code == 401:
            raise Problem("github_app_key", "GitHub rejected the App's key (" + (said or "HTTP 401") + "). " + RECONNECT, 502)
        if r.status_code >= 300:
            raise Problem("github_token", f"GitHub would not issue an installation token (HTTP {r.status_code}"
                          + (": " + said if said else "") + ")", 502)
        data = r.json()
        entry = {"token": data["token"], "expires_at": data["expires_at"], "exp": _iso(data["expires_at"])}
        with self.lock:
            self.cache = {k: v for k, v in self.cache.items() if v["exp"] > time.time()}
            if len(self.cache) >= 256:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = entry
        return entry["token"], entry["expires_at"]

    def mint_reachable(self, repos, permissions, diagnose_empty=False, own=None):
        """Discard confirmed missing repositories and retry the remaining scope once."""
        from .repositories import unreachable, metadata, save_metadata
        with self.store.read() as c:
            missing = unreachable(c)
            names = [name for name in repos if name.lower() not in missing or name.lower() == str(own).lower()]
        if not names:
            if diagnose_empty:
                raise Problem('github_repo_not_accessible', 'Repository is not reachable; check the GitHub App installation (retry in five minutes)', 409)
            return None, None, []
        try:
            token, expires = self.mint(names, permissions, diagnose=False)
        except Problem as problem:
            if problem.code not in ('github_repo_not_accessible', 'github_repo_missing'):
                raise
            wide, _ = self.mint(None, {'metadata': 'read'})
            absent = [name for name in names if name.lower() in missing]
            if not absent:
                absent = [name for name in names if self._call(
                    'GET', '/repos/' + name, headers={'Authorization': 'Bearer ' + wide}).status_code == 404]
            if not absent:
                raise
            with self.store.transaction() as c:
                confirmed = metadata(c, 'repositories-confirmed-missing')
                confirmed = {name: stamp for name, stamp in confirmed.items() if name in unreachable(c)}
                confirmed.update({name.lower(): confirmed.get(name.lower(), time.time()) if name.lower() in missing else time.time() for name in absent})
                save_metadata(c, 'repositories-confirmed-missing', confirmed)
                for name in absent:
                    c.execute('INSERT INTO repositories(id,full_name,reachable,updated) VALUES(?,?,0,?) '
                              'ON CONFLICT(full_name) DO UPDATE SET reachable=0', (uuid.uuid4().hex, name, H.now()))
            from .repositories import repository_health
            repository_health(self)
            names = [name for name in names if name not in absent]
            if not names:
                if diagnose_empty:
                    raise self._unreachable(self.installation(), repos, 404, known_absent=absent)
                return None, None, []
            token, expires, names = self.mint_reachable(names, permissions, diagnose_empty=diagnose_empty)
        from .repositories import reachable
        with self.store.transaction() as c:
            reachable(c, names)
        return token, expires, names

    def _unreachable(self, installation, repos, status, known_absent=None):
        """Why GitHub would not scope a token to `repos`. A repository that does not exist yet answers
        404, the same as one the app cannot see, so ask GitHub what the installation can see: with
        access to every repository a 404 means it is not there yet; with selected repositories it could
        be either, and the message says both. A 403 is always access."""
        access = ("give it access to those repositories in the app's installation settings")
        problem = Problem("github_repo_not_accessible",
                          f"The GitHub App cannot reach {', '.join(repos)}; {access}", 409)
        if status == 403:
            return problem
        try:
            with self.store.transaction() as c:
                token = self.app_jwt(c, self.row(c))
            info = self._call("GET", f"/app/installations/{installation}", headers={"Authorization": "Bearer " + token})
            selection = info.json().get("repository_selection") if info.status_code < 300 else ""
            absent = known_absent
            if absent is None:
                wide, _ = self.mint(None, {"metadata": "read"})
                absent = [repo for repo in repos
                          if self._call("GET", f"/repos/{repo}", headers={"Authorization": "Bearer " + wide}).status_code == 404]
        except Problem:
            return problem
        if not absent:
            return problem
        slugs = ", ".join(re.sub(r"^(?:emp|bot)-", "", repo.split("/", 1)[1]) for repo in absent)   # a bot's repository may be either
        if self.can_create_repos():
            create = (f"Create it with BotOps: `hub bot repo-create {slugs.split(', ')[0]}` "
                      "(add `--empty` for a bot built locally, whose history its runner then pushes)")
        else:
            create = ("The GitHub App can't create repositories (Administration is off): create it yourself in GitHub "
                      "(empty, for a bot built locally, whose history its runner then pushes), or turn on "
                      "Administration for the app in GitHub and accept it for the organisation")
        if selection == "all":
            return Problem("github_repo_missing", f"The repository {', '.join(absent)} does not exist yet on GitHub. {create}.", 409)
        return Problem("github_repo_not_accessible",
                       f"GitHub answers 404 for {', '.join(absent)}: it does not exist yet, or the app is not installed on it. "
                       f"If it does not exist yet: {create[0].lower() + create[1:]}. If it exists: {access}.", 409)

    def create_repo(self, slug, template, empty=False):
        row = self.row()
        if not row:
            raise Problem("github_not_connected", "GitHub is not connected. Connect it in Tools first.", 409)
        name = "bot-" + slug.removeprefix("emp-")
        if not self.can_create_repos(refresh=True):
            how = ("as an empty private repository" if empty else
                   f"from the {template} template (https://github.com/{template} > Use this template)")
            raise Problem(
                "github_permission_missing",
                f"The GitHub App was set up without permission to create repositories. Create {row['org']}/{name} "
                f"yourself {how}, or turn on Administration for the app in GitHub and accept it for the organisation.", 409)
        token, _ = self.mint(None, CREATE_PERMISSIONS)
        if empty:
            # For a bot whose history already exists on a computer: nothing generated, the runner pushes into it.
            r = self._call("POST", f"/orgs/{row['org']}/repos", headers={"Authorization": "Bearer " + token},
                           json={"name": name, "private": True, "auto_init": False,
                                 "description": f"Tico bot repository for {slug.removeprefix('emp-')}"})
            if r.status_code == 422:
                raise Problem("github_repo_exists", f"{row['org']}/{name} already exists or the name is not allowed", 409)
            if r.status_code >= 300:
                raise Problem("github_create_failed", f"GitHub would not create {row['org']}/{name} "
                              f"(HTTP {r.status_code}). Create it manually if this persists.", 502)
            data = r.json()
            from .repositories import reachable
            with self.store.transaction() as c:
                reachable(c, [data.get("full_name") or f"{row['org']}/{name}"])
            return {"repository": data.get("full_name") or f"{row['org']}/{name}", "html_url": data.get("html_url", ""),
                    "empty": True, "note": "Empty repository. Push the bot's existing history to it; if the app is "
                    "installed on selected repositories only, add this repository to the installation first."}
        t_owner, t_repo = template.split("/", 1)
        r = self._call("POST", f"/repos/{t_owner}/{t_repo}/generate", headers={"Authorization": "Bearer " + token},
                       json={"owner": row["org"], "name": name, "private": True,
                             "description": f"Tico bot repository for {slug.removeprefix('emp-')}"})
        if r.status_code == 422:
            raise Problem("github_repo_exists", f"{row['org']}/{name} already exists or the name is not allowed", 409)
        if r.status_code >= 300:
            raise Problem("github_create_failed", f"GitHub would not create {row['org']}/{name} from {template} "
                          f"(HTTP {r.status_code}). Create it manually if this persists.", 502)
        data = r.json()
        from .repositories import reachable
        with self.store.transaction() as c:
            reachable(c, [data.get("full_name") or f"{row['org']}/{name}"])
        return {"repository": data.get("full_name") or f"{row['org']}/{name}", "html_url": data.get("html_url", ""),
                "note": "If the app is installed on selected repositories only, add this repository to the installation."}


class Repo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(min_length=1, max_length=64)
    template: str | None = Field(default=None, max_length=140)
    empty: bool = False


class ExtraRepos(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repositories: list[str] = Field(max_length=MAX_EXTRA_REPOS)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot: str = Field(min_length=1, max_length=100)
    repository: str | None = None


def _said(response):
    """GitHub's own words for a refusal, when it gave any."""
    try:
        return str(response.json().get("message") or "")[:200]
    except (ValueError, AttributeError):
        return ""


def manifest(settings, name, administration):
    public = settings.public_url
    permissions = {**TURN_PERMISSIONS, "checks": "read", "statuses": "read"}
    if administration:
        permissions["administration"] = "write"
    return {"name": name[:34], "url": public, "redirect_url": public + "/api/v2/github/app/callback",
            "setup_url": public + "/api/v2/github/app/installed",
            "hook_attributes": {"url": public + "/api/v2/github/webhook", "active": True},
            "public": False, "default_permissions": permissions,
            "default_events": ["pull_request", "pull_request_review", "pull_request_review_comment",
                               "check_run", "check_suite", "status", "push"]}


def extra_repos(c, bot):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (EXTRA_KEY,)).fetchone()
    try:
        value = json.loads(row[0]).get(bot, []) if row else []
    except (ValueError, AttributeError):
        value = []
    return [r for r in value if isinstance(r, str)]


def save_extra_repos(c, bot, repos):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (EXTRA_KEY,)).fetchone()
    every = json.loads(row[0]) if row else {}
    if repos:
        every[bot] = repos
    else:
        every.pop(bot, None)
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (EXTRA_KEY, json.dumps(every, sort_keys=True)))


def install_github_app(app, settings, store):
    service = app.state.github_app = GitHubApp(settings, store, getattr(app.state, "vault", None))

    from . import repositories as R
    R.install(app, store, service)

    def owner(request):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner connects GitHub", 403)
        return who

    def manager(request, bot):
        """The owner, or whoever manages this bot (`Auth.bot_manager`): the extra repositories are the bot's settings."""
        who = request.state.identity
        if who.role != "owner":
            with store.read() as c:
                if not app.state.auth.bot_manager(c, who, bot):
                    raise Problem("forbidden", "Only the owner, or someone who manages this bot, sets its repositories", 403)
        return who

    @app.get("/api/v2/github/app")
    def status(request: Request):
        owner(request)
        row = service.row()
        if not row:
            return {"connected": False}
        return {"connected": True, "slug": row["slug"], "org": row["org"], "app_id": row["app_id"],
                "administration": service.can_create_repos(), "installed": bool(row["installation_id"]),
                "install_url": f"https://github.com/apps/{row['slug']}/installations/new",
                "settings_url": f"https://github.com/organizations/{row['org']}/settings/apps/{row['slug']}",
                "uninstall_url": f"https://github.com/organizations/{row['org']}/settings/installations"}

    @app.get("/api/v2/github/app/manifest")
    def manifest_form(request: Request, org: str, name: str = "", administration: bool = False, html: bool = False):
        who = owner(request)
        if not ORG.match(org):
            raise Problem("github_org", "Enter the GitHub organization's name (letters, digits and hyphens)", 422)
        if service.row():
            raise Problem("github_connected", "GitHub is already connected; disconnect it first to start over", 409)
        nonce = secrets.token_urlsafe(24)
        with store.transaction() as c:
            c.execute("DELETE FROM github_app_states WHERE created<?", (time.time() - STATE_TTL,))
            c.execute("INSERT INTO github_app_states VALUES(?,?,?,?,?)", (nonce, who.actor, org, int(administration), time.time()))
        body = manifest(settings, (name or f"{settings.company_name} Tico").strip(), administration)
        action = f"https://github.com/organizations/{org}/settings/apps/new?state={nonce}"
        if html:
            page = ("<!doctype html><meta charset=utf-8><title>Connecting GitHub</title>"
                    f"<form id=f method=post action='{action}'><input type=hidden name=manifest value='"
                    + json.dumps(body).replace("&", "&amp;").replace("'", "&#39;") + "'></form>"
                    "<script>document.getElementById('f').submit()</script>")
            return HTMLResponse(page, headers={"Cache-Control": "no-store"})
        return {"action": action, "manifest": body, "state": nonce}

    @app.get("/api/v2/github/app/callback")
    def callback(request: Request, code: str = "", state: str = ""):
        who = owner(request)
        with store.transaction() as c:
            row = c.execute("SELECT * FROM github_app_states WHERE nonce=?", (state,)).fetchone()
            # Single use, and only in the session that started it.
            if row:
                c.execute("DELETE FROM github_app_states WHERE nonce=?", (state,))
        if (not row or row["actor"] != who.actor or time.time() - row["created"] > STATE_TTL
                or not secrets.compare_digest(row["nonce"], state)):
            raise Problem("github_state", "This GitHub setup link is not valid or has expired; start again from Tools", 400)
        if not code:
            raise Problem("github_exchange", "GitHub did not send a setup code", 400)
        conversion = service.exchange(code)
        with store.transaction() as c:
            service.save(c, who.actor, row["org"], row["administration"], conversion)
            H.event(c, who.actor, "github.app_connected", conversion["slug"], {"org": row["org"], "app_id": conversion["id"]})
        return RedirectResponse(f"https://github.com/apps/{conversion['slug']}/installations/new", status_code=302)

    @app.get("/api/v2/github/app/installed")
    def installed(request: Request):
        who = owner(request)
        found = service.installation(refresh=True)
        if found:
            try:
                R.sync(service)
            except Problem as exc:
                log.warning("Repository sync failed: %s", exc.code)
        with store.transaction() as c:
            H.event(c, who.actor, "github.app_installed", "", {"installed": bool(found)})
        return RedirectResponse("/#/settings?github=" + ("connected" if found else "pending"), status_code=302)

    @app.post("/api/v2/github/app/disconnect")
    def disconnect(request: Request):
        who = owner(request)
        with store.transaction() as c:
            row = service.row(c)
            service.forget(c)
            H.event(c, who.actor, "github.app_disconnected", row["slug"] if row else "", {})
        return {"ok": True, "note": "Forgotten here. Uninstall or delete the app on GitHub to revoke it there."}

    @app.post("/api/v2/github/token")
    def token(request: Request, body: TokenRequest):
        who = request.state.identity
        with store.transaction() as c:
            validate_identity(c, who)
            row = service.row(c)
            if not row:
                return {"configured": False}
            # A turn may ask only for its own bot; a runner only for a bot assigned to it.
            if who.role == "bot":
                allowed = H.actor_id(who.actor) == body.bot
            elif who.role == "runner":
                allowed = c.execute("SELECT 1 FROM assignments WHERE bot=? AND runner_id=?",
                                    (body.bot, who.runner_id)).fetchone() is not None
            else:
                allowed = False
            if not allowed:
                raise Problem("forbidden", "This credential does not run that bot", 403)
            from .shared_bots import declared, source_of
            bot_config = declared(c, body.bot)
            if bot_config.get("assignment_branch"):
                raise Problem("assignment_repository", "Temporary task assignments use a private local branch; GitHub credentials are not issued for them", 409)
            source = source_of(bot_config) or body.bot
            config = c.execute("SELECT repo FROM bot_config WHERE bot=?", (source,)).fetchone()
            # A bare emp-<slug> means the connected org, whatever the default owner is.
            repo = repo_of(config["repo"] if config else "", row["org"] or settings.github_owner)
            if not repo or repo.split("/")[0].lower() != row["org"].lower():
                raise Problem("forbidden", f"That bot's repository is not in the connected organization ({row['org']})", 403)
            grants = R.access(c, body.bot, row['org'])['effective']
        repos = [r['full_name'] for r in grants]
        write_repos = [r['full_name'] for r in grants if r['access'] == 'write']
        read_repos = [r['full_name'] for r in grants if r['access'] == 'read']
        if body.repository:
            requested = repo_of(body.repository, row['org'])
            grant = next((g for g in grants if requested and g['full_name'].lower() == requested.lower()), None)
            if not grant:
                raise Problem('forbidden', 'That repository is outside this bot’s access', 403)
            write_repos = [grant['full_name']] if grant['access'] == 'write' else []
            read_repos = [grant['full_name']] if grant['access'] == 'read' else []
            repos = [grant['full_name']]
        tokens = []
        try:
            # GitHub permissions are token-wide. Never put read grants in a write token.
            if write_repos:
                value, expires, write_repos = service.mint_reachable(write_repos, TURN_PERMISSIONS, diagnose_empty=write_repos == [repo], own=repo)
                if value:
                    tokens.append({'token': value, 'expires_at': expires, 'repositories': write_repos, 'access': 'write'})
            if read_repos:
                read_value, read_expires, read_repos = service.mint_reachable(read_repos, {'contents': 'read', 'metadata': 'read'})
                if read_value:
                    tokens.append({'token': read_value, 'expires_at': read_expires, 'repositories': read_repos, 'access': 'read'})
            if not tokens:
                raise Problem('github_repo_not_accessible', f'{repo} is not reachable; check the GitHub App installation', 409)
            repos = write_repos + read_repos
            value, expires = tokens[0]['token'], tokens[0]['expires_at']
        except Problem as problem:
            if problem.code not in BOT_SCOPED:
                note_github_token(store, problem.detail, "Open Settings > Tools and check the GitHub connection.")
            raise
        R.repository_health(service)
        with store.transaction() as c:
            H.event(c, who.actor, "github.token", body.bot, {"repository": repo, "repositories": repos})
        return {"configured": True, "token": value, "expires_at": expires, "repository": repo, "repositories": repos, "tokens": tokens}

    def bot_repos(c, bot):
        if not c.execute("SELECT 1 FROM bot_config WHERE bot=?", (bot,)).fetchone():
            raise Problem("not_found", "No such bot", 404)
        row = service.row(c)
        own = repo_of((c.execute("SELECT repo FROM bot_config WHERE bot=?", (bot,)).fetchone() or {"repo": ""})["repo"],
                      settings.github_owner or (row["org"] if row else ""))
        return row, own

    @app.get("/api/v2/bots/{bot}/github-repos")
    def extra_get(request: Request, bot: str):
        manager(request, bot)
        with store.read() as c:
            row, own = bot_repos(c, bot)
            return {"connected": bool(row), "org": row["org"] if row else "", "repository": own or "",
                    "repositories": [r["full_name"] for r in R.access(c, bot, row["org"] if row else settings.github_owner)["chosen"] if r["access"] == "write"]}

    @app.put("/api/v2/bots/{bot}/github-repos")
    def extra_put(request: Request, bot: str, body: ExtraRepos):
        who = manager(request, bot)
        with store.transaction() as c:
            row, own = bot_repos(c, bot)
            if not row:
                raise Problem("github_not_connected", "Connect GitHub first (Tools)", 409)
            wanted = []
            for value in body.repositories:
                repo = repo_of(value, row["org"])
                if not repo or repo.split("/")[0].lower() != row["org"].lower():
                    raise Problem("github_repo", f"'{value}' is not a repository in {row['org']}; extra repositories "
                                  "must be in the connected organization", 422)
                if (own or "").lower() != repo.lower() and repo.lower() not in [w.lower() for w in wanted]:
                    wanted.append(repo)
            before = R.access(c, bot, row['org'])
            old = {r['full_name'].lower() for r in before['chosen'] if r['access'] == 'write'}
            if {r.lower() for r in wanted} == old:
                return {"connected": True, "org": row['org'], "repository": own or '', "repositories": wanted}
            if before['mode'] != 'chosen':
                raise Problem('github_repo', 'This bot uses repository access; change it in Settings', 409)
            R.set_access(c, bot, R.RepoAccessUpdate(mode='chosen', chosen=[R.Grant(**r) for r in before['chosen']] +
                                                   [R.Grant(full_name=r) for r in wanted if r.lower() not in {g['full_name'].lower() for g in before['chosen']}]),
                         row['org'], who.actor, legacy=True, team_list=app.state.auth.bot_admin(who))
        return {"connected": True, "org": row["org"], "repository": own or "", "repositories": wanted}

    @app.post("/api/v2/github/repos")
    def create_repo(request: Request, body: Repo):
        who = request.state.identity
        slug = body.slug.removeprefix("emp-")
        if who.role != "owner":
            from .repositories import can_create_repositories
            with store.read() as c:
                validate_identity(c, who)
                if who.role != "bot" or not can_create_repositories(c, H.actor_id(who.actor)):
                    raise Problem("forbidden", "Only the owner, BotOps or a bot granted repository creation may "
                                  "create bot repositories. Ask BotOps, or ask an Owner or admin to enable "
                                  "Create bot repositories in this bot's settings.", 403)
                row, bot = service.row(c), H.bot(c, slug)
            if not row or not service.can_create_repos(refresh=True):
                raise Problem("forbidden", "GitHub was connected without permission to create repositories; "
                              "the owner must turn on Administration for the app in GitHub and accept it for the organisation", 403)
            if not bot or bot.get("state") == "archived":
                raise Problem("forbidden", f"{slug} is not a bot being set up or running, so no repository is created for it", 403)
            if body.slug != slug and body.slug != "emp-" + slug:
                raise Problem("forbidden", "Bots create only bot-<slug> for a bot", 403)
            if body.template and body.template != DEFAULT_TEMPLATE and body.template.split("/")[0].lower() != row["org"].lower():
                raise Problem("forbidden", "Bots generate from the default template or one in the connected organization", 403)
        template = body.template or DEFAULT_TEMPLATE
        if body.empty and body.template:
            raise Problem("github_repo", "An empty repository has no template; give one or the other", 422)
        if not SLUG.match(slug) or not re.match(r"^[\w.-]+/[\w.-]+$", template):
            raise Problem("github_repo", "Use a lowercase bot name (letters, digits, hyphens) and an owner/name template", 422)
        result = service.create_repo(slug, template, empty=body.empty)
        with store.transaction() as c:
            H.event(c, who.actor, "github.repo_created", result["repository"],
                    {"empty": True} if body.empty else {"template": template})
        return result

    @app.delete("/api/v2/github/repos/{org}/{repo}")
    def delete_repo(request: Request, org: str, repo: str):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner deletes a GitHub repository", 403)
        result = service.delete_repo(org + "/" + repo)
        with store.transaction() as c:
            H.event(c, who.actor, "github.repo_deleted", result["repository"], {})
        return result
