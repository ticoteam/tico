"""Per-company GitHub access through a GitHub App the company creates in its own account or organization.

The owner registers the app with GitHub's manifest flow (nothing to copy by hand), installs it on the
account, and from then on the hub mints short-lived installation tokens scoped to one bot repository.
Runners fetch that token per turn (runner_support/git_credentials.py); no long-lived personal token is used.

The app's private key and secrets are encrypted at rest and are never returned by any endpoint or
written to a log. See docs/github-app.md.
"""

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from collections import namedtuple
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode, urlparse

import httpx
import jwt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from . import hubdb as H, task_privacy
from .auth import validate_identity
from .config import DEFAULT_COMPANY_NAME
from .health import GITHUB_HEALTH, note_github_token
from .store import Problem, digest, encode
from . import bot_settings as BS
from . import brand_icons

log = logging.getLogger("tico.github_app")

API = "https://api.github.com"
STATE_TTL = 3600
LIVE_TTL = 60  # how long the installation's live permissions are trusted
# Token requests sit on the request thread pool, so GitHub failing to answer must fail fast, not hold a thread.
TIMEOUT = httpx.Timeout(10, connect=3)
# Start-of-turn tokens seed `GH_TOKEN` and may be used throughout a turn, so keep at least 45 minutes
# remaining. The git credential helper can ask again per call, so its explicit purpose needs only ten.
TURN_START_REFRESH_MARGIN = 45 * 60
GIT_CALL_REFRESH_MARGIN = 10 * 60
# Kept as the default for callers that are not the per-call git helper.
REFRESH_MARGIN = TURN_START_REFRESH_MARGIN
# What a bot's turn needs in its own repository, and nothing else.
TURN_PERMISSIONS = {"contents": "write", "pull_requests": "write", "issues": "write", "metadata": "read"}
READ_PERMISSIONS = {"contents": "read", "metadata": "read"}
# What a bot reads beside its code in any repository it can reach: workflow runs (a deploy's result), check runs
# and commit statuses (a pull request's checks; `gh pr checks` needs both), pull requests and their comments
# (`gh pr view`, `gh pr diff`; a PR's conversation is its issue), and deployments (a preview's link). A write token
# keeps its own write on pull requests and issues. GitHub refuses a token naming a permission the installation has
# not accepted, so turns narrow to the ones it has accepted (mint_turn).
TURN_READS = {
    "actions": "read",
    "checks": "read",
    "statuses": "read",
    "pull_requests": "read",
    "issues": "read",
    "deployments": "read",
}
# A write grant an Owner or admin lets run workflows (start, rerun, cancel) gets its own token with Actions: write:
# GitHub permissions are token-wide, so the bot's other repositories never carry it.
WORKFLOWS_WITHHELD = (
    "Running workflows needs the GitHub App's Actions permission set to Read and write, accepted on "
    "the installation; until then these repositories get the ordinary write token."
)
WORKFLOWS_REFUSED = (
    "GitHub refused a token with Actions: write although the installation reports it, so these "
    "repositories get the ordinary write token; it is asked again after a minute."
)
# A write grant an Owner or admin lets edit workflow files gets Workflows: write the same way. Every other token goes
# without it, so workflow files stay unedited there: a workflow pushed on a branch runs with the repository's secrets.
WORKFLOW_EDITS_WITHHELD = (
    "Editing workflow files needs the GitHub App's Workflows permission set to Read and write, "
    "accepted on the installation; until then these repositories get the ordinary write token."
)
WORKFLOW_EDITS_REFUSED = (
    "GitHub refused a token with Workflows: write although the installation reports it, so these "
    "repositories get the ordinary write token; it is asked again after a minute."
)
# The Merge stage's `tico/merge-ok` check run on a pull request's head (backend/github.py `AppGitHub.post_check`) needs
# Checks: write. The manifest asks for it, but no turn token carries it (TURN_NEVER_WRITE): the server mints its own
# token for it, and only after the installation reports Checks as read and write. An App made before it asks the Owner
# to accept the change; until then nothing is posted.
CHECKS_PERMISSIONS = {"checks": "write", "metadata": "read"}
# Asked for in the manifest only because GitHub refuses a manifest whose `merge_group` event no permission covers. No
# token ever names it (a token naming a permission an older installation lacks is refused), so an App without it works.
MERGE_GROUP_EVENT_PERMISSIONS = {"merge_queues": "read"}
# What no bot's turn token ever writes, whatever a repository opts in to (RAISES): a bot that could post a check or a
# commit status could pass `tico/merge-ok` itself and merge around the Merge stage.
TURN_NEVER_WRITE = ("checks", "statuses", "administration")
CHECKS_WITHHELD = (
    "Posting Tico's approval check on pull requests needs the GitHub App's Checks permission set to Read and write, "
    "accepted on the installation; until then no check is posted."
)
# registry_metadata: the installation's permissions as GitHub last reported them (`live_permissions`), for Health.
PERMISSIONS_SEEN = "github-app-permissions"


def permission_seen(c, name):
    """The level GitHub last reported for one of the installation's permissions ("write", "read" or "" for none), or
    None when Tico has not learned the installation's permissions yet. Reads no network."""
    from .repositories import metadata

    seen = metadata(c, PERMISSIONS_SEEN)
    if not isinstance(seen.get("permissions"), dict):
        return None
    return str(seen["permissions"].get(name) or "")


# Each opt-in that raises a write grant's token: the grant flag, the permission raised to write, the `live` key that
# remembers GitHub refusing it, the reasons a token answer gives, and the answer field that carries them. A grant
# with both gets one token with both, as the git credential helper picks one token per remote.
Raise = namedtuple("Raise", "flag permission refused_key withheld refused unknown answer")
RAISES = (
    Raise(
        "run_workflows",
        "actions",
        "workflows-refused",
        WORKFLOWS_WITHHELD,
        WORKFLOWS_REFUSED,
        "GitHub did not say whether the App may run workflows, so these repositories get the ordinary write "
        "token; it is asked again on the next token.",
        "workflows_withheld",
    ),
    Raise(
        "edit_workflows",
        "workflows",
        "workflow-edits-refused",
        WORKFLOW_EDITS_WITHHELD,
        WORKFLOW_EDITS_REFUSED,
        "GitHub did not say whether the App may edit workflow files, so these repositories get the ordinary write "
        "token; it is asked again on the next token.",
        "workflow_edits_withheld",
    ),
)
READS_RETRY = 6 * 3600  # until the installation webhook or Check permissions says it changed
CREATE_PERMISSIONS = {"administration": "write", "contents": "read"}
PRODUCT_CREATE_PERMISSIONS = {"administration": "write", "metadata": "read"}
ORG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
REPO_PART = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
PRODUCT_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
DEFAULT_TEMPLATE = "ticoteam/botops"
BOTOPS = "botops"
EXTRA_KEY = "github-extra-repos"  # registry_metadata: {bot: ["owner/name", ...]}
MAX_EXTRA_REPOS = 20
# A refusal about one bot's repository (not on GitHub yet, or not shared with the app), or a rehearsal that never calls GitHub. The bot's own readiness
# and Health already name it; it says nothing about whether the App works, so it never marks the token unhealthy.
BOT_SCOPED = ("github_repo_missing", "github_repo_not_accessible", "rehearsal")
RECONNECT = "Reconnect GitHub in Tools."
WEB = "https://github.com"  # the OAuth endpoints, which are not the REST API
USER_AAD = b"tico-github-user-token:v1"
ACCESS_MARGIN = 10 * 60  # a user token this close to its end is refreshed first
NOT_AUTHORIZED = (
    "Tico is not authorized to create repositories in the personal account yet: authorize Tico in Tools > GitHub"
)

# Replaced in tests with an httpx.MockTransport so nothing reaches the network.
TRANSPORT = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS github_app(
 id TEXT PRIMARY KEY, app_id INTEGER NOT NULL, slug TEXT NOT NULL, client_id TEXT NOT NULL, org TEXT NOT NULL,
 administration INTEGER NOT NULL, installation_id INTEGER, html_url TEXT NOT NULL DEFAULT '',
 ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
 account_type TEXT NOT NULL DEFAULT 'Organization');
CREATE TABLE IF NOT EXISTS github_app_states(
 nonce TEXT PRIMARY KEY, actor TEXT NOT NULL, org TEXT NOT NULL, administration INTEGER NOT NULL,
 created_at REAL NOT NULL, account_type TEXT NOT NULL DEFAULT 'Organization', code TEXT, returned_at REAL);
"""
# GitHub's account types. `org` holds the account's login for either: for a personal account the App lives under the
# user's settings, not an organization's.
ORGANIZATION, PERSONAL = "Organization", "User"
ACCOUNT_TYPES = (ORGANIZATION, PERSONAL)


def _sign_in_path(auth):
    """The built-in sign-in page for the proxy this server signs people in with, or None when there is none to return from
    (the same paths the sign-in refusal names, backend/auth.py)."""
    proxy = getattr(auth, "proxy", None)
    if proxy is None:
        return None
    if proxy.name == "oidc":
        return proxy.sign_in_path
    if proxy.name == "owner-link":
        return "/auth/owner-link"
    return None


def account_kind(row):
    """The type of the account that owns the App, as the row records it."""
    return row["account_type"] or ORGANIZATION


def account_word(row):
    """What to call the App's account in a message: an organization, or a personal account."""
    return "account" if account_kind(row) == PERSONAL else "organization"


# The install link is Tico's own route: it mints the single-use state the install's return must carry, then sends
# the owner on to GitHub's install page.
INSTALL_PATH = "/api/v2/github/app/install"
# GitHub's redirect after the owner clicks Create on the App's manifest page. The browser of that redirect may hold no
# owner session (the agent holds the API token), so the route is reachable without one: it parks the code and the owner's
# agent finishes the setup (`finish`). The middleware lets it through without a session (backend/app.py request_guard).
CALLBACK_PATH = "/api/v2/github/app/callback"
FINISH_PATH = "/api/v2/github/app/finish"


def links(row):
    """The GitHub pages for the App and its installation, built from the row: an organization's settings live under its
    own URL, a personal account's under the user's. Every link Tico shows or sends a person to is built here."""
    slug = quote(str(row["slug"]), safe="")
    if account_kind(row) == PERSONAL:
        base = f"https://github.com/settings/apps/{slug}"
        installations = "https://github.com/settings/installations"
    else:
        org = quote(str(row["org"]), safe="")
        base = f"https://github.com/organizations/{org}/settings/apps/{slug}"
        installations = f"https://github.com/organizations/{org}/settings/installations"
    return {
        "settings_url": base,
        "permissions_url": base + "/permissions",
        "install_url": INSTALL_PATH,
        "uninstall_url": installations,
    }


def create_url(org, name=""):
    """GitHub's new-repository page with the owner, name and private visibility filled in."""
    query = {"owner": org, "visibility": "private"}
    if name:
        query["name"] = name
    return "https://github.com/new?" + urlencode(query)


def fixes(c, row, slug=""):
    """The two ways out when Tico may not create a bot's repository, as links: create the repository (prefilled), or
    let BotOps create repositories. In a personal account the second is authorizing Tico to create empty repositories
    there."""
    name = H.bot_repository_name(c, slug) if slug else ""  # named for the bot, id or slug in
    return {
        "repository": f"{row['org']}/{name}" if name else "",
        "account_type": account_kind(row),
        "create_url": create_url(row["org"], name),
        "permissions_url": links(row)["permissions_url"],
    }


def _client(base=API):
    return httpx.Client(
        base_url=base,
        timeout=TIMEOUT,
        transport=TRANSPORT,
        headers={
            "Accept": "application/vnd.github+json" if base == API else "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Tico-GitHub-App",
        },
    )


def _iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _ends(seconds):
    """The ISO time `seconds` from now, in the format the rest of the database keeps."""
    return H.shift(H.now(), seconds=int(seconds))


def user_callback(settings):
    """Where GitHub sends the owner after they authorize Tico for a personal account: the App's callback URL."""
    return settings.public_url + "/api/v2/github/app/authorized"


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


def validate_product_repo_name(name):
    """A single exact GitHub repository name, never an owner/name or URL."""
    if not isinstance(name, str) or not PRODUCT_REPO_NAME.fullmatch(name) or name in (".", ".."):
        raise Problem(
            "github_repo",
            "Use one repository name (letters, digits, dots, underscores or hyphens); do not include an organization or URL",
            422,
        )
    return name


KEY_FILE = "github-app.key"
MISSING_KEY = (
    "The GitHub App key file (github-app.key) is missing from the data volume: restore it from the backup "
    "made with this database (docs/install.md, Backups and restore)"
)
LOST_KEY = (
    "The key for the saved GitHub App and directory sync secrets (github-app.key) is missing from the data volume, "
    "so they cannot be opened. Restore it from the backup (credential-key/github-app.key) and restart; or, if no "
    "backup has it, confirm replacing it (POST /api/v2/github/app/replace-lost-key, or replace_lost_key on the "
    "directory save) to drop those secrets and enter them again."
)


