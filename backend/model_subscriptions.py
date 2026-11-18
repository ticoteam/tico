"""Company subscriptions, given to computers (Settings > Models).

A subscription is one of the company's model logins or keys, saved once in Tico rather than on a computer: a Claude
setup-token (`claude setup-token`), a ChatGPT login (Codex's `auth.json` from a `codex login`), or an Anthropic or
OpenAI API key. It belongs to the company. `added_by` identifies the person who saved it and can remove it. A plan
(Claude or ChatGPT) has an owner (`owner_id`), the person who adds it unless an owner or admin names another active person of
the company, then or later. A plan is meant for its owner's own bots, not shared ones (the Add pop-up says so). An API
key bills per use and has no owner; a company may save many. The value is a service row of the credential vault
(`credential_id`, CREDENTIAL + the subscription's id; backend/credentials.py `keep_service`), never shown again, never
in a list, an event or an idempotency receipt (saves go through Vault.change, which keeps only a keyed fingerprint).
Before D11-11 it was sealed in the subscription's own row (`sealed`, context `model_subscription:<id>`): a row the move
left (backend/migrations/20261117103033_one_encrypted_store.py) opens as before, and its next save moves it.

A computer (runner) is given at most one subscription per runtime (`computer_subscriptions`); one subscription may be
given to many computers. A bot does not pick one: it runs on its computer, and a turn signs in, for its runtime, its
named fallback and eligible automatic fallbacks, with

  1. the computer's subscription for that runtime, unless it needs reconnecting (then it is passed over),
  2. else the login signed in on that computer (as before),
  3. else the Tico AI Gateway when the company's models choice allows it (backend/models_preset.py),
  4. else the turn waits.

Changing a computer's subscription for a runtime (giving, switching, taking it off) or removing a subscription in use
stops that computer's live runs on that runtime and puts their work back in the queue (quarantine_stops.restart), and
every session of that runtime on that computer starts fresh (`_restart`): nothing begun on the old login is resumed on
the new one. Automatic fallback candidates alone do not fence a bot's primary conversations: fallback session keys
already name the subscription home. Runners with activation support fetch an automatic candidate's value only
when that runtime is about to start. Until then its login can change without stopping the primary. Older runners
receive values up front, so those runs still stop on a login change to revoke their cached value. The page confirms
first (409 `confirm_restart` with the counts).
A bot there that was Rate limited on the
old sign-in is re-judged against the one it runs on now, so a bot waiting on the old plan's limit resumes on the new
plan at once (execution.lift_limited_on_computer).

A computer given a person's plans moves to another of them on its own, between turns (`settle`). For Claude that move
keeps every session: Claude Code keeps a session's transcript in the bot's own `~/.claude/projects` whichever plan
signs it in (a setup-token from the environment, a full login through a config home that links `projects`), and
resumes it under another account, of another organization too. A runner that says `plan_switch_resume_v1` keys a
Claude thread by no one plan, so the next turn on the new plan resumes the same session; should that resume fail (the
transcript is gone, or Claude refuses it), the turn starts a new session told what the room said since, and says so
(runner-rs/src/continuity.rs). Codex keeps its threads in each plan's own home, so a move there, or on an older runner,
still starts that runtime's sessions fresh. Only a person's change asks first (`confirm_restart`) and starts fresh.

Usage limits: a subscription is one login wherever it runs, so a limit met on it holds it on every computer it is given
to until the reset (login_holds `sub:<id>`). While held it is passed over like one that needs reconnecting: turns there
run on the computer's own login, else the Gateway under the company's choice, else wait. Saving a new value ends its
hold as Try now does (a new value may be another account); a rename does not.

Connect (`connect`): instead of pasting, an owner or admin signs a plan in from the page. One online computer that can
runs the CLI through the sign-in relay (backend/model_login.py): `claude setup-token`, or `codex login` in a home of its
own, so that computer's own login stays as it was. The person gets the link in the page, and the runner sends the
result (`POST runner-logins/<id>/subscription`), which `connected` seals as the subscription the person named, or as
the new value of the one they are reconnecting. The browser never holds it.

Owners and admins save, rename, reconnect, remove and give subscriptions. A person who sees Settings > Models reads the
list (never a value). A bot, a run or a Tico support session never saves a value; support writes nothing here.

Delivery: the claim records what it handed each attempt (`attempt_subscriptions`) and names it in the attempt
(`subscriptions`, ids and kinds only). The run reads values with its own run token from `GET credential-runtime`. An
environment subscription (a token or Anthropic key) is set in the turn's environment for that CLI; a home subscription
(a ChatGPT login or OpenAI key for Codex) becomes `auth.json` in a home of its own per subscription on that computer,
there only while a turn on it runs (runner-rs/src/model_subscriptions.rs). A ChatGPT login is also handed to every
turn on its computer, whatever runtime the turn runs, so a bot on Claude can run `codex` from its shell (`for_shell`). Codex refreshes a ChatGPT login in that
file; the runner sends the refreshed file back (`POST runner-model-subscription-refresh`) so the next computer starts
from it. Only a live run still given the subscription on that computer, at the revision it was handed, writes back
(`refreshed`).

Revisions: a run reads only what it was handed. A reconnect (a new value) is a change like any other: it stops the
live runs given it (confirmed first) and starts their sessions fresh (`_restart`), so a run never reads another
account's value. A refresh is the same account: a run still going that reads after one gets the refreshed value and
its record moves with it (`run_values`), so its write-back and its verdict (`turn_rejected`, `turn_worked`) match
what it ran on.

One computer is one trust group: every bot on a computer runs as the same OS user there (SECURITY.md).

A run the provider refuses on a subscription marks it `needs_reconnect` and leaves the computer's own login alone: it is
passed over until someone saves a new value (Reconnect), and a run on it that completes marks it working. Only a run on
the subscription's current revision decides either: a late refusal of a value already replaced says nothing of the new
one.
"""

import base64
import json
from datetime import datetime, timezone

from pydantic import Field, SecretStr

from . import bot_settings as BS
from . import hubdb as H
from .credentials import SERVICE, keep_service, may_remove
from .models import Contract, ID
from .store import Problem, readiness_document
from .api_contract import api_v2

STATUSES = ("new", "working", "needs_reconnect")
MAX_SUBSCRIPTIONS = 200
MAX_VALUE = 64 * 1024

# What each kind is, the name a new one gets when the person gives none (`default_name`), the runtime it signs in, and
# how a turn receives it: `env` in the turn's environment, or `file` in
# the CLI's own home (set by the runner). `billing` is what the run records (backend/usage.py). Listed in the page's order.
KINDS = {
    "claude_token": {
        "name": "Claude",
        "runtime": "claude",
        "label": "Claude plan",
        "env": "CLAUDE_CODE_OAUTH_TOKEN",
        "billing": "subscription",
        "secret_label": "Setup token",
        "help": "Run `claude setup-token` on any computer and paste the token.",
        "connect": True,
    },
    # A full login (`claude auth login --claudeai`), made only by Connect on a computer that can (backend/claude_login.py):
    # a turn gets its access token in a `.credentials.json` of its own, in the home CLAUDE_CONFIG_DIR names.
    "claude_login": {
        "name": "Claude",
        "runtime": "claude",
        "label": "Claude plan",
        "file": ".credentials.json",
        "home_env": "CLAUDE_CONFIG_DIR",
        "billing": "subscription",
        "secret_label": "Sign-in",
        "help": "Connect signs the plan in on one of your computers.",
        "connect": True,
        "hidden": True,
    },
    "codex_login": {
        "name": "ChatGPT",
        "runtime": "codex",
        "label": "ChatGPT plan",
        "file": "auth.json",
        "billing": "subscription",
        "secret_label": "auth.json",
        "help": "Run `codex login` on any computer, then paste the whole ~/.codex/auth.json.",
        "connect": True,
    },
    "anthropic_key": {
        "name": "Anthropic API",
        "runtime": "claude",
        "label": "Anthropic API key",
        "env": "ANTHROPIC_API_KEY",
        "billing": "api",
        "secret_label": "API key",
        "help": "An Anthropic API key (sk-ant-…).",
    },
    "openai_key": {
        "name": "OpenAI API",
        "runtime": "codex",
        "label": "OpenAI API key",
        "file": "auth.json",
        "billing": "api",
        "secret_label": "API key",
        "help": "An OpenAI API key (sk-…).",
    },
    "cursor_key": {
        "name": "Cursor API",
        "runtime": "cursor",
        "label": "Cursor API key",
        "env": "CURSOR_API_KEY",
        "billing": "api",
        "secret_label": "API key",
        "help": "A Cursor API key.",
    },
    "meta_key": {
        "name": "Meta API",
        "runtime": "musecode",
        "label": "Meta API key",
        "env": "META_API_KEY",
        "billing": "api",
        "secret_label": "API key",
        "help": "A Meta API key.",
    },
}
RUNTIMES = tuple(dict.fromkeys(k["runtime"] for k in KINDS.values()))
RUNTIME_LABELS = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor", "musecode": "Muse Code"}
API_KINDS = tuple(kind for kind, spec in KINDS.items() if spec.get("billing") == "api")


class SubscriptionWrite(Contract):
    """A new subscription, or a saved one's reconnect (a new `secret`), rename or new owner (`owner_id`, a person; a new
    plan defaults to whoever adds it; an API key has none). `confirm` accepts that a reconnect stops and restarts the
    live runs on every computer given it."""

    name: str | None = Field(default=None, max_length=80)
    owner_id: str | None = Field(default=None, max_length=120)
    kind: str | None = Field(default=None, max_length=40)
    secret: SecretStr | None = Field(default=None, max_length=MAX_VALUE)
    confirm: bool = False


class SubscriptionConnect(Contract):
    """Sign a plan in from the page: a new subscription (`kind`; `name`, else its plan's), or a saved one's new value
    (`subscription`).
    `owner_id` names the new one's owner (default: whoever connects it). `confirm` accepts that a reconnect stops and
    restarts the live runs on every computer given it."""

    kind: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=80)
    owner_id: str | None = Field(default=None, max_length=120)
    subscription: str | None = Field(default=None, max_length=80)
    confirm: bool = False


class SubscriptionConnected(Contract):
    """What a computer's CLI gave it for a Connect job."""

    value: SecretStr = Field(max_length=MAX_VALUE)


class ComputerSubscription(Contract):
    """Give a computer a subscription for one runtime, or a person's plans for it (`person`: every plan for that runtime
    the person owns, used in turn); a null `subscription` and no `person` takes it off. `confirm` accepts that the
    computer's live runs on that runtime stop and start again."""

    runner_id: ID
    runtime: str = Field(pattern="^(claude|codex|cursor|musecode)$")
    subscription: ID | None = None
    person: str | None = Field(default=None, max_length=120)
    confirm: bool = False


class SubscriptionRefresh(Contract):
    """A runner's copy of a home subscription that its CLI refreshed during a turn."""

    attempt: ID
    subscription: ID
    revision: int = Field(ge=1)
    value: SecretStr = Field(max_length=MAX_VALUE)


# ------------------------------------------------------------------------------------------------ values
def _context(sid):
    return "model_subscription:" + sid


# A subscription's own value is the service row CREDENTIAL + its id. A `credential_id` naming any other row is a key kept
# in the vault as a credential (added in Settings > Computers before D11-4, "keys on computers" below): it still follows
# its computer's login (`_given`), reaches runners of v0.6.0-rc.1 as their key (`keys_for`) and gives way to a bot's own
# key grant (`for_claim`). KEPT_KEY is that test in SQL, on the alias it is formatted with.
CREDENTIAL = "model-subscription-"
KEPT_KEY = "{0}credential_id IS NOT NULL AND {0}credential_id<>'" + CREDENTIAL + "'||{0}id"


def _kept_key(sub):
    return bool(sub["credential_id"]) and sub["credential_id"] != CREDENTIAL + sub["id"]


def _keep_value(c, vault, sub, value, preview, actor, now):
    """Put `value` where `sub` keeps it: its own service row (made on its first save), or the vault credential of a kept
    key, where a computer's key fetch reads it too. Returns the subscription's `credential_id`."""
    if _kept_key(sub):
        ciphertext, nonce = vault.cipher.encrypt(c, sub["credential_id"], value)
        c.execute(
            "UPDATE credentials SET ciphertext=?,nonce=?,preview=?,revision=revision+1,updated_at=?,updated_by=? "
            "WHERE id=?",
            (ciphertext, nonce, preview, now, actor, sub["credential_id"]),
        )
        return sub["credential_id"]
    cid = CREDENTIAL + sub["id"]
    keep_service(c, vault.cipher, cid, "Model subscription", value, actor, "model_subscriptions", now, False)
    return cid


def _jwt_email(token):
    """The email in a ChatGPT login's id_token, for the list's preview only. Not verified."""
    try:
        part = str(token).split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, TypeError):
        return ""
    email = claims.get("email") if isinstance(claims, dict) else ""
    return email if isinstance(email, str) and "@" in email and len(email) <= 200 else ""


def _codex_login(text):
    """A Codex auth.json holding a ChatGPT login: its parsed form, or a Problem saying what to paste."""
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    tokens = value.get("tokens") if isinstance(value, dict) else None
    if not isinstance(tokens, dict) or not any(
        isinstance(tokens.get(k), str) and tokens[k] for k in ("refresh_token", "access_token")
    ):
        raise Problem(
            "secret",
            "Paste the whole auth.json from a computer where `codex login` is signed in (~/.codex/auth.json)",
            422,
        )
    return value


def checked(kind, secret):
    """(value to seal, preview) for `kind`, or a Problem naming what is wrong. Whitespace around a token or key goes."""
    if kind not in KINDS:
        raise Problem("kind", "Choose " + ", ".join(spec["label"] for spec in KINDS.values()), 422)
    text = str(secret or "")
    if kind == "claude_login":
        from . import claude_login

        return claude_login.checked(text)
    if kind == "codex_login":
        value = _codex_login(text.strip())
        email = _jwt_email((value.get("tokens") or {}).get("id_token"))
        return json.dumps(value, separators=(",", ":")), email or "ChatGPT login"
    text = text.strip()
    if not text or any(ch.isspace() for ch in text) or len(text) > 4096:
        raise Problem("secret", "Paste the token or key on its own", 422)
    prefix = {"claude_token": "sk-ant-", "anthropic_key": "sk-ant-", "openai_key": "sk-"}.get(kind, "")
    if not text.startswith(prefix):
        what = {
            "claude_token": "A Claude setup-token (from `claude setup-token`)",
            "anthropic_key": "An Anthropic API key",
            "openai_key": "An OpenAI API key",
        }[kind]
        raise Problem("secret", f"{what} starts with {prefix}", 422)
    return text, text[:7] + "…" + text[-4:]


def delivered(kind, value):
    """What the runner puts in place for one turn: {"env": NAME, "value": v} or {"file": NAME, "value": text}."""
    spec = KINDS[kind]
    if spec.get("env"):
        return {"env": spec["env"], "value": value}
    if kind == "claude_login":
        from . import claude_login

        # The access token only: no turn can rotate the login under the others.
        return {"file": spec["file"], "home_env": spec["home_env"], "value": claude_login.turn_file(value)}
    if kind == "openai_key":
        # The file `codex login --with-api-key` writes.
        return {"file": spec["file"], "value": json.dumps({"OPENAI_API_KEY": value}, separators=(",", ":"))}
    return {"file": spec["file"], "value": value}


