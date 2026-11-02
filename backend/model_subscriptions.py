"""Company subscriptions, given to computers (Settings > Models).

A subscription is one of the company's model logins or keys, saved once in Tico rather than on a computer: a Claude
setup-token (`claude setup-token`), a ChatGPT login (Codex's `auth.json` from a `codex login`), or an Anthropic or
OpenAI API key. It belongs to the company; `added_by` only says who saved it. The value is sealed with the credential
vault's data key (CredentialCipher.seal, context `model_subscription:<id>`), never shown again, never in a list, an
event or an idempotency receipt (saves go through Vault.change, which keeps only a keyed fingerprint).

A computer (runner) is given at most one subscription per runtime (`computer_subscriptions`); one subscription may be
given to many computers. A bot does not pick one: it runs on its computer, and a turn signs in, for its runtime and its
named fallback's, with

  1. the computer's subscription for that runtime, unless it needs reconnecting (then it is passed over),
  2. else the login signed in on that computer (as before),
  3. else the Tico AI Gateway when the company's models choice allows it (backend/models_preset.py),
  4. else the turn waits.

Changing a computer's subscription for a runtime (giving, switching, taking it off) or removing a subscription in use
stops that computer's live runs on that runtime and puts their work back in the queue (quarantine_stops.restart), and
every session of that runtime on that computer starts fresh (`_restart`): nothing begun on the old login is resumed on
the new one. The page confirms first (409 `confirm_restart` with the counts). A bot there that was Rate limited on the
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

from pydantic import Field, SecretStr

from . import hubdb as H
from .models import Contract, ID
from .store import Problem, readiness_document

CAPABILITY = "model_subscriptions_v1"
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

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_subscriptions(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, sealed TEXT NOT NULL,
 preview TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'new',
 status_detail TEXT NOT NULL DEFAULT '', status_at TEXT, added_by TEXT NOT NULL, created TEXT NOT NULL,
 updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS computer_subscriptions(
 runner_id TEXT NOT NULL, runtime TEXT NOT NULL, subscription_id TEXT NOT NULL, set_by TEXT NOT NULL,
 updated TEXT NOT NULL, PRIMARY KEY(runner_id, runtime));
CREATE INDEX IF NOT EXISTS computer_subscriptions_sub ON computer_subscriptions(subscription_id);
CREATE TABLE IF NOT EXISTS attempt_subscriptions(
 attempt_id TEXT NOT NULL, runtime TEXT NOT NULL, subscription_id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(attempt_id, runtime));
CREATE INDEX IF NOT EXISTS attempt_subscriptions_sub ON attempt_subscriptions(subscription_id);
CREATE TABLE IF NOT EXISTS subscription_connects(
 login_id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', subscription_id TEXT NOT NULL DEFAULT '');
"""


class SubscriptionWrite(Contract):
    """A new subscription, or a saved one's reconnect (a new `secret`) or rename. `confirm` accepts that a reconnect
    stops and restarts the live runs on every computer given it."""
    name: str | None = Field(default=None, max_length=80)
    kind: str | None = Field(default=None, max_length=40)
    secret: SecretStr | None = Field(default=None, max_length=MAX_VALUE)
    confirm: bool = False


class SubscriptionConnect(Contract):
    """Sign a plan in from the page: a new subscription (`kind`, `name`), or a saved one's new value (`subscription`).
    `confirm` accepts that a reconnect stops and restarts the live runs on every computer given it."""
    kind: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=80)
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
    caps = H._json(runner["capabilities_json"], []) if runner else []
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


def in_effect(c, runner_id, runtime):
    """The subscription a turn on `runtime` on this computer signs in with, or None for the computer's own login (none
    given, the one given needs reconnecting or is held at a usage limit until its reset, or a runner too old to take
    one). A held one is passed over like one that needs reconnecting: the turn runs on the computer's own login, else
    the Gateway under the company's choice, else waits; it never waits on the subscription's hold. After the reset its
    first turn is the hold's probe as usual (backend/login_holds.py)."""
    given = _given(c)
    sub = given.get((runner_id, runtime)) if given else None
    if not sub or sub["status"] == "needs_reconnect" or KINDS.get(sub["kind"], {}).get("runtime") != runtime:
        return None
    from . import login_holds
    if login_holds.holding(c, "", login_holds.subscription_login(sub["id"]), runtime) == "held":
        return None
    from .read_cache import one
    runner = one(c, "SELECT capabilities_json FROM runners WHERE id=? AND revoked_at IS NULL", (runner_id,))
    return sub if runner_takes(runner) else None