class GitHubApp:
    """The stored app, its at-rest encryption, and installation-token minting."""

    def __init__(self, settings, store, vault=None):
        self.settings, self.store, self.vault = settings, store, vault
        self.cache = {}
        self.audited_tokens = {}
        self.live = {}
        self.lock = threading.Lock()
        self.minting = {}  # (cache key, diagnose) -> Future of the one mint in flight for it
        # Serialize confirmed product creates in this process so same-process retries see the
        # completed receipt. The committed operation row is the cross-process/crash fence.
        self.product_repo_create_lock = threading.Lock()
        self.user_lock = threading.Lock()  # one refresh of the owner's user token at a time (it rotates)
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
    def key_file(self):
        """The local key file: backed up beside credential.key (backend/replication.py), since a restored database
        without it cannot open the app's secrets or the directory sync credentials."""
        return Path(self.settings.db_path).parent / KEY_FILE

    def _sealed_elsewhere(self, c, replacing):
        """Whether anything but what this save replaces is sealed with the local key: a GitHub App row, or directory sync
        credentials of another source. `replacing` is "app" or "directory:<source>"."""
        if replacing != "app" and c.execute("SELECT 1 FROM github_app WHERE id='app'").fetchone():
            return True
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='directory_credentials'").fetchone():
            return False
        source = replacing.split(":", 1)[1] if replacing.startswith("directory:") else None
        return bool(c.execute("SELECT 1 FROM directory_credentials WHERE source IS NOT ?", (source,)).fetchone())

    def _key(self, c, create=True, replacing="app"):
        """The vault's KMS-wrapped key when the deployment has one; otherwise a local key file kept
        beside the database, so a copy of the database alone does not carry the app's private key.
        Only a save makes the file: a read with it missing returns None, so a restore that lost it is not
        papered over by a new key that opens nothing. A save makes one only when nothing else is sealed with the
        missing key: a new key would leave that unreadable, and the backup copy of the old key is what opens it."""
        if self.settings.credential_kms_key and self.vault is not None:
            return self.vault.cipher.key(c)
        path = self.key_file()
        if not path.exists():
            if not create:
                return None
            if self._sealed_elsewhere(c, replacing):
                raise Problem("github_key_missing", LOST_KEY, 409)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(os.urandom(32))
        return path.read_bytes()

    def key_lost(self, c):
        """No local key file, and secrets sealed with the one that was there: nothing here can open them again."""
        if self.kms():
            return False
        return not self.key_file().exists() and self._sealed_elsewhere(c, "")

    def replace_lost_key(self, c, actor):
        """The owner's confirmed way out when no copy of github-app.key exists anywhere: drop what only that key opens
        (the GitHub App row and every directory source's credentials), so the next save makes a new key. Does nothing
        while the key is there. A copy the backup held is kept there as credential-key/github-app.key.replaced-<time>."""
        if not self.key_lost(c):
            return False
        app = self.row(c)
        sources = (
            [r["source"] for r in c.execute("SELECT source FROM directory_credentials")]
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='directory_credentials'").fetchone()
            else []
        )
        if app:
            self.forget(c)
        if sources:
            c.execute("DELETE FROM directory_credentials")
        H.event(
            c,
            actor,
            "github.lost_key_replaced",
            app["slug"] if app else "",
            {"github_app": bool(app), "directory_sources": sources},
        )
        return True

    def kms(self):
        return bool(self.settings.credential_kms_key and self.vault is not None)

    def open_sealed(self, c, row, aad, table="github_app", where="id", value="app"):
        """A sealed row's plaintext, or None when no key is there to open it. Under KMS a row saved before
        TICO_CREDENTIAL_KMS_KEY was set opens with github-app.key (if it is still on the data volume) and is
        re-sealed with the vault key once. Raises when no key opens it."""
        key = self._key(c, create=False)
        if key is None:
            return None
        from .credentials import open_or_reseal

        return open_or_reseal(
            c,
            key,
            self.key_file() if self.kms() else None,
            row["nonce"],
            row["ciphertext"],
            aad,
            table,
            where,
            value,
            log,
        )

    def secret_key(self, c, create=True, replacing=""):
        """The same at-rest key, for other stored secrets (directory sync credentials)."""
        return self._key(c, create, replacing)

    def account_type(self, name):
        """What GitHub says `name` is, a "User" or an "Organization", from its public profile (no token is sent). An
        unknown name is a 404 Problem; GitHub unreachable, rate limited or silent is a 502. Nothing is guessed."""
        if self.settings.rehearsal:
            raise Problem("rehearsal", "GitHub is not contacted from a rehearsal", 409)
        try:
            with _client() as http:
                r = http.get("/users/" + quote(name, safe=""))
        except httpx.HTTPError:
            raise Problem(
                "github_account",
                "GitHub could not be reached to check that account; try again shortly",
                502,
                retryable=True,
            ) from None
        if r.status_code == 404:
            raise Problem("github_account_missing", f"No GitHub account or organization named {name}", 404)
        if rate_limited(r):
            raise Problem("github_account", "GitHub is limiting account checks; try again shortly", 502, retryable=True)
        try:
            kind = r.json().get("type") if r.status_code < 300 else None
        except (ValueError, AttributeError):
            kind = None
        if kind in ACCOUNT_TYPES:
            return kind
        if r.status_code < 300 and kind is not None:
            raise Problem(
                "github_account",
                "GitHub says that account is not a user or an organization, so Tico cannot connect it. Enter a user or an organization name.",
                422,
            )
        raise Problem(
            "github_account", "GitHub could not say what that account is; try again shortly", 502, retryable=True
        )

    def save(self, c, actor, org, administration, conversion, account=ORGANIZATION):
        """Store the App GitHub's manifest conversion made. The conversion's owner is the truth: under a personal account
        GitHub makes the App for whoever is signed in, which may not be the name typed, so that login is kept."""
        owner = conversion.get("owner") if isinstance(conversion.get("owner"), dict) else {}
        kind = owner.get("type") if owner.get("type") in ACCOUNT_TYPES else account
        if kind == PERSONAL and owner.get("login"):
            org = str(owner["login"])
        if not org:
            # A personal connect names no account up front, so the conversion's owner is the only one there is.
            raise Problem("github_exchange", "GitHub's answer did not say which account the app was made for", 502)
        secrets_blob = json.dumps(
            {
                "pem": conversion["pem"],
                "webhook_secret": conversion.get("webhook_secret") or "",
                "client_secret": conversion.get("client_secret") or "",
                "owner_id": int(owner.get("id") or 0),
                "callback_in_manifest": kind == PERSONAL,
            }
        ).encode()
        nonce = os.urandom(12)
        sealed = AESGCM(self._key(c)).encrypt(nonce, secrets_blob, b"tico-github-app:v1")
        c.execute("DELETE FROM github_app")
        c.execute("DELETE FROM github_user_token")  # an authorization is for one App: a new App starts with none
        c.execute(
            "INSERT INTO github_app(id,app_id,slug,client_id,org,administration,installation_id,html_url,ciphertext,nonce,"
            "created_at,created_by,account_type) VALUES('app',?,?,?,?,?,NULL,?,?,?,?,?,?)",
            (
                int(conversion["id"]),
                conversion["slug"],
                conversion["client_id"],
                org,
                int(administration),
                str(conversion.get("html_url") or ""),
                sealed,
                nonce,
                H.now(),
                actor,
                kind,
            ),
        )
        c.execute("DELETE FROM registry_metadata WHERE key=?", (PERMISSIONS_SEEN,))  # a new App's are learned anew
        self.cache.clear()
        self.live.clear()

    def row(self, c=None):
        if c is not None:
            return c.execute("SELECT * FROM github_app WHERE id='app'").fetchone()
        with self.store.read() as c:
            return self.row(c)

    def private_key(self, c, row):
        try:
            blob = self.open_sealed(c, row, b"tico-github-app:v1")
        except Problem:
            raise
        except Exception:
            raise Problem("github_unavailable", "The stored GitHub App key could not be decrypted", 503) from None
        if blob is None:
            raise Problem("github_unavailable", MISSING_KEY, 503)
        return json.loads(blob)["pem"]

    def webhook_secret(self):
        with self.store.read() as c:
            row = self.row(c)
            if not row:
                return ""
            try:
                return json.loads(self.open_sealed(c, row, b"tico-github-app:v1")).get("webhook_secret") or ""
            except Exception:
                return ""

    def forget(self, c):
        c.execute("DELETE FROM github_app")
        c.execute("UPDATE repositories SET reachable=0 WHERE reachable<>0")
        c.execute("DELETE FROM github_app_states")
        c.execute("DELETE FROM github_user_token")
        c.execute("DELETE FROM github_user_states")
        c.execute(
            "DELETE FROM registry_metadata WHERE key IN "
            "('repositories-reachability-verified','repositories-confirmed-missing',?)",
            (PERMISSIONS_SEEN,),
        )
        c.execute("DELETE FROM service_health WHERE service=?", (GITHUB_HEALTH,))  # nothing left to be unhealthy
        self.cache.clear()
        self.live.clear()

    # -- the owner's user token (a personal account) ------------------------------------------
    # The App's user-to-server token lets Tico create an EMPTY repository in the personal account (POST /user/repos); nothing
    # else uses it. It is sealed like the App's secrets, refreshed when near its end, and forgotten when GitHub refuses it.
    def _app_secrets(self, c, row):
        """The App's sealed secrets, opened: its client secret is what GitHub's OAuth endpoints check."""
        blob = self.open_sealed(c, row, b"tico-github-app:v1")
        if blob is None:
            raise Problem("github_unavailable", MISSING_KEY, 503)
        return json.loads(blob)

    def _user_token_row(self, c):
        return c.execute("SELECT * FROM github_user_token WHERE id='app'").fetchone()

    def _open_user(self, c, stored):
        """The sealed user token pair (access_token, refresh_token), opened. The values never leave this module."""
        blob = self.open_sealed(c, stored, USER_AAD, table="github_user_token")
        if blob is None:
            raise Problem("github_unavailable", MISSING_KEY, 503)
        return json.loads(blob)

    def _seal_user(self, c, tokens):
        blob = json.dumps(
            {"access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token") or ""}
        ).encode()
        key = self._key(c)
        if key is None:
            raise Problem("github_unavailable", MISSING_KEY, 503)
        nonce = os.urandom(12)
        return AESGCM(key).encrypt(nonce, blob, USER_AAD), nonce

    def user_authorized(self, c=None):
        """Whether the owner authorized Tico for the personal account: a stored user token for the App's own login. It says
        nothing about whether GitHub still accepts the token; a refused refresh forgets it (user_token)."""
        if c is None:
            with self.store.read() as conn:
                return self.user_authorized(conn)
        row, stored = self.row(c), self._user_token_row(c)
        return bool(
            row
            and stored
            and account_kind(row) == PERSONAL
            and stored["client_id"] == row["client_id"]  # a token from an earlier App is not this App's
            and str(stored["login"]).lower() == str(row["org"]).lower()
        )

    def owner_id(self, c):
        """The numeric id of the account the App was made for, sealed at save (0 for an App saved before it was recorded)."""
        row = self.row(c)
        return int(self._app_secrets(c, row).get("owner_id") or 0) if row else 0

    def callback_in_manifest(self):
        """Whether the App's manifest asked for the authorization callback. Apps saved before that did not, so GitHub needs the
        callback URL added on its settings page."""
        with self.store.read() as c:
            row = self.row(c)
            return bool(row and self._app_secrets(c, row).get("callback_in_manifest"))

    def save_user_token(self, c, actor, login, github_id, tokens):
        """Store the authorization the owner just gave, replacing an earlier one: the sealed pair, the expiry times, and the
        App and account it is for (a token from another App or account is never used)."""
        app = self.row(c)
        sealed, nonce = self._seal_user(c, tokens)
        c.execute("DELETE FROM github_user_token")
        c.execute(
            "INSERT INTO github_user_token(id,login,github_id,client_id,ciphertext,nonce,access_expires_at,"
            "refresh_expires_at,authorized_by,authorized_at,refreshed_at) VALUES('app',?,?,?,?,?,?,?,?,?,NULL)",
            (
                login,
                int(github_id),
                app["client_id"],
                sealed,
                nonce,
                _ends(tokens["expires_in"]) if tokens.get("expires_in") else None,
                _ends(tokens["refresh_token_expires_in"]) if tokens.get("refresh_token_expires_in") else None,
                actor,
                H.now(),
            ),
        )

    def _user_grant(self, form):
        """GitHub's answer to an OAuth token request made with the App's client credentials: the token fields, or None when
        GitHub refused it (GitHub answers refusals in the body). GitHub unreachable, rate limited or failing is a retryable
        Problem, and nothing about the stored authorization is then known."""
        row = self.row()
        if not row:
            raise Problem("github_not_connected", "Connect the GitHub App first", 409)
        with self.store.read() as c:
            credentials = self._app_secrets(c, row)
        r = self._call(
            "POST",
            "/login/oauth/access_token",
            base=WEB,
            data={**form, "client_id": row["client_id"], "client_secret": credentials.get("client_secret") or ""},
        )
        if r.status_code >= 500 or rate_limited(r):
            raise Problem(
                "github_unreachable",
                "GitHub could not check Tico's authorization; try again shortly",
                502,
                retryable=True,
            )
        try:
            data = r.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict) or "error" in data or not data.get("access_token"):
            return None
        return data

    def exchange_user(self, code):
        """The owner's authorization for the code GitHub sent back after they authorized Tico."""
        tokens = self._user_grant({"code": code})
        if tokens is None:
            raise Problem("github_exchange", "GitHub did not accept that authorization; start again from Tools", 400)
        return tokens

    def user_identity(self, token):
        """GitHub's login and numeric id for the token (GET /user). The id is the account itself; a login can be renamed."""
        r = self._call("GET", "/user", headers={"Authorization": "Bearer " + token})
        if r.status_code >= 300:
            raise Problem(
                "github_unreachable", "GitHub would not say who authorized Tico; try again shortly", 502, retryable=True
            )
        data = r.json()
        return str(data.get("login") or ""), int(data.get("id") or 0)

    def _lose_user_token(self, reason, stored):
        """The authorization `stored` (the row that was read) is no good: GitHub refused its refresh, or it was rejected. Only
        that row is deleted (compare-and-delete on its nonce), so an authorization made in the meantime is kept. Recorded
        without any token value."""
        with self.store.transaction() as c:
            gone = c.execute("DELETE FROM github_user_token WHERE id='app' AND nonce=?", (stored["nonce"],)).rowcount
            if gone:
                H.event(c, H.KEEPER, "github.user_authorization_lost", "", {"reason": reason})

    def _forget_rejected(self, token):
        """GitHub rejected this access token (401): forget the authorization if it is still the stored one, compared by value,
        so a newer authorization is never dropped. Only the reason is recorded."""
        with self.user_lock:
            with self.store.read() as c:
                stored = self._user_token_row(c)
                current = self._open_user(c, stored) if stored else None
            if stored and current and current.get("access_token") == token:
                self._lose_user_token("rejected", stored)

    def user_token(self):
        """The owner's user access token, for the empty repositories Tico creates in the personal account, refreshed when it is
        near its end. GitHub rotates the refresh token on each refresh, so refreshes run one at a time (user_lock, which
        authorizing takes too), and the new pair is stored before its access token is used. GitHub refusing the refresh
        forgets the authorization: the owner authorizes again (github_user_token, 409). GitHub unreachable is retryable
        (github_unreachable, 502) and forgets nothing."""
        with self.user_lock:
            with self.store.read() as c:
                stored = self._user_token_row(c)
                if not stored or not self.user_authorized(c):
                    raise Problem("github_user_token", NOT_AUTHORIZED, 409)
                current = self._open_user(c, stored)
            if not stored["access_expires_at"] or _iso(stored["access_expires_at"]) - ACCESS_MARGIN > time.time():
                return current["access_token"]
            if not current.get("refresh_token"):
                self._lose_user_token("no_refresh_token", stored)
                raise Problem("github_user_token", NOT_AUTHORIZED, 409)
            # GitHub rotates the refresh token as it accepts this one, so the old pair dies here. Unavoidable: if the process
            # stops between GitHub's answer and the commit below, the stored pair is dead too and the owner authorizes again.
            tokens = self._user_grant({"grant_type": "refresh_token", "refresh_token": current["refresh_token"]})
            if tokens is None:
                self._lose_user_token("refresh_refused", stored)
                raise Problem("github_user_token", NOT_AUTHORIZED, 409)
            with self.store.transaction() as c:
                sealed, nonce = self._seal_user(
                    c,
                    {
                        "access_token": tokens["access_token"],
                        "refresh_token": tokens.get("refresh_token") or current["refresh_token"],
                    },
                )
                c.execute(
                    "UPDATE github_user_token SET ciphertext=?,nonce=?,access_expires_at=?,refresh_expires_at=?,"
                    "refreshed_at=? WHERE id='app'",
                    (
                        sealed,
                        nonce,
                        _ends(tokens["expires_in"]) if tokens.get("expires_in") else None,
                        _ends(tokens["refresh_token_expires_in"]) if tokens.get("refresh_token_expires_in") else None,
                        H.now(),
                    ),
                )
            return tokens["access_token"]

    def _user_create(self, token, body):
        """POST /user/repos as the owner: the empty repository in their own account. A token GitHub rejects is forgotten."""
        r = self._call("POST", "/user/repos", headers={"Authorization": "Bearer " + token}, json=body)
        if r.status_code == 401:
            self._forget_rejected(token)
            raise Problem("github_user_token", NOT_AUTHORIZED, 409)
        return r

    def revoke_user_token(self, token=None):
        """Best effort, on disconnect and when a new authorization is refused: GitHub revokes the owner's whole authorization of
        the App (the grant, with its refresh token).
        A failure is logged without any token value; the owner can also remove the authorization in GitHub's settings."""
        with self.store.read() as c:
            row, stored = self.row(c), self._user_token_row(c)
            if not row:
                return False
            try:
                credentials = self._app_secrets(c, row)
                if token is None and stored is not None:
                    token = self._open_user(c, stored)["access_token"]
            except (Problem, InvalidTag, ValueError, KeyError, TypeError):
                return False
        if not token:
            return False
        client_id = str(row["client_id"])
        try:
            r = self._call(
                "DELETE",
                f"/applications/{quote(client_id, safe='')}/grant",
                auth=(client_id, credentials.get("client_secret") or ""),
                json={"access_token": token},
            )
        except Problem:
            log.warning("The GitHub App's user authorization could not be revoked on GitHub")
            return False
        if r.status_code >= 300:
            log.warning("The GitHub App's user authorization was not revoked on GitHub (HTTP %s)", r.status_code)
            return False
        return True

    # -- GitHub calls -----------------------------------------------------------------------
    def app_jwt(self, c, row):
        now = int(time.time())
        with self.lock:
            hit = self.live.get("jwt")
            if hit and hit[0] == row["app_id"] and hit[1] - 60 > now:
                return hit[2]
        # GitHub rejects an exp more than ten minutes out; iat is backdated for clock drift.
        token = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": str(row["app_id"])}, self.private_key(c, row), algorithm="RS256"
        )
        with self.lock:
            self.live["jwt"] = (row["app_id"], now + 540, token)
        return token

    def _jwt(self):
        """The app's JWT, from memory when one is still good: reading the row needs no write lock."""
        with self.lock:
            hit = self.live.get("jwt")
            if hit and hit[1] - 60 > time.time():
                return hit[2]
        with self.store.transaction() as c:  # the at-rest key may be written the first time it is read
            return self.app_jwt(c, self.row(c))

    def _call(self, method, path, base=API, **kw):
        if self.settings.rehearsal:
            raise Problem("rehearsal", "GitHub is not contacted from a rehearsal", 409)
        try:
            with _client(base) as http:
                return http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise Problem(
                "github_unreachable",
                "GitHub could not be reached; try again shortly",
                502,
                retryable=True,
                extra={"timed_out": True} if isinstance(exc, httpx.TimeoutException) else None,
            ) from None

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
        row = self.row()
        if not row:
            return None
        if row["installation_id"] and not refresh:
            return int(row["installation_id"])
        token = self._jwt()
        r = self._call(
            "GET", "/app/installations", params={"per_page": 100}, headers={"Authorization": "Bearer " + token}
        )
        if r.status_code >= 300:
            limited = rate_limited(r)
            raise Problem(
                "github_installations",
                "GitHub would not list the app's installations",
                502,
                retryable=limited,
                extra={"github_status": r.status_code, "rate_limited": limited},
            )
        found = next(
            (i for i in r.json() if str((i.get("account") or {}).get("login", "")).lower() == row["org"].lower()), None
        )
        with self.store.transaction() as c:
            c.execute("UPDATE github_app SET installation_id=? WHERE id='app'", (found["id"] if found else None,))
        return int(found["id"]) if found else None

    def installation_account_id(self, installation_id):
        """The numeric id of the account one of this App's installations is on, or None when GitHub does not know the
        installation as this App's: GitHub answers for this App's own installations only."""
        token = self._jwt()
        r = self._call("GET", f"/app/installations/{installation_id}", headers={"Authorization": "Bearer " + token})
        if r.status_code == 404:
            return None
        if r.status_code >= 300:
            limited = rate_limited(r)
            raise Problem(
                "github_installations",
                "GitHub would not show the app's installation",
                502,
                retryable=limited,
                extra={"github_status": r.status_code, "rate_limited": limited},
            )
        account = r.json().get("account") or {}
        return int(account["id"]) if account.get("id") else None

    def live_permissions(self, refresh=False):
        """The installation's permissions as GitHub reports them now, cached briefly; None when they
        cannot be learned (not installed, GitHub unreachable or silent, a stored key that will not sign, or a demo,
        which never asks GitHub), so callers keep what is stored."""
        if self.settings.demo:
            return None
        with self.lock:
            hit = self.live.get("permissions")
            if hit and hit[0] > time.time() and not refresh:
                return hit[1]
        try:
            installation = self.installation()
            token = self._jwt() if installation else None
            if not installation or not token:
                return None
            r = self._call("GET", f"/app/installations/{installation}", headers={"Authorization": "Bearer " + token})
            info = r.json() if r.status_code < 300 else {}
            permissions = info.get("permissions") if isinstance(info, dict) else None
        except (Problem, ValueError, jwt.PyJWTError):
            return None
        if info.get("repository_selection") in ("all", "selected"):
            with self.lock:
                self.live["selection"] = info["repository_selection"]
        if not isinstance(permissions, dict):
            return None
        with self.lock:
            self.live["permissions"] = (time.time() + LIVE_TTL, permissions)
        self._remember_permissions(permissions)
        return permissions

    def _remember_permissions(self, permissions):
        """Keep what GitHub reported (PERMISSIONS_SEEN), written only when it changed, so Health says what the App
        lacks without asking GitHub. Never fails the read it follows."""
        from .repositories import metadata, save_metadata

        try:
            with self.store.read() as c:
                if metadata(c, PERMISSIONS_SEEN).get("permissions") == permissions:
                    return
            with self.store.transaction() as c:
                save_metadata(c, PERMISSIONS_SEEN, {"permissions": permissions, "at": H.now()})
        except Exception:  # noqa: BLE001 - Health learns it on the next read
            log.info("The App's permissions were not kept for Health", exc_info=True)

    def installation_selection(self, refresh=True):
        """What the App is installed on: "all", "selected", or "" when unknown. GitHub's answer when it gives one (and
        remembered for repositories.home), else the last one remembered. `refresh` False uses the answer the last
        live_permissions call got. Called holding no transaction."""
        from .repositories import save_selection, selection

        if refresh:
            with self.lock:
                self.live.pop("selection", None)
            self.live_permissions(refresh=True)
        with self.lock:
            live = self.live.get("selection")
        with self.store.read() as c:
            stored = selection(c)
        if live and live != stored:
            with self.store.transaction() as c:
                save_selection(c, live)
        return live or stored

    def checks_write_accepted(self):
        """Whether the installation reports Checks as read and write now (CHECKS_WITHHELD says what to do when not)."""
        permissions = self.live_permissions()
        return bool(permissions) and permissions.get("checks") == "write"

    def mint_checks(self, repo):
        """A token that can create check runs on `repo` (`owner/name`), or None while Checks: write is withheld."""
        if not self.checks_write_accepted():
            return None
        return self.mint([repo], CHECKS_PERMISSIONS, diagnose=False)[0]

    def raise_refused(self, raised):
        """Whether GitHub refused a token raising `raised` (a RAISES entry) the installation reports within LIVE_TTL:
        until then a token request falls back at once instead of repeating the refused mints."""
        with self.lock:
            until = self.live.get(raised.refused_key)
        return bool(until and until > time.time())

    def can_create_repos(self, refresh=False, empty=False):
        """Whether Tico creates repositories for the app: the installation's live Administration permission
        (write), falling back to the stored choice when GitHub cannot be asked. The stored flag follows
        the live value so status views stay right. An EMPTY repository in a personal account is made with the owner's user
        token, so it also needs that authorization; a template one needs Administration alone."""
        row = self.row()
        if not row:
            return False
        permissions = self.live_permissions(refresh)
        if permissions is None:
            allowed = bool(row["administration"])
        else:
            allowed = permissions.get("administration") == "write"
            if allowed != bool(row["administration"]):
                with self.store.transaction() as c:
                    c.execute("UPDATE github_app SET administration=? WHERE id='app'", (int(allowed),))
        if empty and account_kind(row) == PERSONAL:
            return allowed and self.user_authorized()
        return allowed

    def mint_turn(self, repos, base, narrow=True, **kw):
        """`mint_reachable` for a bot's turn: `base` plus the TURN_READS the installation has accepted (`base` wins, so it
        may raise Actions to write). A refusal narrows the ask to what the installation reports it has, then to `base`
        alone, and is remembered, so an older installation costs one or two extra requests every few hours and a token
        served from the cache none. `narrow=False` raises the refusal instead: a refused raise in `base` says nothing
        about the reads."""
        with self.lock:
            narrowed = self.live.get("turn-reads")
        reads = narrowed[1] if narrowed and narrowed[0] > time.time() else TURN_READS
        if any(name in TURN_NEVER_WRITE and level != "read" for name, level in {**reads, **base}.items()):
            raise Problem("github_turn_permissions", "A bot's token never writes checks, statuses or settings", 403)
        while True:
            try:
                return self.mint_reachable(repos, {**reads, **base}, **kw)
            except Problem as problem:
                if problem.code != "github_app_permissions" or not reads or not narrow:
                    raise
            granted = self.live_permissions(refresh=True) or {}
            accepted = {name: level for name, level in reads.items() if granted.get(name) in ("read", "write")}
            reads = accepted if accepted != reads else {}
            with self.lock:
                self.live["turn-reads"] = (time.time() + READS_RETRY, reads)

    def delete_repo(self, repository):
        row = self.row()
        if not row:
            raise Problem("github_not_connected", "Connect the GitHub App first", 409)
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or any(
            part in (".", "..") for part in repository.split("/")
        ):
            raise Problem("github_repo", "Use an owner/name repository", 422)
        if repository.split("/")[0].lower() != row["org"].lower():
            raise Problem(
                "github_repo", f"The repository must be in the connected {account_word(row)} ({row['org']})", 422
            )
        if not self.can_create_repos(refresh=True):
            raise Problem(
                "github_app_permissions",
                "Deleting repositories needs Administration (write) on the GitHub App. "
                "Enable it in GitHub and accept the updated installation permissions",
                403,
            )
        token, _ = self.mint([repository], {"administration": "write", "metadata": "read"})
        response = self._call("DELETE", f"/repos/{repository}", headers={"Authorization": "Bearer " + token})
        if response.status_code != 204:
            raise Problem(
                "github_repo_delete",
                f"GitHub did not delete {repository} (HTTP {response.status_code}). "
                "Check the App's repository access and Administration permission",
                409,
            )
        with self.lock:
            self.cache = {key: value for key, value in self.cache.items() if repository not in key[0]}
        return {"repository": repository, "deleted": True}

    def mint(self, repos, permissions, diagnose=True, refresh_margin=REFRESH_MARGIN, include_cache_status=False):
        """A token for the repositories (`owner/name` each), or the whole installation when there are none.
        Cached until it has less than `refresh_margin` seconds left."""
        repos = sorted(set(repos or ()))
        key = (tuple(repos), tuple(sorted(permissions.items())))
        while True:
            with self.lock:
                hit = self.cache.get(key)
                if hit and hit["exp"] - refresh_margin > time.time():
                    result = (hit["token"], hit["expires_at"])
                    return (*result, True) if include_cache_status else result
                flight = self.minting.get((key, diagnose))
                if flight is None:
                    flight = self.minting[(key, diagnose)] = Future()
                    break
            # Another request is minting this exact scope: share its token (or its refusal), not a second mint.
            try:
                flight.result()
            except Problem as problem:
                raise Problem(problem.code, problem.detail, problem.status, problem.retryable, problem.extra) from None
        try:
            entry = self._mint(key, repos, permissions, diagnose)
            flight.set_result(None)
        except BaseException as exc:
            flight.set_exception(exc)
            raise
        finally:
            with self.lock:
                self.minting.pop((key, diagnose), None)
        result = (entry["token"], entry["expires_at"])
        return (*result, False) if include_cache_status else result

    def _mint(self, key, repos, permissions, diagnose):
        if not permissions:
            # GitHub gives a token that names no permission every one the App has, Checks: write among them.
            raise Problem("github_app_permissions", "Name the permissions a GitHub token needs", 500)
        installation = self.installation()
        if not installation:
            current = self.row()
            word = account_word(current) if current else "organization"
            raise Problem("github_not_installed", f"The GitHub App is not installed on the {word} yet", 409)
        token = self._jwt()
        body = {"permissions": permissions}
        if repos:
            body["repositories"] = [r.split("/", 1)[1] for r in repos]
        r = self._call(
            "POST",
            f"/app/installations/{installation}/access_tokens",
            json=body,
            headers={"Authorization": "Bearer " + token},
        )
        if rate_limited(r):
            raise Problem(
                "github_token",
                "GitHub rate limited the installation token request; try again shortly",
                502,
                retryable=True,
                extra={"github_status": r.status_code, "rate_limited": True},
            )
        said = _said(r)
        if r.status_code in (403, 422) and "permission" in said.lower():
            raise Problem(
                "github_app_permissions",
                "The GitHub App has not been granted the permissions bots need (contents, pull requests, issues). "
                "Accept its updated permissions on the installation page in GitHub, or "
                + RECONNECT[0].lower()
                + RECONNECT[1:],
                409,
                extra={"github_status": r.status_code},
            )
        if r.status_code in (403, 404, 422) and repos:
            if diagnose:
                problem = self._unreachable(installation, repos, r.status_code)
                problem.extra["github_status"] = r.status_code
                raise problem
            raise Problem(
                'github_repo_not_accessible',
                'GitHub could not reach the requested repositories',
                409,
                extra={"github_status": r.status_code},
            )
        if r.status_code == 401:
            raise Problem(
                "github_app_key",
                "GitHub rejected the App's key (" + (said or "HTTP 401") + "). " + RECONNECT,
                502,
                extra={"github_status": r.status_code},
            )
        if r.status_code >= 300:
            raise Problem(
                "github_token",
                f"GitHub would not issue an installation token (HTTP {r.status_code}"
                + (": " + said if said else "")
                + ")",
                502,
                extra={"github_status": r.status_code},
            )
        data = r.json()
        entry = {"token": data["token"], "expires_at": data["expires_at"], "exp": _iso(data["expires_at"])}
        with self.lock:
            self.cache = {k: v for k, v in self.cache.items() if v["exp"] > time.time()}
            if len(self.cache) >= 256:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = entry
        return entry

    def should_audit_token(self, bot, token):
        """Keep token values out of durable state and audit each bot/token pair at most once."""
        key = (bot, hashlib.sha256(token.encode("utf-8")).hexdigest())
        with self.lock:
            if key in self.audited_tokens:
                return False
            self.audited_tokens[key] = None
            if len(self.audited_tokens) > 1024:
                self.audited_tokens.pop(next(iter(self.audited_tokens)))
            return True

    def mint_reachable(
        self,
        repos,
        permissions,
        diagnose_empty=False,
        own=None,
        refresh_margin=REFRESH_MARGIN,
        include_cache_status=False,
        missing=None,
    ):
        """Discard confirmed missing repositories and retry the remaining scope once."""
        from .repositories import unreachable, metadata, save_metadata

        if missing is None:
            with self.store.read() as c:
                missing = unreachable(c)
        names = [name for name in repos if name.lower() not in missing or name.lower() == str(own).lower()]
        # A known-missing own repository is kept only so a lone own scope can still be diagnosed; beside
        # other repositories it would just fail the first mint every call until the mark expires.
        if any(name.lower() not in missing for name in names):
            names = [name for name in names if name.lower() not in missing]
        if not names:
            if diagnose_empty:
                raise Problem(
                    'github_repo_not_accessible',
                    'Repository is not reachable; check the GitHub App installation (retry in five minutes)',
                    409,
                )
            result = (None, None, [])
            return (*result, False, False) if include_cache_status else result
        try:
            minted = self.mint(
                names,
                permissions,
                diagnose=False,
                refresh_margin=refresh_margin,
                include_cache_status=include_cache_status,
            )
            token, expires = minted[:2]
            cached = minted[2] if include_cache_status else False
        except Problem as problem:
            if problem.code not in ('github_repo_not_accessible', 'github_repo_missing'):
                raise
            wide, _ = self.mint(None, {'metadata': 'read'})
            absent = [name for name in names if name.lower() in missing]
            if not absent:
                absent = self._absent(names, wide)
            if not absent:
                raise
            with self.store.transaction() as c:
                confirmed = metadata(c, 'repositories-confirmed-missing')
                confirmed = {name: stamp for name, stamp in confirmed.items() if name in unreachable(c)}
                confirmed.update(
                    {
                        name.lower(): confirmed.get(name.lower(), time.time())
                        if name.lower() in missing
                        else time.time()
                        for name in absent
                    }
                )
                save_metadata(c, 'repositories-confirmed-missing', confirmed)
                for name in absent:
                    c.execute(
                        'INSERT INTO repositories(id,full_name,reachable,updated_at) VALUES(?,?,0,?) '
                        'ON CONFLICT(full_name) DO UPDATE SET reachable=0',
                        (uuid.uuid4().hex, name, H.now()),
                    )
            from .repositories import repository_health

            repository_health(self)
            names = [name for name in names if name not in absent]
            if not names:
                if diagnose_empty:
                    raise self._unreachable(self.installation(), repos, 404, known_absent=absent)
                result = (None, None, [])
                return (*result, False, False) if include_cache_status else result
            recursive = self.mint_reachable(
                names,
                permissions,
                diagnose_empty=diagnose_empty,
                refresh_margin=refresh_margin,
                include_cache_status=include_cache_status,
            )
            return recursive
        from .repositories import mark_reachable

        reachability_changed = mark_reachable(self.store, names)
        result = (token, expires, names)
        return (*result, cached, reachability_changed) if include_cache_status else result

    def _absent(self, repos, wide):
        """Which of `repos` the installation cannot see: one paged listing instead of a GET per repository
        (a bot may hold many grants), falling back to probing each when GitHub will not list them."""
        headers = {'Authorization': 'Bearer ' + wide}
        visible, seen, page = set(), 0, 1
        try:
            while True:
                r = self._call(
                    'GET', '/installation/repositories', params={'per_page': 100, 'page': page}, headers=headers
                )
                if r.status_code >= 300:
                    raise ValueError
                data = r.json()
                batch, total = data['repositories'], data.get('total_count')
                visible.update(str(repo.get('full_name') or '').lower() for repo in batch)
                seen += len(batch)
                if len(batch) < 100 or (isinstance(total, int) and seen >= total):
                    break
                page += 1
        except (Problem, ValueError, KeyError, TypeError, AttributeError):
            return [name for name in repos if self._call('GET', '/repos/' + name, headers=headers).status_code == 404]
        return [name for name in repos if name.lower() not in visible]

    def _unreachable(self, installation, repos, status, known_absent=None):
        """Why GitHub would not scope a token to `repos`. A repository that does not exist yet answers
        404, the same as one the app cannot see, so ask GitHub what the installation can see: with
        access to every repository a 404 means it is not there yet; with selected repositories it could
        be either, and the message says both. A 403 is always access."""
        access = "give it access to those repositories in the app's installation settings"
        problem = Problem(
            "github_repo_not_accessible", f"The GitHub App cannot reach {', '.join(repos)}; {access}", 409
        )
        if status == 403:
            return problem
        try:
            token = self._jwt()
            info = self._call("GET", f"/app/installations/{installation}", headers={"Authorization": "Bearer " + token})
            selection = info.json().get("repository_selection") if info.status_code < 300 else ""
            absent = known_absent
            if absent is None:
                wide, _ = self.mint(None, {"metadata": "read"})
                absent = self._absent(repos, wide)
        except Problem:
            return problem
        if not absent:
            return problem
        slugs = ", ".join(
            re.sub(r"^(?:emp|bot)-", "", repo.split("/", 1)[1]) for repo in absent
        )  # a bot's repository may be either
        if self.can_create_repos():
            create = (
                f"Create it with BotOps: `tico bot repo-create {slugs.split(', ')[0]}` "
                "(add `--empty` for a bot built locally, whose history its runner then pushes)"
            )
        elif (row := self.row()) and account_kind(row) == PERSONAL:
            create = (
                "Tico can't create repositories in the personal account right now (Administration is off, or it is not "
                "authorized there): create it yourself in GitHub (empty, for a bot built locally, whose history its runner "
                "then pushes)"
            )
        else:
            create = (
                "The GitHub App can't create repositories (Administration is off): create it yourself in GitHub "
                "(empty, for a bot built locally, whose history its runner then pushes), or turn on "
                "Administration for the app in GitHub and accept it for the organisation"
            )
        if selection == "all":
            return Problem(
                "github_repo_missing",
                f"The repository {', '.join(absent)} does not exist yet on GitHub. {create}.",
                409,
            )
        return Problem(
            "github_repo_not_accessible",
            f"GitHub answers 404 for {', '.join(absent)}: it does not exist yet, or the app is not installed on it. "
            f"If it does not exist yet: {create[0].lower() + create[1:]}. If it exists: {access}.",
            409,
        )

    def repository_state(self, repository):
        """Whether GitHub has `repository` (`owner/name`): ("present", None), ("missing", the Problem naming the fix),
        or ("unknown", None) when it cannot be learned (no app connected, GitHub unreachable or answering oddly)."""
        if not repository or not self.row():
            return "unknown", None
        try:
            wide, _ = self.mint(None, {"metadata": "read"})
            r = self._call("GET", "/repos/" + repository, headers={"Authorization": "Bearer " + wide})
            if r.status_code < 300:
                return "present", None
            if r.status_code != 404:
                return "unknown", None
            return "missing", self._unreachable(self.installation(), [repository], 404, known_absent=[repository])
        except Problem:
            return "unknown", None

    def repository_history(self, repository):
        """Whether GitHub's `repository` holds any commit: True, False (empty or missing), or None when it cannot
        be learned. A repository `tico bot create` made is empty until the computer that built the bot publishes it."""
        if not repository or not self.row():
            return None
        try:
            wide, _ = self.mint(None, {"metadata": "read", "contents": "read"})
            r = self._call(
                "GET",
                f"/repos/{repository}/commits",
                params={"per_page": 1},
                headers={"Authorization": "Bearer " + wide},
            )
        except Problem:
            return None
        if r.status_code < 300:
            return bool(r.json())
        return False if r.status_code in (404, 409) else None  # 409: "Git Repository is empty"

    def create_repo(self, slug, template, empty=False):
        row = self.row()
        if not row:
            raise Problem("github_not_connected", "GitHub is not connected. Connect it in Tools first.", 409)
        with self.store.read() as c:  # the repository is named for the bot (H.bot_repository_name), id or slug in
            name = H.bot_repository_name(c, slug)
            slug = (H.resolve_bot(c, slug) or {}).get("slug", slug)
        personal = account_kind(row) == PERSONAL
        ready = self.can_create_repos(refresh=True, empty=empty)
        if self.installation_selection(refresh=False) == "selected":
            # GitHub does not add a repository the App makes to an installation on chosen repositories, so it could
            # never reach it: nothing is made, and the bot works from its computer (repositories.home).
            raise Problem(
                "github_selected_repos",
                f"No repository was made for {slug}: the GitHub App is installed on chosen repositories only, so it "
                f"could not reach {row['org']}/{name}. {slug} works from its computer as usual; its history is not "
                "backed up to GitHub. Nobody needs to act. To back bot history up, install the App on all "
                "repositories, or make the repository and add it to the App's repositories on GitHub.",
                409,
            )
        user = None
        if ready and personal and empty:
            try:
                user = self.user_token()
            except Problem as problem:
                if problem.code != "github_user_token":
                    raise
                ready = False
        if not ready:
            with self.store.read() as c:
                found = fixes(c, row, slug)
            how = (
                "as an empty private repository"
                if empty
                else f"from the {template} template (https://github.com/{template} > Use this template)"
            )
            if personal and empty and not self.user_authorized():
                detail = (
                    f"A person must act: Tico is not authorized to create repositories in {row['org']} yet, so "
                    f"{row['org']}/{name} was not created. Authorize Tico in Tools > GitHub, or create it {how} "
                    f"({found['create_url']})."
                )
            else:
                word = "account" if personal else "organisation"
                detail = (
                    f"A person must act: the GitHub App was set up without permission to create repositories in the "
                    f"{word}, so {row['org']}/{name} was not created. Create it {how} ({found['create_url']}), or let "
                    f"BotOps create repositories by turning on Administration for the app ({found['permissions_url']}) "
                    f"and accepting it for the {word}."
                )
            raise Problem("github_permission_missing", detail, 409, extra=found)
        if empty and personal:
            if user is None:  # ready said the owner's token is there; this keeps the type honest for the calls below
                raise Problem("github_user_token", NOT_AUTHORIZED, 409)
            token = user  # the owner's user token: the empty repository is made as them, in their own account
        else:
            token, _ = self.mint(None, CREATE_PERMISSIONS)
        from .repositories import note_asked

        with self.store.transaction() as c:
            note_asked(c, f"{row['org']}/{name}")
        if empty:
            # For a bot whose history already exists on a computer: nothing generated, the runner pushes into it.
            body = {
                "name": name,
                "private": True,
                "auto_init": False,
                "description": f"Tico bot repository for {slug.removeprefix('emp-')}",
            }
            if personal:
                r = self._user_create(token, body)
            else:
                r = self._call(
                    "POST",
                    f"/orgs/{row['org']}/repos",
                    headers={"Authorization": "Bearer " + token},
                    json=body,
                )
            if r.status_code == 422:
                return self._existing(row, name, slug, empty=True)
            if r.status_code >= 300:
                raise self._create_failed(r, f"{row['org']}/{name}")
            data = r.json()
            from .repositories import MADE, note_repository, reachable

            with self.store.transaction() as c:
                reachable(c, [data.get("full_name") or f"{row['org']}/{name}"])
                note_repository(c, MADE, data.get("full_name") or f"{row['org']}/{name}")
            return {
                "repository": data.get("full_name") or f"{row['org']}/{name}",
                "html_url": data.get("html_url", ""),
                "empty": True,
                "note": "Empty repository. Push the bot's existing history to it; if the app is "
                "installed on selected repositories only, add this repository to the installation first.",
            }
        t_owner, t_repo = template.split("/", 1)
        r = self._call(
            "POST",
            f"/repos/{t_owner}/{t_repo}/generate",
            headers={"Authorization": "Bearer " + token},
            json={
                "owner": row["org"],
                "name": name,
                "private": True,
                "description": f"Tico bot repository for {slug.removeprefix('emp-')}",
            },
        )
        if r.status_code == 422:
            return self._existing(row, name, slug)
        if r.status_code >= 300:
            raise self._create_failed(r, f"{row['org']}/{name} from {template}")
        data = r.json()
        from .repositories import MADE, note_repository, reachable

        with self.store.transaction() as c:
            reachable(c, [data.get("full_name") or f"{row['org']}/{name}"])
            note_repository(c, MADE, data.get("full_name") or f"{row['org']}/{name}")
        return {
            "repository": data.get("full_name") or f"{row['org']}/{name}",
            "html_url": data.get("html_url", ""),
            "note": "If the app is installed on selected repositories only, add this repository to the installation.",
        }

    def _create_failed(self, r, what):
        """GitHub's refusal of a create. Its own failure or a rate limit passes: the same call works later."""
        limited = rate_limited(r)
        if limited or r.status_code >= 500:
            why = "GitHub is limiting requests" if limited else f"GitHub answered HTTP {r.status_code}"
            return Problem(
                "github_create_failed",
                f"GitHub did not create {what}: {why}. Nothing is wrong here; try again in a few minutes.",
                502,
                retryable=True,
                extra={"github_status": r.status_code, "rate_limited": limited},
            )
        return Problem(
            "github_create_failed",
            f"GitHub would not create {what} (HTTP {r.status_code}). Create it manually if this persists.",
            502,
        )

    def _made_since_asked(self, repository):
        """Whether GitHub made `repository` after Tico first asked for it (give or take the clocks): a create whose
        answer was lost, or one made twice at once. One GitHub has had for longer is someone else's."""
        from .repositories import ASKED, MADE_BEFORE_SLACK, metadata

        with self.store.read() as c:
            asked = metadata(c, ASKED).get(repository.lower())
        if not asked:
            return False
        try:
            wide = self.mint(None, {"metadata": "read"})[0]
            r = self._call("GET", "/repos/" + repository, headers={"Authorization": "Bearer " + wide})
            made = H.parse_ts(r.json().get("created_at")) if r.status_code < 300 else None
        except (Problem, ValueError, AttributeError):
            made = None
        if not made:
            # GitHub will not say: leave the question open, so the next try asks again, and mark nothing.
            raise Problem("github_unreachable", "GitHub could not be reached; try again shortly", 502, retryable=True)
        return made.timestamp() >= float(asked) - MADE_BEFORE_SLACK

    def _existing(self, row, name, slug, empty=False):
        """GitHub already has `name`. This company's own (an earlier create of it, or one a person pointed the bot at) is
        the answer a retry wants. Anyone else's is never taken over: it is remembered as not this company's, so no
        token, fetch or push uses it (repositories.home), and the bot keeps working from its computer."""
        from .repositories import TAKEN, made_here, note_repository

        repository = f"{row['org']}/{name}"
        with self.store.read() as c:
            ours = made_here(c, repository)
        if not ours and self._made_since_asked(repository):
            from .repositories import MADE, reachable

            with self.store.transaction() as c:
                reachable(c, [repository])
                note_repository(c, MADE, repository)
            ours = True
        if ours:
            return {
                "repository": repository,
                "html_url": "https://github.com/" + repository,
                **({"empty": True} if empty else {}),
                "existing": True,
                "note": "Tico made this repository before; nothing new was made.",
            }
        with self.store.transaction() as c:
            note_repository(c, TAKEN, repository)
        raise Problem(
            "github_repo_taken",
            f"No repository was made for {slug}: {repository} already exists on GitHub and this company did not make "
            f"it, so Tico leaves it alone and never pushes there. {slug} works from its computer as usual; its "
            "history is not backed up to GitHub. If that repository is this company's, point the bot at it in "
            "Settings > Bots; if it is another company's, connect a GitHub organization of your own.",
            409,
        )

    def product_repo_capability(self):
        """Report the live installation capability; a saved setup choice is not proof of permission."""
        row = self.row()
        if not row:
            return {"status": "not_connected", "detail": "Connect the GitHub App before creating a product repository."}
        try:
            installation = self.installation()
        except (Problem, ValueError):
            return {
                "status": "unknown",
                "detail": "Could not verify the GitHub App installation. No repository has been created.",
            }
        if not installation:
            return {"status": "not_installed", "detail": "Install the GitHub App on the connected organization first."}
        permissions = self.live_permissions(refresh=True)
        if permissions is None:
            return {
                "status": "unknown",
                "detail": "Could not verify the installation's current Administration permission. No repository has been created.",
            }
        if permissions.get("administration") != "write":
            url = links(row)["permissions_url"]
            return {
                "status": "missing",
                "detail": (
                    "The GitHub App installation is missing Administration: write, so it cannot create repositories. "
                    f"The Owner changes this once on GitHub: set Administration to Read and write on {url}, then accept "
                    f"the new permission for the {'account' if account_kind(row) == PERSONAL else 'organization'} when "
                    "GitHub asks. No repository has been created."
                ),
            }
        if account_kind(row) == PERSONAL and not self.user_authorized():
            return {
                "status": "missing",
                "detail": f"Tico is not authorized to create repositories in {row['org']} yet. Authorize Tico in Tools > GitHub first. No repository has been created.",
            }
        return {"status": "available", "detail": "The connected installation currently has Administration: write."}

    def prepare_product_repo(self, org):
        """Verify creation capability and mint its token before an operation is durably bound."""
        row = self.row()
        if not row:
            raise Problem("github_not_connected", "Connect the GitHub App first", 409)
        if org.lower() != row["org"].lower():
            raise Problem("github_repo", "The preview organization no longer matches the connected organization", 409)
        capability = self.product_repo_capability()
        if capability["status"] != "available":
            code = {
                "missing": "github_permission_missing",
                "not_connected": "github_not_connected",
                "not_installed": "github_not_installed",
                "not_available": "github_account",
            }.get(capability["status"], "github_capability_unknown")
            raise Problem(code, capability["detail"], 409)
        try:
            if account_kind(row) == PERSONAL:
                token = self.user_token()  # the owner's user token makes the empty repository in their account
            else:
                token = self.mint(None, PRODUCT_CREATE_PERMISSIONS)[0]
        except (Problem, ValueError) as problem:
            if getattr(problem, "code", "") == "github_user_token":
                raise
            # GitHub error bodies are not an appropriate place to echo an installation or connection secret.
            raise Problem(
                "github_token_unavailable",
                "Could not obtain the narrowly scoped GitHub App token. Check the current Administration permission and installation; no repository was created.",
                409,
            ) from None
        return row["org"], token

    def create_product_repo(self, org, name, token, personal=False):
        """Create one exact, empty private repository. Never changes bot grants or repo selection. In a personal account it is
        made with the owner's user token (POST /user/repos), as a bot's empty repository is."""
        body = {"name": name, "private": True, "auto_init": False, "description": "Tico product repository"}
        try:
            if personal:
                response = self._user_create(token, body)
            else:
                response = self._call(
                    "POST", f"/orgs/{org}/repos", headers={"Authorization": "Bearer " + token}, json=body
                )
        except Problem as problem:
            if problem.code == "github_unreachable":
                raise Problem(
                    "github_create_outcome_unknown",
                    f"GitHub's create response for {org}/{name} was not received. Tico will not repeat this operation automatically; check the organization before taking another action.",
                    409,
                ) from None
            raise
        if response.status_code in (408, 429) or response.status_code >= 500:
            raise Problem(
                "github_create_outcome_unknown",
                f"GitHub returned HTTP {response.status_code} for the create request for {org}/{name}. The result may be unknown; Tico will not repeat this operation automatically.",
                409,
            )
        if response.status_code == 422:
            raise Problem(
                "github_repo_exists",
                f"{org}/{name} already exists or GitHub rejected that name; nothing was changed",
                409,
            )
        if response.status_code == 403:
            raise Problem(
                "github_create_forbidden",
                "GitHub refused repository creation. Check the app's current Administration: write permission and organization installation access; no repository was changed by Tico.",
                403,
            )
        if response.status_code >= 300:
            raise Problem(
                "github_create_failed",
                f"GitHub would not create {org}/{name} (HTTP {response.status_code}); no credentials or response body were exposed",
                409,
            )

        repository = f"{org}/{name}"
        try:
            self.mint([repository], {"metadata": "read"}, diagnose=False)
            installation_access = "available"
            note = "Created as a private empty repository. Tico will refresh its repository inventory; no bot access was granted."
            try:
                from .repositories import queue_sync

                queue_sync(self, refresh=True)
            except Exception:
                log.warning("Product repository was created; repository inventory refresh could not be queued")
                note = "Created as a private empty repository. Refresh repository inventory in Settings; no bot access was granted."
        except (Problem, ValueError) as problem:
            if isinstance(problem, Problem) and problem.code == "github_repo_not_accessible":
                installation_access = "owner_action_required"
                note = "Created as a private empty repository, but the selected GitHub App installation cannot access it yet. An Owner must add it in GitHub App installation settings; Tico did not change repository access."
            else:
                installation_access = "unverified"
                note = "Created as a private empty repository. Tico could not verify installation access; an Owner can check the app installation and refresh repository inventory. No bot access was granted."
        return {
            "repository": repository,
            "html_url": f"https://github.com/{repository}",
            "visibility": "private",
            "auto_init": False,
            "installation_access": installation_access,
            "note": note,
        }


