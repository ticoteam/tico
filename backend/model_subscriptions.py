"""Company subscriptions, given to computers (Settings > Models).

A subscription is one of the company's model logins or keys, saved once in Tico rather than on a computer: a Claude
setup-token (`claude setup-token`), a ChatGPT login (Codex's `auth.json` from a `codex login`), or an Anthropic or
OpenAI API key. It belongs to the company; `added_by` only says who saved it. A plan (Claude or ChatGPT) has an owner
(`owner_id`), the person whose plan it is: the one who adds it, unless an owner or admin names another active person of
the company, then or later. A plan is meant for its owner's own bots, not shared ones (the Add pop-up says so). An API
key bills per use and has no owner; a company may save many. The value is sealed with the credential
vault's data key (CredentialCipher.seal, context `model_subscription:<id>`), never shown again, never in a list, an
event or an idempotency receipt (saves go through Vault.change, which keeps only a keyed fingerprint).

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
there only while a turn on it runs (runner-rs/src/model_subscriptions.rs). Codex refreshes a ChatGPT login in that
file; the runner sends the refreshed file back (`POST runner-model-subscription-refresh`) so the next computer starts
from it. Only a live run still given the subscription on that computer, at the revision it was handed, writes back
(`refreshed`).

Revisions: a run reads only what it was handed. A reconnect (a new value) is a change like any other: it stops the
live runs given it (confirmed first) and starts their sessions fresh (`_restart`), so a run never reads another
account's value. A refresh is the same account: a run still going that reads after one gets the refreshed value and
its record moves with it (`run_values`), so its write-back and its verdict (`turn_rejected`, `turn_worked`) match
what it ran on.

Only a runner that says it can (`CAPABILITY`) is handed subscriptions; an older one runs as before and the page says it
needs an update. One computer is one trust group: every bot on a computer runs as the same OS user there (SECURITY.md).

A run the provider refuses on a subscription marks it `needs_reconnect` and leaves the computer's own login alone: it is
passed over until someone saves a new value (Reconnect), and a run on it that completes marks it working. Only a run on
the subscription's current revision decides either: a late refusal of a value already replaced says nothing of the new
one.
"""
import base64
import json
from datetime import datetime, timedelta, timezone

from pydantic import Field, SecretStr

from . import hubdb as H
from .models import Contract, ID
from .store import Problem, readiness_document

CAPABILITY = "model_subscriptions_v1"
ACTIVATION_CAPABILITY = "model_subscription_activation_v1"
STATUSES = ("new", "working", "needs_reconnect")
MAX_SUBSCRIPTIONS = 200
MAX_VALUE = 64 * 1024

# What each kind is, the runtime it signs in, and how a turn receives it: `env` in the turn's environment, or `file` in
# the CLI's own home (set by the runner). `billing` is what the run records (backend/usage.py). Listed in the page's order.
KINDS = {
    "claude_token": {"runtime": "claude", "label": "Claude plan", "env": "CLAUDE_CODE_OAUTH_TOKEN",
                     "billing": "subscription", "secret_label": "Setup token",
                     "help": "Run `claude setup-token` on any computer and paste the token.", "connect": True},
    "codex_login": {"runtime": "codex", "label": "ChatGPT plan", "file": "auth.json", "billing": "subscription",
                    "secret_label": "auth.json",
                    "help": "Run `codex login` on any computer, then paste the whole ~/.codex/auth.json.", "connect": True},
    "anthropic_key": {"runtime": "claude", "label": "Anthropic API key", "env": "ANTHROPIC_API_KEY", "billing": "api",
                      "secret_label": "API key", "help": "An Anthropic API key (sk-ant-…)."},
    "openai_key": {"runtime": "codex", "label": "OpenAI API key", "file": "auth.json", "billing": "api",
                   "secret_label": "API key", "help": "An OpenAI API key (sk-…)."},
}
RUNTIMES = tuple(dict.fromkeys(k["runtime"] for k in KINDS.values()))
RUNTIME_LABELS = {"claude": "Claude Code", "codex": "Codex"}
API_KINDS = tuple(kind for kind, spec in KINDS.items() if spec.get("billing") == "api")

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_subscriptions(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, sealed TEXT NOT NULL,
 preview TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'new',
 status_detail TEXT NOT NULL DEFAULT '', status_at TEXT, added_by TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT '', credential_at TEXT);
CREATE TABLE IF NOT EXISTS computer_subscriptions(
 runner_id TEXT NOT NULL, runtime TEXT NOT NULL, subscription_id TEXT NOT NULL, set_by TEXT NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(runner_id, runtime));
CREATE INDEX IF NOT EXISTS computer_subscriptions_sub ON computer_subscriptions(subscription_id);
CREATE TABLE IF NOT EXISTS attempt_subscriptions(
 attempt_id TEXT NOT NULL, runtime TEXT NOT NULL, subscription_id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(attempt_id, runtime));
CREATE INDEX IF NOT EXISTS attempt_subscriptions_sub ON attempt_subscriptions(subscription_id);
CREATE TABLE IF NOT EXISTS pending_subscription_fallbacks(
 attempt_id TEXT NOT NULL, runtime TEXT NOT NULL, PRIMARY KEY(attempt_id, runtime));
CREATE TABLE IF NOT EXISTS subscription_connects(
 login_id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', subscription_id TEXT NOT NULL DEFAULT '',
 owner_id TEXT NOT NULL DEFAULT '');
"""


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
    """Sign a plan in from the page: a new subscription (`kind`, `name`), or a saved one's new value (`subscription`).
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
    """Give a computer a subscription for one runtime; a null `subscription` takes it off. `confirm` accepts that the
    computer's live runs on that runtime stop and start again."""
    runner_id: ID
    runtime: str = Field(pattern="^(claude|codex)$")
    subscription: ID | None = None
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
    if not isinstance(tokens, dict) or not any(isinstance(tokens.get(k), str) and tokens[k]
                                               for k in ("refresh_token", "access_token")):
        raise Problem("secret", "Paste the whole auth.json from a computer where `codex login` is signed in "
                      "(~/.codex/auth.json)", 422)
    return value


def checked(kind, secret):
    """(value to seal, preview) for `kind`, or a Problem naming what is wrong. Whitespace around a token or key goes."""
    if kind not in KINDS:
        raise Problem("kind", "Choose Claude plan, ChatGPT plan, Anthropic API key or OpenAI API key", 422)
    text = str(secret or "")
    if kind == "codex_login":
        value = _codex_login(text.strip())
        email = _jwt_email((value.get("tokens") or {}).get("id_token"))
        return json.dumps(value, separators=(",", ":")), email or "ChatGPT login"
    text = text.strip()
    if not text or any(ch.isspace() for ch in text) or len(text) > 4096:
        raise Problem("secret", "Paste the token or key on its own", 422)
    prefix = {"claude_token": "sk-ant-", "anthropic_key": "sk-ant-", "openai_key": "sk-"}[kind]
    if not text.startswith(prefix):
        what = {"claude_token": "A Claude setup-token (from `claude setup-token`)",
                "anthropic_key": "An Anthropic API key", "openai_key": "An OpenAI API key"}[kind]
        raise Problem("secret", f"{what} starts with {prefix}", 422)
    return text, text[:7] + "…" + text[-4:]


