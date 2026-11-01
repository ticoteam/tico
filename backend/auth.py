"""No ambient owner identity: all requests carry verified human or machine credentials."""

import hashlib
import hmac
import json
import logging
import re
import sqlite3
import stat
from dataclasses import dataclass, replace
from http.cookies import SimpleCookie, CookieError

import yaml

from . import bot_access as A
from . import identity_proxy
from . import people as P
from . import rooms
from . import service_keys
from . import team_rules
from . import read_cache
from . import report_managers
from .store import H, Problem, digest

# Loopback sign-in for an environment with no identity proxy in front of it.
# Cookies are shared across ports on one host, so each install names its own; the bare name is
# the one earlier versions set, still read so a sign-in survives the upgrade.
LOCAL_COOKIE = "tico_local_session"
LOCAL_COOKIE_DAYS = 30
LOCAL_SIGNIN_PATH = "/api/v2/local-signin"
LOGOUT_PATH = "/api/v2/logout"


def cookies(headers):
    jar = SimpleCookie()
    try:
        jar.load(headers.get("cookie", ""))
    except CookieError:
        return {}
    return {name: morsel.value for name, morsel in jar.items()}


TASK_PREFIX_MIN = 8


def _edit_distance(a, b, limit):
    """Levenshtein distance when it is at most `limit`, else None (gives up as soon as it must exceed it)."""
    if abs(len(a) - len(b)) > limit:
        return None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(row) > limit:
            return None
        previous = row
    return previous[-1] if previous[-1] <= limit else None


OPERATOR_SQL = "SELECT operator FROM bot_config WHERE bot=?"


@dataclass(frozen=True)
class Identity:
    actor: str
    # owner, human, bot, runner, or service: another system's key (backend/service_keys.py), actor
    # "service:<key id>", its label in token_label, which reaches one route and nothing else.
    role: str
    email: str = ""
    runner_id: str = ""
    attempt_id: str = ""
    # The harness id when this bot is run by an external agent (a Hermes profile) with its
    # own standing credential rather than a runner's per-turn lease (backend/agents.py).
    agent: str = ""
    # A person acting through a personal API token (backend/personal_tokens.py) rather than a
    # browser sign-in. The same person with the same rights, except that a token cannot make
    # or revoke tokens.
    via_token: bool = False
    # That token's label ("grok-bot", "muse"): which assistant is acting for the person, said
    # where it matters, e.g. on the message a batch sends a bot (backend/batch.py).
    token_label: str = ""
    # That token's id, recorded on a service key it makes so the key ends with it (backend/service_keys.py).
    token_id: str = ""
    # A browser session vouched for by the identity proxy in front of the server (Cloudflare Access,
    # the AWS load balancer), the only kind that /api/v2/logout can end; the UI offers "Sign out" for it.
    via_proxy: bool = False
    # "assistant" when the Assistant is acting for this person (backend/assistant.py): either a
    # turn of the assistant bot in the person's own Assistant room, which may only do what a person
    # may do and propose the rest, or (confirmed) the person's own click on a proposal it made.
    via: str = ""
    confirmed: bool = False
    # Acting for a human does not grant a bot membership in that human's private tasks.
    task_actor: str = ""
    # A service key's scope (backend/service_keys.py ROUTES): "tasks" or "update"; empty for everyone else.
    scope: str = ""
    # Tico support in a session a person opened (backend/support_access.py): "app" or "full". Its actor is
    # human:tico-support, its session id in token_id, and `support_access.gate` decides what it reaches.
    support: str = ""


def touch(c, sql, args):
    """A last_used hint, written only if the database is free right now: on a pooled read connection (outside the
    writer gate) the 30 s busy timeout would otherwise hold every sign-in, SSE re-auth included, behind a writer."""
    try:
        wait = c.execute("PRAGMA busy_timeout").fetchone()[0]
        c.execute("PRAGMA busy_timeout=0")
        try:
            c.execute(sql, args)
        finally:
            c.execute(f"PRAGMA busy_timeout={int(wait)}")
    except sqlite3.Error:
        pass


def standing(c, pid):
    """"left" when marked as left on the roster (backend/app.py person_update, which also ended their tokens),
    "off" when an owner or admin turned their sign-in off (Settings > Humans), else ""."""
    row = read_cache.metadata(c, "people")
    if not row:
        return ""
    try:
        people = json.loads(row[0]).get("people") or []
    except (ValueError, AttributeError):
        return ""
    for p in people:
        if isinstance(p, dict) and p.get("id") == pid:
            return "left" if p.get("hidden") else "off" if p.get("sign_in") is False else ""
    return ""


def validate_identity(c, who):
    if who.support:
        # No roster row: the session it came in with must still be live.
        from . import support_access
        support_access.check_live(c, who)
        return
    if who.task_actor:
        validate_identity(c, Identity(who.task_actor, 'bot', runner_id=who.runner_id,
                                      attempt_id=who.attempt_id, agent=who.agent))
    if who.role == "service":
        # Again under a write's lock: a key revoked since the request began writes nothing.
        row = c.execute("SELECT made_with_token FROM service_keys WHERE id=? AND revoked_at IS NULL",
                        (H.actor_id(who.actor),)).fetchone()
        if not row or service_keys.token_ended(c, row["made_with_token"]):
            raise Problem("identity", "Invalid credential", 401)
        return
    if who.role in ("owner", "human"):
        if not H.human(c, H.actor_id(who.actor)):
            raise Problem("identity", "Unknown person", 401)
        barred = standing(c, H.actor_id(who.actor))
        # The owner can always sign in: nobody turns the owner's sign-in off (backend/access.py edit_person).
        if barred == "left":
            raise Problem("identity", "This person has left the company", 403)
        if barred == "off" and who.role != "owner":
            raise Problem("identity", "Sign-in is off for this account. Ask an admin to turn it on", 403)
        return
    if who.role == "bot" and who.agent:
        record = c.execute("SELECT revoked_at FROM agents WHERE bot=?", (H.actor_id(who.actor),)).fetchone()
        if not record or record["revoked_at"]:
            raise Problem("revoked", "Agent credential is revoked or unknown", 401)
        state = (H.bot(c, H.actor_id(who.actor)) or {}).get("state")
        if state == "archived":
            # A stable code the connector recognises; `bot` lets Health say the agent is still reporting in.
            raise Problem("bot_archived", "This bot is archived. Restore it in Settings > Bots, or revoke its "
                          "credential", 409, extra={"bot": H.actor_id(who.actor)})
        if state != "active":
            raise Problem("paused", "This bot is " + str(state or "unknown") + ", not active; "
                          "a person changes that in Settings", 409)
        return
    runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL",
                       (who.runner_id,)).fetchone()
    if not runner:
        raise Problem("revoked", "Runner credential is revoked or unknown", 401)
    if who.role == "bot":
        row = c.execute("SELECT a.*, x.runner_id AS assigned_runner, x.generation AS assigned_generation "
                        "FROM attempts a JOIN assignments x ON x.bot=a.bot WHERE a.id=?",
                        (who.attempt_id,)).fetchone()
        if (not row or row["state"] not in ("leased", "running") or row["lease_until"] <= H.now()
                or row["assigned_runner"] != who.runner_id
                or row["assigned_generation"] != row["generation"]
                or H.bot(c, row["bot"])["state"] != "active"):
            raise Problem("stale_lease", "Execution ownership expired; stop work and reconnect", 409)


