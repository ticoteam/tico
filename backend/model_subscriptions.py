"""Provider pools in Settings > Providers, local computer keys, and credentials bound to an agent turn.

People manage their own plans and API keys. Admins can manage any pool. Everyone can read
the provider list. Running bots and Agent Ops cannot save or reveal provider secrets.

An agent's Paid by choice is resolved for its turn's tool (backend/agent_subscriptions.py).
The claim keeps the payer's id and revision. Modern runners receive the credential with the
claim. Matching older runners use credential-runtime. A Codex shell can receive the agent's
own Codex payer. A computer's saved binding is used only for old-runner compatibility.
Provider usage holds follow the provider across computers.
"""

import base64
import json
from datetime import datetime, timezone

from pydantic import Field, SecretStr

from . import bot_settings as BS
from . import hubdb as H
from .credentials import SERVICE, keep_service
from .models import Contract, ID
from .store import Problem, encode, readiness_document
from .api_contract import KEYLESS, api_v2, error

STATUSES = ("new", "working", "needs_reconnect")
MAX_SUBSCRIPTIONS = 200
MAX_NAME = 80
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
    """Add a provider to your pool, reconnect it with a new `secret`, or rename it. If supplied, `owner_id` must match
    the provider's owner. `confirm` accepts that a reconnect stops and restarts the live runs on computers given it."""

    name: str | None = Field(default=None, max_length=MAX_NAME)
    owner_id: str | None = Field(default=None, max_length=120)
    kind: str | None = Field(default=None, max_length=40)
    secret: SecretStr | None = Field(default=None, max_length=MAX_VALUE)
    confirm: bool = False


class SubscriptionConnect(Contract):
    """Sign a plan in from the page: a new subscription (`kind`; `name`, else its plan's), or a saved one's new value
    (`subscription`).
    A new plan belongs to whoever connects it. If supplied, `owner_id` must match the plan's owner. `confirm` accepts
    that a reconnect stops and restarts the live runs on computers given it."""

    kind: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=MAX_NAME)
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


# The computers' grants as _given reads them: the FROM and WHERE, with `{runners}` an extra filter on g.runner_id.
_GIVEN_FROM = (
    "FROM computer_subscriptions g "
    "LEFT JOIN model_subscriptions s ON s.id=g.subscription_id "
    "LEFT JOIN claude_logins l ON l.subscription_id=s.id WHERE (s.id IS NOT NULL OR g.person<>'') "
    # A computer's key follows its login: not while a person cleared it or signed it out there
    # (backend/model_login.py model_login_cleared), as GET runner-model-credentials does (`keys_for`).
    f"AND NOT ({KEPT_KEY.format('s.')} AND EXISTS (SELECT 1 FROM model_login_cleared m "
    "WHERE m.runner_id=g.runner_id AND m.runtime=g.runtime AND m.profile=''))"
)


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
    found = _given_rows(c) if _present(c) else {}
    if values is not None:
        values["model_subscriptions.given"] = found
    return found


def given_of(c, runner_ids):
    """_given for these computers only: the same entries for their keys, and a mapping that reads the whole of _given
    for any other key, an iteration or its length (claim_loads.Part). A claim's check of one job reads only its
    computer's grants under the write lock, not every computer's."""
    from . import read_cache
    from .claim_loads import Part

    values = read_cache.cache(c)
    if values is not None and "model_subscriptions.given" in values:
        return values["model_subscriptions.given"]
    wanted = set(runner_ids)
    if not _present(c):
        return {}
    return Part(
        _given_rows(c, sorted(wanted)) if wanted else {},
        lambda key: isinstance(key, tuple) and len(key) == 2 and key[0] in wanted,
        lambda: _given(c),
        # Whether any computer is given one, read now with the rows (the whole read's truth), once.
        c.execute("SELECT 1 " + _GIVEN_FROM + " LIMIT 1").fetchone() is not None,
    )


def _keys_of(given, runner_id):
    """The (runner_id, runtime) keys `given` holds for this computer, in its order, without reading every computer's
    when `given` is a claim's narrow read that holds this one's (given_of)."""
    from .claim_loads import Part

    keys = given.rows if isinstance(given, Part) and given.inside((runner_id, "")) else given
    return [key for key in keys if key[0] == runner_id]


def _given_rows(c, runner_ids=None):
    """_given's entries, for every computer or (`runner_ids`) for these."""
    from .subscription_refresh import PLAN_PREFIX

    found = {}
    people = set()
    # In table order, whole or for a few computers, so one computer's keys come in the same order either way (for_claim
    # takes a computer's other runtimes in it).
    only = f" AND g.runner_id IN ({','.join('?' * len(runner_ids))})" if runner_ids is not None else ""
    for r in c.execute(
        "SELECT g.runner_id AS runner_id,g.runtime AS runtime,g.person AS person,s.id,s.name,s.kind,s.revision,"
        "s.status,l.access_expires_at AS access_expires_at " + _GIVEN_FROM + only + " ORDER BY g.rowid",
        list(runner_ids or ()),
    ):
        sub = {k: r[k] for k in ("id", "name", "kind", "revision", "status", "access_expires_at")}
        if r["person"]:
            sub = {**{k: v or "" for k, v in sub.items()}, "person": r["person"], "members": []}
            people.add(r["person"])
        found[(r["runner_id"], r["runtime"])] = sub
    if people:
        members = {}
        for m in c.execute(
            "SELECT s.id,s.name,s.kind,s.revision,s.status,s.status_at,s.owner_id,s.created_at,s.credential_at,"
            "coalesce(l.account,'') AS account,l.access_expires_at,m.value AS payer_usage FROM model_subscriptions s "
            "LEFT JOIN claude_logins l ON l.subscription_id=s.id "
            "LEFT JOIN registry_metadata m ON m.key=?||s.id "
            f"WHERE s.owner_id IN ({','.join('?' * len(people))}) ORDER BY s.created_at, s.id",
            (PLAN_PREFIX, *sorted(people)),
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
                            "credential_at",
                            "account",
                            "access_expires_at",
                            "payer_usage",
                        )
                    }
                )
        for (_, runtime), sub in found.items():
            if sub.get("person"):
                sub["members"] = members.get((sub["person"], runtime), [])
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


def _pick(c, runner_id, runtime, entry, loads=None, *, scoped=False):
    """The plan a turn on `runtime` here signs in with from a person's set (`entry`, as `_given` reads it), or None when
    every plan is held or needs reconnecting. The plan in use stays while it is available, so a bot's session does not
    restart each time the readings shift; else the next is the first available plan in pick order (`pick_key`). Read
    once per snapshot: the claim asks for every bot on every poll."""
    from . import read_cache

    current = next((m for m in entry["members"] if m["id"] == entry["id"]), None)
    if current and available(c, current, runtime, loads):
        return current
    values = read_cache.cache(c)
    key = ("model_subscriptions.pick", runner_id, runtime, scoped)
    if values is not None and key in values:
        return values[key]
    ready = [m for m in entry["members"] if available(c, m, runtime, loads)]
    if scoped:
        chosen = min(
            ready,
            key=lambda sub: pick_key(sub, _plan_usage(c, sub, runtime, payer_usage_context(sub))),
            default=None,
        )
    else:
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
    return sub if _runner_takes(runner, sub) else None


def _runner_takes(runner, sub):
    if runner and sub["kind"] == "claude_login":
        from . import claude_login

        # A runner that cannot put the token in a turn's own file never gets one: its turns run as if none were given.
        return claude_login.takes_turns(runner)
    return bool(runner)


def legacy_current(c, runner_id, runtime, caps, loads=None):
    """The payer an older computer can carry, using only its binding and the bound plans' usage."""
    from .agent_subscriptions import CAPABILITY
    from . import claude_login

    if "model_subscriptions_v1" not in caps or CAPABILITY in caps:
        return None
    given = loads.given if loads is not None else given_of(c, [runner_id])
    current = given.get((runner_id, runtime))
    if current and current.get("person"):
        current = _pick(c, runner_id, runtime, current, loads, scoped=True)
    if (
        current
        and available(c, current, runtime, loads)
        and (current["kind"] != "claude_login" or claude_login.CAPABILITY in caps)
    ):
        return current
    return None


def legacy_matches(c, runner_id, runtime, sub, caps):
    """A runner with a shared binding can carry the turn's exact payer and revision."""
    current = legacy_current(c, runner_id, runtime, caps)
    return bool(current and (current["id"], current["revision"]) == (sub["id"], sub["revision"]))


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
    """A plan label for this tool when the team has a saved plan, for Paid by guidance in readiness."""
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