def delivered(kind, value):
    """What the runner puts in place for one turn: {"env": NAME, "value": v} or {"file": NAME, "value": text}."""
    spec = KINDS[kind]
    if spec.get("env"):
        return {"env": spec["env"], "value": value}
    if kind == "openai_key":
        # The file `codex login --with-api-key` writes.
        return {"file": spec["file"], "value": json.dumps({"OPENAI_API_KEY": value}, separators=(",", ":"))}
    return {"file": spec["file"], "value": value}


# ------------------------------------------------------------------------------------------------ reads
def _present(c):
    return H._has_table(c, "model_subscriptions")


def row(c, sid):
    return H._one(c, "SELECT * FROM model_subscriptions WHERE id=?", (sid,)) if _present(c) else None


def runner_takes(runner):
    caps = H._json(runner["capabilities"], []) if runner else []
    return CAPABILITY in (caps or [])


def _given(c):
    """{(runner_id, runtime): subscription (no value)} for every computer given one, read once per snapshot: the claim
    asks for every bot on every poll, and most installs have none."""
    from . import read_cache
    values = read_cache.cache(c)
    if values is not None and "model_subscriptions.given" in values:
        return values["model_subscriptions.given"]
    found = {}
    if _present(c):
        for r in c.execute("SELECT g.runner_id AS runner_id,g.runtime AS runtime,s.id,s.name,s.kind,s.revision,"
                           "s.status FROM computer_subscriptions g JOIN model_subscriptions s "
                           "ON s.id=g.subscription_id"):
            found[(r["runner_id"], r["runtime"])] = {k: r[k] for k in ("id", "name", "kind", "revision", "status")}
    if values is not None:
        values["model_subscriptions.given"] = found
    return found


def given_to(c, runner_id):
    """[(runtime, subscription id)] the computer is given, whatever their status."""
    return [(runtime, sub["id"]) for (rid, runtime), sub in _given(c).items() if rid == runner_id]


def still_given(c, runner_id, runtime, subscription_id):
    """Whether the computer is given this subscription for `runtime` now (whatever its status)."""
    sub = _given(c).get((runner_id, runtime))
    return bool(sub) and sub["id"] == subscription_id


RUNNER_READINESS_SQL = "SELECT readiness FROM runners_with_state WHERE id=?"
RUNNER_CAPABILITIES_SQL = "SELECT capabilities FROM runners_with_state WHERE id=? AND revoked_at IS NULL"


def in_effect(c, runner_id, runtime, loads=None):
    """The subscription a turn on `runtime` on this computer signs in with, or None for the computer's own login (none
    given, the one given needs reconnecting or is held at a usage limit until its reset, or a runner too old to take
    one). A held one is passed over like one that needs reconnecting: the turn runs on the computer's own login, else
    the Gateway under the company's choice, else waits; it never waits on the subscription's hold. After the reset its
    first turn is the hold's probe as usual (backend/login_holds.py)."""
    given = loads.given if loads is not None else _given(c)
    sub = given.get((runner_id, runtime)) if given else None
    if not sub or sub["status"] == "needs_reconnect" or KINDS.get(sub["kind"], {}).get("runtime") != runtime:
        return None
    from . import login_holds
    if login_holds.holding(c, "", login_holds.subscription_login(sub["id"]), runtime, loads=loads) == "held":
        return None
    if loads is not None:
        runner = loads.runner_of(c, runner_id, live=True)
    else:
        from .read_cache import one
        runner = one(c, RUNNER_CAPABILITIES_SQL, (runner_id,))
    return sub if runner_takes(runner) else None


def turn_runtimes(config, record=None):
    """Subscription runtimes this turn may use, including the company's automatic fallbacks when `record` is given.
    Gateway models and desktop delivery never use these credentials."""
    from .gateway_connection import is_tico
    from .harnesses import runtime_of
    config = config if isinstance(config, dict) else {}
    if config.get("app_delivery"):
        return []
    out = [] if is_tico(config.get("model")) else [str(config.get("runtime") or "")]
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    if fallback.get("harness") and not fallback.get("off") and not is_tico(fallback.get("model")):
        out.append(runtime_of(fallback["harness"]))
    if record is not None and not fallback.get("off"):
        from . import providers
        from .harnesses import harness_caps
        # Like the runner's limited_config, ignore a named model on the same spent login.
        same_login = (fallback.get("model") and runtime_of(fallback.get("harness") or "") == config.get("runtime")
                      and harness_caps(config.get("harness"), config.get("runtime")).get("api_keys") != "bot"
                      and harness_caps(fallback.get("harness")).get("api_keys") != "bot")
        if not fallback.get("harness") or same_login:
            out.extend(r["runtime"] for r in providers.default_fallbacks(record)
                       if not is_tico(r["model"]))
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
# An API key's month cost, driven from the month's turns (turns_started), each one's attempt_subscriptions rows by their
# primary key, then the attempt. CROSS JOIN keeps that order whatever sqlite_stat1 says: the plan without it read every
# attempt a key ever ran, and probed each turn, on every listing.
COSTS_SQL = ("SELECT s.subscription_id, coalesce(sum(t.est_cost_usd),0) AS cost FROM turns t "
             "CROSS JOIN attempt_subscriptions s ON s.attempt_id=t.id CROSS JOIN attempts a ON a.id=t.id "
             "WHERE t.started_at>=? AND s.subscription_id IN ({ids}) AND coalesce(a.handed_runtime,s.runtime)=s.runtime "
             "GROUP BY s.subscription_id")


def costs_sql(count):
    """The month-cost statement for `count` API keys (its placeholders: the month, then the keys)."""
    return COSTS_SQL.format(ids=",".join("?" * count))


def _api_costs(c, month, sid=None):
    """{subscription id: this month's cost} for the API keys, which bill per use; {} when none is saved (the usual company's
    plans alone), so a list of plans runs no cost query. With `sid`, only that key."""
    where = " AND id=?" if sid else ""
    ids = [r[0] for r in c.execute(
        f"SELECT id FROM model_subscriptions WHERE kind IN ({','.join('?' * len(API_KINDS))}){where}",
        (*API_KINDS, *([sid] if sid else []))).fetchall()]
    if not ids:
        return {}
    return {r["subscription_id"]: r["cost"] for r in H._rows(c.execute(costs_sql(len(ids)), (month, *ids)))}