def above(parents, me, slug):
    """Whether the bot `me` is above `slug` in the `reports_to` chain; `parents` is {bot: reports_to}."""
    seen, parent = {slug}, parents.get(slug, "")
    while parent and parent not in seen:
        if parent == me:
            return True
        seen.add(parent)
        parent = parents.get(parent, "")
    return False


def _own_ask_to_a_person(who, row):
    """A bot's own question to a person: reachable from whichever run it is in.

    A turn is granted the conversations its attempt was handed, which for a scheduled routine is
    that routine's task and nothing else. So a routine had no way to ask a person anything --
    `hub question ask ben` came back "outside the current execution context" -- and the bots that run
    entirely on routines fell back to `hub task create --owner ben`. That put questions on a
    person's task list in a shape nobody wanted, and made them clear the human-item lint first:
    a bot could spend several attempts getting one title past it, to file something
    that was a question the whole time.

    An ask conversation is two participants, one of them human, and no task. Granting a bot its
    own exposes no chat history -- that is the `chat` kind, and it stays scoped to the run.
    """
    if row.get("kind") != "ask" or row.get("task_id"):
        return False
    participants = row.get("participants") or []
    people = [p for p in participants if H.is_human(p)]
    return len(participants) == 2 and len(people) == 1 and who.actor in participants