def turn_runtimes(config):
    """The subscription runtimes a turn on `config` (runtime and model filled in) may use: its own and its named
    fallback's, for the CLI harness of that runtime only. Not a Tico model (the Gateway signs that in) and not a bot
    delivered through a desktop app."""
    from .gateway_connection import is_tico
    from .harnesses import runtime_of
    config = config if isinstance(config, dict) else {}
    if config.get("app_delivery"):
        return []
    out = [] if is_tico(config.get("model")) else [str(config.get("runtime") or "")]
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    if fallback.get("harness") and not fallback.get("off") and not is_tico(fallback.get("model")):
        out.append(runtime_of(fallback["harness"]))
    return [r for r in dict.fromkeys(out) if r in RUNTIMES]


def covered_runtimes(c, runner, config):
    """{runtime: kind} the computer's subscriptions sign in for a bot on `config` there: a covered runtime needs no
    sign-in on that computer, for the bot's readiness and the claim's check."""
    if not runner or not _given(c):
        return {}
    out = {}
    for runtime in turn_runtimes(config):
        sub = in_effect(c, runner["id"], runtime)
        if sub:
            out[runtime] = sub["kind"]
    return out


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
def _usage(c, sub):
    """What is known of a subscription's use without new telemetry: an API key's cost this month from the runs that
    used it; a plan's limit while its hold stands (backend/login_holds.py). None when nothing is known."""
    from . import login_holds
    spec = KINDS.get(sub["kind"]) or {}
    if spec.get("billing") == "api":
        month = H.now()[:7] + "-01"
        cost = c.execute("SELECT coalesce(sum(t.cost),0) FROM attempt_subscriptions s JOIN turns t ON t.id=s.attempt_id "
                         "JOIN attempts a ON a.id=s.attempt_id WHERE s.subscription_id=? AND t.started>=? "
                         "AND coalesce(a.handed_runtime,s.runtime)=s.runtime", (sub["id"], month)).fetchone()[0]
        return {"window": None, "used_pct": None, "cost_usd_month": round(float(cost or 0), 2)}
    hold = login_holds.get(c, "", login_holds.subscription_login(sub["id"]), spec.get("runtime", ""))
    if hold and (hold.get("until") or "") > H.now() and not hold.get("capacity"):
        kind = login_holds.window(hold.get("error"))
        window = "week" if "weekly" in kind else "5h" if "5-hour" in kind else None
        return {"window": window, "used_pct": 100, "cost_usd_month": None, "resets_at": hold["until"]}
    return None


def view(c, sub):
    """A subscription as Settings > Models lists it: no value, a preview, its status, the computers given it."""
    spec = KINDS.get(sub["kind"]) or {}
    computers = [r[0] for r in c.execute("SELECT runner_id FROM computer_subscriptions WHERE subscription_id=? "
                                         "ORDER BY runner_id, runtime", (sub["id"],))]
    added = H.human(c, H.actor_id(sub["added_by"])) or {}
    return {"id": sub["id"], "name": sub["name"], "kind": sub["kind"], "runtime": spec.get("runtime", ""),
            "label": spec.get("label", sub["kind"]), "status": sub["status"], "status_detail": sub["status_detail"],
            "status_at": sub["status_at"], "preview": sub["preview"], "revision": sub["revision"],
            "computers": list(dict.fromkeys(computers)), "usage": _usage(c, sub), "added_by": sub["added_by"],
            "added_by_name": added.get("name") or added.get("email") or "",
            "created": sub["created"], "updated": sub["updated"]}