class Repo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(min_length=1, max_length=64)
    template: str | None = Field(default=None, max_length=140)
    empty: bool = False


class ProductRepoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org: str = Field(min_length=1, max_length=39)
    name: str = Field(min_length=1, max_length=100)
    visibility: Literal["private"]
    auto_init: Literal[False]
    confirmed: Literal[True]


class ExtraRepos(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repositories: list[str] = Field(max_length=MAX_EXTRA_REPOS)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot: str = Field(min_length=1, max_length=100)
    repository: str | None = None
    # This selects cache headroom only; grants and permissions are identical for both purposes.
    purpose: Literal["turn", "git"] = "turn"


def _said(response):
    """GitHub's own words for a refusal, when it gave any."""
    try:
        return str(response.json().get("message") or "")[:200]
    except (ValueError, AttributeError):
        return ""


def rate_limited(response):
    """A 403 can be a temporary limit, so classify it before access diagnostics discard its headers and message."""
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    headers = getattr(response, "headers", {})
    return (
        headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in headers
        or any(word in _said(response).lower() for word in ("rate limit", "abuse"))
    )


def manifest(settings, name, administration, personal=False):
    public = settings.public_url
    permissions = {**TURN_READS, **TURN_PERMISSIONS}  # a write a bot needs wins over the same read
    permissions.update(CHECKS_PERMISSIONS)  # the server's own tico/merge-ok check; no bot's token carries it
    permissions.update(MERGE_GROUP_EVENT_PERMISSIONS)  # what GitHub requires for the `merge_group` event below
    # Tico itself runs a repository's release workflow when the Release Owner asks (item_sources/github_release.py),
    # with a server token naming Actions: write alone. A bot's turn still reads Actions only (TURN_READS, mint_turn),
    # unless an Owner lets that bot run workflows (`run_workflows`).
    permissions["actions"] = "write"
    if administration:
        permissions["administration"] = "write"
    body = {
        "name": name[:34],
        "url": public,
        "redirect_url": public + "/api/v2/github/app/callback",
        "setup_url": public + "/api/v2/github/app/installed",
        "hook_attributes": {"url": public + "/api/v2/github/webhook", "active": True},
        "public": False,
        "default_permissions": permissions,
        "default_events": [
            "pull_request",
            "pull_request_review",
            "pull_request_review_comment",
            "check_run",
            "check_suite",
            "workflow_run",
            "status",
            "push",
            "release",
            "merge_group",  # the merge queue asks for tico/merge-ok on its group's commit (github.py `merge_group`)
        ],
    }
    if personal:
        # A personal account's App takes the owner's authorization for the empty repositories Tico creates there: the
        # callback that receives it, asked for at install as well.
        body["callback_urls"] = [user_callback(settings)]
        body["request_oauth_on_install"] = True
    return body


def extra_repos(c, bot):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (EXTRA_KEY,)).fetchone()
    try:
        value = json.loads(row[0]).get(bot, []) if row else []
    except (ValueError, AttributeError):
        value = []
    return [r for r in value if isinstance(r, str)]