def _listing(c, sid=None):
    """What a plan list reads about plans and their computers, in a fixed number of queries however many plans and
    computers there are (Settings > Models shows every plan's usage at once): the grants with their computers, each
    computer's readiness, its named logins' reported runtimes, the login holds, the API keys' month cost and the
    registry metadata. With `sid`, only that plan's grants and computers are read."""
    from . import login_holds
    from .subscriptions import subscription_meta
    where, args = ("WHERE g.subscription_id=?", (sid,)) if sid else ("", ())
    grants = {}
    for g in H._rows(c.execute("SELECT g.subscription_id,g.runner_id,g.runtime,g.updated_at AS granted_at "
                               f"FROM computer_subscriptions g {where} ORDER BY g.runner_id, g.runtime", args)):
        grants.setdefault(g["subscription_id"], []).append(g)
    computers = f"SELECT g.runner_id FROM computer_subscriptions g {where}"
    runners = {r["id"]: r for r in H._rows(c.execute(
        "SELECT id,label,last_seen_at,revoked_at,readiness,capabilities FROM runners_with_state "
        f"WHERE revoked_at IS NULL AND id IN ({computers})", args))}
    ready = {rid: readiness_document(r["readiness"]) for rid, r in runners.items()}
    profiles = {(r["runner_id"], r["profile"]): r["runtimes"] for r in H._rows(c.execute(
        f"SELECT runner_id,profile,runtimes FROM computer_profiles WHERE runner_id IN ({computers})", args))}
    costs = _api_costs(c, H.now()[:7] + "-01", sid)
    holds = {row["key"]: row["value"] for row in c.execute(
        "SELECT key,value FROM registry_metadata WHERE key>=? AND key<?",
        (login_holds.PREFIX, login_holds.PREFIX[:-1] + ";"))}
    return {"grants": grants, "runners": runners, "ready": ready, "profiles": profiles, "costs": costs,
            "holds": holds, "meta": subscription_meta(c), "people": {}}


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
    hold = login_holds.get(c, "", login_holds.subscription_login(sub["id"]), spec.get("runtime", ""),
                           loads=SimpleNamespace(holds=ctx["holds"]))
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
    return preview if sub.get("kind") == "codex_login" and "@" in preview else ""


# A reading is taken during a turn on its computer. The computer's clock and this server's can differ a little, so a turn
# counts for a reading from OWNER_SKEW before its start to OWNER_SKEW after its end; a turn that began up to OWNER_RUN
# before the reading (a turn runs for hours at most) is one that may have taken it.
OWNER_RUN = timedelta(hours=12)
OWNER_SKEW = timedelta(minutes=2)


def _stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def reading_owner(c, runner_id, runtime, reported):
    """Whose a computer's reading for `runtime` is, from the turns on that computer that overlapped it: the one
    subscription every one of them signed in with (its id), "" when each ran on the computer's own login, or None when
    they did not agree or none overlapped it, so it cannot be told apart and is no plan's. A turn on the other runtime
    that overlaps counts too: nothing in the report says which turn took it."""
    at = H.parse_ts(reported)
    if at is None or at.tzinfo is None or not H._has_table(c, "attempt_subscriptions"):
        return None
    owners = {row[0] for row in c.execute(
        "SELECT coalesce(s.subscription_id,'') FROM attempts a LEFT JOIN attempt_subscriptions s "
        "ON s.attempt_id=a.id AND s.runtime=? WHERE a.runner_id=? AND a.created_at>=? AND a.created_at<=? "
        "AND coalesce(a.finished_at,a.lease_until)>=?",
        (runtime, runner_id, _stamp(at - OWNER_RUN), _stamp(at + OWNER_SKEW), _stamp(at - OWNER_SKEW)))}
    return owners.pop() if len(owners) == 1 else None


def stamped(c, runner_id, runtime, reading, previous=None):
    """A computer's `reading` (one usage window, or None) with its `owner`: the same report `previous` already had keeps
    that owner, and a new one takes `reading_owner`. Whatever owner the computer sent is never kept."""
    if not reading:
        return None
    if previous and previous.get("reported_at") == reading.get("reported_at") and "owner" in previous:
        owner = previous["owner"]
    else:
        owner = reading_owner(c, runner_id, runtime, reading.get("reported_at"))
    return {**reading, "owner": owner}


def stamp_readiness(c, runner_id, readiness, before):
    """Each runtime's usage windows on a computer's readiness document (its own login's), owned (`stamped`) against the
    document they replace (`before`)."""
    old = (before or {}).get("runtimes") or {}
    for runtime, row in ((readiness or {}).get("runtimes") or {}).items():
        if not isinstance(row, dict):
            continue
        previous = old.get(runtime) if isinstance(old.get(runtime), dict) else {}
        for window in ("weekly", "five_hour"):
            if row.get(window):
                row[window] = stamped(c, runner_id, runtime, row[window], previous.get(window))


def _plan_usage(c, sub, runtime, ctx):
    """A plan's used percent from the computers it is given to. Each signs in with it on its turns, and the runner files
    those turns' usage windows under the computer's default profile (runner-rs drive.rs `remember_weekly`, with the
    profile `turn_profile` picks, which the heartbeat names as readiness `computer_profile`; "" is its own home). So that
    profile's windows for `runtime` are this plan's, but only a reading that is this plan's: taken since the plan was
    given to that computer or its credential was last replaced (`credential_at`: a new value may be another account; a
    rename, an owner change or a refresh of the same account is not), taken on a turn that signed in with this plan
    (the reading's `owner`, `stamped`), and, where the plan names its account, reported for that account. A window whose
    reset has passed is no longer any reading.
    The most-used window wins, its freshest report breaking a tie. Otherwise the reason is what the data tells: not on a
    computer, a reconnect due, a computer too old, a plan that sends no percent, readings from before it was added,
    computers offline, or a computer yet to report."""
    from . import model_login
    from .subscriptions import WINDOWS, computer_logins
    runners = ctx["runners"]
    computers = [g for g in ctx["grants"].get(sub["id"], []) if g["runtime"] == runtime and g["runner_id"] in runners]
    if not computers:
        return _reason("Not on a computer yet")
    if sub["status"] == "needs_reconnect":
        return _reason("Reconnect to see usage")
    readers = [g for g in computers if runner_takes(runners[g["runner_id"]])
               and computer_logins(runners[g["runner_id"]], ctx["ready"][g["runner_id"]])]
    if not readers:
        return _reason("Update the computer to see usage")
    now = datetime.now(timezone.utc)
    account_of_plan = _plan_identity(sub)
    best, percent_missing, outside = None, False, False
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
            # Not taken on a turn that signed in with this plan (`owner`), or no turn overlapped it: not this plan's.
            if (reported < since or reading.get("owner") != sub["id"]
                    or (account_of_plan and account.lower() != account_of_plan.lower())):
                outside = True
                continue
            if reading.get("used_percent") is None:
                percent_missing = True
                continue
            candidate = (reading["used_percent"], reported, label, reading.get("resets_at"))
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    if best:
        used, _, label, resets = best
        return {"window": label, "used_pct": used, "cost_usd_month": None, "resets_at": resets}
    if percent_missing:
        return _reason("No percentage from the plan")
    if outside:
        return _reason("No usage reported since this subscription was added")
    if not any(model_login._online(runners[g["runner_id"]]) for g in readers):
        return _reason("Its computers are offline")
    return _reason("Waiting for a computer to report")