def value_of(c, cipher, sub):
    """A subscription's value, from the vault row `credential_id` names: its own service row, or a kept key's
    credential. None when that row is gone. A row from before D11-11 the move left opens from `sealed`."""
    if sub["credential_id"]:
        # The credential the subscription names at the revision `sub` was read at, in one statement: on a connection
        # outside a transaction (a runner's poll, `Store.read`) a save between two reads would otherwise pair this
        # row (its kind, its revision) with the value saved for the next one. A save since then: None, as if gone.
        found = c.execute(
            "SELECT * FROM credentials WHERE id=(SELECT credential_id FROM model_subscriptions WHERE id=? AND revision=?)"
            " AND ciphertext IS NOT NULL",
            (sub["id"], sub["revision"]),
        ).fetchone()
        if not found:
            return None
        return cipher.open_service(c, found) if found["kind"] == SERVICE else cipher.decrypt(c, found)
    return cipher.unseal(c, _context(sub["id"]), sub["sealed"])


# ------------------------------------------------------------------------------------------------ reads
def _present(c):
    return H._has_table(c, "model_subscriptions")


# The plans as they are read: a full Claude login's account and times live beside its plan (`claude_logins`,
# backend/claude_login.py) and are read with it, '' and NULL for any other plan.
SUBS = (
    "(SELECT s.*,coalesce(l.account,'') AS account,l.access_expires_at AS access_expires_at,"
    "l.login_expires_at AS login_expires_at,l.refreshed_at AS refreshed_at "
    "FROM model_subscriptions s LEFT JOIN claude_logins l ON l.subscription_id=s.id)"
)


def row(c, sid):
    return H._one(c, f"SELECT * FROM {SUBS} WHERE id=?", (sid,)) if _present(c) else None


def _given(c):
    """{(runner_id, runtime): subscription (no value)} for every computer given one, read once per snapshot: the claim
    asks for every bot on every poll, and most installs have none. A computer given a person's plans (`person`) names
    the plan in use now as its subscription (its `id` is '' when that plan is gone) and lists the person's plans for
    that runtime (`members`, oldest first): the set is read from the plans' owners each time, so a plan the person adds
    later joins it and one given to someone else leaves it."""
    from . import read_cache

    values = read_cache.cache(c)
    if values is not None and "model_subscriptions.given" in values:
        return values["model_subscriptions.given"]
    found = {}
    if _present(c):
        people = set()
        for r in c.execute(
            "SELECT g.runner_id AS runner_id,g.runtime AS runtime,g.person AS person,s.id,s.name,s.kind,s.revision,"
            "s.status,l.access_expires_at AS access_expires_at FROM computer_subscriptions g "
            "LEFT JOIN model_subscriptions s ON s.id=g.subscription_id "
            "LEFT JOIN claude_logins l ON l.subscription_id=s.id WHERE (s.id IS NOT NULL OR g.person<>'') "
            # A computer's key follows its login: not while a person cleared it or signed it out there
            # (backend/model_login.py model_login_cleared), as GET runner-model-credentials does (`keys_for`).
            f"AND NOT ({KEPT_KEY.format('s.')} AND EXISTS (SELECT 1 FROM model_login_cleared m "
            "WHERE m.runner_id=g.runner_id AND m.runtime=g.runtime AND m.profile=''))"
        ):
            sub = {k: r[k] for k in ("id", "name", "kind", "revision", "status", "access_expires_at")}
            if r["person"]:
                sub = {**{k: v or "" for k, v in sub.items()}, "person": r["person"], "members": []}
                people.add(r["person"])
            found[(r["runner_id"], r["runtime"])] = sub
        if people:
            members = {}
            for m in c.execute(
                "SELECT s.id,s.name,s.kind,s.revision,s.status,s.status_at,s.owner_id,s.created_at,"
                "l.access_expires_at AS access_expires_at FROM model_subscriptions s "
                "LEFT JOIN claude_logins l ON l.subscription_id=s.id "
                f"WHERE s.owner_id IN ({','.join('?' * len(people))}) ORDER BY s.created_at, s.id",
                sorted(people),
            ):
                runtime = (KINDS.get(m["kind"]) or {}).get("runtime")
                if owned(m["kind"]):
                    members.setdefault((m["owner_id"], runtime), []).append(
                        {
                            k: m[k]
                            for k in (
                                "id",
                                "name",
                                "kind",
                                "revision",
                                "status",
                                "status_at",
                                "created_at",
                                "access_expires_at",
                            )
                        }
                    )
            for (_, runtime), sub in found.items():
                if sub.get("person"):
                    sub["members"] = members.get((sub["person"], runtime), [])
    if values is not None:
        values["model_subscriptions.given"] = found
    return found


def given_to(c, runner_id):
    """[(runtime, subscription id)] the computer is given, whatever their status: for a person's plans, the one in use."""
    return [(runtime, sub["id"]) for (rid, runtime), sub in _given(c).items() if rid == runner_id and sub["id"]]


def still_given(c, runner_id, runtime, subscription_id):
    """Whether the computer is given this subscription for `runtime` now (whatever its status): for a person's plans,
    whether it is the plan in use."""
    sub = _given(c).get((runner_id, runtime))
    return bool(sub) and sub["id"] == subscription_id


RUNNER_READINESS_SQL = "SELECT readiness FROM runners_with_state WHERE id=?"
RUNNER_CAPABILITIES_SQL = "SELECT capabilities FROM runners_with_state WHERE id=? AND revoked_at IS NULL"
# A runner that keys a Claude thread by no one plan, so a move between plans resumes it (runner-rs/src/execute.rs).
RESUME_CAPABILITY = "plan_switch_resume_v1"


def _held(c, sid, runtime, loads=None):
    from . import login_holds

    return login_holds.holding(c, "", login_holds.subscription_login(sid), runtime, loads=loads) == "held"


def _hold_end(c, sid, runtime, loads=None):
    from . import login_holds

    return login_holds.get(c, "", login_holds.subscription_login(sid), runtime, loads).get("until") or ""


def available(c, sub, runtime, loads=None):
    """Whether a plan can sign a turn in now: not waiting to be reconnected, not held at its usage limit until a reset,
    of that runtime, and, for a full Claude login, with an access token that has not run out (its refresh is late)."""
    from . import claude_login

    return (
        bool(sub)
        and bool(sub.get("id"))
        and sub["status"] != "needs_reconnect"
        and KINDS.get(sub["kind"], {}).get("runtime") == runtime
        and not claude_login.expired(sub)
        and not _held(c, sub["id"], runtime, loads)
    )


def pick_key(sub, usage):
    """Where an available plan of a person's set comes in the pick: the soonest weekly reset first (the 5-hour window
    resets too often to sort by), then the more usage left, then the oldest plan. A plan with no weekly reset known
    (never used here, or its window has just reset) comes after every plan with one, oldest first."""
    resets = (usage or {}).get("weekly_resets_at") or ""
    used = (usage or {}).get("weekly_used_pct")
    # A plan near a limit (`near`, backend/claude_login.py: 95% of a window, until back under 90%) comes after the others;
    # only a live refusal holds one.
    near = bool((usage or {}).get("near"))
    # A reset with no percent known sorts after one that names usage left at the same reset, not ahead of it.
    return (
        near,
        not resets,
        resets,
        used if isinstance(used, (int, float)) else 100,
        sub.get("created_at") or "",
        sub["id"],
    )


def _ordered(c, subs, runtime, ctx=None):
    """Available `subs` in pick order (`pick_key`), reading each one's weekly reading once."""
    if len(subs) < 2:
        return list(subs)
    ctx = _listing(c) if ctx is None else ctx
    return sorted(subs, key=lambda sub: pick_key(sub, _plan_usage(c, row(c, sub["id"]) or sub, runtime, ctx)))


def _pick(c, runner_id, runtime, entry, loads=None):
    """The plan a turn on `runtime` here signs in with from a person's set (`entry`, as `_given` reads it), or None when
    every plan is held or needs reconnecting. The plan in use stays while it is available, so a bot's session does not
    restart each time the readings shift; else the next is the first available plan in pick order (`pick_key`). Read
    once per snapshot: the claim asks for every bot on every poll."""
    from . import read_cache

    current = next((m for m in entry["members"] if m["id"] == entry["id"]), None)
    if current and available(c, current, runtime, loads):
        return current
    values = read_cache.cache(c)
    key = ("model_subscriptions.pick", runner_id, runtime)
    if values is not None and key in values:
        return values[key]
    ready = [m for m in entry["members"] if available(c, m, runtime, loads)]
    chosen = (_ordered(c, ready, runtime) or [None])[0]
    if values is not None:
        values[key] = chosen
    return chosen


def in_effect(c, runner_id, runtime, loads=None):
    """The subscription a turn on `runtime` on this computer signs in with, or None for the computer's own login (none
    given, the one given needs reconnecting or is held at a usage limit until its reset, or a runner too old to take
    one). A held one is passed over like one that needs reconnecting: the turn runs on the computer's own login, else
    the Gateway where the computer falls back to it, else waits; it never waits on the subscription's hold. After the reset its
    first turn is the hold's probe as usual (backend/login_holds.py). A computer given a person's plans signs in with
    the one `_pick` names, and falls back the same way only when every one of them is passed over."""
    given = loads.given if loads is not None else _given(c)
    sub = given.get((runner_id, runtime)) if given else None
    if sub and sub.get("person"):
        sub = _pick(c, runner_id, runtime, sub, loads)
    elif not available(c, sub, runtime, loads):
        return None
    if not sub:
        return None
    if loads is not None:
        runner = loads.runner_of(c, runner_id, live=True)
    else:
        from .read_cache import one

        runner = one(c, RUNNER_CAPABILITIES_SQL, (runner_id,))
    if runner and sub["kind"] == "claude_login":
        from . import claude_login

        # A runner that cannot put the token in a turn's own file never gets one: its turns run as if none were given.
        return sub if claude_login.takes_turns(runner) else None
    return sub if runner else None


def held_plan(c, runner_id, runtime, loads=None, profile=""):
    """The login of the company plan (or, for a person's plans, the one whose hold ends first) given this computer for
    `runtime` while its usage limit passes it over and the computer has no sign-in of its own for that runtime to run on
    instead (no Gateway either: that is the claim's
    models_preset.plans_out), or None. A turn then waits on that plan's hold (execution.login_key), and its first turn
    after the reset is the hold's probe."""
    from . import login_holds
    from .models_preset import SIGNED_IN, computer_falls_back

    given = loads.given if loads is not None else _given(c)
    sub = given.get((runner_id, runtime)) if given else None
    if sub and sub.get("person"):
        # A person's set waits on the plan whose hold ends first, while none of its plans can sign the turn in.
        if not passed_over(c, runner_id, runtime, loads):
            return None
        held = [m for m in sub["members"] if m.get("id") and _held(c, m["id"], runtime, loads)]
        ends = {m["id"]: _hold_end(c, m["id"], runtime, loads) for m in held}
        sub = min(held, key=lambda m: (ends[m["id"]], m["id"]), default=None)
    if not sub or not sub.get("id") or not _held(c, sub["id"], runtime, loads):
        return None
    if computer_falls_back(c, runner_id):
        return None  # its turns run on the Gateway meanwhile
    if loads is not None:
        runner = loads.runner_of(c, runner_id, live=True)
    else:
        from .read_cache import one

        runner = one(c, "SELECT readiness FROM runners_with_state WHERE id=?", (runner_id,))
    ready = readiness_document(runner["readiness"]) if runner else {}
    row = ((ready.get("runtimes") or {}).get(runtime) or {}) if runner else {}
    own = (row.get("profiles") or {}).get(profile) if profile else None
    # The login the turn would sign in with instead: `profile` (the default home a computer names, or a profile), read
    # from its own row when the computer reports one, else the runtime's.
    if not runner or (own if isinstance(own, dict) else row).get("authenticated") == SIGNED_IN:
        return None
    return login_holds.subscription_login(sub["id"])


def company_plan(c, runtime):
    """The label of the company's plan for `runtime` ("Claude plan"), when it holds one a computer could be given, else
    '': a computer signed out of a runtime the company has a plan for needs that plan, not a sign-in (readiness.can_run)."""
    kinds = [k for k, spec in KINDS.items() if spec["runtime"] == runtime and spec.get("billing") == "subscription"]
    if not kinds or not _present(c):
        return ""
    found = c.execute(
        f"SELECT kind FROM model_subscriptions WHERE kind IN ({','.join('?' * len(kinds))}) LIMIT 1", kinds
    ).fetchone()
    return KINDS[found["kind"]]["label"] if found else ""


def passed_over(c, runner_id, runtime, loads=None):
    """Whether this computer is given a plan (or a person's plans) for `runtime` and every one is passed over now: at
    its usage limit or needing reconnecting. Its turns then run on its own login, else wait, unless the computer falls
    back to the Gateway (backend/models_preset.py plans_out)."""
    given = loads.given if loads is not None else _given(c)
    sub = given.get((runner_id, runtime)) if given else None
    if not sub:
        return False
    if sub.get("person"):
        # A person's set with no plan for the runtime left is a computer not signed in, not plans used up.
        return bool(sub.get("members")) and _pick(c, runner_id, runtime, sub, loads) is None
    return not available(c, sub, runtime, loads)


def turn_runtimes(config, record=None):
    """Subscription runtimes this turn may use, including the company's automatic fallbacks when `record` is given.
    Gateway models never use these credentials."""
    from .gateway_connection import is_tico
    from .harnesses import runtime_of

    config = config if isinstance(config, dict) else {}
    out = [] if is_tico(config.get("model")) else [str(config.get("runtime") or "")]
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    if fallback.get("harness") and not fallback.get("off") and not is_tico(fallback.get("model")):
        out.append(runtime_of(fallback["harness"]))
    if record is not None and not fallback.get("off"):
        from . import providers
        from .harnesses import harness_caps

        # Like the runner's limited_config, ignore a named model on the same spent login.
        same_login = (
            fallback.get("model")
            and runtime_of(fallback.get("harness") or "") == config.get("runtime")
            and harness_caps(config.get("harness"), config.get("runtime")).get("api_keys") != "bot"
            and harness_caps(fallback.get("harness")).get("api_keys") != "bot"
        )
        if not fallback.get("harness") or same_login:
            out.extend(r["runtime"] for r in providers.default_fallbacks(record) if not is_tico(r["model"]))
    return [r for r in dict.fromkeys(out) if r in RUNTIMES]


def covered_runtimes(c, runner, config, loads=None):
    """{runtime: kind} the computer's subscriptions sign in for a bot on `config` there: a covered runtime needs no
    sign-in on that computer, for the bot's readiness and the claim's check."""
    return {item["runtime"]: item["subscription"]["kind"] for item in for_claim(c, runner, config, loads)}