class Auth:
    def __init__(self, store):
        self.store = store
        self.settings = store.settings
        acl_path = self.settings.registry_dir / "hub-access.yaml"
        self.acl = yaml.safe_load(acl_path.read_text()) if acl_path.exists() else {}
        self.bot_admins = set()
        self.allowed_emails, self.allowed_domains = set(), set()
        self.retired_emails = set()
        self.owner_email = self.settings.owner_email
        self._owner_id = ""
        self._access_seen = None
        self._credential_followed = None
        self.proxy = identity_proxy.build(self.settings, store)

    def sync_access(self, c):
        """Adopt the stored owner and access list. One indexed read per request keeps a transfer
        or a list change effective at once, with no restart."""
        from . import row_clock
        rows = row_clock.access_rows(c)
        if rows == self._access_seen:
            return
        from . import access
        owner = access.load_owner(c, self.settings)
        lists = access.load_access(c, self.settings)
        self._access_seen = rows
        self.owner_email = owner["email"]
        self._owner_id = ""
        self.bot_admins = set(lists["admins"])
        self.allowed_emails, self.allowed_domains = set(lists["allowed"]), set(lists["allowed_domains"])
        self.retired_emails = set(access.retired_emails(c))
        # Everything that still reads the settings sees the owner in force.
        previous, self.settings.owner_email = self.settings.owner_email, self.owner_email
        # Unless the server names its own list (TICO_CREDENTIAL_ADMINS), the credential administrators are the owner
        # and the Admins; the owner's rule "Admins store credentials" off leaves the vault to the owner alone.
        if getattr(self.settings, "credential_admins_follow_owner", False):
            expected = self._credential_followed if self._credential_followed is not None else (
                (previous,) if previous else ())
            if tuple(self.settings.credential_admins) == expected:
                admins = sorted(lists["admins"]) if team_rules.load(c)["admin_credentials"] else []
                self._credential_followed = tuple(dict.fromkeys(e for e in (self.owner_email, *admins) if e))
                self.settings.credential_admins = self._credential_followed

    def admits(self, email):
        email = str(email or "").strip().lower()
        return bool(email and "@" in email and (email in self.allowed_emails
                                                 or email.rsplit("@", 1)[1] in self.allowed_domains))

    def warm(self):
        """Fetch the proxy's signing keys once at startup, so no person's request waits for them."""
        from . import owner_link
        if owner_link.break_glass(self.settings):
            try:
                # The hosted owner's break-glass link, first: an issuer that is down must not keep it out.
                owner_link.place_first(self.settings, self.store)
            except Exception:
                logging.getLogger("tico.auth").exception("the owner sign-in link could not be put in place")
        try:
            if self.proxy:
                self.proxy.warm()
        except Exception:
            pass                        # the first sign-in fetches them instead
        from . import people_mirror
        people_mirror.start(self)       # a Tico account sign-in keeps its install's list on the account (no-op otherwise)

    def owner_id(self, c):
        """The roster id of this environment's owner. One process serves one company, so the
        lookup is kept once it succeeds; an empty roster is retried on the next request."""
        if not self._owner_id and self.owner_email:
            row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (self.owner_email,)).fetchone()
            self._owner_id = row["id"] if row else ""
        return self._owner_id

    def sign_in_name(self, headers):
        """The display name the identity proxy vouched for on this request, or "" (a proxy that sends none)."""
        name = getattr(self.proxy, "display_name", None)
        try:
            return name(headers) if name else ""
        except Problem:
            return ""

    def owner_identity(self, c):
        pid = self.owner_id(c)
        if not pid:
            raise Problem("identity", "The owner of this environment (" + (self.owner_email or "unset")
                          + ") is not on the people roster", 403)
        return Identity("human:" + pid, "owner", email=self.owner_email)

    def local_token(self):
        """The loopback session secret, or "" when local sign-in is not configured."""
        path = self.settings.local_owner_token_file
        if not path:
            return ""
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            token = path.read_text().strip()
        except OSError:
            raise Problem("identity", "The local sign-in token file is missing or unreadable", 500) from None
        if mode & 0o077:
            raise Problem("identity", "The local sign-in token file must be readable only by its owner (chmod 600)", 500)
        return token

    def local_owner(self, token):
        """Whether this bearer token or cookie is the loopback owner session secret."""
        expected = self.local_token()
        return bool(expected and token and hmac.compare_digest(expected, str(token)))

    def local_cookie(self):
        """This install's own session cookie name: the bare name plus a short hash of its environment id,
        public URL and local secret, so two installs on one browser never share or clear each other's."""
        try:
            secret = self.local_token()
        except Problem:
            secret = ""
        seed = "|".join((self.settings.environment_id, self.settings.public_url, secret))
        return LOCAL_COOKIE + "_" + hashlib.sha256(seed.encode()).hexdigest()[:8]

    def local_session(self, jar):
        """The local owner secret a browser sent (a cookie mapping), under this install's name or the old one."""
        return next((v for v in (jar.get(self.local_cookie(), ""), jar.get(LOCAL_COOKIE, "")) if self.local_owner(v)), "")

    def identity_from_local_owner_token(self, c, token):
        """Admit a configured local-only bearer when no identity proxy is in use.

        For self-hosted / loopback installs without an identity proxy: set
        TICO_LOCAL_OWNER_EMAIL and TICO_LOCAL_OWNER_TOKEN (at least 32 characters).
        The bearer maps to the roster human with that email; role is owner only when
        the email matches this environment's owner.
        """
        if self.settings.proxy_kind:
            return None
        email = (self.settings.local_owner_email or "").strip().lower()
        expected = self.settings.local_owner_token or ""
        if not email or len(expected) < 32:
            return None
        if not token or len(token) != len(expected) or not hmac.compare_digest(expected, token):
            return None
        row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone()
        if not row:
            raise Problem("identity", "This account is not on the " + self.settings.app_name
                          + " roster", 403)
        role = "owner" if email and email == self.owner_email else "human"
        return Identity("human:" + row["id"], role, email=email)

    def identity_from_personal_token(self, c, token):
        """The person behind a personal API token, or None when no live token has this hash.

        A revoked or expired token is refused exactly like an unknown one. The lookup answers
        with the same identity the browser sign-in builds for that person, marked `via_token`.
        """
        row = c.execute("SELECT t.id,t.label,t.last_used,t.expires_at,t.revoked_at,h.id AS human,h.email "
                        "FROM human_tokens t JOIN humans h ON h.id=t.human WHERE t.token_hash=?",
                        (digest(token),)).fetchone()
        if not row or row["revoked_at"] or (row["expires_at"] and row["expires_at"] <= H.now()):
            return None
        now = H.now()
        # last_used is a hint for the token list, not part of the decision: a script in a loop
        # should not write a row per request, and a locked database must not refuse a read.
        if not row["last_used"] or row["last_used"] < H.shift(now, seconds=-60):
            touch(c, "UPDATE human_tokens SET last_used=? WHERE id=?", (now, row["id"]))
        email = str(row["email"] or "").lower()
        role = "owner" if email and email == self.owner_email else "human"
        return Identity("human:" + row["human"], role, email=email, via_token=True,
                        token_label=str(row["label"] or ""), token_id=row["id"])

    def identity_from_service_key(self, c, token, path, method):
        """Another system's service key (backend/service_keys.py), on the routes of its scope.

        It is no person and no bot: anywhere else it is refused here, before any route could take
        its unfamiliar role for one with more reach. A revoked key is refused like an unknown one."""
        row = c.execute("SELECT id,label,last_used,revoked_at,scope,made_with_token FROM service_keys WHERE key_hash=?",
                        (digest(token),)).fetchone()
        if not row or row["revoked_at"] or service_keys.token_ended(c, row["made_with_token"]):
            raise Problem("identity", "Invalid credential", 401)
        if not service_keys.allowed(row["scope"], method, path):
            raise Problem("forbidden", service_keys.refusal(row["scope"]), 403)
        now = H.now()
        if not row["last_used"] or row["last_used"] < H.shift(now, seconds=-60):
            touch(c, "UPDATE service_keys SET last_used=? WHERE id=?", (now, row["id"]))
        return Identity("service:" + row["id"], "service", token_label=str(row["label"]), scope=row["scope"] or "tasks")

    def assistant_principal(self, c, attempt):
        """The person an assistant chat turn acts for, as that person and nobody more, or None.

        Only a turn the person started themselves in their own Assistant room counts: the message that queued the job
        came from the room's owner through /api/v2/assistant/messages (the only door that sets the `assistant`
        reference). Any other turn of the assistant bot (a Slack route, a meeting delivery, a routine) stays the bot's
        own.

        Everyone's chat is the Assistant, the owner's too (backend/assistant.py): a Strategy Bot turn, even one queued
        in the owner's earlier Strategy Bot chat (`rooms.STRATEGY_ROOM`), is the bot's own and never the owner's."""
        # Only a live lease: a token kept from a finished turn means nothing (validate_identity says the
        # same for the plain bot identity; this repeats it where the token turns into a person).
        room_key = rooms.ASSISTANT_ROOM if attempt["bot"] == self.settings.assistant_bot else None
        if (not room_key or attempt["state"] not in ("leased", "running")
                or str(attempt["lease_until"] or "") <= H.now()):
            return None
        row = c.execute(
            "SELECT v.owner_actor FROM jobs j JOIN messages m ON m.id=j.message_id "
            "JOIN conversations v ON v.id=m.conversation_id WHERE j.id=? AND v.scope='personal' "
            "AND v.room_key=? AND m.from_actor=v.owner_actor AND json_extract(m.refs_json,'$.assistant')=1",
            (attempt["job_id"], room_key)).fetchone()
        if not row:
            return None
        return self.chat_person(c, row["owner_actor"], attempt["bot"], attempt["id"], attempt["runner_id"])

    def chat_person(self, c, owner_actor, bot, attempt_id="", runner_id=""):
        """The person a turn of their chat bot (`bot`: the Assistant) acts as, with their rights and nobody more, or
        None: a person who is gone or suspended, or a bot that is not the Assistant."""
        if not str(owner_actor or "").startswith("human:") or bot != self.settings.assistant_bot:
            return None
        human = H.human(c, H.actor_id(owner_actor))
        if not human or standing(c, human["id"]):
            return None
        email = str(human.get("email") or "").lower()
        owner = bool(email) and email == self.owner_email
        return Identity(owner_actor, "owner" if owner else "human",
                        email=email, via_token=True, token_label=self.settings.assistant_name,
                        via="assistant", task_actor="bot:" + bot,
                        attempt_id=attempt_id, runner_id=runner_id)

    def authenticate(self, headers, path="", method=""):
        bearer = headers.get("authorization", "")
        token = bearer[7:] if bearer.startswith("Bearer ") else ""
        if token.startswith("tico_st_") and getattr(self.proxy, "sessions", False):
            # A frontend's bearer session (backend/oidc.py): looked up like the browser's cookie.
            headers = {"cookie": self.proxy.cookie + "=" + token[len("tico_st_"):]}
            token = ""
        with self.store.read() as c:
            self.sync_access(c)
            if token in self.settings.test_identities:
                who = self.settings.test_identities[token]
            elif token and self.local_owner(token):
                who = self.owner_identity(c)
            elif token and (who := self.identity_from_local_owner_token(c, token)):
                pass
            elif token.startswith(service_keys.PREFIX):
                who = self.identity_from_service_key(c, token, path, method)
            elif token.startswith("tico_sa_"):
                from . import support_access
                who = support_access.identity(c, token, self.settings)
            elif token:
                row = c.execute("SELECT id FROM runners WHERE token_hash=? AND revoked_at IS NULL",
                                (digest(token),)).fetchone()
                agent = None if row else c.execute(
                    "SELECT bot,harness FROM agents WHERE token_hash=? AND revoked_at IS NULL",
                    (digest(token),)).fetchone()
                if row:
                    who = Identity("runner:" + row["id"], "runner", runner_id=row["id"])
                elif agent:
                    who = Identity("bot:" + agent["bot"], "bot", agent=agent["harness"])
                else:
                    attempt = c.execute("SELECT * FROM attempts WHERE token_hash=?", (digest(token),)).fetchone()
                    if attempt:
                        who = Identity("bot:" + attempt["bot"], "bot", runner_id=attempt["runner_id"],
                                       attempt_id=attempt["id"])
                        # The runner's lease calls and credential fetch stay the bot's; the
                        # assistant's tools otherwise act as the person it works for. Older
                        # runners fetch the bot's credentials before acknowledging start.
                        own_credentials = method == "GET" and path == "/api/v2/credential-runtime"
                        if path and not own_credentials and not path.startswith(("/api/v2/attempts/", "/api/v2/jobs/")):
                            validate_identity(c, who)
                            who = self.assistant_principal(c, attempt) or who
                    elif not (who := self.identity_from_personal_token(c, token)):
                        raise Problem("identity", "Invalid credential", 401)
            elif self.local_session(cookies(headers)):
                who = self.owner_identity(c)
            else:
                if self.proxy and getattr(self.proxy, "sessions", False):
                    email = self.proxy.email(headers, c)
                else:
                    email = self.proxy.email(headers) if self.proxy else None
                if email is None:
                    # The UI's fetch layer follows `sign_in` to the built-in login page.
                    extra = ({"sign_in": self.proxy.sign_in_path} if self.proxy and self.proxy.name == "oidc"
                             else {"sign_in": "/auth/owner-link"} if self.proxy and self.proxy.name == "owner-link" else None)
                    raise Problem("identity", "Sign in to " + self.settings.app_name, 401, extra=extra)
                # A signed assertion without an email (a Cloudflare service token) would otherwise
                # match a roster entry that has none, and "" is also an unset owner email.
                if not email:
                    raise Problem("identity", "This sign-in carries no email address", 401)
                row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone()
                # The owner's old address after an email change joins no one: it would be a second owner-to-be.
                if not row and email in self.retired_emails:
                    raise self.retired()
                if not row and self.admits(email):
                    row = self.join(email)
                # The people roster is the approved internal-user list; the allow list adds to it.
                if not row:
                    raise Problem("identity", "This account is not on the " + self.settings.app_name
                                  + " roster", 403)
                who = Identity("human:" + row["id"],
                               "owner" if email == self.owner_email else "human", email=email, via_proxy=True)
            validate_identity(c, who)
            if who.support:
                from . import support_access
                support_access.gate(who, path, method)
            action = headers.get("x-tico-assistant-action", "")
            if action:
                # The person's own click on something the Assistant proposed (backend/assistant.py
                # confirm), and only that: never the Assistant's own identity or a token credential, and
                # only with the secret made at confirm time, for the exact method and path stored (or a step of a bulk card), within
                # two minutes of the click.
                aid, _, secret = action.partition(".")
                from .assistant import covers
                row = c.execute("SELECT method,path,body_json,confirm_hash,running_since,proposer FROM assistant_actions "
                                "WHERE id=? AND owner=? AND status='running'", (aid, who.actor)).fetchone()
                if (who.role not in ("owner", "human") or who.via or who.via_token or not secret or not row
                        or not row["confirm_hash"] or not hmac.compare_digest(row["confirm_hash"], digest(secret))
                        or not covers(row, method, path)
                        or str(row["running_since"] or "") < H.shift(H.now(), seconds=-120)):
                    raise Problem("assistant_action", "That assistant action is not being confirmed", 403)
                # Recorded via whoever proposed it (the Assistant, or BotOps for what a person asked it for).
                who = replace(who, via=row["proposer"] if row["proposer"] in ("assistant", "botops") else "assistant",
                              confirmed=True)
            return who

    def join(self, email):
        """The allow list admitted this verified address: add them to the roster as a person. Decided again from the
        stored lists under the write lock, since an owner email change that committed after this request read them
        retires the old address and takes it off the lists. Returns the row, or None when no longer admitted."""
        from . import access
        with self.store.transaction() as c:
            row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone()
            if row:
                return {"id": row["id"]}
            if email in access.retired_emails(c):
                raise self.retired()
            if not access.admitted(access.load_access(c, self.settings), email):
                return None
            return {"id": access.join_on_sign_in(c, email)}

    def retired(self):
        return Problem("identity", "This address no longer signs in to " + self.settings.app_name
                       + ". Sign in with your new address, or ask the owner to add this one", 403)

    # ------------------------------------------------------------------ per-bot access
    # See, Read and Write are decided here and nowhere else (backend/bot_access.py has the stored
    # shape, docs/permissions.md the model). Always full, whatever the audiences say: the company
    # owner, the bot itself, a person above the bot on the org chart (`manages`), a bot
    # administrator for the bots run under their own identity (the rule that lets them change
    # the bot's settings, `SettingsAdmin._manager`), and, for a bot caller, a bot above it in
    # the `reports_to` chain. Every other credential (a runner) keeps the reach it always had.
    FULL = {"see": True, "read": True, "write": True}

    def system_bot(self, slug):
        """The built-in bots (the Assistant, BotOps, the Librarian, the Strategy Bot, the Health Monitor, the Task
        Manager, the Bookkeeper): they act for the whole company, so only the company owner changes them. Admins
        manage every other bot."""
        return slug in (self.settings.assistant_bot, "assistant", "botops", "librarian", "goal-manager", "health-monitor", "task-manager",
                        "bookkeeper", H.COUNSEL)

    def bot_manager(self, c, who, slug, operator=None):
        """Whether this person may manage the bot: the one "can manage this bot" rule (docs/permissions.md).
        The owner, an Admin, one of the bot's owners (its creator and any co-owner, and its operator),
        or a person above it on the org chart. A built-in bot is the owner's alone."""
        if who.role == "owner":
            return True
        if who.role != "human" or self.system_bot(slug):
            return False
        if self.bot_admin(who):
            return True
        row = c.execute("SELECT operator,bot_owners_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
        pid = H.actor_id(who.actor)
        if row and (row["operator"] == pid or pid in A.owner_ids(row["bot_owners_json"])):
            return True
        return self.manages(c, who, "bot", slug)

    def bot_above(self, c, who, slug):
        """Whether the caller is a bot's own run that sits above this bot in the `reports_to` chain: a manager bot,
        or one above its manager. It already has full access to the bot (`bot_accesses`); this is for the few reads
        that check the caller's kind before access, like a bot's routines. Never true for a run acting for someone."""
        if who.role != "bot" or who.via or not str(who.actor).startswith("bot:"):
            return False
        me = H.actor_id(who.actor)
        parents = {r["bot"]: r["reports_to"] or "" for r in read_cache.configs(c)}
        seen, parent = {slug}, parents.get(slug, "")
        while parent and parent not in seen:
            if parent == me:
                return True
            seen.add(parent)
            parent = parents.get(parent, "")
        return False

    def botops_manages(self, c, who, slug):
        """BotOps in its own run (the daily update, or a routine only the owner wrote: `botops_act.own_run`) manages
        every bot that is not built in: its tools, repositories, contact and Instructions, not its people, status,
        credentials or deletion (`SettingsAdmin.update_bot` keeps the definition to its contact). A run a person or
        a bot asked for keeps that requester's rights, so BotOps never lends this to anyone. The team rule
        `botops_manages_bots` turns it off."""
        if (who.actor != "bot:botops" or who.role != "bot" or who.via or self.system_bot(slug)
                or not team_rules.load(c)["botops_manages_bots"]):
            return False
        from . import botops_act
        return botops_act.own_run(c, self, who.attempt_id)

    def report_manager(self, c, who, slug, scope):
        """Whether this bot's own run manages `slug` for `scope` as its manager: `slug` is a direct report (its raw
        `reports_to` names the caller, so a report's report does not count), the caller is not `slug` itself, and it is
        not a built-in bot. `scope` is one of report_managers.SCOPES. Both switches must be on (report_managers.manages).
        Never true for a person, for BotOps acting for one, or for any delegated identity: the change is the bot's own."""
        if (scope not in report_managers.SCOPES or who.role != "bot" or who.via or who.via_token or who.support
                or not str(who.actor).startswith("bot:") or self.system_bot(slug)):
            return False
        me = H.actor_id(who.actor)
        row = c.execute("SELECT reports_to,config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
        if not row or me == slug:
            return False
        declared = json.loads(row["config_json"]) if row["config_json"] else {}
        parent = row["reports_to"] or (declared or {}).get("reports_to") or ""
        return parent == me and report_managers.manages(c, me)

    def report_lifecycle(self, c, who, slug, op, parent=""):
        """Whether BotOps, acting for a manager bot on its own ask, may `op` (create, archive or restore) `slug` as that
        manager's direct report. `who` is that acting identity: the manager's actor with `via` "botops", which only
        BotOps' ask makes (app.py `manager_asker`). The manager's own token has no `via` and never passes. For a create,
        `parent` is the new bot's `reports_to`, which must be the manager; for the others, `slug` must report to it now.
        Both switches must be on (report_managers.manages). Never for a person, for a built-in bot, or for a support session."""
        if (op not in report_managers.LIFECYCLE_OPS or who.role != "bot" or who.via != "botops" or who.via_token
                or who.support or not str(who.actor).startswith("bot:") or self.system_bot(slug)):
            return False
        me = H.actor_id(who.actor)
        if op == "create":
            if H.actor_id(parent) != me:
                return False
        else:
            row = c.execute("SELECT reports_to,config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
            if not row:
                return False
            declared = json.loads(row["config_json"]) if row["config_json"] else {}
            if H.actor_id(row["reports_to"] or declared.get("reports_to") or "") != me:
                return False
        return report_managers.manages(c, me)

    @staticmethod
    def member_bot_row(c, slug):
        """The same as `member_bot`, from the database alone (Health has no Auth): the creator's role."""
        row = c.execute("SELECT created_by FROM bot_config WHERE bot=?", (slug,)).fetchone()
        creator = H.human(c, H.actor_id(row["created_by"])) if row and str(row["created_by"] or "").startswith("human:") else None
        if not creator:
            return False
        from . import access
        email = str(creator.get("email") or "").lower()
        stored = access._load_json(c, access.ACCESS) or {}
        admins = access.emails(stored["admins"] if "admins" in stored else stored.get("bot_admins"))
        owner_row = access._load_json(c, access.OWNER) or {}
        return email not in admins and email != str(owner_row.get("email") or "").lower()

    def member_bot(self, c, slug, row=None, creators=None):
        """Whether a member (not an owner or an Admin) created this bot: those go only on computers that
        accept members' bots. A bot with no recorded creator is the company's. `row` is the bot's
        bot_config row when read already; `creators` ({created_by: answer}) keeps the answer per creator
        across many bots in one request."""
        row = row or c.execute("SELECT created_by FROM bot_config WHERE bot=?", (slug,)).fetchone()
        if not row or not row["created_by"] or not str(row["created_by"]).startswith("human:"):
            return False
        if creators is not None and row["created_by"] in creators:
            return creators[row["created_by"]]
        try:
            member = self.company_role(self.identity_for_actor(c, row["created_by"])) == "member"
        except Problem:
            member = True
        if creators is not None:
            creators[row["created_by"]] = member
        return member

    def company_role(self, who):
        """`owner`, `admin` or `member`: what the person is to the company."""
        return "owner" if who.role == "owner" else "admin" if self.bot_admin(who) else "member"

    def bot_accesses(self, c, who, slugs=None):
        """{slug: {see, read, write}} for this caller, for `slugs` (default every bot with a
        configuration). One pass, so a list answers with the same rules as a single check."""
        wanted = list(dict.fromkeys(slugs)) if slugs is not None else None
        rows = {r["bot"]: r for r in read_cache.configs(c, wanted)}
        out = {}
        if who.role not in ("human", "owner", "bot") or who.role == "owner":
            return {slug: dict(self.FULL) for slug in (wanted if wanted is not None else rows)}
        shared = {}     # the roster, org-chart inputs and reports_to map, read once and only when needed

        def context():
            if not shared:
                from . import views
                # Read only: P.person, P.group_chain, P.manages and above never change them, so the snapshot's own
                # copies are shared rather than copied for every bot list.
                roster = views.roster(c, shared=True)
                shared.update(
                    roster=roster, entries=views.entries(c, shared=True),
                    archived={r["slug"] for r in H.bots(c) if r.get("state") == "archived"},
                    parents={r["bot"]: r["reports_to"] or "" for r in read_cache.configs(c)},
                    bare=True)               # a bot's bare person id in reports_to is that person (people.py)
                pid = H.actor_id(who.actor)
                shared["person"] = pid
                own = (P.person(pid, roster) or {}).get("team") or ""
                shared["team"] = P.group_chain(own, roster) or ([own] if own else [])
            return shared

        for slug in (wanted if wanted is not None else rows):
            row = rows.get(slug)
            doc = A.document(row["access_json"]) if row else None
            if doc is None or A.is_open(doc):
                out[slug] = dict(self.FULL)
                continue
            if who.role == "bot":
                me = H.actor_id(who.actor)
                if me == slug or above(context()["parents"], me, slug):
                    out[slug] = dict(self.FULL)
                    continue
                levels = {level: A.names(doc[level], bot=me) for level in A.LEVELS}
                # A bot may answer one that wrote to it, or that holds work it asked for, so a
                # private bot can still be replied to: nothing else about it opens up.
                if not levels["write"] and (
                        c.execute("SELECT 1 FROM messages WHERE to_actor=? AND from_actor=? LIMIT 1",
                                  (who.actor, "bot:" + slug)).fetchone()
                        or c.execute("SELECT 1 FROM tasks WHERE requester=? AND owner=? AND status NOT IN "
                                     "('done','closed','declined') LIMIT 1", ("bot:" + slug, who.actor)).fetchone()):
                    levels["write"] = True
            else:
                ctx = context()
                pid = ctx["person"]
                managed = (self.bot_admin(who) or row["operator"] == pid or pid in A.owner_ids(row["bot_owners_json"])
                           or P.manages(pid, "bot", slug, ctx["roster"], ctx["entries"], ctx["archived"], ctx["bare"]))
                if managed:
                    out[slug] = dict(self.FULL)
                    continue
                levels = {level: A.names(doc[level], person=pid, team=ctx["team"]) for level in A.LEVELS}
            levels["see"] = levels["see"] or levels["read"] or levels["write"]
            out[slug] = levels
        return out

    def bot_access(self, c, who, slug):
        """`{see, read, write}` for this caller on one bot. A slug with no configuration is Open."""
        return self.bot_accesses(c, who, [slug])[slug]

    def person_can(self, c, actor, slug, level):
        """Whether the person `human:<id>` holds `level` on the bot: for a room's members, not a request."""
        try:
            who = self.identity_for_actor(c, actor)
        except Problem:
            return False
        return self.bot_access(c, who, slug)[level]

    def visible_bot(self, c, who, slug):
        return self.bot_access(c, who, slug)["see"]

    def require(self, c, who, slug, level, missing="Bot not found"):
        """Refuse unless the caller has `level` on the bot: 404 when they cannot even see it, 403
        `forbidden` naming what is missing when they can. The one gate every route uses."""
        access = self.bot_access(c, who, slug)
        if not access["see"]:
            raise Problem("not_found", missing, 404)
        if not access[level]:
            raise Problem("forbidden", {
                "read": f"You can see {slug} but not read its activity. Ask the person who runs it for Read access.",
                "write": f"You can see {slug} but not send it requests. Ask the person who runs it for Write access.",
            }[level], 403)
        return access

    def require_see(self, c, who, slug, missing="Bot not found"):
        return self.require(c, who, slug, "see", missing)

    def require_read(self, c, who, slug, missing="Bot not found"):
        return self.require(c, who, slug, "read", missing)

    def require_write(self, c, who, slug, missing="Bot not found"):
        return self.require(c, who, slug, "write", missing)

    def unreadable_bots(self, c, who):
        """The slugs whose activity this caller may not read: tasks that involve one are hidden
        from them unless they are a party to the task."""
        if who.role == "owner" or who.role not in ("human", "bot"):
            return set()
        return {slug for slug, level in self.bot_accesses(c, who).items() if not level["read"]}

    @staticmethod
    def task_snapshot(c):
        # A detail response must not mix pre-revocation authorization with later private comments/files.
        if not c.in_transaction:
            c.execute('BEGIN')
            return True
        return False

    def task_sql(self, c, who, delegations="task_delegations", unreadable=None):
        """Company tasks respect bot activity controls; private tasks admit parties, their managers and a guest.

        Type-wide work, task ancestry and delegation never widen private visibility.
        `delegations` stays accepted for compatibility with the guarded SQL caller.
        """
        self.task_snapshot(c)
        if who.role not in ("owner", "human", "bot"):
            return "0"
        hidden = ["bot:" + slug for slug in sorted(self.unreadable_bots(c, who) if unreadable is None else unreadable)]
        clear = ("NOT (owner IN %s OR requester IN %s)" % ((A.qlist(hidden),) * 2)) if hidden else "1"
        from .task_rights import subjects, private_subjects
        inherited_private = A.qlist(sorted(private_subjects(c, who.actor)))
        parties = A.qlist(sorted(subjects(c, who.actor)))
        me = A.q(who.actor)
        # A private task's guest (hubdb.task_reach) reads it as its two parties do.
        audience = (f"(owner IN {inherited_private} OR requester IN {inherited_private} "
                    f"OR (coalesce(private,1)<>0 AND guest={me}) "
                    f"OR (coalesce(private,1)=0 AND (owner IN {parties} OR requester IN {parties} OR ({clear}))))")
        if who.task_actor:
            actor = A.q(who.task_actor)
            carried = A.qlist(sorted(private_subjects(c, who.task_actor)))
            audience += f" AND (coalesce(private,1)=0 OR owner IN {carried} OR requester IN {carried} OR guest={actor})"
        return audience

    def operator(self, c, who, bot):
        row = read_cache.one(c, OPERATOR_SQL, (bot,))
        return bool(row and (who.role == "owner" or
                            who.role == "human" and not self.system_bot(bot) and who.actor == "human:" + row["operator"]))

    def manages(self, c, who, kind, ident):
        """The owner, or a person above this person or bot on the org chart (backend/people.py
        `manages`). The one rule behind editing a profile, a bot, or a place in the chart."""
        if who.role == "owner":
            return True
        if who.role != "human":
            return False
        from . import views
        archived = {row["slug"] for row in H.bots(c) if row.get("state") == "archived"}
        return P.manages(H.actor_id(who.actor), kind, ident, views.roster(c), views.entries(c), archived, True)

    def bot_admin(self, who):
        """Whether a person may administer bots assigned to their own operator identity. Tico support with app access
        reads as an admin does (backend/support_access.py limits what it writes)."""
        return who.role == "owner" or (who.role == "human" and (who.support == "app" or who.email.lower() in self.bot_admins))

    def identity_for_actor(self, c, actor):
        """Rebuild a human identity for deferred work without elevating its permissions."""
        human = H.human(c, H.actor_id(actor)) if str(actor).startswith("human:") else None
        if not human or standing(c, human["id"]):
            raise Problem("identity", "The person who requested this change is no longer on the roster", 403)
        email = str(human.get("email") or "").lower()
        role = "owner" if email and email == self.owner_email else "human"
        return Identity(actor, role, email=email)

    def domain(self, who):
        if who.role == "runner":
            raise Problem("identity", "Runner credentials cannot act as a human or bot", 403)

    def bot_contact(self, c, who, slug, conversation_id=None, task_id=None, kind="message"):
        """Whether this sender may open something with `slug`.

        `tasks` is the strict one and the simplest to reason about: work arrives as a task and
        nothing else. No other bot may chat it, ask it anything or steer it — not a bot holding
        one of its tickets. A task is a thing to do and it is dispatched without a reply; a
        message is a conversation, and a conversation between bots is a run spent on somebody
        else's business. Bots file work on each other and walk away. Its manager (`reports_to`)
        and BotOps still leave it notes, comments and messages: the one directs it, the other
        keeps it running.

        A bot set to `bot_contact: replies` does not take chats, questions or tasks from other
        bots it has no business with. Every run costs its operator real money, and a bot with a
        narrow job should not be a free help desk for the rest of the fleet.

        People are never affected: the point is to stop bots pinging bots, not to make a bot
        unreachable. Four things still get through, because each one is the protected bot's own
        doing or its own chain of command:

        - itself, and the keeper (notices about its own tasks are written directly, not here)
        - its direct bot manager named in `reports_to`; task comments and questions also
          admit managers through the full org chain when they inherit a task party's rights
        - a bot holding an open task this bot requested — how a ticket gets a question back
        - a bot in a conversation this bot opened with it

        The last two are scoped to that task and that conversation, so answering once does not
        hand out a standing invitation. An answer to a waiting `ask` never comes through here at
        all (`hubdb.answer` checks only that the ask was addressed to you), so refusing contact
        cannot strand a bot that is blocking on a reply.
        """
        row = c.execute("SELECT config_json,reports_to FROM bot_config WHERE bot=?", (slug,)).fetchone()
        if who.role != "bot":
            return True
        sender = who.actor
        if sender == "bot:" + slug or sender == H.KEEPER:
            return True
        if not row:
            return True
        declared = json.loads(row["config_json"]) if row["config_json"] else {}
        mode = declared.get("bot_contact", "open")
        manager = H.bot_actor(row["reports_to"] or declared.get("reports_to") or "")
        # Task comments/questions inherit a party's authority through the whole chart;
        # this does not grant chat, credentials or other bot-wide access.
        if task_id and kind in ("comment", "question"):
            task = H.task(c, task_id)
            if task and H.task_participant(c, sender, task):
                if H.task_represents(c, sender, "bot:" + slug) and sender != "bot:" + slug:
                    return True
        if mode == "tasks":
            return kind == "task" or sender in (manager, "bot:" + H.FLEET_MAINTAINER)
        if mode != "replies":
            return True
        if manager == sender:
            return True
        asked = "bot:" + slug
        if task_id:
            task = c.execute("SELECT requester,owner FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task and task["requester"] == asked and task["owner"] == sender:
                return True
        if c.execute("SELECT 1 FROM tasks WHERE requester=? AND owner=? AND status NOT IN "
                     "('done','closed','declined') LIMIT 1", (asked, sender)).fetchone():
            return True
        if conversation_id and c.execute(
                "SELECT 1 FROM messages WHERE conversation_id=? AND from_actor=? AND to_actor=? LIMIT 1",
                (conversation_id, asked, sender)).fetchone():
            return True
        return False

    def require_bot_contact(self, c, who, actor, conversation_id=None, task_id=None, kind="message"):
        """`bot_contact` as a gate. The refusal names the way back in, because the sending bot
        reads it and has to decide what to do next."""
        if not str(actor).startswith("bot:"):
            return
        slug = H.actor_id(actor)
        if self.bot_contact(c, who, slug, conversation_id, task_id, kind):
            return
        row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
        declared = json.loads(row["config_json"]) if row and row["config_json"] else {}
        if declared.get("bot_contact") == "tasks":
            raise Problem("bot_contact", f"{slug} takes work as a task and nothing else (notes only from its manager "
                                         "and BotOps). File one "
                                         f"with `hub task create --owner {slug}`, say what you want "
                                         "done in the body, and do not wait for it.", 403)
        raise Problem("bot_contact", f"{slug} does not take contact from other bots. Ask its "
                                     "manager or a person to pass this on, or reply on a task or "
                                     "conversation it opened with you.", 403)

    def target(self, c, who, target, need="see"):
        """The actor a request names. `need` is the level the caller must hold on it when it is a
        bot: `see` to name it at all, `write` to send it something, `read` to look at its work."""
        self.domain(who)
        actor = H.resolve_actor(c, target)
        if not actor and (why := H.recruiting(c, target)):
            raise Problem("recruiting", why, 409)
        if not actor or actor == H.KEEPER:
            raise Problem("not_found", "Unknown recipient", 404)
        if actor.startswith("bot:"):
            self.require(c, who, H.actor_id(actor), need, missing="Unknown recipient")
        return actor

    def run_conversation(self, c, who, bot, message):
        """The conversation check for reading one of `bot`'s runs, started by `message`. A bot above `bot` in the
        `reports_to` chain also reads its reports' work: a run on a task or a routine, or one a bot asked for. A run
        a person started by chatting stays theirs, and peers and the bots above `bot` keep the conversation rule.
        The task fence (backend/task_privacy.py) still applies on top."""
        from .live_privacy import sources
        from .task_privacy import actor
        if not sources(c).message_readable(actor(who), message):
            raise Problem("not_found", "Turn not found", 404)
        if who.role == "bot" and H.actor_id(who.actor) != bot and above(
                {r["bot"]: r["reports_to"] or "" for r in read_cache.configs(c)}, H.actor_id(who.actor), bot):
            from . import task_privacy
            try:
                if (not str(message.get("from_actor") or "").startswith("human:")
                        or task_privacy.message_tasks(c, message, include_run=False)):
                    return
            except Problem:
                pass
        self.conversation(c, who, message["conversation_id"])

    def conversation(self, c, who, conversation_id, *, write=False):
        self.domain(who)
        row = H.conversation(c, conversation_id)
        if not row:
            raise Problem("not_found", "Conversation not found", 404)
        from .live_privacy import require_conversation, sources
        from .task_privacy import actor
        meeting_room = require_conversation(c, actor(who), conversation_id)
        if write and (meeting_room or sources(c).conversation(conversation_id)):
            raise Problem("forbidden", "Use live meeting chat for scoped meeting replies", 403)
        if row.get("task_id"):
            self.task(c, who, row["task_id"])
            if row.get("kind") == "task":
                return row
        if who.role == "bot":
            # A runner turn sees the conversations its attempt was handed. An external agent
            # has no attempt: it sees the conversations it is in, which is what a claim would
            # have granted a turn for the same message.
            if who.agent:
                grant = who.actor in row["participants"] or rooms.room_bot(row) == H.actor_id(who.actor)
            else:
                grant = c.execute("SELECT 1 FROM attempt_conversations WHERE attempt_id=? AND conversation_id=?",
                                  (who.attempt_id, conversation_id)).fetchone()
            # Task discussions are shared with their owners/delegates. Personal chat history
            # is scoped to this execution, not every human who has used the same bot.
            if not grant:
                if row.get("task_id") and row["kind"] == "task":
                    self.task(c, who, row["task_id"])
                elif _own_ask_to_a_person(who, row):
                    pass
                else:
                    raise Problem("forbidden", "This conversation is outside the current execution context", 403)
            return row
        if meeting_room:
            return row
        scope = row.get("scope") or "direct"
        if scope == rooms.PERSONAL:
            owner = row.get("owner_actor") or next(
                (p for p in row["participants"] if str(p).startswith("human:")), None)
            # Personal assistant rooms do not inherit the normal owner inspection bypass.
            if who.actor != owner:
                raise Problem("forbidden", "This personal conversation belongs to another person", 403)
            # The owner's earlier Strategy Bot chat belongs to the owner seat: a person who is no longer the owner reads
            # it no more, even before it moves to the new owner (backend/assistant.py move_strategy_room).
            if row.get("room_key") == rooms.STRATEGY_ROOM and who.role != "owner":
                raise Problem("forbidden", "This chat belongs to the company's owner", 403)
        elif scope == rooms.SHARED:
            bot = rooms.room_bot(row)
            if not bot or not rooms.shared_member(c, self, who.actor, bot) or not self.bot_access(c, who, bot)["read"]:
                raise Problem("forbidden", "You are not a member of this shared bot room", 403)
        elif who.role != "owner" and who.actor not in row["participants"]:
            raise Problem("forbidden", "This conversation is private", 403)
        return row

    def tico_principal(self, c, who):
        """The signed-in person represented by a private assistant execution."""
        name = self.settings.assistant_name
        if who.role in ("owner", "human"):
            return who
        if who.role != "bot" or who.actor != "bot:" + self.settings.assistant_bot:
            raise Problem("forbidden", "This endpoint is available only to people and " + name, 403)
        row = c.execute(
            "SELECT v.scope,v.owner_actor,m.from_actor FROM attempts a "
            "JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
            "JOIN conversations v ON v.id=m.conversation_id WHERE a.id=?", (who.attempt_id,)).fetchone()
        if not row or row["scope"] != rooms.PERSONAL:
            raise Problem("forbidden", "Fleet access requires a private " + name + " conversation", 403)
        actor = row["owner_actor"]
        if not actor or not str(actor).startswith("human:"):
            raise Problem("forbidden", "This " + name + " turn is not acting for a signed-in person", 403)
        human = H.human(c, H.actor_id(actor))
        if not human:
            raise Problem("forbidden", "The initiating person is no longer on the roster", 403)
        email = str(human.get("email") or "").lower()
        role = "owner" if email and email == self.owner_email else "human"
        return Identity(actor, role, email=email, task_actor=who.task_actor or who.actor,
                        runner_id=who.runner_id, attempt_id=who.attempt_id, agent=who.agent, via=who.via)

    def task_row(self, c, who, row):
        if self.task_snapshot(c) and row:
            row = H.task(c, row["id"])
        self.domain(who)
        if not row:
            raise Problem("not_found", "Task not found", 404)
        if who.role not in ("owner", "human", "bot") or not H.task_private_readable(c, who.actor, row):
            raise Problem("not_found", "Task not found", 404)
        if who.task_actor and not H.task_private_readable(c, who.task_actor, row):
            raise Problem("not_found", "Task not found", 404)
        participants = (row["owner"], row["requester"])
        if not H.task_participant(c, who.actor, row) and not (H.task_private(c, row) and H.task_private_readable(c, who.actor, row)):
            # A party to a task, or a private task's guest, always sees it; anyone else needs Read on every bot it
            # involves.
            bots = [H.actor_id(a) for a in participants if str(a).startswith("bot:")]
            for slug, level in (self.bot_accesses(c, who, bots).items() if bots else ()):
                if not level["see"]:
                    raise Problem("not_found", "Task not found", 404)
                if not level["read"]:
                    raise Problem("forbidden", f"This task involves {slug}, whose activity you cannot read", 403)
        return row

    def task(self, c, who, task_id):
        self.task_snapshot(c)
        return self.task_row(c, who, H.task(c, task_id))

    def resolve_task(self, c, who, ident, visible=None):
        """The full task id for what a caller typed: the id itself, a unique prefix of at least
        TASK_PREFIX_MIN characters, a task's number (`#18945`), or a refusal. Models copy a long
        UUID imperfectly, so an ambiguous prefix lists its candidates and a near miss is only
        suggested, never used; for the same reason bare digits, which a cut-short id can be, are
        never read as a number. Only tasks `visible` (default: what this caller may read) are
        matched, so a refusal never names a task the caller cannot see."""
        self.task_snapshot(c)
        if not isinstance(ident, str) or not ident.strip():
            return ident
        ident = ident.strip()
        if H.task(c, ident):
            return ident            # the caller's permission is auth.task's to decide, as before
        self.domain(who)
        visible = visible or self.task_sql(c, who)

        def readable(rows):
            found = []
            for row in rows:
                try:
                    self.task_row(c, who, row)
                except Problem:
                    continue
                found.append(row)
            return found

        number = re.fullmatch(r"#(\d{1,18})", ident)
        if number:
            rows = readable(H._rows(c.execute("SELECT id,title,owner,requester FROM tasks WHERE number=? AND ("
                                              + visible + ")", (int(number.group(1)),))))
            if rows:
                return rows[0]["id"]
            raise Problem("not_found", "No task %s" % ident, 404)

        if TASK_PREFIX_MIN <= len(ident) < 36:
            rows = readable(H._rows(c.execute(
                "SELECT id,title,owner,requester FROM tasks WHERE substr(id,1,?)=? AND (" + visible + ") "
                "ORDER BY created LIMIT 50", (len(ident), ident.lower()))))
            if len(rows) == 1:
                return rows[0]["id"]
            if rows:
                raise Problem("ambiguous_id", "Task id %s matches %d tasks: %s. Use more characters of the id."
                              % (ident, len(rows), "; ".join("%s (%s)" % (r["id"][:TASK_PREFIX_MIN], r["title"])
                                                             for r in rows[:10])), 409)
        if len(ident) >= 34:
            near = []
            for row in H._rows(c.execute("SELECT id,title,owner,requester FROM tasks WHERE length(id) BETWEEN ? AND ? "
                                         "AND (" + visible + ")", (len(ident) - 2, len(ident) + 2))):
                distance = _edit_distance(ident.lower(), row["id"], 2)
                if distance is not None:
                    near.append((distance, row))
            if near:
                closest = min(d for d, _ in near)
                best = readable([row for d, row in near if d == closest])[:3]
                if best:
                    raise Problem("not_found", "No task %s. Did you mean %s?" % (
                        ident, " or ".join("%s (%s)" % (r["id"], r["title"]) for r in best)), 404)
        raise Problem("not_found", "Task not found", 404)

    def fleet_task_sql(self, c, who):
        """Every task except those that involve a bot this caller may not read."""
        hidden = ["bot:" + slug for slug in sorted(self.unreadable_bots(c, who))]
        return ("NOT (owner IN %s OR requester IN %s)" % ((A.qlist(hidden),) * 2)) if hidden else "1"

    def approval(self, c, who, approval_id, decide=False):
        self.domain(who)
        row = H.approval(c, approval_id)
        if not row:
            raise Problem("not_found", "Approval not found", 404)
        msg = H.message(c, row["message_id"])
        if decide:
            allowed = who.role in ("owner", "human") and msg["to_actor"] == who.actor
        else:
            allowed = who.role == "owner" or who.actor in (row["requested_by"], msg["to_actor"])
        if not allowed:
            raise Problem("forbidden", "This approval is not yours", 403)
        return row


def bot_reads_task(c, bot, row):
    """Whether `bot` may read this task under the same rules as reading it (Auth.task_row): what a notice or an
    answer sent to it may carry. A bot's access needs none of the server's sign-in settings, so no Auth is built.
    With no transaction open, the read runs in a savepoint released at the end, so task_row's snapshot never leaves
    a BEGIN open on an autocommit connection."""
    policy = Auth.__new__(Auth)
    opened = not c.in_transaction
    if opened:
        c.execute("SAVEPOINT bot_reads_task")
    try:
        if opened and row:
            row = H.task(c, row["id"])      # read in this snapshot, as task_row's own BEGIN would
        policy.task_row(c, Identity(bot, "bot"), row)
    except Problem:
        return False
    finally:
        if opened:
            c.execute("RELEASE bot_reads_task")
    return True