def view(c, sub, ctx=None):
    """A subscription as Settings > Models lists it: no value, a preview, its status, the computers given it."""
    ctx = _listing(c, sub["id"]) if ctx is None else ctx
    spec = KINDS.get(sub["kind"]) or {}
    computers = [g["runner_id"] for g in ctx["grants"].get(sub["id"], [])]
    added = _person(c, ctx, sub["added_by"])
    owner = _person(c, ctx, sub["owner_id"]) if sub.get("owner_id") else {}
    return {"id": sub["id"], "name": sub["name"], "kind": sub["kind"], "runtime": spec.get("runtime", ""),
            "label": spec.get("label", sub["kind"]), "status": sub["status"], "status_detail": sub["status_detail"],
            "status_at": sub["status_at"], "preview": sub["preview"], "revision": sub["revision"],
            "computers": list(dict.fromkeys(computers)), "usage": _usage(c, sub, ctx), "added_by": sub["added_by"],
            "added_by_name": added.get("name") or added.get("email") or "", "owner_id": sub.get("owner_id") or "",
            "owner_name": owner.get("name") or owner.get("email") or "",
            "created_at": sub["created_at"], "updated_at": sub["updated_at"]}


def listed(c):
    if not _present(c):
        return []
    subs = H._rows(c.execute("SELECT * FROM model_subscriptions ORDER BY lower(name), id LIMIT ?", (MAX_SUBSCRIPTIONS,)))
    ctx = _listing(c) if subs else None
    return [view(c, s, ctx) for s in subs]



def kinds():
    return [{"kind": k, "label": v["label"], "runtime": v["runtime"], "secret_label": v["secret_label"],
             "help": v["help"], "connect": bool(v.get("connect")), "owned": owned(k)} for k, v in KINDS.items()]


def assignments(c):
    """What `GET computer-subscriptions` answers: every computer's subscription per runtime, and the computers that
    can take one (the rest need an update)."""
    given = [{"runner_id": r["runner_id"], "runtime": r["runtime"], "subscription_id": r["subscription_id"]}
             for r in c.execute("SELECT g.* FROM computer_subscriptions g JOIN runners r ON r.id=g.runner_id "
                                "WHERE r.revoked_at IS NULL ORDER BY g.runner_id, g.runtime")] if _present(c) else []
    capable = [r["id"] for r in c.execute("SELECT id,capabilities FROM runners_with_state WHERE revoked_at IS NULL "
                                          "ORDER BY id") if runner_takes(r)]
    return {"assignments": given, "capable": capable}


# ------------------------------------------------------------------------------------------------ permissions
def _reader(who):
    if who.role not in ("human", "owner") or not str(who.actor).startswith("human:"):
        raise Problem("forbidden", "Only a person reads the company's subscriptions", 403)


def _admin(auth, who, secret=False):
    """An owner or admin. A secret is pasted by the person themselves: never through the Assistant or BotOps acting
    for them, a bot run, or a Tico support session; support changes nothing here."""
    if (who.role not in ("human", "owner") or not str(who.actor).startswith("human:") or who.support
            or not auth.bot_admin(who)):
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
    if (not pid or (":" in text and not text.startswith("human:")) or pid == "tico-support" or not H.human(c, pid)
            or standing(c, pid)):
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
    for a in c.execute("SELECT a.* FROM attempts a WHERE a.runner_id=? AND a.state IN ('leased','running') "
                       "AND a.stop_requested_at IS NULL ORDER BY a.created_at", (runner_id,)).fetchall():
        own = a["handed_runtime"] == runtime and not is_tico(a["handed_model"])
        given = handed(c, a["id"], runtime)
        pending = c.execute("SELECT 1 FROM pending_subscription_fallbacks WHERE attempt_id=? AND runtime=?",
                            (a["id"], runtime)).fetchone()
        if own or (given and not pending):
            out.append(a)
    return out


def _bots(c, settings, runner_id, runtime):
    """The bots on this computer whose turns run `runtime` (their own or their named fallback's)."""
    from . import providers
    from .shared_bots import follow
    record = providers.load(c, settings)
    out = []
    for r in c.execute("SELECT a.bot,b.config FROM assignments a JOIN bot_config b ON b.bot=a.bot "
                       "WHERE a.runner_id=? ORDER BY a.bot", (runner_id,)).fetchall():
        config = providers.fill(record, follow(c, r["bot"], json.loads(r["config"] or "{}")))
        # Automatic candidates must not reset unrelated primary conversations; their session keys bind the login.
        if runtime in turn_runtimes(config):
            out.append(r["bot"])
    return out


def _sessions(c, bot):
    """How many sessions app.reset_bot_sessions starts fresh for `bot`: each of its conversations."""
    return sum(1 for (p,) in c.execute("SELECT participants FROM conversations WHERE participants LIKE ?",
                                       (f'%"bot:{bot}"%',)) if "bot:" + bot in json.loads(p or "[]"))


def _reset(c, settings, runner_id, runtime):
    """Every session of `runtime` on this computer starts fresh: each bot there that runs it has its sessions fenced
    (app.reset_bot_sessions, as a move to another computer does) and its saved thread pointer for that runtime let go.
    Returns how many sessions were reset."""
    from .app import reset_bot_sessions
    n = 0
    for bot in _bots(c, settings, runner_id, runtime):
        n += reset_bot_sessions(c, bot)
        c.execute("DELETE FROM bot_sessions WHERE bot=? AND runtime=?", (bot, runtime))
    return n


def _plan(c, settings, runner_id, runtime):
    return {"running": len(_live(c, runner_id, runtime)),
            "sessions": sum(_sessions(c, bot) for bot in _bots(c, settings, runner_id, runtime))}


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
    lift_limited_on_computer(c, settings, H.KEEPER, runner_id, runtime)
    return {"stopped": stopped, "sessions_reset": reset}


def _signed_in_with(c, sid):
    """The computers given this subscription that sign in with it: (runner_id, runtime) of each whose runner takes one.
    An older runner never did (its turns use its own login, `in_effect`), so a change of the subscription leaves its
    runs and sessions alone."""
    return [(g["runner_id"], g["runtime"]) for g in c.execute(
        "SELECT g.runner_id,g.runtime,r.capabilities FROM computer_subscriptions g "
        "JOIN runners_with_state r ON r.id=g.runner_id WHERE g.subscription_id=? ORDER BY g.runner_id, g.runtime",
        (sid,)).fetchall() if runner_takes(g)]