OTHER_PLANS_SQL = (
    "SELECT s.id,s.name,s.kind,s.revision,s.status,s.owner_id,s.created_at,l.access_expires_at AS access_expires_at "
    "FROM model_subscriptions s LEFT JOIN claude_logins l ON l.subscription_id=s.id ORDER BY s.created_at, s.id"
)


def plan_hold(c, runner_id, runtime, loads=None):
    """Why this computer's turns on `runtime` wait for a plan, not a sign-in: every plan it is given for the runtime is
    held at its usage limit (the one plan given to it, or a person's plans, some of which may need reconnecting
    instead), or None. A plan that only needs reconnecting is not this: Reconnect is its fix. {plan (the name of the
    plan whose hold ends first), until, limit ("weekly", "5-hour" or ""), owner, owner_name, reconnect (the names of
    the given plans that need reconnecting too), fix}. `fix` is the one change that runs its turns now:
    {"reconnect", "name"} for a given plan that needs reconnecting (it has no hold, so it signs in once reconnected),
    {"person", "name"} for the plan owner's plans in turn when the owner has another plan for the runtime with usage
    left (the computer moves between them by itself, `settle`), {"plan", "name"} for another plan with usage left, or
    None when no plan has any."""
    from . import claude_login
    from . import login_holds

    given = loads.given if loads is not None else _given(c)
    sub = given.get((runner_id, runtime)) if given else None
    if not sub:
        return None
    if sub.get("person"):
        members = sub.get("members") or []
        held = [m for m in members if m.get("id") and _held(c, m["id"], runtime, loads)]
        if not held or _pick(c, runner_id, runtime, sub, loads) is not None:
            return None
        first = min(held, key=lambda m: (_hold_end(c, m["id"], runtime, loads), m["id"]))
        owner, mine = sub["person"], {m["id"] for m in members}
        stuck = [m for m in members if m.get("id") and m not in held]
    else:
        if not sub.get("id") or sub.get("status") == "needs_reconnect" or not _held(c, sub["id"], runtime, loads):
            return None
        first, owner, mine = sub, (row(c, sub["id"]) or {}).get("owner_id") or "", {sub["id"]}
        stuck = [sub] if claude_login.expired(sub) else []
    hold = login_holds.get(c, "", login_holds.subscription_login(first["id"]), runtime, loads)
    kind = login_holds.window(hold.get("error") or "")
    others = [
        dict(r)
        for r in c.execute(OTHER_PLANS_SQL)
        if r["id"] not in mine and owned(r["kind"]) and available(c, dict(r), runtime, loads)
    ]
    own = [r for r in others if owner and r["owner_id"] == owner]
    fix = None
    # A given plan that is not held is passed over only for its sign-in (it needs reconnecting, or its login ran out):
    # reconnecting it runs the turns now, with no reset to wait for.
    unheld = [m for m in stuck if not _held(c, m["id"], runtime, loads)]
    if unheld:
        fix = {"reconnect": unheld[0]["id"], "name": unheld[0].get("name") or ""}
    elif own and not sub.get("person"):
        fix = {"person": owner, "name": _name_of(c, owner)}
    elif others:
        best = _ordered(c, others, runtime)[0]
        fix = {"plan": best["id"], "name": best["name"]}
    return {
        "plan": first.get("name") or "",
        "until": hold.get("until") or "",
        "limit": "weekly" if "weekly" in kind else "5-hour" if "5-hour" in kind else "",
        "owner": owner,
        "owner_name": _name_of(c, owner) if owner else "",
        "pinned": not sub.get("person"),
        "reconnect": [m.get("name") or "" for m in stuck],
        "fix": fix,
    }


def _name_of(c, actor):
    person = H.human(c, H.actor_id(actor)) or {}
    return _display_name(person) or "A teammate"


def _when(ts):
    """A hold's end as a person reads it: "Nov 22 09:00 UTC"."""
    try:
        when = _at(str(ts)).astimezone(timezone.utc)
    except ValueError:
        return str(ts or "")
    return f"{when:%b} {when.day} {when:%H:%M} UTC"


def hold_words(held, label, runtime, *, manages=False):
    """(problem, fix) for a computer whose plans for `runtime` are all passed over, at least one held at
    its usage limit (`plan_hold`), never "sign in": the plan, its limit and its reset, any given plan that needs
    reconnecting, and the one change that runs its turns now."""
    tool = next(
        (
            spec["name"]
            for spec in KINDS.values()
            if spec["runtime"] == runtime and spec.get("billing") == "subscription"
        ),
        RUNTIME_LABELS.get(runtime, runtime),
    )
    limit = f"its {held['limit']} usage limit" if held["limit"] else "its usage limit"
    until = f" until {_when(held['until'])}" if held["until"] else ""
    if held["pinned"]:
        problem = f"{label}'s only {tool} plan, {held['plan']}, is at {limit}{until}"
    else:
        problem = (
            f"{label} is given {held['owner_name']}'s {tool} plans and "
            + ("none can sign in" if held["reconnect"] else "each is at its usage limit")
            + f"; the first to reset is {held['plan']}{until}"
        )
    if held["reconnect"]:
        names = ", ".join(held["reconnect"])
        problem += f"; {names} {'needs' if len(held['reconnect']) == 1 else 'need'} reconnecting"
    fix = held["fix"]
    if fix and fix.get("reconnect"):
        action = f"reconnect {fix['name']} in Settings > Providers"
    elif fix:
        provider = f"{fix['name']}'s plans" if fix.get("person") else fix["name"]
        action = f"select {provider} under Paid by on the agent"
    else:
        action = f"add a {tool} plan with usage left in Settings > Providers"
    words = action[0].upper() + action[1:] if manages else f"An owner or admin can {action}"
    if until:
        words += f", or wait{until}"
    return problem, words


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


def covered_runtimes(c, runner, config, loads=None, *, bot=""):
    """{runtime: kind} the computer's subscriptions sign in for a bot on `config` there: a covered runtime needs no
    sign-in on that computer, for the bot's readiness and the claim's check."""
    return {item["runtime"]: item["subscription"]["kind"] for item in for_claim(c, runner, config, loads, bot=bot)}


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
    computers there are. Read agent picks and legacy bindings with their computers, readiness, login reports, holds,
    API key costs and registry metadata. With `sid`, only that plan's associations and computers are read."""
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
    pool_match = " OR ".join(
        f"(s.kind='{kind}' AND p.runtime='{spec['runtime']}')" for kind, spec in KINDS.items() if owned(kind)
    )
    picked = (
        "SELECT s.id AS subscription_id,a.runner_id,p.runtime,min(p.updated_at) AS granted_at "
        "FROM agent_subscriptions p JOIN assignments a ON a.bot=p.bot JOIN bots b ON b.id=p.bot "
        "JOIN model_subscriptions s ON (s.id=p.subscription_id OR "
        f"(p.kind='pool' AND p.person=s.owner_id AND ({pool_match}))) WHERE b.state='active'"
    )
    if sid:
        picked += " AND s.id=?"
    for grant in H._rows(c.execute(picked + " GROUP BY s.id,a.runner_id,p.runtime", (sid,) if sid else ())):
        grants.setdefault(grant["subscription_id"], []).append(grant)
    computers = "SELECT value FROM json_each(?)"
    args = (encode(sorted({g["runner_id"] for rows in grants.values() for g in rows})),)
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


def _display_name(person):
    name = (person.get("name") or "").strip()
    return name if name and "@" not in name else ""


def _usage(c, sub, ctx=None):
    """What is known of a subscription's use without new telemetry: an API key's cost this month from the runs that
    used it; a plan's limit while its hold stands (backend/login_holds.py), else its used percent from its computers'
    reports (`_plan_usage`). A plan always answers: with no reading, `reason` says why in a short line."""
    from types import SimpleNamespace
    from . import login_holds

    ctx = _listing(c, sub["id"]) if ctx is None else ctx
    spec = KINDS.get(sub["kind"]) or {}
    hold = login_holds.get(
        c,
        "",
        login_holds.subscription_login(sub["id"]),
        spec.get("runtime", ""),
        loads=SimpleNamespace(holds=ctx["holds"]),
    )
    until = hold.get("until") or ""
    active = until > H.now()
    capacity = hold.get("capacity")
    if spec.get("billing") == "api":
        usage = {"window": None, "used_pct": None, "cost_usd_month": round(float(ctx["costs"].get(sub["id"]) or 0), 2)}
    elif active and not capacity:
        kind = login_holds.window(hold.get("error"))
        window = "week" if "weekly" in kind else "5h" if "5-hour" in kind else None
        usage = {
            "window": window,
            "used_pct": 100,
            "cost_usd_month": None,
            "resets_at": until,
        }
    else:
        usage = _plan_usage(c, sub, spec.get("runtime", ""), ctx)
    if active:
        usage["capacity_until" if capacity else "quota_hold_until"] = until
    return usage


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