def covered(ready, runtimes):
    """A copy of a readiness document where each runtime in `runtimes` reads as signed in: the turn signs it in with a
    subscription, so the computer's own login (missing, rejected) does not decide."""
    if not runtimes:
        return ready
    ready = dict(ready or {})
    rows = dict(ready.get("runtimes") or {})
    for runtime in runtimes:
        rows[runtime] = {**(rows.get(runtime) or {"installed": True}), "authenticated": "ready"}
    ready["runtimes"] = rows
    return ready


# ------------------------------------------------------------------------------------------------ views
# An API key's month cost, driven from the month's runs (their attempts rows, by created_at), each one's
# attempt_subscriptions rows by their primary key. CROSS JOIN keeps that order whatever sqlite_stat1 says: the plan
# without it read every attempt a key ever ran, and probed each run, on every listing.
COSTS_SQL = (
    "SELECT s.subscription_id, coalesce(sum(t.est_cost_usd),0) AS cost FROM attempts t "
    "CROSS JOIN attempt_subscriptions s ON s.attempt_id=t.id "
    "WHERE (t.job_id IS NULL OR t.trigger IS NOT NULL) AND t.created_at>=? AND s.subscription_id IN ({ids}) "
    "AND coalesce(t.handed_runtime,s.runtime)=s.runtime GROUP BY s.subscription_id"
)


def costs_sql(count):
    """The month-cost statement for `count` API keys (its placeholders: the month, then the keys)."""
    return COSTS_SQL.format(ids=",".join("?" * count))


def _api_costs(c, month, sid=None):
    """{subscription id: this month's cost} for the API keys, which bill per use; {} when none is saved (the usual company's
    plans alone), so a list of plans runs no cost query. With `sid`, only that key."""
    where = " AND id=?" if sid else ""
    ids = [
        r[0]
        for r in c.execute(
            f"SELECT id FROM model_subscriptions WHERE kind IN ({','.join('?' * len(API_KINDS))}){where}",
            (*API_KINDS, *([sid] if sid else [])),
        ).fetchall()
    ]
    if not ids:
        return {}
    return {r["subscription_id"]: r["cost"] for r in H._rows(c.execute(costs_sql(len(ids)), (month, *ids)))}


def _listing(c, sid=None):
    """What a plan list reads about plans and their computers, in a fixed number of queries however many plans and
    computers there are (Settings > Models shows every plan's usage at once): the grants with their computers, each
    computer's readiness, its named logins' reported runtimes, the login holds, the API keys' month cost and the
    registry metadata. With `sid`, only that plan's grants and computers are read."""
    from .claim_loads import open_holds
    from .subscriptions import subscription_meta

    # A plan in a person's set counts as given to each computer given that set, for its readings: the turns there that
    # sign in with it report its windows as a single plan's do.
    member = (
        "(g.person<>'' AND g.person=(SELECT owner_id FROM model_subscriptions m WHERE m.id=?) "
        "AND g.runtime=(CASE (SELECT kind FROM model_subscriptions m WHERE m.id=?) "
        + " ".join(f"WHEN '{k}' THEN '{v['runtime']}'" for k, v in KINDS.items() if owned(k))
        + " END))"
    )
    where, args = (f"WHERE g.subscription_id=? OR {member}", (sid, sid, sid)) if sid else ("", ())
    grants = {}
    sets = {}
    for g in H._rows(
        c.execute(
            "SELECT g.subscription_id,g.runner_id,g.runtime,g.person,g.updated_at AS granted_at "
            f"FROM computer_subscriptions g {where} ORDER BY g.runner_id, g.runtime",
            args,
        )
    ):
        if g["person"]:
            sets.setdefault((g["person"], g["runtime"]), []).append(g)
        elif g["subscription_id"]:
            grants.setdefault(g["subscription_id"], []).append(g)
    if sets:
        people = sorted({person for person, _ in sets})
        for m in c.execute(
            f"SELECT id,owner_id,kind FROM model_subscriptions WHERE owner_id IN ({','.join('?' * len(people))})",
            people,
        ):
            if owned(m["kind"]):
                grants.setdefault(m["id"], []).extend(sets.get((m["owner_id"], KINDS[m["kind"]]["runtime"]), []))
    computers = f"SELECT g.runner_id FROM computer_subscriptions g {where}"
    runners = {
        r["id"]: r
        for r in H._rows(
            c.execute(
                "SELECT id,label,last_seen_at,revoked_at,readiness,capabilities FROM runners_with_state "
                f"WHERE revoked_at IS NULL AND id IN ({computers})",
                args,
            )
        )
    }
    ready = {rid: readiness_document(r["readiness"]) for rid, r in runners.items()}
    profiles = {
        (r["runner_id"], r["profile"]): r["runtimes"]
        for r in H._rows(
            c.execute(
                f"SELECT runner_id,profile,runtimes FROM computer_profiles WHERE runner_id IN ({computers})", args
            )
        )
    }
    costs = _api_costs(c, H.now()[:7] + "-01", sid)
    holds = open_holds(c)
    return {
        "grants": grants,
        "runners": runners,
        "ready": ready,
        "profiles": profiles,
        "costs": costs,
        "holds": holds,
        "meta": subscription_meta(c),
        "people": {},
    }


def _person(c, ctx, pid):
    if pid not in ctx["people"]:
        ctx["people"][pid] = H.human(c, H.actor_id(pid)) or {}
    return ctx["people"][pid]


def _usage(c, sub, ctx=None):
    """What is known of a subscription's use without new telemetry: an API key's cost this month from the runs that
    used it; a plan's limit while its hold stands (backend/login_holds.py), else its used percent from its computers'
    reports (`_plan_usage`). A plan always answers: with no reading, `reason` says why in a short line."""
    from types import SimpleNamespace
    from . import login_holds

    ctx = _listing(c, sub["id"]) if ctx is None else ctx
    spec = KINDS.get(sub["kind"]) or {}
    if spec.get("billing") == "api":
        return {"window": None, "used_pct": None, "cost_usd_month": round(float(ctx["costs"].get(sub["id"]) or 0), 2)}
    hold = login_holds.get(
        c,
        "",
        login_holds.subscription_login(sub["id"]),
        spec.get("runtime", ""),
        loads=SimpleNamespace(holds=ctx["holds"]),
    )
    if hold and (hold.get("until") or "") > H.now() and not hold.get("capacity"):
        kind = login_holds.window(hold.get("error"))
        window = "week" if "weekly" in kind else "5h" if "5-hour" in kind else None
        return {"window": window, "used_pct": 100, "cost_usd_month": None, "resets_at": hold["until"]}
    return _plan_usage(c, sub, spec.get("runtime", ""), ctx)


def _reason(text):
    """A plan's usage when no percent is known: the one short line why, which the Usage column shows muted."""
    return {"window": None, "used_pct": None, "cost_usd_month": None, "reason": text}