def _effect_changed(c, settings, sid):
    """The subscription stopped counting (it needs reconnecting): each computer signing in with it now signs that
    runtime in another way, so its sessions there start fresh. Its runs are not stopped: the refused one already ended.
    (A reconnect restarts them, `save`.)"""
    for runner_id, runtime in _signed_in_with(c, sid):
        _reset(c, settings, runner_id, runtime)


def _confirm(plan, extra=None):
    raise Problem("confirm_restart", f"{plan['running']} running run{'s' if plan['running'] != 1 else ''} will stop "
                  "and start again", 409, extra={**plan, **(extra or {})})


# ------------------------------------------------------------------------------------------------ writes
def _tick_provider(c, settings, actor, kind):
    """Saving a subscription turns its provider on in the company's providers, so its models are offered."""
    from . import providers
    provider = providers.runtime_provider(KINDS[kind]["runtime"])
    record = providers.load(c, settings)
    if not provider or provider in record["enabled"]:
        return
    before, after = providers.save(c, actor, {"expected_revision": record["revision"],
                                              "enabled": [*record["enabled"], provider],
                                              "runtime": record["runtime"], "model": record["model"]},
                                   H.now(), settings)
    H.event(c, actor, "providers.updated", "", {"before": {"enabled": before["enabled"]},
                                                 "after": {"enabled": after["enabled"]}, "why": "subscription"})


def _confirm_reconnect(c, settings, given):
    """A new value stops the runs on each computer signed in with this subscription: with some going, ask first."""
    plans = [_plan(c, settings, runner_id, runtime) for runner_id, runtime in given]
    running = sum(p["running"] for p in plans)
    if running:
        _confirm({"running": running, "sessions": sum(p["sessions"] for p in plans)}, {"computers": len(given)})


def save(c, vault, settings, who, body, sid=None):
    """Add a subscription, or change a saved one: a new secret reconnects it (status `new`, so it counts again) and ends
    its usage-limit hold as Try now does, a name renames it, an `owner_id` gives it another owner. Returns its view."""
    now = H.now()
    if sid:
        sub = row(c, sid)
        if not sub:
            raise Problem("not_found", "Subscription not found", 404)
        if body.kind and body.kind != sub["kind"]:
            raise Problem("kind", "A subscription keeps its kind; add a new one instead", 422)
        name = body.name.strip() if body.name is not None else None
        if body.name is not None and not name:
            raise Problem("name", "Enter a name", 422)
        owner = owner_of(c, body.owner_id, sub["kind"]) if body.owner_id is not None else None
        given = []
        if body.secret is not None:
            value, preview = checked(sub["kind"], body.secret.get_secret_value())
            given = _signed_in_with(c, sid)
            if not body.confirm:
                _confirm_reconnect(c, settings, given)
        if name is not None:
            c.execute("UPDATE model_subscriptions SET name=?,updated_at=? WHERE id=?", (name, now, sid))
        if owner is not None and owner != sub["owner_id"]:
            c.execute("UPDATE model_subscriptions SET owner_id=?,updated_at=? WHERE id=?", (owner, now, sid))
            H.event(c, who.actor, "subscription.owner_changed", sid, {"from": sub["owner_id"], "to": owner})
        if body.secret is not None:
            # The credential's own time: readings taken before it are the old account's (`_plan_usage`). Only a replaced or
            # reconnected value moves it; a rename, an owner change or a same-account refresh (`refreshed`) does not.
            c.execute("UPDATE model_subscriptions SET sealed=?,preview=?,revision=revision+1,status='new',"
                      "status_detail='',status_at=?,updated_at=?,credential_at=? WHERE id=?",
                      (vault.cipher.seal(c, _context(sid), value), preview, now, now, now, sid))
            # A new value may be another account: its own usage-limit hold ends as Try now does, one probe turn tests it
            # (login_holds.try_now), before the bots are re-judged. As for a change, every computer given it stops its
            # live runs on that runtime (their work goes back in the queue; confirmed first) and starts its sessions
            # there fresh. An older runner never signed in with it, so nothing changes there.
            from . import login_holds
            login_holds.try_now(c, "", login_holds.subscription_login(sid), KINDS[sub["kind"]]["runtime"])
            stopped = sum(_restart(c, settings, runner_id, runtime)["stopped"] for runner_id, runtime in given)
            H.event(c, who.actor, "subscription.updated", sid, {"secret_changed": True, "stopped": stopped})
            return view(c, row(c, sid))
        H.event(c, who.actor, "subscription.updated", sid, {"secret_changed": False})
        return view(c, row(c, sid))
    if not body.kind or body.secret is None:
        raise Problem("secret", "Choose a kind and paste the token, key or login", 422)
    name = (body.name or "").strip()
    if not name:
        raise Problem("name", "Enter a name", 422)
    if c.execute("SELECT count(*) FROM model_subscriptions").fetchone()[0] >= MAX_SUBSCRIPTIONS:
        raise Problem("limit", f"The company has {MAX_SUBSCRIPTIONS} subscriptions; remove one first", 422)
    value, preview = checked(body.kind, body.secret.get_secret_value())
    owner = _default_owner(c, who, body, body.kind)
    sid = H.new_id()
    c.execute("INSERT INTO model_subscriptions(id,name,kind,sealed,preview,added_by,owner_id,created_at,updated_at,"
              "credential_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (sid, name, body.kind, vault.cipher.seal(c, _context(sid), value), preview, who.actor, owner, now, now,
               now))
    H.event(c, who.actor, "subscription.created", sid, {"kind": body.kind})
    _tick_provider(c, settings, who.actor, body.kind)
    return view(c, row(c, sid))


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
    c.execute("DELETE FROM computer_subscriptions WHERE subscription_id=?", (sid,))
    stopped = reset = 0
    for runner_id, runtime in given:
        done = _restart(c, settings, runner_id, runtime)
        stopped, reset = stopped + done["stopped"], reset + done["sessions_reset"]
    c.execute("DELETE FROM model_subscriptions WHERE id=?", (sid,))
    H.event(c, who.actor, "subscription.deleted", sid, {"kind": sub["kind"], "computers": computers,
                                                         "stopped": stopped})
    return {"deleted": True, "id": sid, "computers": computers, "stopped": stopped, "sessions_reset": reset}