def remember_turn_usage(c, runner_id, runtimes):
    """Keep each payer's latest window when another agent's reading replaces it on the computer."""
    from .repositories import metadata, save_metadata
    from .subscription_refresh import plan_key

    for runtime, state in runtimes.items():
        for window in ("weekly", "five_hour"):
            reading = provenance(state.get(window))
            if not reading or reading.get("origin") != "subscription" or not (sid := reading.get("subscription_id")):
                continue
            if not c.execute(
                "SELECT 1 FROM attempt_subscriptions s JOIN attempts a ON a.id=s.attempt_id "
                "JOIN model_subscriptions m ON m.id=s.subscription_id "
                "WHERE s.subscription_id=? AND s.runtime=? AND a.runner_id=? AND s.delivery<>'' "
                "AND a.created_at>=m.credential_at LIMIT 1",
                (sid, runtime, runner_id),
            ).fetchone():
                continue
            key = plan_key(sid)
            saved = metadata(c, key) or {}
            field = "turn_" + window
            previous = (saved.get(field) or {}).get("reported_at")
            if not previous or _at(reading["reported_at"]) > _at(previous):
                save_metadata(c, key, {**saved, field: reading})


def payer_usage_context(sub):
    from .subscription_refresh import plan_key

    return {"runners": {}, "grants": {}, "meta": {plan_key(sub["id"]): H._json(sub["payer_usage"], {})}}


def _plan_usage(c, sub, runtime, ctx, windows=None):
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
    meta = ctx["meta"].get(SR.plan_key(sub["id"])) or {}
    has_turn_reading = bool(meta.get("turn_weekly") or meta.get("turn_five_hour"))
    has_own_reading = bool(SR.plan_reading(meta) or meta.get("five_hour"))
    if not computers and not has_turn_reading and not has_own_reading and windows is None:
        return _reason("Not on a computer yet")
    if sub["status"] == "needs_reconnect":
        return _reason("Reconnect to see usage")
    readers = [g for g in computers if computer_logins(runners[g["runner_id"]], ctx["ready"][g["runner_id"]])]
    # A full login's usage is the plan's own read (backend/claude_login.py), whichever computers it is on.
    if (
        not readers
        and not has_turn_reading
        and not has_own_reading
        and sub["kind"] != "claude_login"
        and windows is None
    ):
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
            if windows is not None:
                windows.append((label, "", reading["used_percent"], ends, reported))
            if best is None or candidate[:2] > best[:2]:
                best = candidate
            if window == "weekly" and reading.get("resets_at") and (weekly is None or reported > weekly[1]):
                weekly = candidate
    # The plan's own read (subscription_refresh.request_plan_read, or a full login's usage read, backend/claude_login.py):
    # this plan's by its asking, and not from before its token was replaced. A full login's read has its 5-hour window
    # and each model's week too.
    since = _at(sub["credential_at"] or sub["created_at"])
    for reading, window, label in (
        (SR.plan_reading(meta), "weekly", "week"),
        (meta.get("five_hour") if sub["kind"] == "claude_login" else None, "five_hour", "5h"),
        (meta.get("turn_weekly"), "weekly", "week"),
        (meta.get("turn_five_hour"), "five_hour", "5h"),
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
        if windows is not None:
            windows.append(
                (
                    label,
                    "",
                    own["used_percent"],
                    _at(own["resets_at"]) if own["resets_at"] else taken + WINDOWS[window],
                    taken,
                )
            )
        if best is None or candidate[:2] > best[:2]:
            best = candidate
        if window == "weekly" and own["resets_at"] and (weekly is None or taken > weekly[1]):
            weekly = candidate
    models = [
        m
        for m in (meta.get("models") or [] if sub["kind"] == "claude_login" else [])
        if isinstance(m, dict) and m.get("resets_at") and _at(m["resets_at"]) > now
    ]
    if windows is not None:
        reported = (SR.plan_reading(meta) or {}).get("reported_at")
        if reported and _at(reported) >= since:
            for model in models:
                own = weekly_snapshot(
                    {
                        "reported_at": reported,
                        "resets_at": model["resets_at"],
                        "used_percent": model.get("used_percent"),
                    }
                )
                if own and own["used_percent"] is not None and model.get("model"):
                    windows.append(
                        ("model", model["model"], own["used_percent"], _at(model["resets_at"]), _at(reported))
                    )
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


def payment_item(c, sid):
    """The one Needs-you task for the person who holds a plan the provider refused for its billing (its owner, else who
    added it), and its id; None when nobody can be asked. The plan's login hold keeps it and closes it once a turn works
    on the plan again (login_holds.clear)."""
    from . import login_holds, person_asks

    sub = row(c, sid)
    if not sub:
        return None
    person = sub.get("owner_id") or sub["added_by"]
    if not person or not person_asks.is_human(person):
        return None
    plan = f"{(KINDS.get(sub['kind']) or {}).get('runtime', '').capitalize()} plan {sub['name']}".strip()
    body = (
        f"The provider refused the {plan}: payment past due. Every agent on it waits.\n\n"
        f"Pay or renew at {login_holds.BILLING_URL}. Tico tries the plan again every "
        f"{login_holds.PAYMENT_WAIT // 60} minutes and lets the agents go once it works."
    )
    try:
        return person_asks.task_for(
            c,
            H.TICO_ACTOR,
            f"Pay for the {plan}",
            body,
            person,
            question=f"Can you pay for the {plan}?",
            why=person_asks.first_sentence(body),
            asker=person_asks.agent_ops(),
            deduplicate=False,  # one per plan already: its login hold keeps the item it filed
        )["id"]
    except H.Refused:
        return None


def problem(c, sub):
    """What a person must fix on a plan that still counts, in a few words, or "": a payment past due, while the
    provider's refusal holds its login (login_holds.payment)."""
    from . import login_holds

    runtime = (KINDS.get(sub["kind"]) or {}).get("runtime", "")
    hold = login_holds.get(c, "", login_holds.subscription_login(sub["id"]), runtime) if runtime else {}
    return "Payment past due" if hold and login_holds.payment(hold.get("error")) else ""


def view(c, sub, ctx=None):
    """A subscription as Settings > Providers lists it: no value, a preview, its status, the computers given it."""
    from . import claude_login, subscription_refresh as SR
    from types import SimpleNamespace

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
        "problem": problem(c, sub),
        "preview": sub["preview"],
        "revision": sub["revision"],
        "computers": list(dict.fromkeys(computers)),
        "usage": _usage(c, sub, ctx),
        "usage_refresh": SR.plan_view(ctx["meta"].get(SR.plan_key(sub["id"]))),
        "available": available(c, sub, spec.get("runtime", ""), SimpleNamespace(holds=ctx["holds"])),
        "expired": claude_login.expired(sub),
        # A full login runs out about a month after its sign-in: when to reconnect, shown a few days before.
        "reconnect_by": claude_login.reconnect_by(sub),
        "added_by": sub["added_by"],
        "added_by_name": _display_name(added) or "A teammate",
        "owner_id": sub.get("owner_id") or "",
        "owner_name": (_display_name(owner) or "A teammate") if sub.get("owner_id") else "",
        "created_at": sub["created_at"],
        "updated_at": sub["updated_at"],
    }


def agents_sql(count):
    """The bots on `count` computers with their configs, for the plan list (`_agents_on`)."""
    return (
        f"SELECT a.bot,a.runner_id,coalesce(nullif(b.display_name,''),b.slug) AS name,{BS.CONFIG} AS config "
        "FROM assignments a JOIN bots b ON b.id=a.bot JOIN runners r ON r.id=a.runner_id "
        f"JOIN bot_config bc ON bc.bot=a.bot {BS.JOIN} WHERE a.runner_id IN ({','.join('?' * count)}) "
        "AND r.revoked_at IS NULL AND coalesce(b.state,'')<>'archived'"
    )


def originals_sql(count):
    """The configs of `count` branches' originals, as shared_bots.follow reads its `rows`."""
    return (
        f"SELECT bc.bot,{BS.CONFIG} AS config,coalesce(nullif(bc.repo,''),'emp-'||b.slug) AS repo "
        f"FROM bot_config bc JOIN bots b ON b.id=bc.bot {BS.JOIN} WHERE bc.bot IN ({','.join('?' * count)})"
    )