def _at(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _login_state(c, ctx, runner_id, profile, runtime):
    """The usage windows and account a login reports on `runtime`: a named login's from its profile row, the computer's
    own home ("") from its readiness runtimes, which name no profile row. A window is kept only when it passes its
    checks, and an account only beside a sign-in that is not known to be gone."""
    from .subscriptions import reported_runtimes, weekly_snapshot

    if profile:
        raw = ctx["profiles"].get((runner_id, profile))
        if raw is None:
            return {}
        return reported_runtimes(c, runner_id, profile, raw, ctx["meta"]).get(runtime) or {}
    state = dict((ctx["ready"][runner_id].get("runtimes") or {}).get(runtime) or {})
    state["weekly"] = weekly_snapshot(state.get("weekly"))
    state["five_hour"] = weekly_snapshot(state.get("five_hour"), "five_hour")
    if state.get("signed_in") is False or not state.get("account"):
        state.pop("account", None)
    return state


def _plan_identity(sub):
    """The account a plan is signed in as, where Tico knows it: a ChatGPT login's email (its preview, from the id_token
    when it was saved). A Claude token names none, so for it only the time a reading was taken can say whose it is."""
    preview = sub.get("preview") or ""
    return preview if sub.get("kind") in ("codex_login", "claude_login") and "@" in preview else ""


def provenance(reading):
    """A computer's usage window (or None) with its origin kept as the runner sent it: "subscription" with the
    subscription's id, or "login". Anything else is dropped, so a window with no origin counts for no plan (`_plan_usage`)."""
    if not reading:
        return None
    if reading.get("origin") == "subscription" and reading.get("subscription_id"):
        return reading
    if reading.get("origin") == "login":
        return {**reading, "subscription_id": None}
    return {**reading, "origin": None, "subscription_id": None}


def _plan_usage(c, sub, runtime, ctx):
    """A plan's used percent from the computers it is given to. Each signs in with it on its turns, and the runner files
    those turns' usage windows under the computer's default profile (runner-rs drive.rs `remember_weekly`, with the
    profile `turn_profile` picks, which the heartbeat names as readiness `computer_profile`; "" is its own home). So that
    profile's windows for `runtime` are this plan's, but only a reading that is this plan's: taken since the plan was
    given to that computer or its credential was last replaced (`credential_at`: a new value may be another account; a
    rename, an owner change or a refresh of the same account is not), and by its origin (`provenance`): a turn that signed
    in with this plan says so (`origin` "subscription" with its `subscription_id`), and a computer's own login's reading
    (`origin` "login", a background read or a turn on the own login) is this plan's only when the plan's account is known
    and the reading is for that same account. A reading with no origin is no plan's. A window whose reset has passed is no
    longer any reading.
    The most-used window wins, its freshest report breaking a tie. Otherwise the reason is what the data tells: not on a
    computer, a reconnect due, a computer too old, a plan that sends no percent, readings from before it was added,
    computers offline, or a computer yet to report."""
    from . import claude_login, model_login, subscription_refresh as SR
    from .subscriptions import WINDOWS, computer_logins, weekly_snapshot

    runners = ctx["runners"]
    computers = [g for g in ctx["grants"].get(sub["id"], []) if g["runtime"] == runtime and g["runner_id"] in runners]
    if not computers:
        return _reason("Not on a computer yet")
    if sub["status"] == "needs_reconnect":
        return _reason("Reconnect to see usage")
    readers = [g for g in computers if computer_logins(runners[g["runner_id"]], ctx["ready"][g["runner_id"]])]
    # A full login's usage is the plan's own read (backend/claude_login.py), whichever computers it is on.
    if not readers and sub["kind"] != "claude_login":
        return _reason("Update the computer to see usage")
    now = datetime.now(timezone.utc)
    account_of_plan = _plan_identity(sub)
    best, weekly, percent_missing, outside = None, None, False, False
    for grant in readers:
        runner_id = grant["runner_id"]
        profile = ctx["ready"][runner_id].get("computer_profile") or ""
        state = _login_state(c, ctx, runner_id, profile, runtime)
        account = (state.get("account") or {}).get("email") or ""
        since = max(_at(grant["granted_at"]), _at(sub["credential_at"] or sub["created_at"]))
        for window, label in (("weekly", "week"), ("five_hour", "5h")):
            reading = state.get(window)
            if not reading:
                continue
            reported = _at(reading["reported_at"])
            ends = _at(reading["resets_at"]) if reading.get("resets_at") else reported + WINDOWS[window]
            if ends <= now:
                continue
            # Not this plan's by its provenance: another subscription's turn, the own login with no known account for this
            # plan, or a runner that says neither (`provenance`).
            if reading.get("origin") == "subscription":
                mine = reading.get("subscription_id") == sub["id"]
            elif reading.get("origin") == "login":
                mine = bool(account_of_plan) and account.lower() == account_of_plan.lower()
            else:
                mine = False
            if reported < since or not mine:
                outside = True
                continue
            if reading.get("used_percent") is None:
                percent_missing = True
                continue
            candidate = (reading["used_percent"], reported, label, reading.get("resets_at"))
            if best is None or candidate[:2] > best[:2]:
                best = candidate
            if window == "weekly" and reading.get("resets_at") and (weekly is None or reported > weekly[1]):
                weekly = candidate
    # The plan's own read (subscription_refresh.request_plan_read, or a full login's usage read, backend/claude_login.py):
    # this plan's by its asking, and not from before its token was replaced. A full login's read has its 5-hour window
    # and each model's week too.
    meta = ctx["meta"].get(SR.plan_key(sub["id"])) or {}
    since = _at(sub["credential_at"] or sub["created_at"])
    for reading, window, label in (
        (SR.plan_reading(meta), "weekly", "week"),
        (meta.get("five_hour") if sub["kind"] == "claude_login" else None, "five_hour", "5h"),
    ):
        own = weekly_snapshot(reading, window)
        if (
            not own
            or own["used_percent"] is None
            or (own["resets_at"] and _at(own["resets_at"]) <= now)
            or _at(own["reported_at"]) < since
        ):
            continue
        taken = _at(own["reported_at"])
        candidate = (own["used_percent"], taken, label, own["resets_at"])
        if best is None or candidate[:2] > best[:2]:
            best = candidate
        if window == "weekly" and own["resets_at"] and (weekly is None or taken > weekly[1]):
            weekly = candidate
    models = [
        m
        for m in (meta.get("models") or [] if sub["kind"] == "claude_login" else [])
        if isinstance(m, dict) and m.get("resets_at") and _at(m["resets_at"]) > now
    ]
    extra = {
        **({"models": models} if models else {}),
        **({"near": True} if sub["kind"] == "claude_login" and claude_login.near(meta) else {}),
    }
    if best:
        used, _, label, resets = best
        # The weekly window's reset decides where the plan comes in a person's set (`pick_key`), whichever window is
        # the most used.
        week = {"weekly_resets_at": weekly[3], "weekly_used_pct": weekly[0]} if weekly else {}
        return {"window": label, "used_pct": used, "cost_usd_month": None, "resets_at": resets, **week, **extra}
    if percent_missing:
        return _reason("No percentage from the plan")
    if outside:
        return _reason("No usage reported since this subscription was added")
    if not any(model_login._online(runners[g["runner_id"]]) for g in readers):
        return _reason("Its computers are offline")
    return _reason("Waiting for a computer to report")


def view(c, sub, ctx=None):
    """A subscription as Settings > Models lists it: no value, a preview, its status, the computers given it."""
    from . import claude_login, subscription_refresh as SR

    ctx = _listing(c, sub["id"]) if ctx is None else ctx
    spec = KINDS.get(sub["kind"]) or {}
    computers = [g["runner_id"] for g in ctx["grants"].get(sub["id"], [])]
    added = _person(c, ctx, sub["added_by"])
    owner = _person(c, ctx, sub["owner_id"]) if sub.get("owner_id") else {}
    return {
        "id": sub["id"],
        "name": sub["name"],
        "kind": sub["kind"],
        "runtime": spec.get("runtime", ""),
        "label": spec.get("label", sub["kind"]),
        "status": sub["status"],
        "status_detail": sub["status_detail"],
        "status_at": sub["status_at"],
        "preview": sub["preview"],
        "revision": sub["revision"],
        "computers": list(dict.fromkeys(computers)),
        "usage": _usage(c, sub, ctx),
        "usage_refresh": SR.plan_view(ctx["meta"].get(SR.plan_key(sub["id"]))),
        # A full login runs out about a month after its sign-in: when to reconnect, shown a few days before.
        "reconnect_by": claude_login.reconnect_by(sub),
        "added_by": sub["added_by"],
        "added_by_name": added.get("name") or added.get("email") or "",
        "owner_id": sub.get("owner_id") or "",
        "owner_name": owner.get("name") or owner.get("email") or "",
        "created_at": sub["created_at"],
        "updated_at": sub["updated_at"],
    }


def listed(c):
    """Every subscription as Settings > Models lists it, each plan with where it comes in its owner's plans for its
    runtime (`order`: the plans in use on a computer given that person's plans first, as they stay put, then the
    other available ones in pick order; None for one held or needing reconnecting) and the computers whose turns sign
    in with it now (`in_use_on`), and `place`: its place among that person's plans of every tool, those in use first and
    then by the soonest weekly reset (None when held or needing reconnecting)."""
    if not _present(c):
        return []
    subs = H._rows(c.execute(f"SELECT * FROM {SUBS} ORDER BY lower(name), id LIMIT ?", (MAX_SUBSCRIPTIONS,)))
    ctx = _listing(c) if subs else None
    views = [view(c, s, ctx) for s in subs]
    if ctx is None:
        return views
    from types import SimpleNamespace

    holds = SimpleNamespace(holds=ctx["holds"])  # read once by `_listing`: no read per computer
    using = {}
    for (runner_id, runtime), entry in _given(c).items():
        on = _pick(c, runner_id, runtime, entry, holds) if entry.get("person") else entry if entry["id"] else None
        if on and available(c, on, runtime, holds):
            using.setdefault(on["id"], []).append(runner_id)
    groups = {}
    for v in views:
        v["in_use_on"] = using.get(v["id"], [])
        v["order"] = v["place"] = None
        if owned(v["kind"]) and v["owner_id"] and available(c, v, v["runtime"], holds):
            groups.setdefault((v["owner_id"], v["runtime"]), []).append(v)
    for plans in groups.values():
        plans.sort(key=lambda v: (not v["in_use_on"], pick_key(v, v["usage"])))
        for n, v in enumerate(plans, 1):
            v["order"] = n
    # A person's plans of every tool in one list (`place`): the Settings list groups by person and keeps this order.
    people = {}
    for plans in groups.values():
        for v in plans:
            people.setdefault(v["owner_id"], []).append(v)
    for plans in people.values():
        plans.sort(key=lambda v: (not v["in_use_on"], pick_key(v, v["usage"])))
        for n, v in enumerate(plans, 1):
            v["place"] = n
    return views


def kinds():
    return [
        {
            "kind": k,
            "label": v["label"],
            "runtime": v["runtime"],
            "secret_label": v["secret_label"],
            "help": v["help"],
            "connect": bool(v.get("connect")),
            "owned": owned(k),
        }
        for k, v in KINDS.items()
        if not v.get("hidden")
    ]


def set_state(c, runner_id, runtime, entry):
    """What a computer given a person's plans is on now, and why, for its cell in Settings > Computers and for agents:
    {person, person_name, plans, on (the plan's id or None), on_name, why}. `why` is {kind, plan, until}: "soonest" (the
    plan in use has the soonest reset with usage left), "held" or "reconnect" (the plan it moved from, the latest to be
    held or need reconnecting, is at its limit until `until` or needs reconnecting), "all_held" (every plan is passed
    over; `until` is the first hold to end, and the turn runs on the computer's own login, the Gateway or waits) or
    "none" (the person owns no plan for that tool now)."""
    from . import login_holds

    members = entry.get("members") or []
    on = _pick(c, runner_id, runtime, entry) if members else None
    person = H.human(c, H.actor_id(entry["person"])) or {}
    passed = []
    for m in members:
        if on and m["id"] == on["id"]:
            continue
        if m["status"] == "needs_reconnect":
            passed.append(("reconnect", m.get("status_at") or "", m, None))
            continue
        hold = login_holds.get(c, "", login_holds.subscription_login(m["id"]), runtime)
        if hold and (hold.get("until") or "") > H.now():
            passed.append(("held", hold.get("since") or "", m, hold["until"]))
    if not members:
        why = {"kind": "none", "plan": None, "until": None}
    elif not on:
        ends = [until for kind, _, _, until in passed if kind == "held"]
        why = {"kind": "all_held", "plan": None, "until": min(ends) if ends else None}
    elif passed:
        kind, _, m, until = max(passed, key=lambda p: p[1])
        why = {"kind": kind, "plan": m["name"], "until": until}
    else:
        why = {"kind": "soonest", "plan": None, "until": None}
    return {
        "person": entry["person"],
        "person_name": person.get("name") or person.get("email") or H.actor_id(entry["person"]),
        "plans": len(members),
        "on": on["id"] if on else None,
        "on_name": on["name"] if on else None,
        "why": why,
    }


def assignments(c):
    """What `GET computer-subscriptions` answers: every computer's subscription per runtime, and the computers that
    can take one (the rest need an update). A computer given a person's plans names the person (`person`), the plan in
    use as its `subscription_id` and its `set` (`set_state`); a single subscription's `person` is '' and its `set` null."""
    given = []
    if _present(c):
        entries = _given(c)
        for r in c.execute(
            "SELECT g.* FROM computer_subscriptions g JOIN runners r ON r.id=g.runner_id "
            "WHERE r.revoked_at IS NULL ORDER BY g.runner_id, g.runtime"
        ):
            entry = entries.get((r["runner_id"], r["runtime"]))
            if not entry:
                continue
            state = set_state(c, r["runner_id"], r["runtime"], entry) if r["person"] else None
            given.append(
                {
                    "runner_id": r["runner_id"],
                    "runtime": r["runtime"],
                    "subscription_id": (state["on"] or "") if state else r["subscription_id"],
                    "person": r["person"] or "",
                    "set": state,
                }
            )
    capable = [r["id"] for r in c.execute("SELECT id FROM runners_with_state WHERE revoked_at IS NULL ORDER BY id")]
    people = {}
    for runtime in RUNTIMES:
        for owner, plans in plan_owners(c, runtime).items():
            person = H.human(c, H.actor_id(owner)) or {}
            people.setdefault(owner, {"id": owner, "name": person.get("name") or person.get("email") or owner})
            people[owner][runtime] = len(plans)
    return {"assignments": given, "capable": capable, "people": list(people.values())}


# ------------------------------------------------------------------------------------------------ permissions
def _reader(who):
    if who.role not in ("human", "owner") or not str(who.actor).startswith("human:"):
        raise Problem("forbidden", "Only a person reads the company's subscriptions", 403)


def _may_manage(auth, who):
    return (
        who.role in ("human", "owner")
        and str(who.actor).startswith("human:")
        and not who.support
        and auth.bot_admin(who)
    )


def _admin(auth, who, secret=False):
    """An owner or admin. A person pastes their own secret into Settings."""
    if not _may_manage(auth, who):
        raise Problem("forbidden", "Only an owner or admin changes the company's subscriptions", 403)
    if secret and (who.via or who.attempt_id):
        raise Problem("forbidden", "Save a subscription yourself in Settings > Models", 403)


def owned(kind):
    """Whether a subscription of this kind has an owner: a plan does, an API key does not."""
    return (KINDS.get(kind) or {}).get("billing") == "subscription"


def owner_of(c, value, kind):
    """`value` (a person's id, or `human:<id>`) as the owner actor, or a Problem: an owner is a person of this company
    who has not left and whose sign-in is on. Never Tico support, a bot or a service. An API key takes none."""
    from .auth import standing

    if not owned(kind):
        raise Problem("owner", "An API key has no owner", 422)
    text = str(value or "").strip()
    pid = H.actor_id(text)
    if (
        not pid
        or (":" in text and not text.startswith("human:"))
        or pid == "tico-support"
        or not H.human(c, pid)
        or standing(c, pid)
    ):
        raise Problem("owner", "Choose a person in this company", 422)
    return "human:" + pid


def _default_owner(c, who, body, kind):
    """The owner a new subscription is saved with: the one named, else the person adding it; none for an API key."""
    if body.owner_id is not None:
        return owner_of(c, body.owner_id, kind)
    if not owned(kind):
        return ""
    actor = str(getattr(who, "actor", "") or "")
    return actor if actor.startswith("human:") else ""


# ------------------------------------------------------------------------------------------------ restarts
def _restart_line(c, runner_id, runtime):
    label = (c.execute("SELECT label FROM runners WHERE id=?", (runner_id,)).fetchone() or ["The computer"])[0]
    return f"Stopped: {label}'s {RUNTIME_LABELS.get(runtime, runtime)} sign-in changed"


def _live(c, runner_id, runtime):
    """The live attempts on this computer a change of its `runtime` sign-in stops: those handed that runtime (not a
    Tico model, which the Gateway signs in) or already sent a subscription value for it (a fallback's). An unactivated
    automatic candidate has no value to revoke and leaves the primary running."""
    from .gateway_connection import is_tico

    out = []
    for a in c.execute(
        "SELECT a.* FROM attempts a WHERE a.runner_id=? AND a.state IN ('leased','running') "
        "AND a.stop_requested_at IS NULL ORDER BY a.created_at",
        (runner_id,),
    ).fetchall():
        own = a["handed_runtime"] == runtime and not is_tico(a["handed_model"])
        given = handed(c, a["id"], runtime)
        pending = c.execute(
            "SELECT 1 FROM pending_subscription_fallbacks WHERE attempt_id=? AND runtime=?", (a["id"], runtime)
        ).fetchone()
        if own or (given and not pending):
            out.append(a)
    return out


def _bots(c, settings, runner_id, runtime):
    """The bots on this computer whose turns run `runtime` (their own or their named fallback's)."""
    from . import providers
    from .shared_bots import follow

    record = providers.load(c, settings)
    out = []
    for r in c.execute(
        f"SELECT a.bot,{BS.CONFIG} AS config FROM assignments a JOIN bot_config bc ON bc.bot=a.bot {BS.JOIN} "
        "WHERE a.runner_id=? ORDER BY a.bot",
        (runner_id,),
    ).fetchall():
        config = providers.fill(record, follow(c, r["bot"], json.loads(r["config"] or "{}")))
        # Automatic candidates must not reset unrelated primary conversations; their session keys bind the login.
        if runtime in turn_runtimes(config):
            out.append(r["bot"])
    return out


def _sessions(c, bot):
    """How many sessions execution.reset_bot_sessions starts fresh for `bot`: each of its conversations."""
    return sum(
        1
        for (p,) in c.execute("SELECT participants FROM conversations WHERE participants LIKE ?", (f'%"bot:{bot}"%',))
        if "bot:" + bot in json.loads(p or "[]")
    )


def _reset(c, settings, runner_id, runtime):
    """Every session of `runtime` on this computer starts fresh: each bot there that runs it has its sessions fenced
    (execution.reset_bot_sessions, as a move to another computer does) and its saved thread pointer for that runtime let go.
    Returns how many sessions were reset."""
    from .execution import reset_bot_sessions

    n = 0
    for bot in _bots(c, settings, runner_id, runtime):
        n += reset_bot_sessions(c, bot)
        c.execute("DELETE FROM bot_sessions WHERE bot=? AND runtime=?", (bot, runtime))
    return n


def _plan(c, settings, runner_id, runtime):
    return {
        "running": len(_live(c, runner_id, runtime)),
        "sessions": sum(_sessions(c, bot) for bot in _bots(c, settings, runner_id, runtime)),
    }


def _restart(c, settings, runner_id, runtime):
    """Stop the computer's live runs on `runtime` (their work goes back in the queue, quarantine_stops.restart), start
    its sessions of that runtime fresh, and re-judge the bots there that were Rate limited on the old sign-in
    (execution.lift_limited_on_computer). Call it after the computer_subscriptions row has changed: the re-judge reads
    the sign-in the bots would use now. Returns {stopped, sessions_reset}."""
    from . import quarantine_stops
    from .execution import lift_limited_on_computer

    line = _restart_line(c, runner_id, runtime)
    stopped = sum(quarantine_stops.restart(c, a, line) for a in _live(c, runner_id, runtime))
    reset = _reset(c, settings, runner_id, runtime)
    lift_limited_on_computer(c, settings, H.TICO_ACTOR, runner_id, runtime)
    return {"stopped": stopped, "sessions_reset": reset}


def _signed_in_with(c, sid):
    """The computers given this subscription that sign in with it: (runner_id, runtime) of each whose runner takes one.
    An older runner never did (its turns use its own login, `in_effect`), so a change of the subscription leaves its
    runs and sessions alone."""
    return [
        (g["runner_id"], g["runtime"])
        for g in c.execute(
            "SELECT g.runner_id,g.runtime FROM computer_subscriptions g "
            "JOIN runners_with_state r ON r.id=g.runner_id WHERE g.subscription_id=? ORDER BY g.runner_id, g.runtime",
            (sid,),
        ).fetchall()
    ]


def _effect_changed(c, settings, sid):
    """The subscription stopped counting (it needs reconnecting): each computer signing in with it now signs that
    runtime in another way, so its sessions there start fresh. Its runs are not stopped: the refused one already ended.
    (A reconnect restarts them, `save`.) A computer given a person's plans whose next plan keeps its sessions
    (`keeps_sessions`) is left as it is: its next claim moves the set (`settle`)."""
    for runner_id, runtime in _signed_in_with(c, sid):
        on_set = c.execute(
            "SELECT 1 FROM computer_subscriptions WHERE runner_id=? AND runtime=? AND person<>''", (runner_id, runtime)
        ).fetchone()
        if not (on_set and keeps_sessions(c, runner_id, runtime)):
            _reset(c, settings, runner_id, runtime)


def keeps_sessions(c, runner_id, runtime):
    """Whether an automatic move between plans for `runtime` on this computer keeps its sessions: Claude, whose
    transcripts stay in the bot's own home whichever plan signs a turn in, on a runner that resumes a Claude thread on
    the plan it moved to (`RESUME_CAPABILITY`). Codex keeps its threads in each plan's own home."""
    if runtime != "claude":
        return False
    found = c.execute(RUNNER_CAPABILITIES_SQL, (runner_id,)).fetchone()
    return bool(found) and RESUME_CAPABILITY in (H._json(found[0], []) or [])


def _confirm(plan, extra=None):
    raise Problem(
        "confirm_restart",
        f"{plan['running']} running run{'s' if plan['running'] != 1 else ''} will stop and start again",
        409,
        extra={**plan, **(extra or {})},
    )


# ------------------------------------------------------------------------------------------------ writes
def _tick_provider(c, settings, actor, kind):
    """Saving a subscription turns its provider on in the company's providers, so its models are offered."""
    from . import providers

    provider = providers.runtime_provider(KINDS[kind]["runtime"])
    record = providers.load(c, settings)
    if not provider or provider in record["enabled"]:
        return
    before, after = providers.save(
        c,
        actor,
        {
            "expected_revision": record["revision"],
            "enabled": [*record["enabled"], provider],
            "runtime": record["runtime"],
            "model": record["model"],
        },
        H.now(),
        settings,
    )
    H.event(
        c,
        actor,
        "providers.updated",
        "",
        {"before": {"enabled": before["enabled"]}, "after": {"enabled": after["enabled"]}, "why": "subscription"},
    )


def _confirm_reconnect(c, settings, given):
    """A new value stops the runs on each computer signed in with this subscription: with some going, ask first."""
    plans = [_plan(c, settings, runner_id, runtime) for runner_id, runtime in given]
    running = sum(p["running"] for p in plans)
    if running:
        _confirm({"running": running, "sessions": sum(p["sessions"] for p in plans)}, {"computers": len(given)})


def default_name(c, kind, preview=""):
    """The name of a new subscription whose adder gave none: a ChatGPT login's account email (its `preview`, when it has
    one), else its plan or provider ("Claude", "ChatGPT"), numbered from the second of that name on ("Claude 2"). It is
    renamed under its row's Edit."""
    base = preview if kind in ("codex_login", "claude_login") and "@" in preview else KINDS[kind]["name"]
    taken = {r["name"].strip().lower() for r in c.execute("SELECT name FROM model_subscriptions")}
    n, name = 1, base
    while name.lower() in taken:
        n += 1
        name = f"{base} {n}"
    return name


def save(c, vault, settings, who, body, sid=None, signed_on=""):
    """Add a subscription, or change a saved one: a new secret reconnects it (status `new`, so it counts again) and ends
    its usage-limit hold as Try now does, a name renames it, an `owner_id` gives it another owner. A plan added or given
    a new secret goes on the computers that need it (`spread`; `signed_on`: the computer a Connect ran on). Returns its
    view.

    A full Claude login (`claude_login`, backend/claude_login.py) is one plan per account: a new one whose account is
    already connected is a Reconnect of that plan, and a Reconnect with another plan's account is refused. A Reconnect
    with the same account is a refresh: the plan's turns keep running (they get the new access token) and its readings
    stay. A setup-token plan reconnected with a full login becomes one, the same plan (`kind` given as `claude_login`):
    its owner, computers and history stay; its old readings go, since the login may be another account. The emergency
    path goes back: a full login's plan given a pasted setup-token becomes a setup-token plan the same way."""
    from . import claude_login

    now = H.now()
    if sid and not body.kind and body.secret is not None and (row(c, sid) or {}).get("kind") == "claude_login":
        if not body.secret.get_secret_value().lstrip().startswith("{"):
            body = body.model_copy(update={"kind": "claude_token"})
    kind = body.kind or (row(c, sid) or {}).get("kind") if sid else body.kind
    login = None
    if kind == "claude_login" and body.secret is not None:
        login = claude_login.facts(checked("claude_login", body.secret.get_secret_value())[0])
        taken = claude_login.same_account(c, login["account"], sid)
        if taken and sid:
            raise Problem("account", f"Already connected as {taken['name']}", 409)
        if taken:
            # A second Connect of the same account: a Reconnect of the plan it is.
            body = body.model_copy(update={"name": None, "owner_id": None})
            return save(c, vault, settings, who, body, taken["id"], signed_on)
    if sid:
        sub = row(c, sid)
        if not sub:
            raise Problem("not_found", "Subscription not found", 404)
        converting = (
            body.secret is not None
            and {body.kind, sub["kind"]} == {"claude_login", "claude_token"}
            and body.kind != sub["kind"]
        )
        if body.kind and body.kind != sub["kind"] and not converting:
            raise Problem("kind", "A subscription keeps its kind; add a new one instead", 422)
        name = body.name.strip() if body.name is not None else None
        if body.name is not None and not name:
            raise Problem("name", "Enter a name", 422)
        owner = owner_of(c, body.owner_id, sub["kind"]) if body.owner_id is not None else None
        given = []
        value = preview = ""
        same = bool(login) and not converting and login["account"] == sub["account"]
        if body.secret is not None:
            value, preview = checked(kind, body.secret.get_secret_value())
            if converting and kind == "claude_login":
                claude_login.can_convert(c, sid)
            given = [] if same else _signed_in_with(c, sid)
            if not body.confirm:
                _confirm_reconnect(c, settings, given)
        if name is not None:
            c.execute("UPDATE model_subscriptions SET name=?,updated_at=? WHERE id=?", (name, now, sid))
        if owner is not None and owner != sub["owner_id"]:
            c.execute("UPDATE model_subscriptions SET owner_id=?,updated_at=? WHERE id=?", (owner, now, sid))
            H.event(c, who.actor, "subscription.owner_changed", sid, {"from": sub["owner_id"], "to": owner})
        if body.secret is not None and same:
            # The same account signed in again (its monthly Reconnect): a refresh, not a change. Its runs go on and get
            # the new access token; its readings stay.
            store_refreshed(c, vault, sub, value, preview, who.actor, now)
            claude_login.stamp(c, sid, value, refreshed=True)
            c.execute(
                "UPDATE model_subscriptions SET status='new',status_detail='',status_at=? WHERE id=? "
                "AND status='needs_reconnect'",
                (now, sid),
            )
            claude_login.forget(c, sid)  # a refresh under way began from the old login
            claude_login.pushed(c, sid)
            H.event(c, who.actor, "subscription.updated", sid, {"secret_changed": True, "same_account": True})
            spread(c, settings, sid, who.actor, signed_on)
            _follow(c, settings, who.actor)
            return view(c, row(c, sid))
        if body.secret is not None:
            retired = ""
            keyed = []
            if converting:
                # A setup-token plan becomes a full login, the same plan: owner, computers and history stay. Its readings
                # and asked reads were the token's, and the login may be another account: they go.
                c.execute("UPDATE model_subscriptions SET kind=? WHERE id=?", (body.kind, sid))
                claude_login.reset_usage(c, sid)
                H.event(c, who.actor, "subscription.converted", sid, {"from": sub["kind"], "to": body.kind})
            if kind == "claude_login" and _kept_key(sub):
                # A key kept in the vault reaches older runners and bots' grants as a key (`keys_for`, `for_claim`):
                # a full login, with its refresh token, never goes there. It is kept as the plan's own value, and the
                # key is retired as Remove retires one (its computers clear their copies; the vault row goes once
                # nothing else holds it).
                retired = sub["credential_id"]
                keyed = c.execute(
                    "SELECT runner_id,runtime FROM computer_subscriptions WHERE subscription_id=? AND person=''", (sid,)
                ).fetchall()
                sub = {**dict(sub), "credential_id": None}
            # The credential's own time: readings taken before it are the old account's (`_plan_usage`). Only a replaced or
            # reconnected value moves it; a rename, an owner change or a same-account refresh (`refreshed`) does not.
            c.execute(
                "UPDATE model_subscriptions SET credential_id=?,sealed='',preview=?,revision=revision+1,status='new',"
                "status_detail='',status_at=?,updated_at=?,credential_at=? WHERE id=?",
                (_keep_value(c, vault, sub, value, preview, who.actor, now), preview, now, now, now, sid),
            )
            if retired:
                from .model_login import mark_key_cleared

                for g in keyed:
                    mark_key_cleared(c, who, g["runner_id"], g["runtime"])
                drop_credential(c, retired)
            # A new value may be another account, or no longer a full login: the old one's account, times and lease go.
            claude_login.drop(c, sid)
            if kind == "claude_login":
                claude_login.stamp(c, sid, value)
            # A new value may be another account: its own usage-limit hold ends as Try now does, one probe turn tests it
            # (login_holds.try_now), before the bots are re-judged. As for a change, every computer given it stops its
            # live runs on that runtime (their work goes back in the queue; confirmed first) and starts its sessions
            # there fresh. An older runner never signed in with it, so nothing changes there.
            from . import login_holds

            login_holds.try_now(c, "", login_holds.subscription_login(sid), KINDS[sub["kind"]]["runtime"])
            stopped = sum(_restart(c, settings, runner_id, runtime)["stopped"] for runner_id, runtime in given)
            H.event(c, who.actor, "subscription.updated", sid, {"secret_changed": True, "stopped": stopped})
            spread(c, settings, sid, who.actor, signed_on)  # one that needed reconnecting works again
            if kind == "claude_login":
                claude_login.read_soon(c, sid)
            _follow(c, settings, who.actor)
            return view(c, row(c, sid))
        H.event(c, who.actor, "subscription.updated", sid, {"secret_changed": False})
        return view(c, row(c, sid))
    if not body.kind or body.secret is None:
        raise Problem("secret", "Choose a kind and paste the token, key or login", 422)
    if c.execute("SELECT count(*) FROM model_subscriptions").fetchone()[0] >= MAX_SUBSCRIPTIONS:
        raise Problem("limit", f"The company has {MAX_SUBSCRIPTIONS} subscriptions; remove one first", 422)
    value, preview = checked(body.kind, body.secret.get_secret_value())
    name = (body.name or "").strip() or default_name(c, body.kind, preview)
    owner = _default_owner(c, who, body, body.kind)
    sid = H.new_id()
    cid = _keep_value(c, vault, {"id": sid, "credential_id": None}, value, preview, who.actor, now)
    c.execute(
        "INSERT INTO model_subscriptions(id,name,kind,sealed,preview,added_by,owner_id,created_at,updated_at,"
        "credential_at,credential_id) VALUES(?,?,?,'',?,?,?,?,?,?,?)",
        (sid, name, body.kind, preview, who.actor, owner, now, now, now, cid),
    )
    if body.kind == "claude_login":
        claude_login.stamp(c, sid, value)
    H.event(c, who.actor, "subscription.created", sid, {"kind": body.kind})
    _tick_provider(c, settings, who.actor, body.kind)
    spread(c, settings, sid, who.actor, signed_on)
    if body.kind == "claude_login":
        claude_login.read_soon(c, sid)
    _follow(c, settings, who.actor)
    return view(c, row(c, sid))


def store_refreshed(c, vault, sub, value, preview, actor, now):
    """Keep a refreshed login of the same account: the next revision, the same credential time, so its readings stay
    and a turn refused on the old token is known to have been caught by the rotation (claude_login.gap)."""
    c.execute(
        "UPDATE model_subscriptions SET credential_id=?,sealed='',preview=?,revision=revision+1,updated_at=? WHERE id=?",
        (_keep_value(c, vault, sub, value, preview, actor, now), preview, now, sub["id"]),
    )


def remove(c, settings, who, sid, confirm=False):
    """Delete a subscription and take it off every computer given it (`computers`, as Settings counts them): those
    signing in with it stop their live runs on its runtime and start again (confirmed first), their sessions start
    fresh, and the sealed value is gone. An older runner never signed in with it: nothing there stops."""
    sub = row(c, sid)
    if not sub:
        raise Problem("not_found", "Subscription not found", 404)
    computers = c.execute("SELECT count(*) FROM computer_subscriptions WHERE subscription_id=?", (sid,)).fetchone()[0]
    given = _signed_in_with(c, sid)
    if not confirm:
        plans = [_plan(c, settings, runner_id, runtime) for runner_id, runtime in given]
        running = sum(p["running"] for p in plans)
        if running:
            _confirm({"running": running, "sessions": sum(p["sessions"] for p in plans)}, {"computers": computers})
    # A key kept in the vault reached its computers as their key too (GET runner-model-credentials): each is cleared as
    # Clear key does, so a v0.6.0-rc.1 runner logs out the Codex login the key made instead of keeping it.
    keyed = (
        c.execute(
            "SELECT runner_id,runtime FROM computer_subscriptions WHERE subscription_id=? AND person=''", (sid,)
        ).fetchall()
        if _kept_key(sub)
        else []
    )
    # A computer given the owner's plans keeps them: its set moves on at the next turn (`settle`).
    c.execute("DELETE FROM computer_subscriptions WHERE subscription_id=? AND person=''", (sid,))
    c.execute("UPDATE computer_subscriptions SET subscription_id='' WHERE subscription_id=?", (sid,))
    stopped = reset = 0
    for runner_id, runtime in given:
        done = _restart(c, settings, runner_id, runtime)
        stopped, reset = stopped + done["stopped"], reset + done["sessions_reset"]
    c.execute("DELETE FROM model_subscriptions WHERE id=?", (sid,))
    from . import claude_login

    claude_login.drop(c, sid)
    if sub["credential_id"]:
        from .model_login import mark_key_cleared

        for g in keyed:
            mark_key_cleared(c, who, g["runner_id"], g["runtime"])
        drop_credential(c, sub["credential_id"])  # its own service row always; a kept key once nobody else holds it
    H.event(
        c, who.actor, "subscription.deleted", sid, {"kind": sub["kind"], "computers": computers, "stopped": stopped}
    )
    _follow(c, settings, who.actor)
    return {"deleted": True, "id": sid, "computers": computers, "stopped": stopped, "sessions_reset": reset}


def plan_owners(c, runtime):
    """{owner actor: [plan ids, oldest first]} of the people who own at least one plan for `runtime`: the people a
    computer's picker offers for it. An API key has no owner and is in no person's set."""
    kinds = [k for k, spec in KINDS.items() if spec["runtime"] == runtime and owned(k)]
    out = {}
    if not _present(c) or not kinds:
        return out
    for r in c.execute(
        f"SELECT id,owner_id FROM model_subscriptions WHERE owner_id<>'' AND kind IN ({','.join('?' * len(kinds))}) "
        "ORDER BY created_at, id",
        kinds,
    ):
        out.setdefault(r["owner_id"], []).append(r["id"])
    return out


def owner_names(c):
    """The names of the people who own a plan, for Settings search: searching a person's name finds the picker that
    gives a computer their plans (settings_catalog.find `extra`)."""
    if not _present(c):
        return []
    names = []
    for r in c.execute("SELECT DISTINCT owner_id FROM model_subscriptions WHERE owner_id<>'' ORDER BY owner_id"):
        person = H.human(c, H.actor_id(r["owner_id"])) or {}
        names.append(person.get("name") or "")
    return [n for n in names if n]


def _person_for(c, value, runtime):
    """`value` as the actor whose plans a computer is given for `runtime`, or a Problem: a person who owns at least one
    plan for that runtime (one who owns none is not offered)."""
    text = str(value or "").strip()
    pid = H.actor_id(text)
    actor = "human:" + pid if pid and (":" not in text or text.startswith("human:")) else ""
    if not actor or actor not in plan_owners(c, runtime):
        raise Problem("person", f"Choose a person who owns a {RUNTIME_LABELS.get(runtime, runtime)} plan", 422)
    return actor


def give(c, settings, who, body):
    """Give a computer a subscription for one runtime, a person's plans for it (`person`), switch it, or take it off
    (neither). A change stops the computer's live runs on that runtime (confirmed first), starts its sessions of that
    runtime fresh, and re-judges the bots there that were Rate limited on the old sign-in. A runner too old to take one
    keeps signing in with its own login, so there it is only recorded: nothing stops, nothing is re-judged. A person's
    plans start on the plan the rule picks now (`_pick`)."""
    runner = c.execute(
        "SELECT * FROM runners_with_state WHERE id=? AND revoked_at IS NULL", (body.runner_id,)
    ).fetchone()
    if not runner:
        raise Problem("not_found", "Computer not found", 404)
    if body.person and body.subscription:
        raise Problem("person", "Choose a person's plans or one subscription, not both", 422)
    person = _person_for(c, body.person, body.runtime) if body.person else ""
    sub = row(c, body.subscription) if body.subscription else None
    if body.subscription and not sub:
        raise Problem("not_found", "Subscription not found", 404)
    if sub and KINDS[sub["kind"]]["runtime"] != body.runtime:
        raise Problem(
            "wrong_runtime",
            f"{sub['name']} signs in {RUNTIME_LABELS[KINDS[sub['kind']]['runtime']]}, "
            f"not {RUNTIME_LABELS.get(body.runtime, body.runtime)}",
            422,
        )
    current = c.execute(
        "SELECT subscription_id,person FROM computer_subscriptions WHERE runner_id=? AND runtime=?",
        (body.runner_id, body.runtime),
    ).fetchone()
    want = sub["id"] if sub else None
    out = {"runner_id": body.runner_id, "runtime": body.runtime, "subscription_id": want, "person": person}
    had = (current["person"] or None, None if current["person"] else current["subscription_id"]) if current else None
    if had == (person or None, want) or (not current and not person and not want):
        return {
            **out,
            "subscription_id": current["subscription_id"] if current else None,
            "stopped": 0,
            "sessions_reset": 0,
        }
    if not body.confirm:
        plan = _plan(c, settings, body.runner_id, body.runtime)
        if plan["running"]:
            _confirm(plan)
    if person:
        _set(c, body.runner_id, body.runtime, "", who.actor, person)
        first = _pick(c, body.runner_id, body.runtime, _given(c)[(body.runner_id, body.runtime)])
        if first:
            c.execute(
                "UPDATE computer_subscriptions SET subscription_id=? WHERE runner_id=? AND runtime=?",
                (first["id"], body.runner_id, body.runtime),
            )
        out["subscription_id"] = first["id"] if first else None
    elif want:
        _set(c, body.runner_id, body.runtime, want, who.actor)
    else:
        c.execute("DELETE FROM computer_subscriptions WHERE runner_id=? AND runtime=?", (body.runner_id, body.runtime))
    done = _restart(c, settings, body.runner_id, body.runtime)
    if person:
        action, target = "subscription.person_given", person
    else:
        action = "subscription.given" if want else "subscription.taken_off"
        target = want or current["person"] or current["subscription_id"]
    H.event(c, who.actor, action, target, {"runner": body.runner_id, "runtime": body.runtime, **done})
    return {**out, **done}


def _set(c, runner_id, runtime, subscription_id, actor, person=""):
    """Record that the computer signs `runtime` in with the subscription, or with `person`'s plans: one per runtime
    (`give` and `lend` write it)."""
    c.execute(
        "INSERT INTO computer_subscriptions(runner_id,runtime,subscription_id,set_by,updated_at,person) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(runner_id,runtime) DO UPDATE SET "
        "subscription_id=excluded.subscription_id,set_by=excluded.set_by,updated_at=excluded.updated_at,"
        "person=excluded.person",
        (runner_id, runtime, subscription_id, actor, H.now(), person),
    )


def carry_over(c, old_id, new_id):
    """A computer made in place of one that is gone (a cloud machine replaced or made again: cloud_machines.enrolled)
    takes what the old one was given, runtime by runtime: the same subscription, or the same person's plans, with who
    set it. Nothing it already has is replaced, and a subscription removed since is not brought back. Nothing is
    stopped or reset: the new computer has run nothing yet. Returns the runtimes carried over."""
    carried = []
    for r in c.execute(
        "SELECT runtime,subscription_id,person,set_by FROM computer_subscriptions WHERE runner_id=? ORDER BY runtime",
        (old_id,),
    ).fetchall():
        if not r["person"] and not row(c, r["subscription_id"]):
            continue
        if c.execute(
            "SELECT 1 FROM computer_subscriptions WHERE runner_id=? AND runtime=?", (new_id, r["runtime"])
        ).fetchone():
            continue
        _set(c, new_id, r["runtime"], r["subscription_id"], r["set_by"], r["person"])
        H.event(
            c,
            H.TICO_ACTOR,
            "subscription.carried_over",
            r["person"] or r["subscription_id"],
            {"runner": new_id, "from": old_id, "runtime": r["runtime"]},
        )
        carried.append(r["runtime"])
    return carried


def _why_moved(c, entry, runtime):
    """Why a person's set left the plan it was on: it is gone, given to someone else, needs reconnecting or is held."""
    old = next((m for m in entry["members"] if m["id"] == entry["id"]), None)
    if not entry["id"]:
        return "removed"
    if not old:
        return "left_set"
    if old["status"] == "needs_reconnect":
        return "needs_reconnect"
    return "held" if _held(c, old["id"], runtime) else "available"


def moves_on(c, runner_id, runtime):
    """Whether a computer given a person's plans for `runtime` has another plan to sign its next turn in with than the
    one the set is on: after a limit is met on that plan, its bots' held work runs on the next one, not at its reset."""
    entry = _given(c).get((runner_id, runtime))
    if not entry or not entry.get("person"):
        return False
    on = in_effect(c, runner_id, runtime)
    return bool(on) and on["id"] != entry["id"]


def settle(c, settings, runner_id, chosen):
    """A claim on a computer given a person's plans: when the turn signs in with another plan than the set was on (the
    old one is held, needs reconnecting, is gone or left the set, or a hold has ended after every plan was held), the
    set moves to it, between turns, never in one, and the move is an event (`subscription.set_moved`: computer, tool,
    person, from, to, why, and how many sessions were reset). Claude's sessions are kept, so the next turn resumes each
    on the new plan (`keeps_sessions`); otherwise every session of that runtime on the computer starts fresh, as a
    change of subscription does. Runs already going on the old plan are left to finish. Call it before the claim reads
    its session epochs."""
    for item in chosen:
        runtime, sub = item["runtime"], item["subscription"]
        entry = _given(c).get((runner_id, runtime))
        if not entry or not entry.get("person") or entry["id"] == sub["id"]:
            continue
        why = _why_moved(c, entry, runtime)
        c.execute(
            "UPDATE computer_subscriptions SET subscription_id=? WHERE runner_id=? AND runtime=?",
            (sub["id"], runner_id, runtime),
        )
        kept = keeps_sessions(c, runner_id, runtime)
        reset = 0 if kept else _reset(c, settings, runner_id, runtime)
        H.event(
            c,
            H.TICO_ACTOR,
            "subscription.set_moved",
            sub["id"],
            {
                "runner": runner_id,
                "runtime": runtime,
                "person": entry["person"],
                "from": entry["id"],
                "to": sub["id"],
                "why": why,
                "sessions_reset": reset,
                "sessions_kept": kept,
            },
        )


def bot_runtimes(c, settings, bot):
    """The subscription runtimes a bot may run on its computer: its own turns' (as `_bots` reads them) and its task
    model's, which a task runs on when it is set (backend/task_models.py `apply`, the config a task turn runs with).
    Each goes through `turn_runtimes`, so a Tico model and a desktop-app bot count for none."""
    from . import providers, task_models
    from .shared_bots import follow

    found = c.execute(BS.ONE_SQL, (bot,)).fetchone()
    config = json.loads(found["config"] or "{}") if found else {}
    filled = providers.fill(providers.load(c, settings), follow(c, bot, config))
    out = turn_runtimes(filled)
    task = task_models.of(filled)
    if task:
        out += turn_runtimes(task_models.apply(filled, task))
    return list(dict.fromkeys(out))


def is_admin(auth, who):
    """Whether this request would pass `_admin`."""
    try:
        _admin(auth, who)
    except Problem:
        return False
    return True


def may_grant(c, auth, actor):
    """Whether `actor` would pass `_admin` today, judged from its id: a placement records who chose the machine, not a
    request's identity. The owner, or a person on the roster whose email is a bot admin's, and not left or switched off.
    Never Tico support, a bot, a runner or a service."""
    from .auth import standing

    actor = str(actor or "")
    if not actor.startswith("human:") or H.actor_id(actor) == "tico-support":
        return False
    pid = H.actor_id(actor)
    if pid == auth.owner_id(c):
        return True
    human = H.human(c, pid)
    return bool(human) and not standing(c, pid) and str(human.get("email") or "").lower() in auth.bot_admins


def lend(c, runner_id, runtime, actor, shared, others, only=None):
    """Give a computer the company's plan for `runtime` when it has no plan and no working sign-in of its own there, so
    a bot on it is not left waiting for a sign-in nobody will make. Nothing is replaced, stopped or reset: a computer
    with a plan, a sign-in that works or has not said (any state but missing, failed or rejected), or a live run on that
    runtime, is left as it is (returns None, and the next placement asks again). The plan is the first that works,
    preferring one already given to the shared machine's computer (`shared`), then to another cloud machine's
    (`others`), then the lowest known used percent, then the most recently added; with `only`, that plan or none. Only
    subscription plans count (an API key bills per use, so it is never chosen for a computer). Returns the subscription
    id given, or None when no plan fits."""
    from . import login_holds
    from .readiness import SIGNED_OUT

    runner = c.execute(
        "SELECT readiness FROM runners_with_state WHERE id=? AND revoked_at IS NULL", (runner_id,)
    ).fetchone()
    if (
        not runner
        or c.execute(
            "SELECT 1 FROM computer_subscriptions WHERE runner_id=? AND runtime=?", (runner_id, runtime)
        ).fetchone()
    ):
        return None
    own = (readiness_document(runner["readiness"]).get("runtimes") or {}).get(runtime) or {}
    if own.get("authenticated") not in (None, *SIGNED_OUT) or _live(c, runner_id, runtime):
        return None
    kinds = [
        kind for kind, spec in KINDS.items() if spec["runtime"] == runtime and spec.get("billing") == "subscription"
    ]
    subs = H._rows(
        c.execute(
            f"SELECT * FROM {SUBS} WHERE kind IN ({','.join('?' * len(kinds))}) "
            "AND status<>'needs_reconnect'" + (" AND id=?" if only else "") + " ORDER BY created_at DESC",
            [*kinds, *([only] if only else [])],
        )
    )
    cloud = {r: 0 if r == shared else 1 for r in [shared, *others] if r}
    on_cloud = {}
    for r in c.execute("SELECT runner_id,subscription_id FROM computer_subscriptions WHERE runtime=?", (runtime,)):
        if r["runner_id"] in cloud:
            on_cloud[r["subscription_id"]] = min(cloud[r["runner_id"]], on_cloud.get(r["subscription_id"], 1))
    plans = []
    ctx = _listing(c) if subs else None
    for sub in subs:
        if login_holds.holding(c, "", login_holds.subscription_login(sub["id"]), runtime) == "held":
            continue
        where = on_cloud.get(sub["id"])
        tier = 2 if where is None else where  # shared machine's, another cloud machine's, the rest
        used = (_usage(c, sub, ctx) or {}).get("used_pct")
        plans.append(((tier, used is None, used or 0), sub))
    if not plans:
        return None
    chosen = min(plans, key=lambda p: p[0])[1]  # min is first-seen on a tie: the newest added
    _set(c, runner_id, runtime, chosen["id"], actor)
    H.event(
        c,
        actor,
        "subscription.given",
        chosen["id"],
        {"runner": runner_id, "runtime": runtime, "stopped": 0, "sessions_reset": 0},
    )
    return chosen["id"]


def _follow(c, settings, actor):
    """The plans the company has changed: the bots that follow them move to their set (backend/plan_models.py)."""
    from . import plan_models

    plan_models.follow(c, settings, actor)


def spread(c, settings, sid, actor, signed_on=""):
    """A plan just added, connected or reconnected goes on the computers that need it, by `lend`'s rules (nothing
    replaced, stopped or reset): the computer a Connect signed it in on (`signed_on`), then on a Tico Cloud company each
    cloud machine's computer, the shared one first; on a company with one computer, that one. With several computers of
    its own, a company chooses where (Settings > Computers). A plan placed before the bots is otherwise never on a
    computer, and they run on Tico Medium. Returns the computer ids given it."""
    from . import cloud_machines

    sub = row(c, sid)
    if not sub or sub["status"] == "needs_reconnect":
        return []
    spec = KINDS.get(sub["kind"]) or {}
    if spec.get("billing") != "subscription":
        return []
    shared, others = cloud_machines.lend_order(c)
    if cloud_machines.cloud_company(c, settings):
        computers = [signed_on, shared, *others]
    else:
        live = [r[0] for r in c.execute("SELECT id FROM runners WHERE revoked_at IS NULL")]
        computers = [signed_on, *(live if len(live) == 1 else [])]
    return [
        runner_id
        for runner_id in dict.fromkeys(r for r in computers if r)
        if lend(c, runner_id, spec["runtime"], actor, shared, others, only=sid)
    ]


# ------------------------------------------------------------------------------------------------ keys on computers
# A key kept in the vault (`credential_id`) is a subscription like any other here. Before D11-4 a key was added from a
# computer in Settings > Computers (its credential's source is `computer:<runner id>`: that computer's own key); any
# other key given to computers is shared. Two things still read it that way: runners of v0.6.0-rc.1 on, which fetch
# their key from `GET runner-model-credentials` and write it to their key files (`keys_for`), and the Computers page,
# which says which key each computer uses (`key_listing`).
OWN = "computer:"
# The key a computer is given for a runtime: its subscription and its vault credential (`id` is the credential's).
KEY_SQL = (
    "SELECT cr.*, s.id AS subscription_id, s.revision AS subscription_revision, g.updated_at AS given_at "
    "FROM computer_subscriptions g JOIN model_subscriptions s ON s.id=g.subscription_id "
    "JOIN credentials cr ON cr.id=s.credential_id WHERE g.person='' AND cr.ciphertext IS NOT NULL "
    "AND cr.kind<>'service'"
)


def drop_credential(c, credential_id):
    """Delete a key's vault value once no subscription points at it and nobody else holds a grant to it."""
    if c.execute("SELECT 1 FROM model_subscriptions WHERE credential_id=?", (credential_id,)).fetchone():
        return
    if c.execute(
        "SELECT 1 FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL "
        "AND subject<>'computers' AND subject NOT LIKE 'computer:%'",
        (credential_id,),
    ).fetchone():
        return
    c.execute("DELETE FROM credential_grants WHERE credential_id=?", (credential_id,))
    c.execute("DELETE FROM credentials WHERE id=?", (credential_id,))


def before_delete(c, who, credential_id):
    """Credentials > Delete on a key given to computers: its subscription goes with it, and each computer given it is
    cleared (`model_login.mark_key_cleared`): told at its next key fetch to delete its copies and log out the login the
    key made, which a v0.6.0-rc.1 runner keeps for Codex until told so."""
    if not _present(c):
        return
    sub = c.execute("SELECT id FROM model_subscriptions WHERE credential_id=?", (credential_id,)).fetchone()
    if not sub:
        return
    given = c.execute(
        "SELECT runner_id, runtime FROM computer_subscriptions WHERE subscription_id=? AND person=''", (sub["id"],)
    ).fetchall()
    c.execute("DELETE FROM computer_subscriptions WHERE subscription_id=? AND person=''", (sub["id"],))
    c.execute("DELETE FROM model_subscriptions WHERE id=?", (sub["id"],))
    from . import claude_login

    claude_login.drop(c, sub["id"])
    from .model_login import mark_key_cleared

    for g in given:
        mark_key_cleared(c, who, g["runner_id"], g["runtime"])
        H.event(
            c,
            who.actor,
            "computer_key.deleted",
            g["runner_id"],
            {"runtime": g["runtime"], "credential": credential_id, "cleared": True},
        )


def keys_for(c, vault, runner_id, runtime, login_id=""):
    """What `GET runner-model-credentials` hands this computer for `runtime`: nothing while a person cleared its key
    or signed its login out (`clear_key` tells one that missed it while offline to clear its copy now), else the key it
    is given, as `computer` (its own) or `shared`. A fetch for a key job (`login_id`) gets nothing: no job stages a key
    any more. The reply keeps the shape runners of v0.6.0-rc.1 read. Returns (reply, credential ids sent)."""
    from . import providers

    if login_id:
        return {"credentials": []}, []
    mark = c.execute(
        "SELECT clear_key FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=''", (runner_id, runtime)
    ).fetchone()
    if mark:
        return {"credentials": [], "signed_out": True, **({"clear_key": True} if mark["clear_key"] else {})}, []
    found = c.execute(KEY_SQL + " AND g.runner_id=? AND g.runtime=?", (runner_id, runtime)).fetchone()
    if not found or found["env"] not in providers.MODEL_KEYS.get(runtime, ()):
        return {"credentials": []}, []
    source = "computer" if found["source"] == OWN + runner_id else "shared"
    return {"credentials": [{"env": found["env"], "value": vault.cipher.decrypt(c, found), "source": source}]}, [
        found["id"]
    ]


def key_listing(c):
    """{runner id: {keys, shared_keys, shared_with}} of every computer given a key, read in one pass for the Computers
    page (Operations listing); never a value. `keys`: {runtime: {env, preview, since}} of its own keys. `shared_keys`:
    {runtime: {env, preview}} of the shared key given to it. `shared_with`: {runtime: {id, name, computers}} of the
    shared key it runs on (not while a person cleared its key or signed it out there) and every live computer that
    runs on the same one."""
    if not _present(c):
        return {}
    out, served, used = {}, {}, []
    for row in c.execute(
        "SELECT g.runner_id, g.runtime, g.updated_at AS given_at, cr.id, cr.name, cr.env, cr.preview, cr.source, "
        "r.label, r.revoked_at, EXISTS (SELECT 1 FROM model_login_cleared m WHERE m.runner_id=g.runner_id "
        "AND m.runtime=g.runtime AND m.profile='') AS cleared "
        "FROM computer_subscriptions g JOIN model_subscriptions s ON s.id=g.subscription_id "
        "JOIN credentials cr ON cr.id=s.credential_id JOIN runners r ON r.id=g.runner_id "
        "WHERE g.person='' AND cr.ciphertext IS NOT NULL AND cr.kind<>'service' "
        "ORDER BY lower(r.label), r.id, g.runtime"
    ):
        rid, runtime = row["runner_id"], row["runtime"]
        found = out.setdefault(rid, {"keys": {}, "shared_keys": {}, "shared_with": {}})
        if row["source"] == OWN + rid:
            found["keys"][runtime] = {"env": row["env"], "preview": row["preview"], "since": row["given_at"]}
            continue
        found["shared_keys"][runtime] = {"env": row["env"], "preview": row["preview"]}
        if row["revoked_at"] is None and not row["cleared"]:
            served.setdefault(row["id"], []).append({"id": rid, "label": row["label"]})
            used.append((rid, runtime, row))
    for rid, runtime, row in used:
        out[rid]["shared_with"][runtime] = {"id": row["id"], "name": row["name"], "computers": served[row["id"]]}
    return out


# ------------------------------------------------------------------------------------------------ claim and run
def _bot_key_names(c, bot):
    """The model key variables `bot`'s own key grants set in its turns (backend/credentials.py `granted`)."""
    from .credentials import granted

    ids = sorted(granted(c, "bot:" + bot)) if bot else []
    if not ids:
        return set()
    return {
        r["env"]
        for part in H.chunks(ids)
        for r in c.execute(
            f"SELECT env FROM credentials WHERE id IN ({','.join('?' * len(part))}) AND ciphertext IS NOT NULL", part
        )
    }


def for_claim(c, runner, config, loads=None, bot=""):
    """The subscriptions a claimed turn may use, with automatic candidates limited to installed runtimes. `loads`: the
    claim's loaded values (claim_loads), when the caller has them. A computer's key (one kept in the vault) is not
    handed to a turn of `bot` when the bot's own key grant names that runtime's key: the grant signs it in, as it
    always came before the computer's key (v0.6.0-rc.1 runners take out a granted key for a subscription handed)."""
    given = loads.given if loads is not None else _given(c)
    if not runner or not given:
        return []
    from . import providers
    from .store import readiness_document

    own = turn_runtimes(config)
    if loads is not None:
        row = loads.runner_of(c, runner["id"])
        record = loads.plain_record(c)
    else:
        from .read_cache import one

        row = one(c, RUNNER_READINESS_SQL, (runner["id"],))
        record = providers.load(c)
    ready = readiness_document(row["readiness"]) if row else {}
    out = []
    runtimes = turn_runtimes(config, record)
    # A plan login the computer keeps in a home (a ChatGPT login) also signs its CLI in for the turn's own commands:
    # a bot on Claude runs `codex exec` on it (runner-rs/src/model_subscriptions.rs `for_shell`).
    shell = [rt for (rid, rt) in given if rid == runner["id"] and rt in RUNTIMES and rt not in runtimes]
    for runtime in [*runtimes, *shell]:
        if runtime not in own and ((ready.get("runtimes") or {}).get(runtime) or {}).get("installed") is not True:
            continue  # a subscription authenticates a CLI; it cannot install one
        sub = in_effect(c, runner["id"], runtime, loads=loads)
        if sub and runtime in shell and not for_shell(sub):
            continue
        if (
            sub
            and bot
            and c.execute(
                f"SELECT 1 FROM model_subscriptions WHERE id=? AND {KEPT_KEY.format('')}", (sub["id"],)
            ).fetchone()
            and set(providers.MODEL_KEYS.get(runtime, ())) & _bot_key_names(c, bot)
        ):
            continue
        if sub:
            # Another runtime's value is sent only if the turn moves to it (`activate`), except a shell login, which
            # every process of the turn gets from the start.
            lazy = runtime not in own and not for_shell(sub)
            out.append({"runtime": runtime, "subscription": sub, "activate": lazy})
    return out


def for_shell(sub):
    """Whether a subscription signs its CLI in for any turn's own commands on its computer: a plan login kept in the
    CLI's home (a ChatGPT login), never an API key, which spends per use."""
    spec = KINDS.get(sub.get("kind")) or {}
    return bool(spec.get("file")) and spec.get("billing") == "subscription"


def hand_out(c, attempt_id, chosen):
    """Record what the claim handed this attempt, and the attempt's metadata for the runner (no values)."""
    out = []
    for item in chosen:
        sub = item["subscription"]
        c.execute(
            "INSERT OR REPLACE INTO attempt_subscriptions(attempt_id,runtime,subscription_id,revision) VALUES(?,?,?,?)",
            (attempt_id, item["runtime"], sub["id"], sub["revision"]),
        )
        if item.get("activate"):
            c.execute(
                "INSERT OR IGNORE INTO pending_subscription_fallbacks(attempt_id,runtime) VALUES(?,?)",
                (attempt_id, item["runtime"]),
            )
        spec = KINDS[sub["kind"]]
        out.append(
            {
                **({"activate": True} if item.get("activate") else {}),
                "id": sub["id"],
                "runtime": item["runtime"],
                "kind": sub["kind"],
                "name": sub["name"],
                "revision": sub["revision"],
                "billing": spec["billing"],
                "source": "computer",
                **({"env": spec["env"]} if spec.get("env") else {"file": spec["file"]}),
                **({"home_env": spec["home_env"]} if spec.get("home_env") else {}),
            }
        )
    return out


def run_values(c, vault, who, subscription_runtime=None):
    """What `GET credential-runtime` adds for the bot run `who`: each subscription its claim handed it, with its value.
    Only the run's own attempt, only while it is live, only while its computer is still given that subscription, and
    only the revision it was handed. Automatic candidates on capable runners are metadata until requested by
    `subscription_runtime`; delivering the value atomically activates the restart fence. A reconnect or a take-off
    stops every live run already sent that value (`_restart`): such a run
    gets nothing and ends. A run still going whose subscription moved on was moved by a refresh (`refreshed`, the same
    account): it gets the refreshed value and its record moves with it, so its home, its write-back and its verdict
    agree."""
    if not who.attempt_id or not H._has_table(c, "attempt_subscriptions"):
        return []
    attempt = c.execute(
        "SELECT runner_id,state,bot_id,stop_requested_at FROM attempts WHERE id=?", (who.attempt_id,)
    ).fetchone()
    if not attempt or attempt["state"] not in ("leased", "running") or "bot:" + attempt["bot_id"] != who.actor:
        return []
    out = []
    for given in c.execute(
        "SELECT * FROM attempt_subscriptions WHERE attempt_id=? ORDER BY runtime", (who.attempt_id,)
    ).fetchall():
        pending = c.execute(
            "SELECT 1 FROM pending_subscription_fallbacks WHERE attempt_id=? AND runtime=?",
            (who.attempt_id, given["runtime"]),
        ).fetchone()
        if pending and subscription_runtime != given["runtime"]:
            continue
        sub = row(c, given["subscription_id"])
        if (
            not sub
            or sub["status"] == "needs_reconnect"
            or not still_given(c, attempt["runner_id"], given["runtime"], sub["id"])
        ):
            continue
        if pending:
            # Serialized with give/reconnect/remove: once a value leaves, a later change must stop this run.
            effective = in_effect(c, attempt["runner_id"], given["runtime"])
            if attempt["stop_requested_at"] or not effective or effective["id"] != sub["id"]:
                continue
            c.execute(
                "DELETE FROM pending_subscription_fallbacks WHERE attempt_id=? AND runtime=?",
                (who.attempt_id, given["runtime"]),
            )
        if sub["revision"] != given["revision"]:
            if attempt["stop_requested_at"]:
                continue  # stopped (a reconnect restarts its runs): it ends without the newer value
            c.execute(
                "UPDATE attempt_subscriptions SET revision=? WHERE attempt_id=? AND runtime=?",
                (sub["revision"], who.attempt_id, given["runtime"]),
            )
        value = value_of(c, vault.cipher, sub)
        if value is None:
            continue
        spec = KINDS[sub["kind"]]
        out.append(
            {
                "id": sub["id"],
                "runtime": given["runtime"],
                "kind": sub["kind"],
                "name": sub["name"],
                "revision": sub["revision"],
                "billing": spec["billing"],
                **delivered(sub["kind"], value),
            }
        )
        H.event(
            c,
            who.actor,
            "subscription.sent_to_computer",
            sub["id"],
            {"attempt": who.attempt_id, "runner": attempt["runner_id"], "runtime": given["runtime"]},
        )
    return out


def refreshed(c, vault, who, body):
    """A runner sends back a ChatGPT login its CLI refreshed in a turn it ran on it. Only from a live run (leased or
    running on this computer, its lease current, not stopped by Tico) that was handed that subscription and whose
    computer is still given it for that runtime: a finished, expired, restarted or quarantined run, or a computer the
    subscription was taken off, writes nothing (409 `stale_lease`). Kept only when the subscription is still at the
    revision the turn had and the file is the same account; anything else is ignored (another computer refreshed first,
    or someone reconnected). A kept file moves the run's record to the new revision with it."""
    if who.role != "runner":
        raise Problem("forbidden", "Only a registered computer may send this", 403)
    given = c.execute(
        "SELECT s.runtime,s.revision,a.state,a.lease_until,a.stop_requested_at FROM attempt_subscriptions s "
        "JOIN attempts a ON a.id=s.attempt_id WHERE s.attempt_id=? AND s.subscription_id=? "
        "AND a.runner_id=? AND NOT EXISTS (SELECT 1 FROM pending_subscription_fallbacks p "
        "WHERE p.attempt_id=s.attempt_id AND p.runtime=s.runtime)",
        (body.attempt, body.subscription, who.runner_id),
    ).fetchone()
    sub = row(c, body.subscription)
    if not given or not sub or sub["kind"] != "codex_login":
        raise Problem("forbidden", "That run was not handed this subscription on this computer", 403)
    if (
        given["state"] not in ("leased", "running")
        or (given["lease_until"] or "") <= H.now()
        or given["stop_requested_at"] == H.TICO_ACTOR
        or not still_given(c, who.runner_id, given["runtime"], sub["id"])
    ):
        raise Problem("stale_lease", "That run has ended or no longer signs in with this subscription", 409)
    if sub["revision"] != body.revision or given["revision"] != body.revision:
        return {"stored": False}
    value, preview = checked("codex_login", body.value.get_secret_value())
    old = json.loads(value_of(c, vault.cipher, sub) or "{}")
    account = lambda v: (v.get("tokens") or {}).get("account_id") or ""
    if account(old) and account(json.loads(value)) != account(old):
        return {"stored": False}
    if value == json.dumps(old, separators=(",", ":")):
        return {"stored": False}
    now = H.now()
    c.execute(
        "UPDATE model_subscriptions SET credential_id=?,sealed='',preview=?,revision=revision+1,updated_at=? WHERE id=?",
        (_keep_value(c, vault, sub, value, preview, who.actor, now), preview, now, sub["id"]),
    )
    c.execute(
        "UPDATE attempt_subscriptions SET revision=? WHERE attempt_id=? AND runtime=?",
        (sub["revision"] + 1, body.attempt, given["runtime"]),
    )
    H.event(c, who.actor, "subscription.refreshed", sub["id"], {"attempt": body.attempt})
    return {"stored": True, "revision": sub["revision"] + 1}


def handed(c, attempt_id, runtime):
    return (
        H._one(c, "SELECT * FROM attempt_subscriptions WHERE attempt_id=? AND runtime=?", (attempt_id, runtime))
        if attempt_id and H._has_table(c, "attempt_subscriptions")
        else None
    )


def _status(c, sid, status, detail=""):
    c.execute(
        "UPDATE model_subscriptions SET status=?,status_detail=?,status_at=? WHERE id=? AND status<>?",
        (status, detail[:300], H.now(), sid, status),
    )


def turn_rejected(c, settings, attempt_id, bot, runtime, reason, task_id):
    """The provider refused the turn's sign-in for `runtime`. When the turn ran on a subscription for it, that
    subscription needs reconnecting and is passed over from the next turn on (the computer's own login or the Gateway
    takes the requeued work); the computer's own login is not touched and no card holds the task. A refusal of a
    revision already replaced while the turn ran (a reconnect, or another computer's refresh) leaves the new value as it
    is. Returns the subscription row, or None when the turn was on the computer's login."""
    found = handed(c, attempt_id, runtime)
    sub = row(c, found["subscription_id"]) if found else None
    if not sub:
        return None
    if sub["status"] != "needs_reconnect" and sub["revision"] == found["revision"]:
        _status(c, sub["id"], "needs_reconnect", reason or "The provider refused it")
        _effect_changed(c, settings, sub["id"])
        _follow(c, settings, H.TICO_ACTOR)
    H.event(
        c,
        H.TICO_ACTOR,
        "subscription.rejected",
        sub["id"],
        {"attempt": attempt_id, "runtime": runtime, "bot": bot, "task": task_id or ""},
    )
    return sub


def turn_worked(c, attempt_id, runtime):
    """A turn on a subscription completed: it works."""
    found = handed(c, attempt_id, runtime)
    if not found:
        return
    sub = row(c, found["subscription_id"])
    if sub and sub["revision"] == found["revision"]:
        _status(c, sub["id"], "working")


def assignment_fields(c, runner, config):
    """What `runners/assignments` adds to a bot's entry: {"subscriptions": {runtime: kind}} when the computer's
    subscription covers a runtime, so the runner does not count that runtime's missing sign-in against the bot."""
    covered_now = covered_runtimes(c, runner, config)
    return {"subscriptions": covered_now} if covered_now else {}


# ------------------------------------------------------------------------------------------------ routes
# ------------------------------------------------------------------------------------------------ connect
def _connect_computers(c, runtime, who=None):
    """The computers that can run `runtime`'s sign-in for a Connect: the person's own first, since the result passes
    through the computer, then those with nothing signing in for that tool, then the one seen last."""
    from . import model_login

    if runtime not in model_login.CONNECT_RUNTIMES:
        return []
    busy = {
        row["runner_id"]
        for row in c.execute(
            f"SELECT runner_id FROM model_logins WHERE runtime=? AND {model_login.ACTIVE_SQL} AND expires_at>=?",
            (runtime, H.now()),
        )
    }
    me = H.actor_id(who.actor) if who else ""
    found = []
    for runner in c.execute("SELECT * FROM runners_with_state WHERE revoked_at IS NULL").fetchall():
        report = readiness_document(runner["readiness"]).get("runtimes") or {}
        if model_login._online(runner) and (report.get(runtime) or {}).get("installed"):
            found.append(runner)
    found.sort(key=lambda r: r["last_seen_at"] or "", reverse=True)
    found.sort(key=lambda r: (r["owner_id"] != me, r["id"] in busy))
    return found


def connect_ready(c):
    """The kinds a person can Connect right now: their tool's sign-in has a computer to run on."""
    return [
        kind
        for kind, spec in KINDS.items()
        if spec.get("connect") and not spec.get("hidden") and _connect_computers(c, spec["runtime"])
    ]


def connect(c, settings, who, body):
    """Start a Connect: queue the sign-in on a computer, remembering what its result becomes. Read back, given a code
    and cancelled like any sign-in (backend/model_login.py). Its row says where (`computer`) and what the subscription
    is called (`name`: the one given, else `default_name`). With no computer online on a company whose cloud computer
    can be started, it starts that one and answers `waking` with no sign-in: asked again, it queues once that is up.

    A Claude plan is signed in with a full login (`claude auth login --claudeai`, kind `claude_login`) on a computer
    that can (backend/claude_login.py `can_sign_in`), and named by its email; only when none can does a new plan, or a
    setup-token plan's Reconnect, fall back to `claude setup-token`. A setup-token plan reconnected this way becomes a full
    login. A full login's Reconnect needs such a computer."""
    from . import claude_login, model_login

    name, sid = (body.name or "").strip(), body.subscription or ""
    sub = None
    if sid:
        sub = row(c, sid)
        if not sub:
            raise Problem("not_found", "Subscription not found", 404)
        kind = sub["kind"]
    else:
        kind = body.kind or ""
    spec = KINDS.get(kind)
    if not spec or not spec.get("connect"):
        raise Problem("kind", "Connect works for a Claude plan or a ChatGPT plan; paste an API key instead", 422)
    if sid:
        if not body.confirm:
            _confirm_reconnect(c, settings, _signed_in_with(c, sid))
    elif c.execute("SELECT count(*) FROM model_subscriptions").fetchone()[0] >= MAX_SUBSCRIPTIONS:
        raise Problem("limit", f"The company has {MAX_SUBSCRIPTIONS} subscriptions; remove one first", 422)
    # A reconnect keeps its owner; a new one is checked now, before anyone signs in.
    owner = "" if sid else _default_owner(c, who, body, kind)

    def answer(login, runner):
        return {
            **model_login.view(login),
            "computer": runner["label"],
            "not_yours": runner["owner_id"] != H.actor_id(who.actor),
            "name": sub["name"] if sub else target["name"],
            "waking": False,
        }

    # Asked again (a reloaded page): the sign-in already going picks back up, wherever it runs. Looked for before a
    # name is made up for it: the one it was given is on its row.
    model_login.sweep(c)
    claude = ("claude_token", "claude_login")
    same = claude if kind in claude else (kind,)
    target = c.execute(
        f"SELECT l.id, s.name FROM model_logins l JOIN subscription_connects s ON s.login_id=l.id "
        f"WHERE l.kind='connect' AND l.requested_by=? AND s.kind IN ({','.join('?' * len(same))}) "
        f"AND s.subscription_id=? AND {model_login.ACTIVE_SQL}"
        + (" AND s.name=?" if name else "")
        + " ORDER BY l.created_at",
        (who.actor, *same, sid, *([name] if name else [])),
    ).fetchone()
    if target:
        going = c.execute("SELECT * FROM model_logins WHERE id=?", (target["id"],)).fetchone()
        return answer(going, model_login._runner(c, going["runner_id"]))
    computers = _connect_computers(c, spec["runtime"], who)
    if kind in claude:
        full = [r for r in computers if claude_login.can_sign_in(r)]
        if full or kind == "claude_login":
            computers, kind = full, "claude_login"
    # A ChatGPT login or a full Claude login is named for its account's email once it is saved (`connected`), so it
    # gets no name yet.
    target = {"name": name or ("" if sid or kind in ("codex_login", "claude_login") else default_name(c, kind))}
    if not computers and kind == "claude_login" and sub and sub["kind"] == "claude_login":
        raise Problem(
            "offline",
            "Reconnect needs a Linux computer online, on the latest Tico, with Claude Code installed",
            409,
        )
    if not computers:
        # No computer is online to run it, and the company has a cloud computer: it is started for the sign-in, and
        # the page asks again until it is up (`waking`, no sign-in yet).
        from . import cloud_machines

        if cloud_machines.signin_wake(c, settings):
            return {
                "id": "",
                "kind": "connect",
                "runner_id": "",
                "runtime": spec["runtime"],
                "state": "requested",
                "computer": "",
                "not_yours": False,
                "name": sub["name"] if sub else target["name"],
                "waking": True,
            }
        raise Problem(
            "offline",
            "Connect needs a computer online, on the latest Tico, with "
            f"{RUNTIME_LABELS[spec['runtime']]} installed. Paste instead, or start one",
            409,
        )
    runner = computers[0]
    login, new = model_login._queue(c, who, runner, spec["runtime"], "", "connect", "connect a subscription")
    # The job going there is for something else: its result must not land on this one.
    if not new and login["requested_by"] != who.actor:
        raise Problem(
            "state", f"Someone else is connecting a subscription on {runner['label']}; try again in a minute", 409
        )
    if not new:
        raise Problem(
            "state", f"You are connecting another subscription on {runner['label']}; finish or cancel that first", 409
        )
    c.execute(
        "INSERT INTO subscription_connects(login_id,kind,name,subscription_id,owner_id) VALUES(?,?,?,?,?)",
        (login["id"], kind, "" if sid else target["name"], sid, owner),
    )
    H.event(c, who.actor, "subscription.connect", sid or login["id"], {"kind": kind, "runner": runner["id"]})
    return answer(login, runner)


def connected(c, vault, settings, who, login_id, body):
    """A computer sends what its CLI gave it for a Connect it was asked to run: saved as the person asked, as if they
    had pasted it, and the job is done. A job cancelled or run out by now saves nothing. A value that does not check
    out fails the job, saying why (never the value)."""
    from types import SimpleNamespace

    from . import model_login

    model_login.sweep(c)
    login = c.execute(
        "SELECT * FROM model_logins WHERE id=? AND runner_id=? AND kind='connect'", (login_id, who.runner_id)
    ).fetchone()
    if not login:
        raise Problem("not_found", "That sign-in does not exist", 404)
    if login["state"] not in model_login.ACTIVE:
        raise Problem("state", "That sign-in has ended", 409)
    target = c.execute("SELECT * FROM subscription_connects WHERE login_id=?", (login_id,)).fetchone()
    state, message, category = "done", "", ""
    if not target:
        state, message, category = "failed", "That sign-in has ended", "ended"
    else:
        write = SubscriptionWrite(
            kind=target["kind"],
            name=target["name"] or None,
            secret=body.value,
            confirm=True,
            owner_id=(target["owner_id"] or None) if not target["subscription_id"] else None,
        )
        try:
            save(
                c,
                vault,
                settings,
                SimpleNamespace(actor=login["requested_by"]),
                write,
                target["subscription_id"] or None,
                signed_on=who.runner_id,
            )
        except Problem as problem:
            # A fixed word for the history: never the value, nor what the provider said about it.
            state, message, category = "failed", model_login.redact(problem.detail), "value_refused"
    c.execute(
        "UPDATE model_logins SET state=?, message=?, url='', user_code='', pending_code=NULL, lines='[]', "
        "updated_at=? WHERE id=?",
        (state, message, H.now(), login_id),
    )
    c.execute("DELETE FROM subscription_connects WHERE login_id=?", (login_id,))
    H.event(
        c,
        "runner:" + who.runner_id,
        "runner.login." + state,
        who.runner_id,
        {
            "runtime": login["runtime"],
            "login_id": login_id,
            "kind": "connect",
            **({"category": category} if category else {}),
        },
    )
    return {"state": state, "message": message}


def install(app, store, auth, mutate, settings):
    from fastapi import Request

    from .models import Empty

    vault = app.state.vault

    @app.get(
        "/api/v2/model-subscriptions",
        **api_v2(
            "Model subscriptions",
            "listModelSubscriptions",
            "Saved subscriptions and the kinds that can be added",
            "ModelSubscriptionList",
        ),
    )
    def list_subscriptions(request: Request):
        who = request.state.identity
        auth.domain(who)
        _reader(who)
        with store.read() as c:
            manages = _may_manage(auth, who)
            subscriptions = [
                sub | {"can_remove": may_remove(who, sub["added_by"], manages=manages)} for sub in listed(c)
            ]
            return {"subscriptions": subscriptions, "kinds": kinds(), "connect": connect_ready(c)}

    @app.post(
        "/api/v2/model-subscriptions",
        **api_v2(
            "Model subscriptions",
            "addModelSubscription",
            "Add a subscription: a kind, a name, the pasted value and, for a plan, optionally its owner, a person (default: you; an API key has none; owners and admins)",
            "ModelSubscription",
        ),
    )
    def add(request: Request, body: SubscriptionWrite):
        who = request.state.identity
        _admin(auth, who, secret=True)
        return vault.change(
            who,
            request.url.path,
            request.headers.get("idempotency-key"),
            body,
            lambda c, w: None,
            lambda c: save(c, vault, settings, who, body),
        )

    # Declared before `/{sid}`. It carries no value: that comes from the computer, to the route below.
    @app.post("/api/v2/model-subscriptions/connect")
    def start_connect(request: Request, body: SubscriptionConnect):
        who = request.state.identity
        _admin(auth, who, secret=True)
        return mutate(request, body, lambda c: connect(c, settings, who, body))

    @app.post(
        "/api/v2/model-subscriptions/{sid}",
        **api_v2(
            "Model subscriptions",
            "changeModelSubscription",
            "Rename a subscription, change a plan owner and/or reconnect it with a new value (owners and admins); a reconnect answers 409 `confirm_restart` while runs would stop unless `confirm` is true",
            "ModelSubscription",
        ),
    )
    def change(request: Request, sid: str, body: SubscriptionWrite):
        who = request.state.identity
        _admin(auth, who, secret=body.secret is not None)
        return vault.change(
            who,
            request.url.path,
            request.headers.get("idempotency-key"),
            body,
            lambda c, w: None,
            lambda c: save(c, vault, settings, who, body, sid),
        )

    @app.delete(
        "/api/v2/model-subscriptions/{sid}",
        **api_v2(
            "Model subscriptions",
            "removeModelSubscription",
            "Remove a subscription from every computer (its adder or an admin). "
            "Returns 409 `confirm_restart` if runs would stop without `confirm=1`",
            "ModelSubscriptionDeleted",
        ),
    )
    def delete(request: Request, sid: str, confirm: bool = False):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            if not _may_manage(auth, who):
                _reader(who)
                sub = row(c, sid)
                if sub is None or not may_remove(who, sub["added_by"]):
                    _admin(auth, who)
            return remove(c, settings, who, sid, confirm)

        return mutate(request, Empty(), work)

    # Read a Claude plan's week now (subscription_refresh.request_plan_read): admins, as for changing the plan.
    @app.post("/api/v2/model-subscriptions/{sid}/usage-refresh")
    def refresh_usage(request: Request, sid: str):
        from . import subscription_refresh as SR

        who = request.state.identity

        def work(c):
            auth.domain(who)
            _admin(auth, who)
            return SR.request_plan_read(c, sid, who.actor)

        return mutate(request, Empty(), work)

    @app.get(
        "/api/v2/computer-subscriptions",
        **api_v2(
            "Model subscriptions",
            "listComputerSubscriptions",
            "Which subscription each computer uses per runtime (for a person's plans: the plan in use and why), the people who own plans per runtime, and the computers that can take one",
            "ComputerSubscriptionList",
        ),
    )
    def computer_subscriptions(request: Request):
        who = request.state.identity
        auth.domain(who)
        _reader(who)
        with store.read() as c:
            return assignments(c)

    @app.put(
        "/api/v2/computer-subscriptions",
        **api_v2(
            "Model subscriptions",
            "setComputerSubscription",
            "Give a computer a subscription for one runtime, or a person's plans for it (`person`: used in turn, the soonest weekly reset with usage left first), switch it, or clear it (null); 409 `confirm_restart` while live runs would stop unless `confirm` is true",
            "ComputerSubscriptionResult",
        ),
    )
    def set_computer_subscription(request: Request, body: ComputerSubscription):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            _admin(auth, who)
            return give(c, settings, who, body)

        return mutate(request, body, work)

    # Not through `mutate`: a value is kept in no request receipt, as for the refresh below.
    @app.post("/api/v2/runner-logins/{lid}/subscription")
    def connect_value(request: Request, lid: str, body: SubscriptionConnected):
        who = request.state.identity
        with store.transaction() as c:
            if (
                who.role != "runner"
                or not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL", (who.runner_id,)).fetchone()
            ):
                raise Problem("forbidden", "Only a registered computer may send this", 403)
            return connected(c, vault, settings, who, lid, body)

    @app.post("/api/v2/runner-model-subscription-refresh")
    def refresh(request: Request, body: SubscriptionRefresh):
        who = request.state.identity
        with store.transaction() as c:
            if (
                who.role != "runner"
                or not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL", (who.runner_id,)).fetchone()
            ):
                raise Problem("forbidden", "Only a registered computer may send this", 403)
            return refreshed(c, vault, who, body)

    from . import claude_login

    claude_login.install(app, store)