def give(c, settings, who, body):
    """Give a computer a subscription for one runtime, switch it, or take it off (`subscription` null). A change stops
    the computer's live runs on that runtime (confirmed first), starts its sessions of that runtime fresh, and re-judges
    the bots there that were Rate limited on the old sign-in. A runner too old to take one keeps signing in with its own
    login, so there it is only recorded: nothing stops, nothing is re-judged."""
    runner = c.execute("SELECT * FROM runners_with_state WHERE id=? AND revoked_at IS NULL", (body.runner_id,)).fetchone()
    if not runner:
        raise Problem("not_found", "Computer not found", 404)
    sub = row(c, body.subscription) if body.subscription else None
    if body.subscription and not sub:
        raise Problem("not_found", "Subscription not found", 404)
    if sub and KINDS[sub["kind"]]["runtime"] != body.runtime:
        raise Problem("wrong_runtime", f"{sub['name']} signs in {RUNTIME_LABELS[KINDS[sub['kind']]['runtime']]}, "
                      f"not {RUNTIME_LABELS.get(body.runtime, body.runtime)}", 422)
    current = c.execute("SELECT subscription_id FROM computer_subscriptions WHERE runner_id=? AND runtime=?",
                        (body.runner_id, body.runtime)).fetchone()
    want = sub["id"] if sub else None
    out = {"runner_id": body.runner_id, "runtime": body.runtime, "subscription_id": want}
    if (current[0] if current else None) == want:
        return {**out, "stopped": 0, "sessions_reset": 0}
    takes = runner_takes(runner)
    if takes and not body.confirm:
        plan = _plan(c, settings, body.runner_id, body.runtime)
        if plan["running"]:
            _confirm(plan)
    if want:
        _set(c, body.runner_id, body.runtime, want, who.actor)
    else:
        c.execute("DELETE FROM computer_subscriptions WHERE runner_id=? AND runtime=?", (body.runner_id, body.runtime))
    done = _restart(c, settings, body.runner_id, body.runtime) if takes else {"stopped": 0, "sessions_reset": 0}
    H.event(c, who.actor, "subscription.given" if want else "subscription.taken_off", want or current[0],
            {"runner": body.runner_id, "runtime": body.runtime, **done})
    return {**out, **done}


def _set(c, runner_id, runtime, subscription_id, actor):
    """Record that the computer signs `runtime` in with the subscription: one per runtime (`give` and `lend` write it)."""
    c.execute("INSERT INTO computer_subscriptions(runner_id,runtime,subscription_id,set_by,updated_at) "
              "VALUES(?,?,?,?,?) ON CONFLICT(runner_id,runtime) DO UPDATE SET "
              "subscription_id=excluded.subscription_id,set_by=excluded.set_by,updated_at=excluded.updated_at",
              (runner_id, runtime, subscription_id, actor, H.now()))


def bot_runtimes(c, settings, bot):
    """The subscription runtimes a bot may run on its computer: its own turns' (as `_bots` reads them) and its task
    model's, which a task runs on when it is set (backend/task_models.py `apply`, the config a task turn runs with).
    Each goes through `turn_runtimes`, so a Tico model and a desktop-app bot count for none."""
    from . import providers, task_models
    from .shared_bots import follow
    found = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
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


def lend(c, runner_id, runtime, actor, shared, others):
    """Give a cloud machine's computer the company's plan for `runtime` when it has no plan and no sign-in of its own
    there, so a bot placed on it is not left waiting for a sign-in nobody will make. Nothing is replaced, stopped or
    reset: a computer with a plan, a sign-in reported (any state but missing), or a live run on that runtime, is left
    as it is (returns None, and the next placement asks again). The plan is the first that works, preferring one already
    given to the shared machine's computer (`shared`), then to another cloud machine's (`others`), then the lowest known
    used percent, then the most recently added. Only subscription plans count (an API key bills per use, so it is never
    chosen for a computer). Returns the subscription id given, or None when no plan fits."""
    from . import login_holds
    runner = c.execute("SELECT readiness FROM runners_with_state WHERE id=? AND revoked_at IS NULL", (runner_id,)).fetchone()
    if not runner or c.execute("SELECT 1 FROM computer_subscriptions WHERE runner_id=? AND runtime=?",
                               (runner_id, runtime)).fetchone():
        return None
    own = (readiness_document(runner["readiness"]).get("runtimes") or {}).get(runtime) or {}
    if own.get("authenticated") not in (None, "missing") or _live(c, runner_id, runtime):
        return None
    kinds = [kind for kind, spec in KINDS.items() if spec["runtime"] == runtime and spec.get("billing") == "subscription"]
    subs = H._rows(c.execute(f"SELECT * FROM model_subscriptions WHERE kind IN ({','.join('?' * len(kinds))}) "
                             "AND status<>'needs_reconnect' ORDER BY created_at DESC", kinds))
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
        tier = 2 if where is None else where                        # shared machine's, another cloud machine's, the rest
        used = (_usage(c, sub, ctx) or {}).get("used_pct")
        plans.append(((tier, used is None, used or 0), sub))
    if not plans:
        return None
    chosen = min(plans, key=lambda p: p[0])[1]                  # min is first-seen on a tie: the newest added
    _set(c, runner_id, runtime, chosen["id"], actor)
    H.event(c, actor, "subscription.given", chosen["id"], {"runner": runner_id, "runtime": runtime,
                                                           "stopped": 0, "sessions_reset": 0})
    return chosen["id"]


# ------------------------------------------------------------------------------------------------ claim and run
def for_claim(c, runner, config, loads=None):
    """The subscriptions a claimed turn may use, with automatic candidates limited to installed runtimes. `loads`: the
    claim's loaded values (claim_loads), when the caller has them."""
    given = loads.given if loads is not None else _given(c)
    if not runner or not given:
        return []
    from .store import readiness_document
    own = turn_runtimes(config)
    if loads is not None:
        row = loads.runner_of(c, runner["id"])
        record = loads.plain_record(c)
    else:
        from . import providers
        from .read_cache import one
        row = one(c, RUNNER_READINESS_SQL, (runner["id"],))
        record = providers.load(c)
    ready = readiness_document(row["readiness"]) if row else {}
    out = []
    for runtime in turn_runtimes(config, record):
        if runtime not in own and ((ready.get("runtimes") or {}).get(runtime) or {}).get("installed") is not True:
            continue          # a subscription authenticates a CLI; it cannot install one
        sub = in_effect(c, runner["id"], runtime, loads=loads)
        if sub:
            lazy = runtime not in own and ACTIVATION_CAPABILITY in (H._json(runner["capabilities"], []) or [])
            out.append({"runtime": runtime, "subscription": sub, "activate": lazy})
    return out