def save_extra_repos(c, bot, repos):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (EXTRA_KEY,)).fetchone()
    every = json.loads(row[0]) if row else {}
    if repos:
        every[bot] = repos
    else:
        every.pop(bot, None)
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (EXTRA_KEY, json.dumps(every, sort_keys=True)),
    )


def install_github_app(app, settings, store):
    service = app.state.github_app = GitHubApp(settings, store, getattr(app.state, "vault", None))
    store.github_app = service  # the Check health step reads the App's health for the Health page (no network)

    from . import repositories as R

    R.install(app, store, service)
    from .github_reviews import ReviewerApp, install_reviews

    reviewer = app.state.github_reviewer_app = ReviewerApp(settings, store, service)
    install_reviews(app, store, service, reviewer)

    def owner(request):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner connects GitHub", 403)
        return who

    def product_creator(request):
        """The Owner, or a bot's own run with Create product repositories (`can_create_product_repositories`: every
        bot unless switched off for it).
        Connecting or disconnecting the App stays the Owner's (`owner`). Never a carried identity: a bot's turn acting
        for the Owner (BotOps' on-behalf-of, the Assistant) or an external agent would create without any bot's right,
        name rule, grant or notice."""
        who = request.state.identity
        if who.task_actor or who.agent:
            raise Problem(
                "forbidden",
                "A bot's turn acting for someone, or an external agent, does not create product "
                "repositories. The Owner creates one in Tico, or a bot with the Create product repositories "
                "right in its own turn.",
                403,
            )
        if who.role == "owner":
            return who
        if who.role == "bot":
            from .repositories import can_create_product_repositories

            with store.read() as c:
                validate_identity(c, who)
                if can_create_product_repositories(c, H.actor_id(who.actor)):
                    return who
        raise Problem(
            "forbidden",
            "Creating a product repository needs the Owner, or a bot with the Create product "
            "repositories right. Every bot has it unless the Owner or an admin switched it off; only the "
            "Owner switches it back on, in the bot's settings under Repositories.",
            403,
        )

    def bot_product_name(c, who, name):
        """A bot never creates a name a bot repository uses or would use: write to it would reach that bot's Instructions."""
        if who.role != "bot":
            return
        lowered = name.lower()
        row = service.row(c)
        taken = {
            str(repo_of(r[0], row["org"] if row else "") or "").split("/")[-1].lower()
            for r in c.execute("SELECT repo FROM bot_config")
        }
        if lowered.startswith(("bot-", "emp-")) or lowered in taken:
            raise Problem(
                "forbidden",
                f"{name} is a bot repository name. Bots create product repositories only; a new "
                "bot's repository is made with `tico bot repo-create`",
                403,
            )

    def manager(request, bot):
        """The owner, or whoever manages this bot (`Auth.bot_manager`): the extra repositories are the bot's settings."""
        who = request.state.identity
        if who.role != "owner":
            with store.read() as c:
                if not (app.state.auth.bot_manager(c, who, bot) or app.state.auth.botops_manages(c, who, bot)):
                    raise Problem(
                        "forbidden", "Only the owner, or someone who manages this bot, sets its repositories", 403
                    )
        return who

    def run_scope(c, attempt_id):
        """The other bots whose repositories one BotOps run writes, and whose rights decided it (for the audit): every
        bot but the built-in ones in its own run (team rule `botops_manages_bots`), the bots the person manages in a
        run carrying their rights, none in a run a bot asked for or one that lends nobody's rights."""
        from . import botops_act, team_rules

        auth = app.state.auth
        acting = app.state.botops_run_identity(c, attempt_id)
        run = {"attempt": attempt_id, "rights": acting.actor if acting else None}
        if acting is None:
            return set(), run
        bots = [
            r[0]
            for r in c.execute(
                "SELECT bc.bot FROM bot_config bc JOIN bots b ON b.id=bc.bot WHERE b.state<>'archived' AND bc.bot<>?",
                (BOTOPS,),
            )
        ]
        if acting.actor == "bot:" + BOTOPS:
            run["own_run"] = bool(team_rules.load(c)["botops_manages_bots"] and botops_act.own_run(c, auth, attempt_id))
            return ({bot for bot in bots if not auth.system_bot(bot)} if run["own_run"] else set()), run
        if H.is_human(acting.actor):
            return {bot for bot in bots if auth.bot_manager(c, acting, bot)}, run
        return set(), run

    def botops_extra_repos(c, who):
        """The bots whose repositories BotOps' token also writes, and the runs that decided it. A turn asks for its own
        run; the computer's git helper gets only what every BotOps run in progress on it may have, and nothing extra
        when none is."""
        if who.role == "bot":
            attempts = [who.attempt_id] if who.actor == "bot:" + BOTOPS and not who.via and who.attempt_id else []
        elif who.role == "runner":
            attempts = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM attempts WHERE bot_id=? AND runner_id=? AND state IN ('leased','running')",
                    (BOTOPS, who.runner_id),
                )
            ]
        else:
            attempts = []
        found = [run_scope(c, attempt) for attempt in attempts]
        scopes = [scope for scope, _ in found]
        return (set.intersection(*scopes) if scopes else set()), [run for _, run in found]

    def app_name(name=""):
        """The name the App is made with: the one asked for, else "<team name> Tico"."""
        return (name or f"{settings.company_name} Tico").strip()[:34]  # GitHub's limit, as manifest() sends it

    def status_view(refresh=False):
        row = service.row()
        if not row:
            # The form says the name GitHub will propose; with no team name set there is none worth proposing.
            return {"connected": False, "app_name": "" if settings.company_name == DEFAULT_COMPANY_NAME else app_name()}
        administration = service.can_create_repos(refresh=refresh)
        personal = account_kind(row) == PERSONAL
        authorized = service.user_authorized() if personal else False
        from . import bot_repo_maker

        with store.read() as c:
            fix = fixes(c, row)
            made = bot_repo_maker.progress(c, service, settings)
        page = links(row)
        return {
            "connected": True,
            "checks_withheld": None if service.checks_write_accepted() else CHECKS_WITHHELD,
            "slug": row["slug"],
            "org": row["org"],
            "account_type": account_kind(row),
            "app_id": row["app_id"],
            "administration": administration,
            "installed": bool(row["installation_id"]),
            "authorized": authorized,
            "callback_url": user_callback(settings) if personal and not service.callback_in_manifest() else "",
            "install_url": page["install_url"],
            "settings_url": page["settings_url"],
            "permissions_url": fix["permissions_url"],
            "create_url": fix["create_url"],
            "uninstall_url": page["uninstall_url"],
            "bot_repositories": made,
        }

    @app.get("/api/v2/github/app")
    def status(request: Request):
        owner(request)
        return status_view()

    @app.post("/api/v2/github/app/check-permissions")
    def check_permissions(request: Request):
        """After the owner accepts Administration on GitHub: read the installation's permissions again."""
        owner(request)
        service.live.pop("turn-reads", None)
        for raised in RAISES:
            service.live.pop(raised.refused_key, None)
        return status_view(refresh=True)

    @app.get("/api/v2/github/app/manifest")
    def manifest_form(request: Request, org: str = "", name: str = "", administration: bool = True, html: bool = False):
        """`org` is the GitHub account or organization the App is made under: GitHub says which it is, and the setup
        page is GitHub's for that kind (a personal account's or an organization's). No `org` is a personal account:
        GitHub makes the App for whoever is signed in there, and `save` keeps that login, so nothing is looked up."""
        who = owner(request)
        org = org.strip()
        if org and not ORG.match(org):
            raise Problem(
                "github_org", "Enter the GitHub account or organization's name (letters, digits and hyphens)", 422
            )
        if service.row():
            raise Problem("github_connected", "GitHub is already connected; disconnect it first to start over", 409)
        # Before GitHub makes an app: one it could never be saved for (the key that seals other secrets is lost) is refused
        # here. This GET never drops anything; the owner's confirmed POST replace-lost-key does.
        with store.read() as c:
            if service.key_lost(c):
                raise Problem("github_key_missing", LOST_KEY, 409)
        kind = service.account_type(org) if org else PERSONAL
        if kind == PERSONAL:
            # Always asked for a personal account, so one acceptance covers repository creation once it is built.
            administration = True
        nonce = secrets.token_urlsafe(24)
        with store.transaction() as c:
            c.execute("DELETE FROM github_app_states WHERE created_at<?", (time.time() - STATE_TTL,))
            c.execute(
                "INSERT INTO github_app_states(nonce,actor,org,administration,created_at,account_type) VALUES(?,?,?,?,?,?)",
                (nonce, who.actor, org, int(administration), time.time(), kind),
            )
        body = manifest(settings, app_name(name), administration, personal=kind == PERSONAL)
        base = (
            "https://github.com/settings/apps/new"
            if kind == PERSONAL
            else f"https://github.com/organizations/{org}/settings/apps/new"
        )
        action = f"{base}?state={nonce}"
        if html:
            page = (
                "<!doctype html><meta charset=utf-8>" + brand_icons.HEAD + "<title>Connecting GitHub</title>"
                f"<form id=f method=post action='{action}'><input type=hidden name=manifest value='"
                + json.dumps(body).replace("&", "&amp;").replace("'", "&#39;")
                + "'></form>"
                "<script>document.getElementById('f').submit()</script>"
            )
            return HTMLResponse(page, headers={"Cache-Control": "no-store"})
        return {"action": action, "manifest": body, "state": nonce}

    @app.post("/api/v2/github/app/replace-lost-key")
    def replace_lost_key(request: Request):
        """The owner confirmed it: github-app.key is lost with no copy, so drop the GitHub App and directory sync
        secrets only it opened. Not proposable: a person clicks it themselves."""
        who = owner(request)
        with store.transaction() as c:
            return {"replaced": service.replace_lost_key(c, who.actor)}

    def install_page(actor, slug):
        """GitHub's install page for the App, with a fresh single-use state made for this person. The install's return
        carries it back to /authorized, and only a state Tico made is taken."""
        nonce = secrets.token_urlsafe(24)
        with store.transaction() as c:
            c.execute("DELETE FROM github_user_states WHERE created_at<?", (time.time() - STATE_TTL,))
            c.execute(
                "INSERT INTO github_user_states(nonce,actor,created_at) VALUES(?,?,?)", (nonce, actor, time.time())
            )
        return f"https://github.com/apps/{quote(str(slug), safe='')}/installations/new?" + urlencode({"state": nonce})

    def exchange_and_save(actor, row, code):
        """The App's conversion: GitHub's code becomes the App's secrets, and the connect is recorded. Returns the conversion."""
        conversion = service.exchange(code)
        with store.transaction() as c:
            service.save(c, actor, row["org"], row["administration"], conversion, row["account_type"])
            saved = service.row(c)
            H.event(
                c,
                actor,
                "github.app_connected",
                conversion["slug"],
                {"org": saved["org"], "account_type": saved["account_type"], "app_id": conversion["id"]},
            )
        return conversion

    def live_state(c, state):
        """The unexpired setup row for this state, or None: a state Tico made, not yet used."""
        row = c.execute("SELECT * FROM github_app_states WHERE nonce=?", (state,)).fetchone()
        if not row or time.time() - row["created_at"] > STATE_TTL or not secrets.compare_digest(row["nonce"], state):
            return None
        return row

    def invalid_state():
        return Problem(
            "github_state", "This GitHub setup link is not valid or has expired; start again from Tools", 400
        )

    def stale_return(request):
        """GitHub's return with a state that is used up or too old: a second return, Back to GitHub's Create page, or a
        slow one. A browser lands on Tools > GitHub, which says which in one sentence (`again` when the App is already
        saved: nothing was changed, and a second App GitHub made on the way is never saved here); an agent gets the
        refusal."""
        if "text/html" not in request.headers.get("accept", ""):
            raise invalid_state()
        word = "again" if service.row() else "expired"
        return RedirectResponse("/#/tools/github?github=" + word, status_code=302)

    def park(request, code, state):
        """GitHub's return with no owner session of this state: the code is kept on the state's row, unexchanged, for the
        owner to finish. The first code parked wins. The state is not used up here: `finish` takes it."""
        with store.transaction() as c:
            row = live_state(c, state)
            if row and not code:
                raise Problem("github_exchange", "GitHub did not send a setup code", 400)
            if row:
                c.execute(
                    "UPDATE github_app_states SET code=?, returned_at=? WHERE nonce=? AND (code IS NULL OR code='')",
                    (code, time.time(), state),
                )
        if not row:
            return stale_return(request)
        if "text/html" in request.headers.get("accept", ""):
            sign_in = _sign_in_path(getattr(app.state, "auth", None))
            if sign_in:
                # The sign-in comes back to this same return with the state alone (the code stays parked, out of the
                # sign-in's URLs), and the owner's session then finishes it from the parked code.
                back = CALLBACK_PATH + "?" + urlencode({"state": state})
                return RedirectResponse(sign_in + "?" + urlencode({"next": back}), status_code=302)
            return RedirectResponse("/#/tools/github?github=finish", status_code=302)
        return {"parked": True, "finish": FINISH_PATH}

    @app.get(CALLBACK_PATH)
    def callback(request: Request, code: str = "", state: str = ""):
        who = getattr(request.state, "identity", None)
        with store.transaction() as c:
            row = c.execute("SELECT * FROM github_app_states WHERE nonce=?", (state,)).fetchone()
            mine = row is not None and who is not None and who.role == "owner" and row["actor"] == who.actor
            # Single use, and only in the session that started it.
            if mine:
                c.execute("DELETE FROM github_app_states WHERE nonce=?", (state,))
        if who is None or row is None or not mine:
            return park(request, code, state)
        if time.time() - row["created_at"] > STATE_TTL or not secrets.compare_digest(row["nonce"], state):
            return stale_return(request)
        code = code or row["code"] or ""  # back from the sign-in with the state alone: the code parked before it
        if not code:
            raise Problem("github_exchange", "GitHub did not send a setup code", 400)
        conversion = exchange_and_save(who.actor, row, code)
        # The install's return to the authorize callback carries this state back; the callback takes only a state Tico made.
        return RedirectResponse(install_page(who.actor, conversion["slug"]), status_code=302)

    class FinishBody(BaseModel):
        state: str = Field("", max_length=200)

    @app.post(FINISH_PATH)
    def finish(request: Request, body: FinishBody):
        """The owner's agent finishes a setup GitHub returned with no owner session: the parked code is exchanged here,
        once, for the owner. Returns the install page, as the callback's redirect would."""
        who = owner(request)
        with store.transaction() as c:
            row = live_state(c, body.state)
            if not row or row["actor"] != who.actor:
                raise invalid_state()
            if not row["code"]:
                raise Problem("github_not_returned", "GitHub has not sent this setup back yet", 409)
            c.execute("DELETE FROM github_app_states WHERE nonce=?", (body.state,))
        conversion = exchange_and_save(who.actor, row, row["code"])
        return {"install_url": install_page(who.actor, conversion["slug"])}

    @app.get("/api/v2/github/app/authorize-link")
    def authorize_link(request: Request):
        """The GitHub page where the owner authorizes Tico to create repositories in the personal account. The single-use state
        is bound to this session; GitHub sends the owner back to /authorized with it."""
        who = owner(request)
        row = service.row()
        if not row:
            raise Problem("github_not_connected", "Connect the GitHub App first", 409)
        if account_kind(row) != PERSONAL:
            raise Problem("github_account", "Only a personal account is authorized this way", 409)
        nonce = secrets.token_urlsafe(24)
        with store.transaction() as c:
            c.execute("DELETE FROM github_user_states WHERE created_at<?", (time.time() - STATE_TTL,))
            c.execute(
                "INSERT INTO github_user_states(nonce,actor,created_at) VALUES(?,?,?)", (nonce, who.actor, time.time())
            )
        query = urlencode({"client_id": row["client_id"], "redirect_uri": user_callback(settings), "state": nonce})
        return {"url": f"{WEB}/login/oauth/authorize?{query}"}

    @app.get("/api/v2/github/app/install")
    def install_link(request: Request):
        """The App's install page on GitHub. Every Install link in the UI comes here, so the install's return carries a
        state Tico made for this session, the way the Authorize link's does."""
        who = owner(request)
        row = service.row()
        if not row:
            raise Problem("github_not_connected", "Connect the GitHub App first", 409)
        return RedirectResponse(install_page(who.actor, row["slug"]), status_code=302)

    @app.get("/api/v2/github/app/authorized")
    def authorized(
        request: Request,
        code: str = "",
        state: str = "",
        installation_id: str = "",
        setup_action: str = "",
        error: str = "",
    ):
        """Where GitHub sends the owner after they authorize Tico for the personal account, or after an install. Every return
        must carry the single-use state Tico made for this session (the Authorize link or the install link), unused and
        fresh, except an install's return with no state: that one is taken only for an installation on the owner's own
        account, and with a code only when the token is the owner's too (installed_without_state). An install's return runs
        the install step first, and an installation_id must be the App's own installation. A return without a code (the
        owner declined, or the install asked for no authorization) goes back to Tools after that. An error with no state
        (the owner declined on GitHub's page) goes back to Tools as well, with nothing changed."""
        who = owner(request)
        if error and not state:
            return RedirectResponse("/#/tools/github?github=declined", status_code=302)
        if installation_id and not state:
            return installed_without_state(who, installation_id, code)
        with store.transaction() as c:
            found = c.execute("SELECT * FROM github_user_states WHERE nonce=?", (state,)).fetchone() if state else None
            if found:
                c.execute("DELETE FROM github_user_states WHERE nonce=?", (state,))
        if (
            not found
            or found["actor"] != who.actor
            or time.time() - found["created_at"] > STATE_TTL
            or not secrets.compare_digest(found["nonce"], state)
        ):
            raise Problem(
                "github_state",
                "This GitHub authorization link is not valid or has expired; start again from Tools",
                400,
            )
        row = service.row()
        if not row or account_kind(row) != PERSONAL:
            raise Problem("github_not_connected", "Connect the personal GitHub account first", 409)
        if installation_id or setup_action:
            installation = installed_now(who.actor)
            if installation_id and str(installation or "") != installation_id:
                raise Problem(
                    "github_installation", "That installation is not this GitHub App's; start again from Tools", 409
                )
        if error:
            return RedirectResponse("/#/tools/github?github=declined", status_code=302)
        if not code:
            if installation_id or setup_action:
                # An install that asked the owner for no authorization: the install step ran, and Tools goes on from here.
                return RedirectResponse("/#/tools/github?github=installed", status_code=302)
            raise Problem("github_exchange", "GitHub did not send an authorization code", 400)
        tokens = service.exchange_user(code)
        login, github_id = service.user_identity(tokens["access_token"])
        with store.read_transaction() as c:
            expected = service.owner_id(c)
        if login.lower() != row["org"].lower() or (expected and github_id != expected):
            service.revoke_user_token(tokens["access_token"])
            raise Problem(
                "github_user_login",
                f"Sign in to GitHub as {row['org']} to authorize Tico for that account (this sign-in was {login})",
                403,
            )
        return saved_authorization(who.actor, row, login, github_id, tokens)

    def installed_now(actor):
        """The install step: refresh the App's installation and sync its repositories. Returns the installation's id, or None
        when there is none yet. Runs on the setup redirect and on an install's return to the authorize callback."""
        found = service.installation(refresh=True)
        failed = None
        if found:
            try:
                R.sync(service)
            except Problem as exc:
                log.warning("Repository sync failed: %s", exc.code)
                failed = str(exc.code)
        with store.transaction() as c:
            # The install stands when its first repository list fails; the code says why Tools shows none yet.
            H.event(
                c,
                actor,
                "github.app_installed",
                "",
                {"installed": bool(found), **({"sync_failed": failed} if failed else {})},
            )
        return found

    def saved_authorization(actor, row, login, github_id, tokens):
        """Keeps the owner's authorization for the account, under the lock a refresh takes. Returns Tools' redirect."""
        with service.user_lock:  # the lock a refresh takes: no refresh runs against the pair this replaces
            with store.transaction() as c:
                service.save_user_token(c, actor, login, github_id, tokens)
                H.event(c, actor, "github.user_authorized", row["slug"], {"login": login})
        return RedirectResponse("/#/tools/github?github=authorized", status_code=302)

    def installed_without_state(who, installation_id: str, code: str):
        """An install's return with no state, which GitHub may send without the one Tico made. The installation must be this
        App's and sit on the owner's own account: GitHub answers for this App's installations alone, so the account id is the
        check. A code (the install may ask for the authorization too) is exchanged only then, and its token is kept only when
        GET /user gives the owner's id as well; any other token is revoked and never saved. A code that comes with a state
        never comes here."""
        row = service.row()
        if not row or account_kind(row) != PERSONAL:
            raise Problem("github_not_connected", "Connect the personal GitHub account first", 409)
        with store.read_transaction() as c:
            expected = service.owner_id(c)
        account = service.installation_account_id(int(installation_id)) if installation_id.isdigit() else None
        if not expected or account != expected:
            raise Problem(
                "github_installation", "That installation is not this account's GitHub App; start again from Tools", 409
            )
        installed_now(who.actor)  # the install step does not need the code, so it runs before any token is exchanged
        if not code:
            return RedirectResponse("/#/tools/github?github=installed", status_code=302)
        tokens = service.exchange_user(code)
        login, github_id = service.user_identity(tokens["access_token"])
        if github_id != expected:
            service.revoke_user_token(tokens["access_token"])
            raise Problem(
                "github_user_login",
                f"Sign in to GitHub as {row['org']} to authorize Tico for that account (this sign-in was {login})",
                409,
            )
        return saved_authorization(who.actor, row, login, github_id, tokens)

    @app.get("/api/v2/github/app/installed")
    def installed(request: Request):
        who = owner(request)
        found = installed_now(who.actor)
        return RedirectResponse("/#/tools/github?github=" + ("connected" if found else "pending"), status_code=302)

    @app.post("/api/v2/github/app/disconnect")
    def disconnect(request: Request):
        who = owner(request)
        service.revoke_user_token()  # GitHub's copy of the owner's authorization, while the App's secrets can still sign
        with store.transaction() as c:
            row = service.row(c)
            service.forget(c)
            H.event(c, who.actor, "github.app_disconnected", row["slug"] if row else "", {})
        return {"ok": True, "note": "Forgotten here. Uninstall or delete the app on GitHub to revoke it there."}

    @app.post("/api/v2/github/token")
    def token(request: Request, body: TokenRequest):
        who = request.state.identity
        with store.read_transaction() as c:
            validate_identity(c, who)
            body.bot = (H.resolve_bot(c, body.bot) or {}).get("id", body.bot)
            # A turn may ask only for its own bot; a runner only for a bot assigned to it.
            if who.role == "bot":
                allowed = H.actor_id(who.actor) == body.bot
            elif who.role == "runner":
                allowed = (
                    c.execute(
                        "SELECT 1 FROM assignments WHERE bot=? AND runner_id=?", (body.bot, who.runner_id)
                    ).fetchone()
                    is not None
                )
            else:
                allowed = False
            from .shared_bots import declared, source_of

            bot_config = declared(c, body.bot) if allowed else {}
            row = service.row(c)
            if not row:
                return {"configured": False}
            if not allowed:
                raise Problem("forbidden", "This credential does not run that bot", 403)
            source = source_of(bot_config) or body.bot
            config = c.execute("SELECT repo FROM bot_config WHERE bot=?", (source,)).fetchone()
            # A bare emp-<slug> means the connected org, whatever the default owner is.
            repo = repo_of(config["repo"] if config else "", row["org"] or settings.github_owner)
            if not repo or repo.split("/")[0].lower() != row["org"].lower():
                raise Problem(
                    "forbidden", f"That bot's repository is not in the connected organization ({row['org']})", 403
                )
            reads = R.Reads(c, [body.bot])
            grants = R.access(c, body.bot, row['org'], reads=reads)['effective']
            missing = reads.missing
            # Its own repository only when this company may use it: one the App cannot reach (installed on chosen
            # repositories) or another company's is left out, and the bot works from its computer (R.home).
            homes = R.Homes(c)
            own_unused = homes.why(source, repo)
            if own_unused:
                grants = [g for g in grants if g['full_name'].lower() != repo.lower()]
            extra, runs = botops_extra_repos(c, who) if body.bot == BOTOPS else (set(), [])
            if extra:
                # The server's token cache is keyed on the exact repository set, so a narrower run never gets this one.
                have = {g['full_name'].lower() for g in grants}
                for other in c.execute("SELECT bot,repo FROM bot_config ORDER BY bot"):
                    name = repo_of(other["repo"] or "", row["org"])
                    if (
                        other["bot"] in extra
                        and name
                        and name.lower() not in have
                        and name.split("/")[0].lower() == row["org"].lower()
                        and name.lower() not in missing
                        and not homes.why(other["bot"], name)
                    ):
                        have.add(name.lower())
                        grants.append({'full_name': name, 'access': 'write'})
            if who.attempt_id and task_privacy.private_meeting_run(c, who.attempt_id):
                # A private meeting's turn reads its repositories and pushes to none (task_privacy.PRIVATE_MEETING_WRITES).
                grants = [{'full_name': g['full_name'], 'access': 'read'} for g in grants]
            if own_unused and not grants:
                # Nothing on GitHub for this bot: no token, and no refusal for its computer to report every turn.
                return {
                    "configured": True,
                    "token": "",
                    "expires_at": None,
                    "repository": "",
                    "repositories": [],
                    "tokens": [],
                    "hold": body.bot != BOTOPS,
                    "history": R.HOME_WORDS[own_unused],
                }
        issuer = reviewer.for_bot(body.bot)
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
        if issuer is reviewer:
            # A reviewer's everyday credential may write its own memory repository only. Product approvals
            # use the server-side review endpoint; the runner cannot author product commits as that App.
            read_repos += [name for name in write_repos if name.lower() != repo.lower()]
            write_repos = [name for name in write_repos if name.lower() == repo.lower()]
        # Each flagged write grant wants the raises its flags name; those the installation lacks are withheld up front.
        wants = {}
        for g in grants:
            if g['access'] == 'write':
                if asked := tuple(r for r in RAISES if g.get(r.flag)):
                    wants[g['full_name'].lower()] = asked
        withheld = {}

        def withhold(raised, names, reason):
            entry = withheld.setdefault(raised, {"repositories": [], "reason": reason})
            entry["repositories"] = sorted(set(entry["repositories"]) | set(names))
            entry["reason"] = reason

        flagged = [name for name in write_repos if name.lower() in wants]
        if flagged:
            live = issuer.live_permissions()
            for raised in RAISES:
                asking = [name for name in flagged if raised in wants[name.lower()]]
                if not asking:
                    continue
                if live is not None and issuer is service:
                    # Accepted clears the setting warning at once, whatever the mint below does; whether GitHub
                    # refuses the token is learned there.
                    R.workflow_health(service, live.get(raised.permission) == "write", refused=None, flag=raised.flag)
                if (live or {}).get(raised.permission) != "write":
                    withhold(raised, asking, raised.withheld if live is not None else raised.unknown)
                    for name in asking:
                        wants[name.lower()] = tuple(r for r in wants[name.lower()] if r is not raised)
        # One token per set of raises: a repository is in exactly one write token.
        groups = {}
        for name in flagged:
            if wants[name.lower()]:
                groups.setdefault(wants[name.lower()], []).append(name)
        write_repos = [name for name in write_repos if not wants.get(name.lower())]
        raised_repos = []
        tokens = []
        refresh_margin = GIT_CALL_REFRESH_MARGIN if body.purpose == "git" else TURN_START_REFRESH_MARGIN

        def raised_token(names, wanted):
            """`names` minted with TURN_PERMISSIONS raised by each of `wanted`, dropping (and saying why) each raise
            GitHub refuses, down to the ordinary write token: (mint_turn's answer, the raises it carries)."""
            # A refusal seen within LIVE_TTL falls back at once: each git call asks for a token, and would otherwise
            # repeat three refused mints and two permission reads.
            for raised in [r for r in wanted if issuer.raise_refused(r)]:
                withhold(raised, names, raised.refused)
                wanted.remove(raised)
            kw = dict(own=repo, refresh_margin=refresh_margin, include_cache_status=True, missing=missing)
            while wanted:
                permissions = {**TURN_PERMISSIONS, **{r.permission: "write" for r in wanted}}
                try:
                    return issuer.mint_turn(names, permissions, narrow=False, **kw), wanted
                except Problem as problem:
                    if problem.code != "github_app_permissions":
                        raise
                # Either a raise went away within LIVE_TTL, or a read (Checks, Commit statuses) the installation
                # lacks: GitHub says which now.
                live = issuer.live_permissions(refresh=True) or {}
                if lacking := [r for r in wanted if live.get(r.permission) != "write"]:
                    for raised in lacking:
                        withhold(raised, names, raised.withheld)
                    wanted = [r for r in wanted if r not in lacking]
                    continue
                with issuer.lock:
                    narrowed = issuer.live.get("turn-reads")
                try:
                    return issuer.mint_turn(names, permissions, **kw), wanted
                except Problem as again:
                    if again.code != "github_app_permissions":
                        raise
                # The installation reports every raise yet refuses the token: not the setting Health names. The
                # reads the retry narrowed away on the way come back for the other tokens, unless the webhook or
                # Check permissions reset them meanwhile.
                with issuer.lock:
                    current = issuer.live.get("turn-reads")
                    if current is not narrowed and current and current[1] == {}:
                        if narrowed:
                            issuer.live["turn-reads"] = narrowed
                        else:
                            issuer.live.pop("turn-reads", None)
                refused = wanted[-1:]
                if len(wanted) > 1:
                    # Asked together, GitHub does not say which raise it refuses: ask each alone, without the reads
                    # (the retry above refused it even without them), and drop the ones refused alone (the last, if
                    # each is accepted alone).
                    refused = []
                    for raised in wanted:
                        try:
                            issuer.mint_reachable(names, {**TURN_PERMISSIONS, raised.permission: "write"}, **kw)
                        except Problem as alone:
                            if alone.code != "github_app_permissions":
                                raise
                            refused.append(raised)
                    refused = refused or wanted[-1:]
                with issuer.lock:
                    for raised in refused:
                        issuer.live[raised.refused_key] = time.time() + LIVE_TTL
                for raised in refused:
                    withhold(raised, names, raised.refused)
                wanted = [r for r in wanted if r not in refused]
            return issuer.mint_turn(names, TURN_PERMISSIONS, **kw), []

        try:
            # GitHub permissions are token-wide. Never put read grants in a write token.
            if write_repos:
                value, expires, write_repos, cached, reachability_changed = issuer.mint_turn(
                    write_repos,
                    TURN_PERMISSIONS,
                    # Only a lone own scope is diagnosed (and refused) here: beside read grants or raised write
                    # grants, an unreachable own repository must not take their tokens down (a reviewer whose
                    # bot repository is missing still reads the product repository); `not tokens` below refuses
                    # when nothing was minted at all.
                    diagnose_empty=write_repos == [repo] and not read_repos and not groups,
                    own=repo,
                    refresh_margin=refresh_margin,
                    include_cache_status=True,
                    missing=missing,
                )
                if value:
                    tokens.append(
                        {
                            'token': value,
                            'expires_at': expires,
                            'repositories': write_repos,
                            'access': 'write',
                            'cached': cached,
                            'reachability_changed': reachability_changed,
                        }
                    )
            for wanted, names in sorted(groups.items(), key=lambda item: [RAISES.index(r) for r in item[0]]):
                (run_value, run_expires, names, cached, reachability_changed), carried = raised_token(
                    names, list(wanted)
                )
                if issuer is service:
                    for raised in wanted:
                        if raised in carried:
                            R.workflow_health(service, True, flag=raised.flag)
                        else:
                            reason = withheld[raised]["reason"]
                            R.workflow_health(
                                service, reason != raised.withheld, refused=reason == raised.refused, flag=raised.flag
                            )
                raised_repos += names
                if run_value:
                    tokens.append(
                        {
                            'token': run_value,
                            'expires_at': run_expires,
                            'repositories': names,
                            'access': 'write',
                            **{r.permission: 'write' for r in carried},
                            'cached': cached,
                            'reachability_changed': reachability_changed,
                        }
                    )
            if read_repos:
                read_value, read_expires, read_repos, cached, reachability_changed = issuer.mint_turn(
                    read_repos,
                    READ_PERMISSIONS,
                    refresh_margin=refresh_margin,
                    include_cache_status=True,
                    missing=missing,
                )
                if read_value:
                    tokens.append(
                        {
                            'token': read_value,
                            'expires_at': read_expires,
                            'repositories': read_repos,
                            'access': 'read',
                            'cached': cached,
                            'reachability_changed': reachability_changed,
                        }
                    )
            if not tokens:
                raise Problem(
                    'github_repo_not_accessible', f'{repo} is not reachable; check the GitHub App installation', 409
                )
            repos = write_repos + raised_repos + read_repos
            value, expires = tokens[0]['token'], tokens[0]['expires_at']
        except Problem as problem:
            if issuer is service and problem.code not in BOT_SCOPED:
                note_github_token(store, problem.detail, "Open Tools and check the GitHub connection.")
            raise
        newly_minted = [item for item in tokens if not item["cached"]]
        if issuer is service and (newly_minted or any(item["reachability_changed"] for item in tokens)):
            R.repository_health(service)
        if newly_minted:
            for item in newly_minted:
                if issuer.should_audit_token(body.bot, item["token"]):
                    with store.transaction() as c:
                        # Whose rights shaped a BotOps token: its own run's, a person's, or none beyond its own.
                        H.event(
                            c,
                            who.actor,
                            "github.token",
                            body.bot,
                            {
                                "repository": repo,
                                "repositories": item["repositories"],
                                "attempt": who.attempt_id or None,
                                **({"identity": "reviewer"} if issuer is reviewer else {}),
                                **({"runs": runs} if body.bot == BOTOPS else {}),
                            },
                        )
        response_tokens = [{key: value for key, value in item.items() if key != "cached"} for item in tokens]
        # A BotOps answer is shaped by the runs live right now (botops_extra_repos): a runner must not hand it
        # to a later run, which may be allowed less.
        return {
            "configured": True,
            "token": value,
            "expires_at": expires,
            # The runner publishes the bot's history to this one (git_credentials.publish_history): none when withheld.
            "repository": "" if own_unused else repo,
            "repositories": repos,
            **({"history": R.HOME_WORDS[own_unused]} if own_unused else {}),
            "tokens": response_tokens,
            "hold": body.bot != BOTOPS,
            **{raised.answer: withheld[raised] for raised in RAISES if raised in withheld},
        }

    def bot_repos(c, bot):
        if not c.execute("SELECT 1 FROM bot_config WHERE bot=?", (bot,)).fetchone():
            raise Problem("not_found", "No such bot", 404)
        row = service.row(c)
        own = repo_of(
            (c.execute("SELECT repo FROM bot_config WHERE bot=?", (bot,)).fetchone() or {"repo": ""})["repo"],
            settings.github_owner or (row["org"] if row else ""),
        )
        return row, own

    @app.get("/api/v2/bots/{bot}/github-repos")
    def extra_get(request: Request, bot: str):
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        manager(request, bot)
        with store.read() as c:
            row, own = bot_repos(c, bot)
            return {
                "connected": bool(row),
                "org": row["org"] if row else "",
                "repository": own or "",
                "repositories": [
                    r["full_name"]
                    for r in R.access(c, bot, row["org"] if row else settings.github_owner)["chosen"]
                    if r["access"] == "write"
                ],
            }

    @app.put("/api/v2/bots/{bot}/github-repos")
    def extra_put(request: Request, bot: str, body: ExtraRepos):
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        who = manager(request, bot)
        with store.transaction() as c:
            row, own = bot_repos(c, bot)
            if not row:
                raise Problem("github_not_connected", "Connect GitHub first (Tools)", 409)
            wanted = []
            for value in body.repositories:
                repo = repo_of(value, row["org"])
                if not repo or repo.split("/")[0].lower() != row["org"].lower():
                    raise Problem(
                        "github_repo",
                        f"'{value}' is not a repository in {row['org']}; extra repositories "
                        "must be in the connected account or organization",
                        422,
                    )
                if (own or "").lower() != repo.lower() and repo.lower() not in [w.lower() for w in wanted]:
                    wanted.append(repo)
            before = R.access(c, bot, row['org'])
            old = {r['full_name'].lower() for r in before['chosen'] if r['access'] == 'write'}
            if {r.lower() for r in wanted} == old:
                return {"connected": True, "org": row['org'], "repository": own or '', "repositories": wanted}
            if before['mode'] != 'chosen':
                raise Problem('github_repo', 'This bot uses repository access; change it in Settings', 409)
            R.set_access(
                c,
                bot,
                R.RepoAccessUpdate(
                    mode='chosen',
                    chosen=[R.Grant(**r) for r in before['chosen']]
                    + [
                        R.Grant(full_name=r)
                        for r in wanted
                        if r.lower() not in {g['full_name'].lower() for g in before['chosen']}
                    ],
                ),
                row['org'],
                who.actor,
                legacy=True,
                team_list=app.state.auth.bot_admin(who),
            )
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
                    raise Problem(
                        "forbidden",
                        "Only the owner, BotOps or a bot granted repository creation may "
                        "create bot repositories. Ask BotOps, or ask an Owner or admin to enable "
                        "Create bot repositories in this bot's settings.",
                        403,
                    )
                row, bot = service.row(c), H.resolve_bot(c, slug)
            if not row:
                raise Problem("github_not_connected", "GitHub is not connected. Connect it in Tools first.", 409)
            if not bot or bot.get("state") == "archived":
                raise Problem(
                    "forbidden", f"{slug} is not a bot being set up or running, so no repository is created for it", 403
                )
            if body.slug != slug and body.slug != "emp-" + slug:
                raise Problem("forbidden", "Bots create only bot-<slug> for a bot", 403)
            if (
                body.template
                and body.template != DEFAULT_TEMPLATE
                and body.template.split("/")[0].lower() != row["org"].lower()
            ):
                raise Problem(
                    "forbidden",
                    "Bots generate from the default template or one in the connected account or organization",
                    403,
                )
        template = body.template or DEFAULT_TEMPLATE
        if body.empty and body.template:
            raise Problem("github_repo", "An empty repository has no template; give one or the other", 422)
        if not SLUG.match(slug) or not re.match(r"^[\w.-]+/[\w.-]+$", template):
            raise Problem(
                "github_repo", "Use a lowercase bot name (letters, digits, hyphens) and an owner/name template", 422
            )
        from . import built_on

        # BotOps builds in its turn's workspace: the bot's first placement goes there (backend/built_on.py). Only
        # BotOps's own build is recorded, and only once GitHub made the repository or waits on the permission.
        builder = who.runner_id if who.role == "bot" and H.actor_id(who.actor) == BOTOPS else ""
        try:
            result = service.create_repo(slug, template, empty=body.empty)
        except Problem as problem:
            if problem.code not in (
                "github_permission_missing",
                "github_user_token",
                "github_selected_repos",
                "github_repo_taken",
            ):
                raise
            # A person must act: the refusal names both fixes. The bot keeps its local build; BotOps asks again
            # once either is done. With the App on chosen repositories, or the name another company's, nobody need
            # act: the bot keeps its local build, and its first placement is still where it was built.
            if builder:
                with store.transaction() as c:
                    built_on.remember(c, (H.resolve_bot(c, slug) or {}).get("id", slug), builder)
            raise
        with store.transaction() as c:
            if builder:
                built_on.remember(c, (H.resolve_bot(c, slug) or {}).get("id", slug), builder)
            if not result.get("existing"):
                H.event(
                    c,
                    who.actor,
                    "github.repo_created",
                    result["repository"],
                    {"empty": True} if body.empty else {"template": template},
                )
        return result

    def bot_created(c, who, result):
        """A bot made it: the bot gets write to it (Repositories > Chosen) and the Owner a notice. In the receipt's
        transaction, so a replay neither grants nor tells twice; a refused grant is said, never a lost receipt."""
        from . import repositories as R

        bot, repository = H.actor_id(who.actor), result["repository"]
        org = repository.split("/")[0]
        bot_row = H.bot(c, bot) or {}
        name = bot_row.get("display_name") or bot_row.get("slug") or bot
        moved = None
        c.execute("SAVEPOINT product_repo_grant")
        try:
            # Granting needs it on the team list; a bot set to every ticked repository then reaches it too.
            c.execute(
                "INSERT INTO repositories(id,full_name,enabled,added_by,updated_at) VALUES(?,?,1,?,?) "
                "ON CONFLICT(full_name) DO UPDATE SET enabled=1",
                (uuid.uuid4().hex, repository, who.actor, H.now()),
            )
            before = R.access(c, bot, org)
            if before["mode"] == "all" and before["all_access"] == "write":
                pass  # it writes every ticked repository, this one now too
            else:
                if before["mode"] == "chosen":
                    keep = [R.Grant(**r) for r in before["chosen"]]
                elif before["mode"] == "all":
                    # All ticked repos has one level for every repository, so a read-only bot moves to Chosen
                    # repos: read on each repository it reaches now, write on the new one. Nothing gets wider.
                    # Its own repository is the one write in what it reaches; it stays write whatever the mode.
                    keep = [
                        R.Grant(full_name=r["full_name"], access="read")
                        for r in before["effective"]
                        if r["access"] == "read"
                    ]
                    moved = len([g for g in keep if g.full_name.lower() != repository.lower()])
                else:
                    keep = []
                keep = [g for g in keep if g.full_name.lower() != repository.lower()]
                R.set_access(
                    c,
                    bot,
                    R.RepoAccessUpdate(mode="chosen", chosen=keep + [R.Grant(full_name=repository)]),
                    org,
                    who.actor,
                    own_click=False,
                )
            access = "write"
            c.execute("RELEASE product_repo_grant")
        except Problem as problem:
            c.execute("ROLLBACK TO product_repo_grant")
            c.execute("RELEASE product_repo_grant")
            log.warning("A bot's product repository was created, but its access could not be granted: %s", problem.code)
            access, moved = "none", None
        others = []
        if access != "none":
            for r in c.execute(
                f"SELECT bc.bot,{BS.CONFIG} AS config,b.slug FROM bot_config bc {BS.JOIN} JOIN bots b ON b.id=bc.bot "
                "WHERE b.state<>'archived' AND bc.bot<>? ORDER BY bc.bot",
                (bot,),
            ).fetchall():
                if json.loads(r["config"] or "{}").get("repo_access_mode") == "all":
                    others.append((H.bot(c, r["bot"]) or {}).get("display_name") or r["slug"])
        said = {
            "write": f"{name} has write access to it.",
            "none": f"{name} could not be given access to it; an Owner or admin can give it in Settings > "
            "Repositories.",
        }[access]
        if moved is not None:
            kept = f"read on the {moved} repositor{'y' if moved == 1 else 'ies'} it reached and " if moved else ""
            said += (
                f" It was on All ticked repos with read access, so it is now on Chosen repos: {kept}write on this "
                "one. A repository ticked later reaches it only once chosen."
            )
        if others:
            said += f" Bots set to every ticked repository reach it too: {', '.join(others)}."
        if result["installation_access"] == "owner_action_required":
            said += (
                " The GitHub App is installed on selected repositories only and cannot reach it yet: an Owner "
                "adds it in the app's installation settings on GitHub."
            )
        owner_id = app.state.auth.owner_id(c)
        if owner_id:
            H.notice(
                c,
                H.KEEPER,
                H.human_actor(owner_id),
                f"{name} created private repo {repository.split('/')[1]} ({repository}). {said}",
                refs={"repository": repository, "created_by": who.actor},
            )
        return {
            **result,
            "bot_access": access,
            "note": "Created as a private empty repository. " + said,
            "_notified": bool(owner_id),
            "_moved": moved is not None,
        }

    @app.get("/api/v2/github/product-repos/preview")
    def product_repo_preview(request: Request, name: str):
        who = product_creator(request)
        name = validate_product_repo_name(name)
        with store.read() as c:
            validate_identity(c, who)
            bot_product_name(c, who, name)
        row = service.row()
        if not row:
            raise Problem("github_not_connected", "Connect the GitHub App before creating a product repository", 409)
        capability = service.product_repo_capability()
        return {
            "org": row["org"],
            "name": name,
            "repository": f"{row['org']}/{name}",
            "visibility": "private",
            "auto_init": False,
            "capability": capability["status"],
            "capability_detail": capability["detail"],
        }

    @app.post("/api/v2/github/product-repos")
    def create_product_repo(request: Request, body: ProductRepoCreate):
        who = product_creator(request)
        name = validate_product_repo_name(body.name)
        if body.visibility != "private" or body.auto_init is not False or body.confirmed is not True:
            raise Problem(
                "github_repo_confirmation",
                "Confirm the reviewed private, empty repository preview before creating it",
                422,
            )
        key = request.headers.get("idempotency-key")
        if not key or len(key) > 200:
            raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
        operation = "/api/v2/github/product-repos"
        request_body = body.model_dump()
        request_hash = digest(encode(request_body))

        def existing_operation(c, *, bind=False):
            """Replay a durable result, reject a reused key, or bind it before the GitHub POST. The binding is a row of
            `idempotency` (binding 'github_product_repo', subject org/name), retained: the retention sweep never takes it."""
            previous = c.execute(
                "SELECT * FROM idempotency WHERE binding='github_product_repo' AND actor=? AND operation=? AND key=?",
                (who.actor, operation, key),
            ).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise Problem("idempotency_conflict", "This key was used for different content", 409)
                if previous["state"] == "pending":
                    raise Problem(
                        "github_create_outcome_unknown",
                        f"The result for {previous['subject']} is unresolved. Tico will not repeat this operation automatically; check GitHub before taking another action.",
                        409,
                    )
                if previous["state"] == "refused":
                    raise Problem(previous["error_code"], previous["error_detail"], previous["error_status"])
                return json.loads(previous["response"])

            if bind:
                stamp = H.now()
                c.execute(
                    "INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at,updated_at,"
                    "binding,subject,state) VALUES(?,?,?,?,NULL,?,?,'github_product_repo',?,'pending')",
                    (who.actor, operation, key, request_hash, stamp, stamp, f"{body.org}/{name}"),
                )
            return None

        with service.product_repo_create_lock:
            # Authenticate and replay completed/uncertain operations before consulting the current
            # GitHub connection, so disconnecting the App cannot erase a durable receipt or binding.
            with store.transaction() as c:
                validate_identity(c, who)
                previous = existing_operation(c)
                if previous is not None:
                    return previous

            row = service.row()
            if not row:
                raise Problem(
                    "github_not_connected", "Connect the GitHub App before creating a product repository", 409
                )
            if body.org.lower() != row["org"].lower():
                raise Problem(
                    "github_repo",
                    f"The confirmed {account_word(row)} does not match the currently connected {account_word(row)}",
                    409,
                )

            # Capability checks and token minting are safe to retry. Bind the exact Owner operation
            # only after those succeed, and commit that binding before the side-effecting GitHub POST.
            with store.read() as c:
                bot_product_name(c, who, name)
            org, token = service.prepare_product_repo(row["org"])
            with store.transaction() as c:
                validate_identity(c, who)
                previous = existing_operation(c, bind=True)
                if previous is not None:
                    return previous

            try:
                result = service.create_product_repo(org, name, token, personal=account_kind(row) == PERSONAL)
            except Problem as problem:
                if problem.code != "github_create_outcome_unknown":
                    # A definite GitHub rejection is replayable. If this write fails, the operation
                    # remains pending and future requests fail closed instead of issuing another POST.
                    with store.transaction() as c:
                        c.execute(
                            "UPDATE idempotency "
                            "SET state='refused',error_code=?,error_detail=?,error_status=?,updated_at=? "
                            "WHERE binding='github_product_repo' AND actor=? AND operation=? AND key=? "
                            "AND request_hash=? AND state='pending'",
                            (
                                problem.code,
                                problem.detail,
                                problem.status,
                                H.now(),
                                who.actor,
                                operation,
                                key,
                                request_hash,
                            ),
                        )
                raise

            # The external result and durable receipt share one transaction. A crash or database
            # error here leaves the pre-call pending binding; a retry reports unknown and never
            # creates another repository or adopts an existing one.
            try:
                with store.transaction() as c:
                    validate_identity(c, who)
                    current = c.execute(
                        "SELECT state,request_hash,response FROM idempotency "
                        "WHERE binding='github_product_repo' AND actor=? AND operation=? AND key=?",
                        (who.actor, operation, key),
                    ).fetchone()
                    if not current or current["request_hash"] != request_hash:
                        raise Problem("idempotency_conflict", "The product-repository operation binding changed", 409)
                    if current["state"] == "completed":
                        return json.loads(current["response"])
                    if current["state"] != "pending":
                        raise Problem(
                            "github_create_outcome_unknown",
                            "The product-repository operation is no longer pending; no second create was attempted",
                            409,
                        )
                    if who.role == "bot":
                        result = bot_created(c, who, result)
                    H.event(
                        c,
                        who.actor,
                        "github.product_repo_created",
                        result["repository"],
                        {
                            "visibility": "private",
                            "auto_init": False,
                            "installation_access": result["installation_access"],
                            **(
                                {
                                    "bot_access": result["bot_access"],
                                    "owner_notified": result.pop("_notified"),
                                    "moved_to_chosen": result.pop("_moved"),
                                }
                                if who.role == "bot"
                                else {}
                            ),
                        },
                    )
                    c.execute(
                        "UPDATE idempotency SET state='completed',response=?,updated_at=? "
                        "WHERE binding='github_product_repo' AND actor=? AND operation=? AND key=? "
                        "AND state='pending'",
                        (encode(result), H.now(), who.actor, operation, key),
                    )
            except Problem:
                raise
            except Exception:
                log.error(
                    "Product repository was created, but Tico could not save its receipt; its operation remains unresolved"
                )
                raise Problem(
                    "github_create_outcome_unknown",
                    f"GitHub created or may have created {org}/{name}, but Tico could not save the receipt. Tico will not repeat this operation automatically; check GitHub before taking another action.",
                    409,
                ) from None
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