def _agents_on(c, settings, runner_ids):
    """{runner id: [(bot, name, runtimes, first)]}: the bots on these computers, every runtime their turns may sign in
    (`bot_runtimes`) and those they run before any fallback (`first`), in two reads however many there are."""
    from . import providers, task_models
    from .shared_bots import follow, source_of

    rows = []
    for part in H.chunks(sorted(set(runner_ids))):
        rows.extend(H._rows(c.execute(agents_sql(len(part)), part)))
    if not rows:
        return {}
    configs = {r["bot"]: json.loads(r["config"] or "{}") for r in rows}
    sources = sorted({source_of(config) for config in configs.values()} - {""})
    originals = {}
    for part in H.chunks(sources):
        originals.update((r["bot"], r) for r in c.execute(originals_sql(len(part)), part))
    record = providers.load(c, settings)
    out = {}
    for r in rows:
        filled = providers.fill(record, follow(c, r["bot"], configs[r["bot"]], rows=originals.get))
        alone = {**filled, "fallback": {"off": True}}
        task = task_models.of(filled)
        first = turn_runtimes(alone) + (turn_runtimes(task_models.apply(alone, task)) if task else [])
        out.setdefault(r["runner_id"], []).append((r["bot"], r["name"], _runtimes_of(filled), first))
    return out


def _agents_of(bots, runner_ids, runtime):
    """The bots a subscription for `runtime` serves on these computers (`_agents_on`), by name: {id, name, fallback},
    `fallback` when only a fallback of theirs runs that runtime."""
    found = {}
    for rid in runner_ids:
        for bot, name, runtimes, first in bots.get(rid, ()):
            if runtime in runtimes:
                found[bot] = {"id": bot, "name": name, "fallback": runtime not in first}
    return sorted(found.values(), key=lambda a: (a["name"].lower(), a["id"]))


def listed(c, settings=None):
    """Saved providers, their active computers and assigned agents, and each pool's next eligible plans."""
    from . import agent_subscriptions

    if not _present(c):
        return []
    subs = H._rows(
        c.execute(
            f"SELECT s.* FROM {SUBS} s LEFT JOIN credentials v ON v.id=s.credential_id "
            "WHERE v.source IS NULL OR v.source NOT LIKE 'computer:%' ORDER BY lower(s.name),s.id LIMIT ?",
            (MAX_SUBSCRIPTIONS,),
        )
    )
    ctx = _listing(c) if subs else None
    views = [view(c, s, ctx) for s in subs]
    if ctx is None:
        return views
    using = {}
    rotating = set()
    for attempt in c.execute(
        "SELECT DISTINCT s.subscription_id,a.runner_id,a.bot_id,s.runtime,a.handed_model,p.person "
        "FROM attempt_subscriptions s JOIN attempts a ON a.id=s.attempt_id "
        "JOIN model_subscriptions m ON m.id=s.subscription_id "
        "LEFT JOIN agent_subscriptions p ON p.bot=a.bot_id AND p.runtime=s.runtime "
        "AND p.kind='pool' AND p.person=m.owner_id AND s.runtime=a.handed_runtime AND s.delivery='claim' "
        "WHERE s.delivery<>'' AND a.state IN ('leased','running') AND a.lease_until>? AND a.stop_requested_at IS NULL",
        (H.now(),),
    ):
        using.setdefault(attempt["subscription_id"], set()).add(attempt["runner_id"])
        if attempt["person"]:
            rotating.add((attempt["bot_id"], attempt["runtime"], attempt["handed_model"], attempt["subscription_id"]))
    upcoming = set()
    if rotating:
        from types import SimpleNamespace

        inputs = agent_subscriptions.read(c, sorted({bot for bot, _, _, _ in rotating}))
        loads = SimpleNamespace(holds=ctx["holds"])
        for bot, runtime, model, current in rotating:
            spare = agent_subscriptions.resolve(
                c, bot, runtime, model or "", inputs=inputs, loads=loads, exclude=current
            )
            if spare and spare.subscription:
                upcoming.add(spare.subscription["id"])
    chosen = {}
    for pick in c.execute(
        "SELECT p.*,coalesce(nullif(b.display_name,''),b.slug) AS name "
        "FROM agent_subscriptions p JOIN bots b ON b.id=p.bot AND b.state<>'archived'"
    ):
        key = pick["subscription_id"] or (pick["person"], pick["runtime"])
        chosen.setdefault(key, []).append(pick)
    groups = {}
    for v in views:
        v["in_use_on"] = sorted(using.get(v["id"], ()))
        selecting = chosen.get(v["id"], [])
        if owned(v["kind"]):
            selecting = [*selecting, *chosen.get((v["owner_id"], v["runtime"]), [])]
        agents = {p["bot"]: {"id": p["bot"], "name": p["name"], "fallback": False} for p in selecting}
        v["agents"] = sorted(agents.values(), key=lambda a: (a["name"].lower(), a["id"]))
        v["order"] = v["place"] = None
        v["next_in_rotation"] = v["id"] in upcoming
        if owned(v["kind"]) and v["owner_id"] and v["available"]:
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