def hand_out(c, attempt_id, chosen):
    """Record what the claim handed this attempt, and the attempt's metadata for the runner (no values)."""
    out = []
    for item in chosen:
        sub = item["subscription"]
        c.execute("INSERT OR REPLACE INTO attempt_subscriptions(attempt_id,runtime,subscription_id,revision) "
                  "VALUES(?,?,?,?)", (attempt_id, item["runtime"], sub["id"], sub["revision"]))
        if item.get("activate"):
            c.execute("INSERT OR IGNORE INTO pending_subscription_fallbacks(attempt_id,runtime) VALUES(?,?)",
                      (attempt_id, item["runtime"]))
        spec = KINDS[sub["kind"]]
        out.append({**({"activate": True} if item.get("activate") else {}),
                    "id": sub["id"], "runtime": item["runtime"], "kind": sub["kind"], "name": sub["name"],
                    "revision": sub["revision"], "billing": spec["billing"], "source": "computer",
                    **({"env": spec["env"]} if spec.get("env") else {"file": spec["file"]})})
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
    attempt = c.execute("SELECT runner_id,state,bot_id,stop_requested_at FROM attempts WHERE id=?",
                        (who.attempt_id,)).fetchone()
    if not attempt or attempt["state"] not in ("leased", "running") or "bot:" + attempt["bot_id"] != who.actor:
        return []
    out = []
    for given in c.execute("SELECT * FROM attempt_subscriptions WHERE attempt_id=? ORDER BY runtime",
                           (who.attempt_id,)).fetchall():
        pending = c.execute("SELECT 1 FROM pending_subscription_fallbacks WHERE attempt_id=? AND runtime=?",
                            (who.attempt_id, given["runtime"])).fetchone()
        if pending and subscription_runtime != given["runtime"]:
            continue
        sub = row(c, given["subscription_id"])
        if (not sub or sub["status"] == "needs_reconnect"
                or not still_given(c, attempt["runner_id"], given["runtime"], sub["id"])):
            continue
        if pending:
            # Serialized with give/reconnect/remove: once a value leaves, a later change must stop this run.
            effective = in_effect(c, attempt["runner_id"], given["runtime"])
            if attempt["stop_requested_at"] or not effective or effective["id"] != sub["id"]:
                continue
            c.execute("DELETE FROM pending_subscription_fallbacks WHERE attempt_id=? AND runtime=?",
                      (who.attempt_id, given["runtime"]))
        if sub["revision"] != given["revision"]:
            if attempt["stop_requested_at"]:
                continue        # stopped (a reconnect restarts its runs): it ends without the newer value
            c.execute("UPDATE attempt_subscriptions SET revision=? WHERE attempt_id=? AND runtime=?",
                      (sub["revision"], who.attempt_id, given["runtime"]))
        value = vault.cipher.unseal(c, _context(sub["id"]), sub["sealed"])
        spec = KINDS[sub["kind"]]
        out.append({"id": sub["id"], "runtime": given["runtime"], "kind": sub["kind"], "name": sub["name"],
                    "revision": sub["revision"], "billing": spec["billing"], **delivered(sub["kind"], value)})
        H.event(c, who.actor, "subscription.sent_to_computer", sub["id"],
                {"attempt": who.attempt_id, "runner": attempt["runner_id"], "runtime": given["runtime"]})
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
    given = c.execute("SELECT s.runtime,s.revision,a.state,a.lease_until,a.stop_requested_at FROM attempt_subscriptions s "
                      "JOIN attempts a ON a.id=s.attempt_id WHERE s.attempt_id=? AND s.subscription_id=? "
                      "AND a.runner_id=? AND NOT EXISTS (SELECT 1 FROM pending_subscription_fallbacks p "
                      "WHERE p.attempt_id=s.attempt_id AND p.runtime=s.runtime)",
                      (body.attempt, body.subscription, who.runner_id)).fetchone()
    sub = row(c, body.subscription)
    if not given or not sub or sub["kind"] != "codex_login":
        raise Problem("forbidden", "That run was not handed this subscription on this computer", 403)
    if (given["state"] not in ("leased", "running") or (given["lease_until"] or "") <= H.now()
            or given["stop_requested_at"] == H.KEEPER
            or not still_given(c, who.runner_id, given["runtime"], sub["id"])):
        raise Problem("stale_lease", "That run has ended or no longer signs in with this subscription", 409)
    if sub["revision"] != body.revision or given["revision"] != body.revision:
        return {"stored": False}
    value, preview = checked("codex_login", body.value.get_secret_value())
    old = json.loads(vault.cipher.unseal(c, _context(sub["id"]), sub["sealed"]) or "{}")
    account = lambda v: ((v.get("tokens") or {}).get("account_id") or "")
    if account(old) and account(json.loads(value)) != account(old):
        return {"stored": False}
    if value == json.dumps(old, separators=(",", ":")):
        return {"stored": False}
    c.execute("UPDATE model_subscriptions SET sealed=?,preview=?,revision=revision+1,updated_at=? WHERE id=?",
              (vault.cipher.seal(c, _context(sub["id"]), value), preview, H.now(), sub["id"]))
    c.execute("UPDATE attempt_subscriptions SET revision=? WHERE attempt_id=? AND runtime=?",
              (sub["revision"] + 1, body.attempt, given["runtime"]))
    H.event(c, who.actor, "subscription.refreshed", sub["id"], {"attempt": body.attempt})
    return {"stored": True, "revision": sub["revision"] + 1}


def handed(c, attempt_id, runtime):
    return H._one(c, "SELECT * FROM attempt_subscriptions WHERE attempt_id=? AND runtime=?", (attempt_id, runtime)) \
        if attempt_id and H._has_table(c, "attempt_subscriptions") else None


def _status(c, sid, status, detail=""):
    c.execute("UPDATE model_subscriptions SET status=?,status_detail=?,status_at=? WHERE id=? AND status<>?",
              (status, detail[:300], H.now(), sid, status))


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
    H.event(c, H.KEEPER, "subscription.rejected", sub["id"],
            {"attempt": attempt_id, "runtime": runtime, "bot": bot, "task": task_id or ""})
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
    busy = {row["runner_id"] for row in c.execute(
        f"SELECT runner_id FROM model_logins WHERE runtime=? AND {model_login.ACTIVE_SQL} AND expires_at>=?",
        (runtime, H.now()))}
    me = H.actor_id(who.actor) if who else ""
    found = []
    for runner in c.execute("SELECT * FROM runners_with_state WHERE revoked_at IS NULL").fetchall():
        report = readiness_document(runner["readiness"]).get("runtimes") or {}
        if (model_login.connects(runner) and model_login._online(runner)
                and (report.get(runtime) or {}).get("installed")):
            found.append(runner)
    found.sort(key=lambda r: r["last_seen_at"] or "", reverse=True)
    found.sort(key=lambda r: (r["owner_id"] != me, r["id"] in busy))
    return found


def connect_ready(c):
    """The kinds a person can Connect right now: their tool's sign-in has a computer to run on."""
    return [kind for kind, spec in KINDS.items() if spec.get("connect") and _connect_computers(c, spec["runtime"])]