def listed(c):
    if not _present(c):
        return []
    return [view(c, s) for s in H._rows(c.execute(
        "SELECT * FROM model_subscriptions ORDER BY lower(name), id LIMIT ?", (MAX_SUBSCRIPTIONS,)))]


def kinds():
    return [{"kind": k, "label": v["label"], "runtime": v["runtime"], "secret_label": v["secret_label"],
             "help": v["help"], "connect": bool(v.get("connect"))} for k, v in KINDS.items()]


def assignments(c):
    """What `GET computer-subscriptions` answers: every computer's subscription per runtime, and the computers that
    can take one (the rest need an update)."""
    given = [{"runner_id": r["runner_id"], "runtime": r["runtime"], "subscription_id": r["subscription_id"]}
             for r in c.execute("SELECT g.* FROM computer_subscriptions g JOIN runners r ON r.id=g.runner_id "
                                "WHERE r.revoked_at IS NULL ORDER BY g.runner_id, g.runtime")] if _present(c) else []
    capable = [r["id"] for r in c.execute("SELECT id,capabilities_json FROM runners WHERE revoked_at IS NULL "
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


# ------------------------------------------------------------------------------------------------ restarts
def _restart_line(c, runner_id, runtime):
    label = (c.execute("SELECT label FROM runners WHERE id=?", (runner_id,)).fetchone() or ["The computer"])[0]
    return f"Stopped: {label}'s {RUNTIME_LABELS.get(runtime, runtime)} sign-in changed"


def _live(c, runner_id, runtime):
    """The live attempts on this computer a change of its `runtime` sign-in stops: those handed that runtime (not a
    Tico model, which the Gateway signs in) or handed a subscription for it (a fallback's)."""
    from .gateway_connection import is_tico
    out = []
    for a in c.execute("SELECT a.* FROM attempts a WHERE a.runner_id=? AND a.state IN ('leased','running') "
                       "AND a.stop_requested IS NULL ORDER BY a.created", (runner_id,)).fetchall():
        own = a["handed_runtime"] == runtime and not is_tico(a["handed_model"])
        if own or handed(c, a["id"], runtime):
            out.append(a)
    return out


def _bots(c, settings, runner_id, runtime):
    """The bots on this computer whose turns run `runtime` (their own or their named fallback's)."""
    from . import providers
    from .shared_bots import follow
    record = providers.load(c, settings)
    out = []
    for r in c.execute("SELECT a.bot,b.config_json FROM assignments a JOIN bot_config b ON b.bot=a.bot "
                       "WHERE a.runner_id=? ORDER BY a.bot", (runner_id,)).fetchall():
        config = providers.fill(record, follow(c, r["bot"], json.loads(r["config_json"] or "{}")))
        if runtime in turn_runtimes(config):
            out.append(r["bot"])
    return out


def _sessions(c, bot):
    """How many sessions app.reset_bot_sessions starts fresh for `bot`: each of its conversations."""
    return sum(1 for (p,) in c.execute("SELECT participants_json FROM conversations WHERE participants_json LIKE ?",
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
        "SELECT g.runner_id,g.runtime,r.capabilities_json FROM computer_subscriptions g "
        "JOIN runners r ON r.id=g.runner_id WHERE g.subscription_id=? ORDER BY g.runner_id, g.runtime",
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
    its usage-limit hold as Try now does, a name renames it. Returns its view."""
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
        given = []
        if body.secret is not None:
            value, preview = checked(sub["kind"], body.secret.get_secret_value())
            given = _signed_in_with(c, sid)
            if not body.confirm:
                _confirm_reconnect(c, settings, given)
        if name is not None:
            c.execute("UPDATE model_subscriptions SET name=?,updated=? WHERE id=?", (name, now, sid))
        if body.secret is not None:
            c.execute("UPDATE model_subscriptions SET sealed=?,preview=?,revision=revision+1,status='new',"
                      "status_detail='',status_at=?,updated=? WHERE id=?",
                      (vault.cipher.seal(c, _context(sid), value), preview, now, now, sid))
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
    sid = H.new_id()
    c.execute("INSERT INTO model_subscriptions(id,name,kind,sealed,preview,added_by,created,updated) "
              "VALUES(?,?,?,?,?,?,?,?)",
              (sid, name, body.kind, vault.cipher.seal(c, _context(sid), value), preview, who.actor, now, now))
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
    runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (body.runner_id,)).fetchone()
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
    c.execute("INSERT INTO computer_subscriptions(runner_id,runtime,subscription_id,set_by,updated) "
              "VALUES(?,?,?,?,?) ON CONFLICT(runner_id,runtime) DO UPDATE SET "
              "subscription_id=excluded.subscription_id,set_by=excluded.set_by,updated=excluded.updated",
              (runner_id, runtime, subscription_id, actor, H.now()))


def bot_runtimes(c, settings, bot):
    """The subscription runtimes a bot may run on its computer: its own turns' (as `_bots` reads them) and its task
    model's, which a task runs on when it is set (backend/task_models.py `apply`, the config a task turn runs with).
    Each goes through `turn_runtimes`, so a Tico model and a desktop-app bot count for none."""
    from . import providers, task_models
    from .shared_bots import follow
    found = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    config = json.loads(found["config_json"] or "{}") if found else {}
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
    runner = c.execute("SELECT readiness_json FROM runners WHERE id=? AND revoked_at IS NULL", (runner_id,)).fetchone()
    if not runner or c.execute("SELECT 1 FROM computer_subscriptions WHERE runner_id=? AND runtime=?",
                               (runner_id, runtime)).fetchone():
        return None
    own = (readiness_document(runner["readiness_json"]).get("runtimes") or {}).get(runtime) or {}
    if own.get("authenticated") not in (None, "missing") or _live(c, runner_id, runtime):
        return None
    kinds = [kind for kind, spec in KINDS.items() if spec["runtime"] == runtime and spec.get("billing") == "subscription"]
    subs = H._rows(c.execute(f"SELECT * FROM model_subscriptions WHERE kind IN ({','.join('?' * len(kinds))}) "
                             "AND status<>'needs_reconnect' ORDER BY created DESC", kinds))
    cloud = {r: 0 if r == shared else 1 for r in [shared, *others] if r}
    on_cloud = {}
    for r in c.execute("SELECT runner_id,subscription_id FROM computer_subscriptions WHERE runtime=?", (runtime,)):
        if r["runner_id"] in cloud:
            on_cloud[r["subscription_id"]] = min(cloud[r["runner_id"]], on_cloud.get(r["subscription_id"], 1))
    plans = []
    for sub in subs:
        if login_holds.holding(c, "", login_holds.subscription_login(sub["id"]), runtime) == "held":
            continue
        where = on_cloud.get(sub["id"])
        tier = 2 if where is None else where                        # shared machine's, another cloud machine's, the rest
        used = (_usage(c, sub) or {}).get("used_pct")
        plans.append(((tier, used is None, used or 0), sub))
    if not plans:
        return None
    chosen = min(plans, key=lambda p: p[0])[1]                  # min is first-seen on a tie: the newest added
    _set(c, runner_id, runtime, chosen["id"], actor)
    H.event(c, actor, "subscription.given", chosen["id"], {"runner": runner_id, "runtime": runtime,
                                                           "stopped": 0, "sessions_reset": 0})
    return chosen["id"]


# ------------------------------------------------------------------------------------------------ claim and run
def for_claim(c, runner, config):
    """The subscriptions a claimed turn on this computer runs on: [{runtime, subscription}]."""
    if not runner or not _given(c):
        return []
    out = []
    for runtime in turn_runtimes(config):
        sub = in_effect(c, runner["id"], runtime)
        if sub:
            out.append({"runtime": runtime, "subscription": sub})
    return out


def hand_out(c, attempt_id, chosen):
    """Record what the claim handed this attempt, and the attempt's metadata for the runner (no values)."""
    out = []
    for item in chosen:
        sub = item["subscription"]
        c.execute("INSERT OR REPLACE INTO attempt_subscriptions(attempt_id,runtime,subscription_id,revision) "
                  "VALUES(?,?,?,?)", (attempt_id, item["runtime"], sub["id"], sub["revision"]))
        spec = KINDS[sub["kind"]]
        out.append({"id": sub["id"], "runtime": item["runtime"], "kind": sub["kind"], "name": sub["name"],
                    "revision": sub["revision"], "billing": spec["billing"], "source": "computer",
                    **({"env": spec["env"]} if spec.get("env") else {"file": spec["file"]})})
    return out


def run_values(c, vault, who):
    """What `GET credential-runtime` adds for the bot run `who`: each subscription its claim handed it, with its value.
    Only the run's own attempt, only while it is live, only while its computer is still given that subscription, and
    only the revision it was handed. A reconnect or a take-off stops every live run given it (`_restart`): such a run
    gets nothing and ends. A run still going whose subscription moved on was moved by a refresh (`refreshed`, the same
    account): it gets the refreshed value and its record moves with it, so its home, its write-back and its verdict
    agree."""
    if not who.attempt_id or not H._has_table(c, "attempt_subscriptions"):
        return []
    attempt = c.execute("SELECT runner_id,state,bot,stop_requested FROM attempts WHERE id=?",
                        (who.attempt_id,)).fetchone()
    if not attempt or attempt["state"] not in ("leased", "running") or "bot:" + attempt["bot"] != who.actor:
        return []
    out = []
    for given in c.execute("SELECT * FROM attempt_subscriptions WHERE attempt_id=? ORDER BY runtime",
                           (who.attempt_id,)).fetchall():
        sub = row(c, given["subscription_id"])
        if (not sub or sub["status"] == "needs_reconnect"
                or not still_given(c, attempt["runner_id"], given["runtime"], sub["id"])):
            continue
        if sub["revision"] != given["revision"]:
            if attempt["stop_requested"]:
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
    given = c.execute("SELECT s.runtime,s.revision,a.state,a.lease_until,a.stop_requested FROM attempt_subscriptions s "
                      "JOIN attempts a ON a.id=s.attempt_id WHERE s.attempt_id=? AND s.subscription_id=? "
                      "AND a.runner_id=?", (body.attempt, body.subscription, who.runner_id)).fetchone()
    sub = row(c, body.subscription)
    if not given or not sub or sub["kind"] != "codex_login":
        raise Problem("forbidden", "That run was not handed this subscription on this computer", 403)
    if (given["state"] not in ("leased", "running") or (given["lease_until"] or "") <= H.now()
            or given["stop_requested"] == H.KEEPER
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
    c.execute("UPDATE model_subscriptions SET sealed=?,preview=?,revision=revision+1,updated=? WHERE id=?",
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
    for runner in c.execute("SELECT * FROM runners WHERE revoked_at IS NULL").fetchall():
        report = readiness_document(runner["readiness_json"]).get("runtimes") or {}
        if (model_login.connects(runner) and model_login._online(runner)
                and (report.get(runtime) or {}).get("installed")):
            found.append(runner)
    found.sort(key=lambda r: r["last_seen"] or "", reverse=True)
    found.sort(key=lambda r: (r["operator"] != me, r["id"] in busy))
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
    def answer(login, runner):
        return {**model_login.view(login), "computer": runner["label"],
                "not_yours": runner["operator"] != H.actor_id(who.actor)}

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
    c.execute("INSERT INTO subscription_connects(login_id,kind,name,subscription_id) VALUES(?,?,?,?)",
              (login["id"], kind, name, sid))
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
        write = SubscriptionWrite(kind=target["kind"], name=target["name"] or None, secret=body.value, confirm=True)
        try:
            save(c, vault, settings, SimpleNamespace(actor=login["requested_by"]), write, target["subscription_id"] or None)
        except Problem as problem:
            state, message = "failed", model_login.redact(problem.detail)
    c.execute("UPDATE model_logins SET state=?, message=?, url='', user_code='', pending_code=NULL, lines_json='[]', "
              "updated=? WHERE id=?", (state, message, H.now(), login_id))
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