def assignments(c, settings=None, *, who=None, auth=None):
    """The compatibility view of a computer's agents' Paid by selections."""
    from . import agent_subscriptions, providers
    from .shared_bots import follow

    rows = c.execute(
        "SELECT a.runner_id,p.* FROM agent_subscriptions p JOIN assignments a ON a.bot=p.bot "
        "JOIN runners r ON r.id=a.runner_id AND r.revoked_at IS NULL "
        "JOIN bots b ON b.id=p.bot AND b.state<>'archived' ORDER BY a.runner_id,p.runtime,p.bot"
    )
    placements = c.execute(
        "SELECT a.runner_id,a.bot FROM assignments a JOIN bots b ON b.id=a.bot AND b.state<>'archived'"
    ).fetchall()
    access = auth.bot_accesses(c, who, [r["bot"] for r in placements]) if auth is not None else None
    counts = {}
    for r in placements:
        if access is None or access[r["bot"]]["read"]:
            counts[r["runner_id"]] = counts.get(r["runner_id"], 0) + 1
    groups = {}
    for r in rows:
        if access is None or access[r["bot"]]["read"]:
            groups.setdefault((r["runner_id"], r["runtime"]), []).append(r)
    record = providers.load(c, settings) if groups else None
    inputs = agent_subscriptions.read(c, list(dict.fromkeys(r["bot"] for r in placements)))
    owners = {}
    given = []
    for (runner_id, runtime), picks in groups.items():
        choices = {(p["kind"], p["person"], p["subscription_id"]) for p in picks}
        mixed = len(choices) != 1 or len(picks) != counts[runner_id]
        sid, person, state = "", "", None
        if not mixed:
            kind, owner, sid = choices.pop()
            if kind == "pool":
                person = owner
                if runtime not in owners:
                    owners[runtime] = plan_owners(c, runtime)
                members = owners[runtime].get(owner, [])
                payments = []
                for p in picks:
                    row = c.execute(BS.ONE_SQL, (p["bot"],)).fetchone()
                    config = providers.fill(record, follow(c, p["bot"], H._json(row[0], {}) or {})) if row else {}
                    payments.append(
                        agent_subscriptions.resolve(
                            c,
                            p["bot"],
                            runtime,
                            config.get("model", "") if config.get("runtime") == runtime else "",
                            inputs=inputs,
                        )
                    )
                subs = {p.subscription["id"]: p.subscription for p in payments if p and p.subscription}
                on = (
                    next(iter(subs.values()))
                    if len(subs) == 1 and all(p and p.subscription for p in payments)
                    else None
                )
                reset = min((p.until for p in payments if p and p.until), default=None)
                sid = on["id"] if on else ""
                if len(subs) > 1:
                    given.append(
                        {
                            "runner_id": runner_id,
                            "runtime": runtime,
                            "subscription_id": "",
                            "person": "",
                            "set": None,
                            "mixed": True,
                        }
                    )
                    continue
                state = {
                    "person": person,
                    "person_name": _name_of(c, person),
                    "plans": len(members),
                    "on": sid or None,
                    "on_name": on["name"] if on else None,
                    "why": {
                        "kind": "soonest" if on else "all_held" if members else "none",
                        "plan": None,
                        "until": reset.isoformat() if reset else None,
                    },
                }
        given.append(
            {
                "runner_id": runner_id,
                "runtime": runtime,
                "subscription_id": sid,
                "person": person,
                "set": state,
                "mixed": mixed,
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
    if secret:
        _person_secret(who)


def _person_secret(who):
    _reader(who)
    if who.support or who.via or who.attempt_id:
        raise Problem("forbidden", "Save a subscription yourself in Settings > Providers", 403)


def owned(kind):
    """Whether a subscription is a plan with the legacy plan-owner picker."""
    return (KINDS.get(kind) or {}).get("billing") == "subscription"


def _may_manage_provider(auth, who, sub):
    return _may_manage(auth, who) or (
        who.role in ("human", "owner") and not who.support and who.actor == sub["owner_id"]
    )


def _manage_provider(c, auth, who, sid):
    _reader(who)
    auth.sync_access(c)
    sub = row(c, sid)
    if not sub:
        raise Problem("not_found", "Subscription not found", 404)
    if not _may_manage_provider(auth, who, sub):
        raise Problem("forbidden", "Only the provider's owner or an admin can manage it", 403)
    return sub


def owner_of(c, value):
    """`value` (a person's id, or `human:<id>`) as the owner actor, or a Problem: an owner is a person of this company
    who has not left and whose sign-in is on. Never Tico support, a bot or a service."""
    from .auth import standing

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


def _default_owner(c, auth, who, body):
    mine = owner_of(c, who.actor)
    owner = mine if body.owner_id is None else owner_of(c, body.owner_id)
    if owner != mine:
        auth.sync_access(c)
        if not _may_manage(auth, who):
            raise Problem("owner", "Add providers to your own pool", 422)
    return owner


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
        if given and given["delivery"]:
            continue  # a computer binding change cannot stop a turn on an agent's payer
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


def _confirm_reconnect(c, settings, given, attempts=()):
    """A new value stops the runs on each computer signed in with this subscription: with some going, ask first."""
    plans = [_plan(c, settings, runner_id, runtime) for runner_id, runtime in given]
    running = sum(p["running"] for p in plans) + len(attempts)
    if running:
        computers = {runner for runner, _ in given} | {a["runner_id"] for a in attempts}
        _confirm({"running": running, "sessions": sum(p["sessions"] for p in plans)}, {"computers": len(computers)})


def default_name(c, kind, owner=""):
    """Name a plan for its owner when they have a display name, and an API key for its provider."""
    spec = KINDS[kind]
    base = spec["name"]
    if owned(kind):
        person = H.human(c, owner) if owner else None
        display_name = _display_name(person or {})
        base = spec["label"]
        if display_name:
            ending = f"'s {base}"
            owner_limit = MAX_NAME - len(ending) - len(f" {MAX_SUBSCRIPTIONS}")
            base = display_name[:owner_limit].rstrip() + ending
    taken = {r["name"].strip().lower() for r in c.execute("SELECT name FROM model_subscriptions")}
    n, name = 1, base
    while name.lower() in taken:
        n += 1
        name = f"{base} {n}"
    return name


def save(c, vault, settings, auth, who, body, sid=None):
    """Add a subscription, or change a saved one: a new secret reconnects it (status `new`, so it counts again) and ends
    its usage-limit hold as Try now does, and a name renames it. Returns its view.

    A full Claude login (`claude_login`, backend/claude_login.py) is one plan per account: a new one whose account is
    already connected is a Reconnect of that plan, and a Reconnect with another plan's account is refused. A Reconnect
    with the same account is a refresh: the plan's turns keep running (they get the new access token) and its readings
    stay. A setup-token plan reconnected with a full login becomes one, the same plan (`kind` given as `claude_login`):
    its owner, computers and history stay; its old readings go, since the login may be another account. The emergency
    path goes back: a full login's plan given a pasted setup-token becomes a setup-token plan the same way."""
    from . import claude_login, quarantine_stops

    sub = _manage_provider(c, auth, who, sid) if sid else None
    admin = _may_manage(auth, who)
    owner = _default_owner(c, auth, who, body) if sub is None else sub["owner_id"]
    if sub and body.owner_id is not None and owner_of(c, body.owner_id) != owner:
        raise Problem("owner", "A provider keeps its owner", 422)
    now = H.now()
    if sub and not body.kind and body.secret is not None and sub["kind"] == "claude_login":
        if not body.secret.get_secret_value().lstrip().startswith("{"):
            body = body.model_copy(update={"kind": "claude_token"})
    kind = body.kind or sub["kind"] if sub else body.kind
    login = None
    if kind == "claude_login" and body.secret is not None:
        login = claude_login.facts(checked("claude_login", body.secret.get_secret_value())[0])
        taken = claude_login.same_account(c, login["account"], sid)
        if taken:
            if not sid and taken["owner_id"] == owner:
                # A second Connect of the same account reconnects its owner's plan.
                body = body.model_copy(update={"name": None, "owner_id": None})
                return save(c, vault, settings, auth, who, body, taken["id"])
            label = f" as {taken['name']}" if sid and _may_manage_provider(auth, who, taken) else ""
            raise Problem("account", f"This Claude account is already connected{label}", 409)
    if sid and sub:
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
        paid_attempts = []
        value = preview = ""
        same = bool(login) and not converting and login["account"] == sub["account"]
        if body.secret is not None:
            value, preview = checked(kind, body.secret.get_secret_value())
            if converting and kind == "claude_login":
                claude_login.can_convert(c, sid)
            paid_attempts = [] if same else _agent_attempts(c, sid)
            if not body.confirm:
                _confirm_reconnect(c, settings, [], paid_attempts)
        if name is not None:
            c.execute("UPDATE model_subscriptions SET name=?,updated_at=? WHERE id=?", (name, now, sid))
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
            _follow(c, settings, who.actor, sub["owner_id"])
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
            # A replacement may be another account. Release its usage hold for a probe and restart its own runs.
            from . import login_holds

            login_holds.try_now(c, "", login_holds.subscription_login(sid), KINDS[sub["kind"]]["runtime"])
            stopped = sum(quarantine_stops.restart(c, a, f"{sub['name']} was reconnected") for a in paid_attempts)
            H.event(c, who.actor, "subscription.updated", sid, {"secret_changed": True, "stopped": stopped})
            if kind == "claude_login":
                claude_login.read_soon(c, sid)
            _follow(c, settings, who.actor, sub["owner_id"])
            return view(c, row(c, sid))
        H.event(c, who.actor, "subscription.updated", sid, {"secret_changed": False})
        return view(c, row(c, sid))
    if not body.kind or body.secret is None:
        raise Problem("secret", "Choose a kind and paste the token, key or login", 422)
    if c.execute("SELECT count(*) FROM model_subscriptions").fetchone()[0] >= MAX_SUBSCRIPTIONS:
        raise Problem("limit", f"The company has {MAX_SUBSCRIPTIONS} subscriptions; remove one first", 422)
    value, preview = checked(body.kind, body.secret.get_secret_value())
    name = (body.name or "").strip() or default_name(c, body.kind, owner)
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
    if admin:
        _tick_provider(c, settings, who.actor, body.kind)
    if body.kind == "claude_login":
        claude_login.read_soon(c, sid)
    _follow(c, settings, who.actor, owner)
    return view(c, row(c, sid))


def store_refreshed(c, vault, sub, value, preview, actor, now):
    """Keep a refreshed login of the same account: the next revision, the same credential time, so its readings stay
    and a turn refused on the old token is known to have been caught by the rotation (claude_login.gap)."""
    c.execute(
        "UPDATE model_subscriptions SET credential_id=?,sealed='',preview=?,revision=revision+1,updated_at=? WHERE id=?",
        (_keep_value(c, vault, sub, value, preview, actor, now), preview, now, sub["id"]),
    )


def _agent_attempts(c, sid):
    return c.execute(
        "SELECT a.* FROM attempt_subscriptions s JOIN attempts a ON a.id=s.attempt_id "
        "WHERE s.subscription_id=? AND s.delivery<>'' AND a.state IN ('leased','running') "
        "AND a.stop_requested_at IS NULL",
        (sid,),
    ).fetchall()


def _provider_attempts(c, sid, picks):
    """Runs using the removed value or losing their exact pick for the AI tool they are running."""
    live = "a.state IN ('leased','running') AND a.stop_requested_at IS NULL"
    attempts = {
        a["id"]: a
        for a in c.execute(
            "SELECT a.* FROM attempt_subscriptions s JOIN attempts a ON a.id=s.attempt_id "
            f"WHERE s.subscription_id=? AND {live} AND NOT EXISTS ("
            "SELECT 1 FROM pending_subscription_fallbacks p WHERE p.attempt_id=s.attempt_id AND p.runtime=s.runtime)",
            (sid,),
        )
    }
    for bot, runtime in picks:
        for a in c.execute(
            f"SELECT a.* FROM attempts a WHERE a.bot_id=? AND a.handed_runtime=? AND {live}", (bot, runtime)
        ):
            attempts.setdefault(a["id"], a)
    return list(attempts.values())


def remove(c, settings, who, sid, confirm=False, *, auth=None):
    """Delete a provider, clear exact picks or emptied pools, and restart the affected turns."""
    from . import agent_subscriptions, claude_login, login_holds, quarantine_stops
    from .execution import lift_limited_on_computer

    sub = row(c, sid)
    if not sub:
        raise Problem("not_found", "Subscription not found", 404)
    computers = c.execute("SELECT count(*) FROM computer_subscriptions WHERE subscription_id=?", (sid,)).fetchone()[0]
    given = _signed_in_with(c, sid)
    picks = agent_subscriptions.for_removal(c, sub)
    attempts = _provider_attempts(c, sid, picks)
    if not confirm and (attempts or picks):
        access = auth.bot_accesses(c, who, [bot for bot, _ in picks]) if auth else None
        agents = [{"bot": bot, "runtime": runtime} for bot, runtime in picks if access is None or access[bot]["read"]]
        raise Problem(
            "confirm_restart",
            "Remove this provider and clear its agents' Paid by?",
            409,
            extra={"running": len(attempts), "sessions": 0, "computers": computers, "agents": agents},
        )
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
    agent_subscriptions.unset_removed(c, who.actor, picks)
    c.execute("DELETE FROM model_subscriptions WHERE id=?", (sid,))

    stopped = sum(quarantine_stops.restart(c, a, f"{sub['name']} was removed") for a in attempts)
    for runner_id, runtime in given:
        lift_limited_on_computer(c, settings, H.TICO_ACTOR, runner_id, runtime)

    runtime = (KINDS.get(sub["kind"]) or {}).get("runtime", "")
    if runtime and login_holds.get(c, "", login_holds.subscription_login(sid), runtime):
        # Its hold goes with it, and a payment item it filed closes: nothing can run on it to clear them.
        login_holds.clear(c, "", login_holds.subscription_login(sid), runtime, how="moot", note="The plan was removed.")

    claude_login.drop(c, sid)
    if sub["credential_id"]:
        from .model_login import mark_key_cleared

        for g in keyed:
            mark_key_cleared(c, who, g["runner_id"], g["runtime"])
        drop_credential(c, sub["credential_id"])  # its own service row always; a kept key once nobody else holds it
    H.event(
        c, who.actor, "subscription.deleted", sid, {"kind": sub["kind"], "computers": computers, "stopped": stopped}
    )
    _follow(c, settings, who.actor, sub["owner_id"])
    return {"deleted": True, "id": sid, "computers": computers, "stopped": stopped, "sessions_reset": 0}


def owner_left(c, settings, actor, owner):
    """Revoke the departing person's saved providers and pending sign-ins in the roster write."""
    from types import SimpleNamespace

    from . import model_login

    ended = c.execute(
        f"UPDATE model_logins SET state='canceled',url='',user_code='',pending_code=NULL,lines='[]',updated_at=? "
        f"WHERE {model_login.ACTIVE_SQL} AND id IN ("
        "SELECT x.login_id FROM subscription_connects x JOIN model_logins l ON l.id=x.login_id "
        "LEFT JOIN model_subscriptions s ON s.id=x.subscription_id "
        "WHERE x.owner_id=? OR s.owner_id=? OR l.requested_by=?) RETURNING id",
        (H.now(), owner, owner, owner),
    ).fetchall()
    c.executemany("DELETE FROM subscription_connects WHERE login_id=?", ended)
    lost = c.execute(
        "SELECT bot,runtime FROM agent_subscriptions WHERE (kind='pool' AND person=?) "
        "OR subscription_id IN (SELECT id FROM model_subscriptions WHERE owner_id=?)",
        (owner, owner),
    ).fetchall()
    c.execute("DELETE FROM agent_subscriptions WHERE kind='pool' AND person=?", (owner,))
    c.execute(
        "UPDATE credential_grants SET revoked_at=?,revoked_by=? WHERE revoked_at IS NULL AND credential_id IN "
        "(SELECT credential_id FROM model_subscriptions WHERE owner_id=? AND credential_id IS NOT NULL)",
        (H.now(), actor, owner),
    )
    for (sid,) in c.execute("SELECT id FROM model_subscriptions WHERE owner_id=? ORDER BY id", (owner,)).fetchall():
        remove(c, settings, SimpleNamespace(actor=actor), sid, confirm=True)
    c.execute("DELETE FROM computer_subscriptions WHERE person=?", (owner,))
    for bot, runtime in lost:
        H.event(c, actor, "agent_subscription.owner_left", bot, {"runtime": runtime, "owner": owner})


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
    from . import providers
    from .shared_bots import follow

    found = c.execute(BS.ONE_SQL, (bot,)).fetchone()
    config = json.loads(found["config"] or "{}") if found else {}
    return _runtimes_of(providers.fill(providers.load(c, settings), follow(c, bot, config)))


def _runtimes_of(filled):
    """`bot_runtimes` of a bot's filled config."""
    from . import task_models

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


def _follow(c, settings, actor, owner):
    """Update the template agents that follow their owner’s plans."""
    from . import plan_models

    plan_models.follow(c, settings, actor, owner=owner)


# ------------------------------------------------------------------------------------------------ keys on computers
# A key kept in the vault (`credential_id`) is a subscription like any other here. Before D11-4 a key was added from a
# computer in Settings > Computers (its credential's source is `computer:<runner id>`: that computer's own key); any
# other key given to computers is shared. Two things still read it that way: runners of v0.6.0-rc.1 on, which fetch
# their key from `GET runner-model-credentials` and write it to their key files (`keys_for`), and the Computers page,
# which says which key each computer uses (`key_listing`).
OWN = "computer:"
# The key a computer is given for a runtime: its subscription and its vault credential (`id` is the credential's).
KEY_SQL = (
    "SELECT cr.*, s.id AS subscription_id, s.revision AS subscription_revision, g.updated_at AS given_at, g.runtime "
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
    sub = H._one(c, f"SELECT * FROM {SUBS} WHERE credential_id=?", (credential_id,))
    if not sub:
        return
    given = c.execute(
        "SELECT runner_id, runtime FROM computer_subscriptions WHERE subscription_id=? AND person=''", (sub["id"],)
    ).fetchall()
    c.execute("DELETE FROM computer_subscriptions WHERE subscription_id=? AND person=''", (sub["id"],))
    from . import agent_subscriptions, quarantine_stops

    picks = agent_subscriptions.for_removal(c, sub)
    for attempt in _provider_attempts(c, sub["id"], picks):
        quarantine_stops.restart(c, attempt, "The model key was removed")
    agent_subscriptions.unset_removed(c, who.actor, picks)
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
    if found and found["source"] != OWN + runner_id:
        # An empty shared-key list removes the old cached value without signing out the computer's local login.
        return {"credentials": []}, []
    if not found or found["env"] not in providers.MODEL_KEYS.get(runtime, ()):
        return {"credentials": []}, []
    source = "computer" if found["source"] == OWN + runner_id else "shared"
    return {"credentials": [{"env": found["env"], "value": vault.cipher.decrypt(c, found), "source": source}]}, [
        found["id"]
    ]


def key_listing(c):
    """Only computer-local keys appear as sign-ins in Operations. No values are returned."""
    if not _present(c):
        return {}
    out = {}
    for value in c.execute(KEY_SQL + " AND cr.source='computer:'||g.runner_id ORDER BY g.runner_id,g.runtime"):
        rid = value["source"].removeprefix(OWN)
        runtime = value["runtime"]
        found = out.setdefault(rid, {"keys": {}, "shared_keys": {}, "shared_with": {}})
        found["keys"][runtime] = {"env": value["env"], "preview": value["preview"], "since": value["given_at"]}
    return out


# ------------------------------------------------------------------------------------------------ claim and run
def for_claim(c, runner, config, loads=None, bot=""):
    """The chosen payer for this turn, if one was assigned to the agent."""
    from . import agent_subscriptions

    if runner and bot and (payment := agent_subscriptions.for_runner(c, runner, bot, config, loads=loads)):
        if payment.subscription:
            return [agent_subscriptions.claim_entry(config["runtime"], payment)]
    return []


def for_shell(sub):
    """A shell can use the agent's ChatGPT plan in its own CLI home."""
    spec = KINDS.get(sub.get("kind")) or {}
    return spec.get("runtime") == "codex" and spec.get("billing") == "subscription" and bool(spec.get("file"))


def hand_out(c, attempt_id, chosen):
    """Record what the claim handed this attempt, and the attempt's metadata for the runner (no values)."""
    out = []
    for item in chosen:
        sub = item["subscription"]
        delivery = item.get("delivery", "")
        c.execute(
            "INSERT OR REPLACE INTO attempt_subscriptions(attempt_id,runtime,subscription_id,revision,delivery,parent_grant) "
            "VALUES(?,?,?,?,?,?)",
            (attempt_id, item["runtime"], sub["id"], sub["revision"], delivery, sub.get("payer_parent")),
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
                "source": "agent" if delivery and delivery != "legacy" else "computer",
                **({"env": spec["env"]} if spec.get("env") else {"file": spec["file"]}),
                **({"home_env": spec["home_env"]} if spec.get("home_env") else {}),
            }
        )
    return out


PAYER_UNAVAILABLE = "Paid by: the provider credential is unavailable"


def open_payer(c, cipher, sub):
    try:
        return value_of(c, cipher, sub)
    except Problem:
        return None


def payer_readable(c, sub):
    from .read_cache import value

    cipher = getattr(c, "credential_cipher", None)
    return cipher is None or value(
        c, ("payer_readable", sub["id"], sub["revision"]), lambda: bool(open_payer(c, cipher, sub))
    )


def _leased_payer(c, runner_id, attempt_id, delivery):
    return c.execute(
        "SELECT s.runtime,s.subscription_id,s.revision,r.capabilities FROM attempt_subscriptions s "
        "JOIN attempts a ON a.id=s.attempt_id JOIN runners_with_state r ON r.id=a.runner_id "
        "JOIN assignments x ON x.bot=a.bot_id AND x.runner_id=a.runner_id AND x.generation=a.generation "
        "LEFT JOIN credential_grants parent ON parent.id=s.parent_grant "
        "LEFT JOIN bot_config bc ON bc.bot=a.bot_id "
        "WHERE (s.parent_grant IS NULL OR (parent.id IS NOT NULL AND parent.revoked_at IS NULL "
        "AND parent.subject='human:'||bc.owner_id)) AND a.id=? AND a.runner_id=? AND r.revoked_at IS NULL AND a.state IN ('leased','running') "
        "AND a.stop_requested_at IS NULL AND a.lease_until>? AND s.delivery=?",
        (attempt_id, runner_id, H.now(), delivery),
    ).fetchone()


def _lease_subscription(c, given):
    from .agent_subscriptions import portable

    if (
        given
        and (sub := row(c, given["subscription_id"]))
        and (sub["revision"] == given["revision"] and available(c, sub, given["runtime"]) and portable(c, sub))
    ):
        return sub
    return None


def payer_ready(c, runner_id, attempt_id, delivery):
    """Verify the same bound provider still counts before the harness starts."""
    from .agent_subscriptions import CAPABILITY

    if delivery not in ("claim", "legacy"):
        return False
    given = _leased_payer(c, runner_id, attempt_id, delivery)
    if not (sub := _lease_subscription(c, given)):
        return False
    caps = H._json(given["capabilities"], []) or []
    if delivery == "legacy":
        return legacy_matches(c, runner_id, given["runtime"], sub, caps)
    return CAPABILITY in caps


def _lease_value(c, vault, given, sub):
    if not (value := open_payer(c, vault.cipher, sub)):
        return None
    return {
        "id": sub["id"],
        "runtime": given["runtime"],
        "kind": sub["kind"],
        "name": sub["name"],
        "revision": sub["revision"],
        "billing": KINDS[sub["kind"]]["billing"],
        **delivered(sub["kind"], value),
    }


def claim_credential(c, vault, runner_id, attempt_id, delivery="claim"):
    """Open the lease's payer for the authenticated runner after saving the secret-free receipt."""
    from .agent_subscriptions import CAPABILITY

    given = _leased_payer(c, runner_id, attempt_id, delivery)
    if not given or CAPABILITY not in (H._json(given["capabilities"], []) or []):
        return None
    sub = _lease_subscription(c, given)
    return _lease_value(c, vault, given, sub) if sub else None


def legacy_credential(c, vault, who):
    """Open a legacy turn's payer through the computer's current shared binding."""
    given = _leased_payer(c, who.runner_id, who.attempt_id, "legacy")
    if not (sub := _lease_subscription(c, given)) or not legacy_matches(
        c, who.runner_id, given["runtime"], sub, H._json(given["capabilities"], []) or []
    ):
        return None
    if credential := _lease_value(c, vault, given, sub):
        if given["runtime"] == "claude":
            # Remember the plan actually delivered so its refresh still has a destination after expiry.
            c.execute(
                "UPDATE computer_subscriptions SET subscription_id=? WHERE runner_id=? AND runtime=? "
                "AND person=? AND person<>'' AND subscription_id IS NOT ?",
                (sub["id"], who.runner_id, given["runtime"], sub["owner_id"], sub["id"]),
            )
        H.event(
            c,
            who.actor,
            "subscription.sent_to_computer",
            sub["id"],
            {"attempt": who.attempt_id, "runner": who.runner_id, "runtime": given["runtime"]},
        )
    return credential


def refreshed(c, vault, who, body):
    """A runner sends back a ChatGPT login its CLI refreshed in a turn it ran on it. Only from a live run (leased or
    running on this computer, with a current lease) that was handed the subscription. Legacy attempts still rely
    on their computer's binding. The stored revision must match the turn's and the file must belong to the same
    account. A kept file moves the run's record to the new revision."""
    if who.role != "runner":
        raise Problem("forbidden", "Only a registered computer may send this", 403)
    given = c.execute(
        "SELECT s.runtime,s.revision,s.delivery,a.state,a.lease_until,a.stop_requested_at FROM attempt_subscriptions s "
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
        or (given["delivery"] and given["stop_requested_at"])
        or (not given["delivery"] and not still_given(c, who.runner_id, given["runtime"], sub["id"]))
    ):
        raise Problem("stale_lease", "That run has ended or no longer signs in with this subscription", 409)
    if sub["revision"] != body.revision or given["revision"] != body.revision:
        return {"stored": False}
    if given["delivery"] == "legacy" and not payer_ready(c, who.runner_id, body.attempt, "legacy"):
        raise Problem("stale_lease", "That run has ended or no longer signs in with this subscription", 409)
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
    if not found or not (sub := row(c, found["subscription_id"])):
        return None
    if sub["status"] != "needs_reconnect" and sub["revision"] == found["revision"]:
        _status(c, sub["id"], "needs_reconnect", reason or "The provider refused it")
        if not found["delivery"]:
            _effect_changed(c, settings, sub["id"])
        _follow(c, settings, H.TICO_ACTOR, sub["owner_id"])
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


def assignment_fields(c, runner, config, *, bot="", inputs=None):
    """What `runners/assignments` adds to a bot's entry: {"subscriptions": {runtime: kind}} when the computer's
    subscription covers a runtime, so the runner does not count that runtime's missing sign-in against the bot."""
    from . import agent_subscriptions

    payment = agent_subscriptions.for_runner(c, runner, bot, config, inputs=inputs) if bot else None
    return (
        {"subscriptions": {config["runtime"]: payment.subscription["kind"]}} if payment and payment.subscription else {}
    )


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


def connect(c, settings, auth, who, body):
    """Start a Connect: queue the sign-in on a computer, remembering what its result becomes. Read back, given a code
    and cancelled like any sign-in (backend/model_login.py). Its row says where (`computer`) and what the subscription
    is called (`name`: the one given, else `default_name`). With no computer online on a company whose cloud computer
    can be started, it starts that one and answers `waking` with no sign-in: asked again, it queues once that is up.

    A Claude plan signs in with a full login (`claude auth login --claudeai`, kind `claude_login`) on a computer
    that can (backend/claude_login.py `can_sign_in`). Without one, a new plan or a setup-token plan's Reconnect uses
    `claude setup-token`. Reconnecting a setup-token plan on a computer that can sign in makes it a full login.
    A full login's Reconnect needs such a computer."""
    from . import claude_login, model_login

    name, sid = (body.name or "").strip(), body.subscription or ""
    sub = _manage_provider(c, auth, who, sid) if sid else None
    kind = sub["kind"] if sub else body.kind or ""
    spec = KINDS.get(kind)
    if not spec or not spec.get("connect"):
        raise Problem("kind", "Connect works for a Claude plan or a ChatGPT plan; paste an API key instead", 422)
    if sid:
        if not body.confirm:
            _confirm_reconnect(c, settings, [], _agent_attempts(c, sid))
    elif c.execute("SELECT count(*) FROM model_subscriptions").fetchone()[0] >= MAX_SUBSCRIPTIONS:
        raise Problem("limit", f"The company has {MAX_SUBSCRIPTIONS} subscriptions; remove one first", 422)
    # A reconnect keeps its owner; a new one is checked now, before anyone signs in.
    owner = sub["owner_id"] if sub else _default_owner(c, auth, who, body)
    if sub and body.owner_id is not None and owner_of(c, body.owner_id) != owner:
        raise Problem("owner", "A provider keeps its owner", 422)

    def answer(login, runner, plan_name):
        return {
            **model_login.view(login),
            "computer": runner["label"],
            "not_yours": runner["owner_id"] != H.actor_id(who.actor),
            "name": plan_name,
            "waking": False,
        }

    # A reloaded page picks up the sign-in already going, wherever it runs.
    model_login.sweep(c)
    claude = ("claude_token", "claude_login")
    same = claude if kind in claude else (kind,)
    target = c.execute(
        f"SELECT l.id,s.name,s.kind,s.owner_id FROM model_logins l JOIN subscription_connects s ON s.login_id=l.id "
        f"WHERE l.kind='connect' AND l.requested_by=? AND s.kind IN ({','.join('?' * len(same))}) "
        f"AND s.subscription_id=? AND s.owner_id=? AND {model_login.ACTIVE_SQL}"
        + (" AND s.name=?" if name else "")
        + " ORDER BY l.created_at",
        (who.actor, *same, sid, owner, *([name] if name else [])),
    ).fetchone()
    if target:
        if body.confirm:
            c.execute("UPDATE subscription_connects SET restart_confirmed=1 WHERE login_id=?", (target["id"],))
        going = c.execute("SELECT * FROM model_logins WHERE id=?", (target["id"],)).fetchone()
        plan_name = sub["name"] if sub else target["name"] or default_name(c, target["kind"], target["owner_id"])
        return answer(going, model_login._runner(c, going["runner_id"]), plan_name)
    computers = _connect_computers(c, spec["runtime"], who)
    if kind in claude:
        full = [r for r in computers if claude_login.can_sign_in(r)]
        if full or kind == "claude_login":
            computers, kind = full, "claude_login"
    plan_name = sub["name"] if sub else name or default_name(c, kind, owner)
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
                "name": plan_name,
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
    # The default name is numbered when saved, so concurrent sign-ins still get distinct names.
    c.execute(
        "INSERT INTO subscription_connects(login_id,kind,name,subscription_id,owner_id,restart_confirmed) "
        "VALUES(?,?,?,?,?,?)",
        (login["id"], kind, "" if sid else name, sid, owner, int(body.confirm)),
    )
    H.event(c, who.actor, "subscription.connect", sid or login["id"], {"kind": kind, "runner": runner["id"]})
    return answer(login, runner, plan_name)


def connected(c, vault, settings, auth, who, login_id, body):
    """A computer sends what its CLI gave it for a Connect it was asked to run: saved as the person asked, as if they
    had pasted it, and the job is done. A job cancelled or run out by now saves nothing. A value that does not check
    out fails the job, saying why (never the value)."""
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
            confirm=bool(target["restart_confirmed"]),
            owner_id=(target["owner_id"] or None) if not target["subscription_id"] else None,
        )
        try:
            auth.sync_access(c)
            requester = auth.identity_for_actor(c, login["requested_by"])
            save(
                c,
                vault,
                settings,
                auth,
                requester,
                write,
                target["subscription_id"] or None,
            )
        except Problem as problem:
            # A fixed word for the history: never the value, nor what the provider said about it.
            state, category = "failed", "value_refused"
            message = (
                "Work started during sign-in. Tico kept the previous credential. Reconnect again to confirm the restart."
                if problem.code == "confirm_restart"
                else model_login.redact(problem.detail)
            )
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
        from .agent_subscriptions import can_assign

        who = request.state.identity
        auth.domain(who)
        _reader(who)
        with store.read() as c:
            manages = _may_manage(auth, who)
            subscriptions = listed(c, settings)
            bots = {agent["id"] for sub in subscriptions for agent in sub["agents"]}
            access = auth.bot_accesses(c, who, sorted(bots)) if bots else {}
            assignable = {}
            for sub in subscriptions:
                owner = sub["owner_id"]
                if owner not in assignable:
                    assignable[owner] = can_assign(auth, who, owner)
                    if owner and assignable[owner]:
                        try:
                            owner_of(c, owner)
                        except Problem:
                            assignable[owner] = False
                sub["can_remove"] = _may_manage_provider(auth, who, sub)
                sub["can_assign"] = assignable[owner]
                if not manages and (not owned(sub["kind"]) or sub["owner_id"] != who.actor):
                    sub["preview"] = ""
                    if sub["status_detail"]:
                        sub["status_detail"] = "The provider refused it"
                sub["agents"] = [agent for agent in sub["agents"] if access[agent["id"]]["read"]]
            return {"subscriptions": subscriptions, "kinds": kinds(), "connect": connect_ready(c)}

    @app.post(
        "/api/v2/model-subscriptions",
        **api_v2(
            "Model subscriptions",
            "addModelSubscription",
            "Save a plan or API key in your pool. Owners and admins can specify another person. The value is required and never returned.",
            "ModelSubscription",
        ),
    )
    def add(request: Request, body: SubscriptionWrite):
        who = request.state.identity
        _person_secret(who)

        def authorize(c, actor):
            auth.sync_access(c)
            _default_owner(c, auth, actor, body)

        return vault.change(
            who,
            request.url.path,
            request.headers.get("idempotency-key"),
            body,
            authorize,
            lambda c: save(c, vault, settings, auth, who, body),
        )

    # Declared before `/{sid}`. It carries no value: that comes from the computer, to the route below.
    @app.post("/api/v2/model-subscriptions/connect")
    def start_connect(request: Request, body: SubscriptionConnect):
        who = request.state.identity
        _person_secret(who)
        return mutate(request, body, lambda c: connect(c, settings, auth, who, body))

    @app.post(
        "/api/v2/model-subscriptions/{sid}",
        **api_v2(
            "Model subscriptions",
            "changeModelSubscription",
            "Rename or reconnect a subscription as its owner or an admin. A reconnect answers 409 "
            "`confirm_restart` while runs would stop unless `confirm` is true",
            "ModelSubscription",
        ),
    )
    def change(request: Request, sid: str, body: SubscriptionWrite):
        who = request.state.identity
        _reader(who)
        if body.secret is not None:
            _person_secret(who)

        return vault.change(
            who,
            request.url.path,
            request.headers.get("idempotency-key"),
            body,
            lambda c, actor: _manage_provider(c, auth, actor, sid),
            lambda c: save(c, vault, settings, auth, who, body, sid),
        )

    @app.delete(
        "/api/v2/model-subscriptions/{sid}",
        **api_v2(
            "Model subscriptions",
            "removeModelSubscription",
            "Remove a subscription as its owner or an admin. "
            "Returns 409 `confirm_restart` if runs would stop without `confirm=1`",
            "ModelSubscriptionDeleted",
        ),
    )
    def delete(request: Request, sid: str, confirm: bool = False):
        who = request.state.identity

        def work(c):
            _manage_provider(c, auth, who, sid)
            return remove(c, settings, who, sid, confirm, auth=auth)

        return mutate(request, Empty(), work)

    # Read a Claude plan's week now (subscription_refresh.request_plan_read).
    @app.post("/api/v2/model-subscriptions/{sid}/usage-refresh")
    def refresh_usage(request: Request, sid: str):
        from . import subscription_refresh as SR

        who = request.state.identity

        def work(c):
            _manage_provider(c, auth, who, sid)
            return SR.request_plan_read(c, sid, who.actor)

        return mutate(request, Empty(), work)

    @app.get(
        "/api/v2/computer-subscriptions",
        **api_v2(
            "Model subscriptions",
            "listComputerSubscriptions",
            "Read-only compatibility view of the Paid by choices of agents on each computer, per AI tool",
            "ComputerSubscriptionList",
        ),
    )
    def computer_subscriptions(request: Request):
        who = request.state.identity
        auth.domain(who)
        _reader(who)
        with store.read() as c:
            return assignments(c, settings, who=who, auth=auth)

    @app.put(
        "/api/v2/computer-subscriptions",
        status_code=410,
        deprecated=True,
        **api_v2(
            "Model subscriptions",
            "setComputerSubscription",
            "Use PUT /api/v2/bots/{bot}/subscriptions",
            responses={"410": error("The computer picker moved to the agent's Paid by")},
            idempotency=KEYLESS,
        ),
    )
    def set_computer_subscription(request: Request):
        auth.domain(request.state.identity)
        raise Problem("gone", "Set the agent's Paid by with PUT /api/v2/bots/{bot}/subscriptions", 410)

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
            return connected(c, vault, settings, auth, who, lid, body)

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