def connect(c, settings, who, body):
    """Start a Connect: queue the sign-in on a computer, remembering what its result becomes. Read back, given a code
    and cancelled like any sign-in (backend/model_login.py). Its row says where (`computer`)."""
    from . import model_login
    name, sid = (body.name or "").strip(), body.subscription or ""
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
    elif not name:
        raise Problem("name", "Enter a name", 422)
    elif c.execute("SELECT count(*) FROM model_subscriptions").fetchone()[0] >= MAX_SUBSCRIPTIONS:
        raise Problem("limit", f"The company has {MAX_SUBSCRIPTIONS} subscriptions; remove one first", 422)
    # A reconnect keeps its owner; a new one is checked now, before anyone signs in.
    owner = "" if sid else _default_owner(c, who, body, kind)

    def answer(login, runner):
        return {**model_login.view(login), "computer": runner["label"],
                "not_yours": runner["owner_id"] != H.actor_id(who.actor)}

    # Asked again (a reloaded page): the sign-in already going picks back up, wherever it runs.
    model_login.sweep(c)
    going = c.execute(f"SELECT l.* FROM model_logins l JOIN subscription_connects s ON s.login_id=l.id "
                      f"WHERE l.kind='connect' AND l.requested_by=? AND s.kind=? AND s.name=? AND s.subscription_id=? "
                      f"AND {model_login.ACTIVE_SQL}", (who.actor, kind, name, sid)).fetchone()
    if going:
        return answer(going, model_login._runner(c, going["runner_id"]))
    computers = _connect_computers(c, spec["runtime"], who)
    if not computers:
        raise Problem("offline", "Connect needs a computer online, on the latest Tico, with "
                      f"{RUNTIME_LABELS[spec['runtime']]} installed. Paste instead, or start one", 409)
    runner = computers[0]
    login, new = model_login._queue(c, who, runner, spec["runtime"], "", "connect", "connect a subscription")
    # The job going there is for something else: its result must not land on this one.
    if not new and login["requested_by"] != who.actor:
        raise Problem("state", f"Someone else is connecting a subscription on {runner['label']}; try again in a minute",
                      409)
    if not new:
        raise Problem("state", f"You are connecting another subscription on {runner['label']}; finish or cancel that "
                      "first", 409)
    c.execute("INSERT INTO subscription_connects(login_id,kind,name,subscription_id,owner_id) VALUES(?,?,?,?,?)",
              (login["id"], kind, name, sid, owner))
    H.event(c, who.actor, "subscription.connect", sid or login["id"], {"kind": kind, "runner": runner["id"]})
    return answer(login, runner)


def connected(c, vault, settings, who, login_id, body):
    """A computer sends what its CLI gave it for a Connect it was asked to run: saved as the person asked, as if they
    had pasted it, and the job is done. A job cancelled or run out by now saves nothing. A value that does not check
    out fails the job, saying why (never the value)."""
    from types import SimpleNamespace

    from . import model_login
    model_login.sweep(c)
    login = c.execute("SELECT * FROM model_logins WHERE id=? AND runner_id=? AND kind='connect'",
                      (login_id, who.runner_id)).fetchone()
    if not login:
        raise Problem("not_found", "That sign-in does not exist", 404)
    if login["state"] not in model_login.ACTIVE:
        raise Problem("state", "That sign-in has ended", 409)
    target = c.execute("SELECT * FROM subscription_connects WHERE login_id=?", (login_id,)).fetchone()
    state, message = "done", ""
    if not target:
        state, message = "failed", "That sign-in has ended"
    else:
        write = SubscriptionWrite(kind=target["kind"], name=target["name"] or None, secret=body.value, confirm=True,
                                  owner_id=(target["owner_id"] or None) if not target["subscription_id"] else None)
        try:
            save(c, vault, settings, SimpleNamespace(actor=login["requested_by"]), write, target["subscription_id"] or None)
        except Problem as problem:
            state, message = "failed", model_login.redact(problem.detail)
    c.execute("UPDATE model_logins SET state=?, message=?, url='', user_code='', pending_code=NULL, lines='[]', "
              "updated_at=? WHERE id=?", (state, message, H.now(), login_id))
    c.execute("DELETE FROM subscription_connects WHERE login_id=?", (login_id,))
    H.event(c, "runner:" + who.runner_id, "runner.login." + state, who.runner_id, {"runtime": login["runtime"]})
    return {"state": state, "message": message}


def install(app, store, auth, mutate, settings):
    from fastapi import Request

    from .models import Empty

    vault = app.state.vault

    @app.get("/api/v2/model-subscriptions")
    def list_subscriptions(request: Request):
        who = request.state.identity
        auth.domain(who)
        _reader(who)
        with store.read() as c:
            return {"subscriptions": listed(c), "kinds": kinds(), "connect": connect_ready(c)}

    @app.post("/api/v2/model-subscriptions")
    def add(request: Request, body: SubscriptionWrite):
        who = request.state.identity
        _admin(auth, who, secret=True)
        return vault.change(who, request.url.path, request.headers.get("idempotency-key"), body,
                            lambda c, w: None, lambda c: save(c, vault, settings, who, body))

    # Declared before `/{sid}`. It carries no value: that comes from the computer, to the route below.
    @app.post("/api/v2/model-subscriptions/connect")
    def start_connect(request: Request, body: SubscriptionConnect):
        who = request.state.identity
        _admin(auth, who, secret=True)
        return mutate(request, body, lambda c: connect(c, settings, who, body))

    @app.post("/api/v2/model-subscriptions/{sid}")
    def change(request: Request, sid: str, body: SubscriptionWrite):
        who = request.state.identity
        _admin(auth, who, secret=body.secret is not None)
        return vault.change(who, request.url.path, request.headers.get("idempotency-key"), body,
                            lambda c, w: None, lambda c: save(c, vault, settings, who, body, sid))

    @app.delete("/api/v2/model-subscriptions/{sid}")
    def delete(request: Request, sid: str, confirm: bool = False):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            _admin(auth, who)
            return remove(c, settings, who, sid, confirm)
        return mutate(request, Empty(), work)

    @app.get("/api/v2/computer-subscriptions")
    def computer_subscriptions(request: Request):
        who = request.state.identity
        auth.domain(who)
        _reader(who)
        with store.read() as c:
            return assignments(c)

    @app.put("/api/v2/computer-subscriptions")
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
            if who.role != "runner" or not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL",
                                                     (who.runner_id,)).fetchone():
                raise Problem("forbidden", "Only a registered computer may send this", 403)
            return connected(c, vault, settings, who, lid, body)

    @app.post("/api/v2/runner-model-subscription-refresh")
    def refresh(request: Request, body: SubscriptionRefresh):
        who = request.state.identity
        with store.transaction() as c:
            if who.role != "runner" or not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL",
                                                     (who.runner_id,)).fetchone():
                raise Problem("forbidden", "Only a registered computer may send this", 403)
            return refreshed(c, vault, who, body)
