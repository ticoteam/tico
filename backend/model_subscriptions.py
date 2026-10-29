"""Model subscriptions a person saves once in Tico, and which bots run on them (Settings > Subscriptions).

A subscription is one person's model login or key, kept here rather than on a computer: a Claude setup-token
(`claude setup-token`), a ChatGPT login (Codex's `auth.json` from the person's own `codex login`), or an Anthropic or
OpenAI API key. The value is sealed with the credential vault's data key (CredentialCipher.seal, context
`model_subscription:<id>`), never shown again, never in a list, an event or an idempotency receipt (saves go through
Vault.change, which keeps only a keyed fingerprint).

Who runs on one is a pick per runtime at one of three levels, resolved in this order for each turn:

  task  -> the task's own pick (an override for that task's turns only)
  bot   -> the bot's pick
  team  -> the pick of the bot's team, else of the team above it, up the nested teams
  (none)-> what ran before: the computer's own login for that tool, else the Tico AI Gateway under the company's
           models choice (backend/models_preset.py)

A pick whose subscription needs reconnecting, or whose owner has left or is signed off, is passed over and the next
level decides, so a broken subscription falls back instead of failing every turn.

Permissions: a person attaches only their own subscriptions, and only where they could change the bot's model: a bot
they manage (Auth.bot_manager), a task whose bot they manage, or a team they lead (an owner or admin, any team). A pick
is taken off by anyone who could set it, and always by the subscription's owner. A bot never saves or picks one.

Delivery: the claim resolves the turn's subscriptions for its runtime and its named fallback's, records them per
attempt (`attempt_subscriptions`) and names them in the attempt (`subscriptions`, ids and kinds only). The run then
reads their values with its own run token from `GET credential-runtime`, next to its granted credentials, so they reach
whichever computer runs the turn, a fresh cloud machine included, and never the bot's repository. An environment
subscription (a token or Anthropic key) is set in the turn's environment for that CLI; a home subscription (a ChatGPT
login or OpenAI key for Codex) becomes `auth.json` in a home of its own under the runner's state, kept per subscription
on that computer so threads resume (runner-rs/src/person_subscriptions.rs). Codex refreshes a ChatGPT login in that
file; the runner sends the refreshed file back (`POST runner-subscription-refresh`) so the next computer starts from it.

Only a runner that says it can (`CAPABILITY`) is handed subscriptions; an older one runs as before, and the pickers say
the computer needs an update. One computer is one trust group: every bot on a computer runs as the same OS user there
(SECURITY.md), so a subscription delivered to one bot's turn can be read by another bot's turn on that computer while it
runs. A person who does not want that gives the bot a computer of its own (a cloud machine is one per bot by default).

A run the provider refuses on a subscription marks it `needs_reconnect` and leaves the computer's own login alone: its
picks are passed over until its owner saves a new value (Reconnect), and a run on it that completes marks it working.
"""
import base64
import json

from pydantic import Field, SecretStr

from . import hubdb as H
from .models import Contract, ID
from .store import Problem

CAPABILITY = "person_subscriptions_v1"
SCOPES = ("task", "bot", "team")
STATUSES = ("new", "working", "needs_reconnect")
MAX_PER_PERSON = 50
MAX_VALUE = 64 * 1024

# What each kind is, the runtime it signs in, and how a turn receives it: `env` in the turn's environment, or `file` in
# the CLI's own home (`home_var`, set by the runner). `billing` is what the run records (backend/usage.py).
KINDS = {
    "claude_token": {"runtime": "claude", "label": "Claude", "env": "CLAUDE_CODE_OAUTH_TOKEN", "billing": "subscription"},
    "anthropic_key": {"runtime": "claude", "label": "Anthropic API key", "env": "ANTHROPIC_API_KEY", "billing": "api"},
    "codex_login": {"runtime": "codex", "label": "ChatGPT", "file": "auth.json", "billing": "subscription"},
    "openai_key": {"runtime": "codex", "label": "OpenAI API key", "file": "auth.json", "billing": "api"},
}
RUNTIMES = tuple(dict.fromkeys(k["runtime"] for k in KINDS.values()))
RUNTIME_LABELS = {"claude": "Claude Code", "codex": "Codex"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_subscriptions(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, sealed TEXT NOT NULL,
 preview TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'new',
 status_detail TEXT NOT NULL DEFAULT '', status_at TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS model_subscriptions_owner ON model_subscriptions(owner);
CREATE TABLE IF NOT EXISTS model_subscription_picks(
 scope TEXT NOT NULL, scope_id TEXT NOT NULL, runtime TEXT NOT NULL, subscription_id TEXT NOT NULL,
 set_by TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(scope, scope_id, runtime));
CREATE INDEX IF NOT EXISTS model_subscription_picks_sub ON model_subscription_picks(subscription_id);
CREATE TABLE IF NOT EXISTS attempt_subscriptions(
 attempt_id TEXT NOT NULL, runtime TEXT NOT NULL, subscription_id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(attempt_id, runtime));
"""


class SubscriptionWrite(Contract):
    """A new subscription, or a saved one's reconnect (a new `secret`) or rename."""
    name: str | None = Field(default=None, max_length=80)
    kind: str | None = Field(default=None, max_length=40)
    secret: SecretStr | None = Field(default=None, max_length=MAX_VALUE)


class SubscriptionPick(Contract):
    """Use a subscription for one runtime at one level; a null `subscription` takes the pick off."""
    scope: str = Field(pattern="^(task|bot|team)$")
    id: ID
    runtime: str = Field(pattern="^(claude|codex)$")
    subscription: ID | None = None


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
    """The email in a ChatGPT login's id_token, for the list's preview only. Not verified: it is the person's own file."""
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
        raise Problem("kind", "Choose Claude, ChatGPT, Anthropic API key or OpenAI API key", 422)
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


def _owner_ok(c, owner):
    """A subscription runs only while its owner is still on the roster and signed in (auth.standing)."""
    from .auth import standing
    return bool(H.human(c, owner)) and not standing(c, H.actor_id(owner))


def team_chain(c, team):
    """`team` and every team above it, nearest first (groups nest by `parent`)."""
    from .groups import _roster
    groups = _roster(c).get("org_groups") or {}
    out, at = [], str(team or "")
    while at and at in groups and at not in out:
        out.append(at)
        at = str(groups[at].get("parent") or "")
    return out


def bot_team(c, bot):
    found = c.execute("SELECT team FROM bot_config WHERE bot=?", (bot,)).fetchone()
    return (found[0] if found else "") or ""


def _levels(c, bot, task_id=None, team=None):
    """The (scope, id) pairs a turn's subscription is looked up at, in order."""
    levels = [("task", task_id)] if task_id else []
    if bot:
        levels.append(("bot", bot))
    levels += [("team", gid) for gid in team_chain(c, bot_team(c, bot) if team is None else team)]
    return levels


def _any_picks(c):
    """Whether any pick exists: the claim asks for every bot on every poll, and most installs have none."""
    from . import read_cache
    values = read_cache.cache(c)
    if values is not None and "model_subscriptions.any" in values:
        return values["model_subscriptions.any"]
    found = _present(c) and c.execute("SELECT 1 FROM model_subscription_picks LIMIT 1").fetchone() is not None
    if values is not None:
        values["model_subscriptions.any"] = found
    return found


def resolve(c, bot, runtime, task_id=None, team=None):
    """The subscription in effect for `runtime`: {subscription, scope, scope_id, passed} or None for the default.
    `passed` lists the picks passed over (needs reconnecting, owner gone), nearest first. `team` asks for a team alone
    (no bot)."""
    if not _any_picks(c):
        return None
    passed = []
    for scope, scope_id in _levels(c, bot, task_id, team):
        pick = H._one(c, "SELECT * FROM model_subscription_picks WHERE scope=? AND scope_id=? AND runtime=?",
                      (scope, scope_id, runtime))
        if not pick:
            continue
        sub = row(c, pick["subscription_id"])
        if not sub or KINDS.get(sub["kind"], {}).get("runtime") != runtime:
            continue
        why = ("needs_reconnect" if sub["status"] == "needs_reconnect"
               else "owner_gone" if not _owner_ok(c, sub["owner"]) else "")
        if why:
            passed.append({"scope": scope, "scope_id": scope_id, "subscription": brief(sub), "why": why})
            continue
        return {"subscription": sub, "scope": scope, "scope_id": scope_id, "passed": passed}
    return {"subscription": None, "scope": None, "scope_id": None, "passed": passed} if passed else None


def runner_takes(runner):
    caps = H._json(runner["capabilities_json"], []) if runner else []
    return CAPABILITY in (caps or [])


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


def bot_runtimes(c, runner, bot, config):
    """{runtime: kind} the bot's own and team picks cover on `runner` (no task), for its readiness and the claim's
    bot-wide check: a covered runtime needs no sign-in on that computer."""
    if not runner_takes(runner) or not _any_picks(c):
        return {}
    out = {}
    for runtime in turn_runtimes(config):
        found = resolve(c, bot, runtime)
        if found and found["subscription"]:
            out[runtime] = found["subscription"]["kind"]
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
def brief(sub):
    spec = KINDS.get(sub["kind"]) or {}
    return {"id": sub["id"], "name": sub["name"], "kind": sub["kind"], "label": spec.get("label", sub["kind"]),
            "runtime": spec.get("runtime", ""), "status": sub["status"], "owner": sub["owner"]}


def _scope_name(c, scope, scope_id):
    if scope == "bot":
        return (H.bot(c, scope_id) or {}).get("display_name") or scope_id
    if scope == "team":
        from .groups import _roster
        return ((_roster(c).get("org_groups") or {}).get(scope_id) or {}).get("name") or scope_id
    return (H._one(c, "SELECT title FROM tasks WHERE id=?", (scope_id,)) or {}).get("title") or scope_id


def view(c, sub):
    """A subscription as its owner's list shows it: no value, a preview, its status and where it is picked."""
    used = [{"scope": p["scope"], "id": p["scope_id"], "runtime": p["runtime"], "name": _scope_name(c, p["scope"], p["scope_id"])}
            for p in H._rows(c.execute("SELECT * FROM model_subscription_picks WHERE subscription_id=? "
                                       "ORDER BY scope, scope_id", (sub["id"],)))]
    return {**brief(sub), "preview": sub["preview"], "detail": sub["status_detail"], "status_at": sub["status_at"],
            "revision": sub["revision"], "created": sub["created"], "updated": sub["updated"], "used_by": used[:100]}


def mine(c, who):
    if not _present(c):
        return []
    return [view(c, s) for s in H._rows(c.execute(
        "SELECT * FROM model_subscriptions WHERE owner=? ORDER BY lower(name), id LIMIT ?", (who.actor, MAX_PER_PERSON)))]


def kinds():
    return [{"kind": k, "label": v["label"], "runtime": v["runtime"]} for k, v in KINDS.items()]


# ------------------------------------------------------------------------------------------------ permissions
def _person(who, secret=False):
    """A person, in their own session or with their own API token. A secret is pasted by the person themselves: never
    through the Assistant or BotOps acting for them, a bot run, or a Tico support session."""
    if who.role not in ("human", "owner") or not str(who.actor).startswith("human:"):
        raise Problem("forbidden", "Only a person saves or picks a subscription", 403)
    if secret and (who.via or who.attempt_id or who.support):
        raise Problem("forbidden", "Save a subscription yourself in Settings", 403)


def may_pick(c, auth, who, scope, scope_id):
    """Whether `who` may set or take off a pick at this level: a bot they manage, a task whose bot they manage (and can
    read), a team they lead or one below it; an owner or admin, any team."""
    if who.role not in ("human", "owner"):
        return False
    if scope == "bot":
        return bool(H.bot(c, scope_id)) and bool(auth.bot_manager(c, who, scope_id))
    if scope == "task":
        task = H.task(c, scope_id)
        return bool(task and H.is_bot(task["owner"]) and H.task_private_readable(c, who.actor, task)
                    and auth.bot_manager(c, who, H.actor_id(task["owner"])))
    if scope == "team":
        from .groups import _roster, led_groups
        if scope_id not in (_roster(c).get("org_groups") or {}):
            return False
        return who.role == "owner" or auth.bot_admin(who) or scope_id in led_groups(c, who.actor)
    return False


def _exists(c, scope, scope_id):
    if scope == "bot":
        return bool(H.bot(c, scope_id))
    if scope == "task":
        return bool(H.task(c, scope_id))
    from .groups import _roster
    return scope_id in (_roster(c).get("org_groups") or {})


# ------------------------------------------------------------------------------------------------ writes
def save(c, vault, who, body, sid=None):
    """Add a subscription, or change a saved one of `who`'s: a new secret reconnects it (status `new`, so its picks
    apply again), a name renames it. Returns its view."""
    _person(who, secret=True)
    now = H.now()
    if sid:
        sub = row(c, sid)
        if not sub or sub["owner"] != who.actor:
            raise Problem("not_found", "Subscription not found", 404)
        if body.kind and body.kind != sub["kind"]:
            raise Problem("kind", "A subscription keeps its kind; add a new one instead", 422)
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise Problem("name", "Enter a name", 422)
            c.execute("UPDATE model_subscriptions SET name=?,updated=? WHERE id=?", (name, now, sid))
        if body.secret is not None:
            value, preview = checked(sub["kind"], body.secret.get_secret_value())
            c.execute("UPDATE model_subscriptions SET sealed=?,preview=?,revision=revision+1,status='new',"
                      "status_detail='',status_at=?,updated=? WHERE id=?",
                      (vault.cipher.seal(c, _context(sid), value), preview, now, now, sid))
        H.event(c, who.actor, "subscription.updated", sid, {"secret_changed": body.secret is not None})
        return view(c, row(c, sid))
    if not body.kind or body.secret is None:
        raise Problem("secret", "Choose a kind and paste the token, key or login", 422)
    count = c.execute("SELECT count(*) FROM model_subscriptions WHERE owner=?", (who.actor,)).fetchone()[0]
    if count >= MAX_PER_PERSON:
        raise Problem("limit", f"You have {MAX_PER_PERSON} subscriptions; remove one first", 422)
    value, preview = checked(body.kind, body.secret.get_secret_value())
    sid = H.new_id()
    name = (body.name or "").strip() or KINDS[body.kind]["label"]
    c.execute("INSERT INTO model_subscriptions(id,owner,name,kind,sealed,preview,created,updated) VALUES(?,?,?,?,?,?,?,?)",
              (sid, who.actor, name, body.kind, vault.cipher.seal(c, _context(sid), value), preview, now, now))
    H.event(c, who.actor, "subscription.created", sid, {"kind": body.kind})
    return view(c, row(c, sid))


def remove(c, who, sid):
    """Delete one of `who`'s subscriptions and every pick of it: those bots, teams and tasks fall back at once."""
    _person(who, secret=True)
    sub = row(c, sid)
    if not sub or sub["owner"] != who.actor:
        raise Problem("not_found", "Subscription not found", 404)
    picks = c.execute("SELECT count(*) FROM model_subscription_picks WHERE subscription_id=?", (sid,)).fetchone()[0]
    c.execute("DELETE FROM model_subscription_picks WHERE subscription_id=?", (sid,))
    c.execute("DELETE FROM model_subscriptions WHERE id=?", (sid,))
    H.event(c, who.actor, "subscription.deleted", sid, {"kind": sub["kind"], "picks": picks})
    return {"deleted": True, "id": sid}


def pick(c, auth, who, body):
    """Set or take off the pick for one runtime at one level, returning that level's view (`picks_view`)."""
    _person(who)
    scope, scope_id, runtime = body.scope, body.id, body.runtime
    if not _exists(c, scope, scope_id):
        raise Problem("not_found", "Not found", 404)
    current = H._one(c, "SELECT * FROM model_subscription_picks WHERE scope=? AND scope_id=? AND runtime=?",
                     (scope, scope_id, runtime))
    allowed = may_pick(c, auth, who, scope, scope_id)
    if body.subscription is None:
        held = current and (row(c, current["subscription_id"]) or {}).get("owner") == who.actor
        if current and not (allowed or held):
            raise Problem("forbidden", _refusal(scope), 403)
        if current:
            c.execute("DELETE FROM model_subscription_picks WHERE scope=? AND scope_id=? AND runtime=?",
                      (scope, scope_id, runtime))
            H.event(c, who.actor, "subscription.unpicked", current["subscription_id"],
                    {"scope": scope, "id": scope_id, "runtime": runtime})
        return picks_view(c, auth, who, scope, scope_id)
    if not allowed:
        raise Problem("forbidden", _refusal(scope), 403)
    sub = row(c, body.subscription)
    if not sub or sub["owner"] != who.actor:
        raise Problem("forbidden", "You can use only your own subscriptions", 403)
    if KINDS[sub["kind"]]["runtime"] != runtime:
        raise Problem("runtime", f"{sub['name']} signs in {RUNTIME_LABELS.get(KINDS[sub['kind']]['runtime'])}, "
                      f"not {RUNTIME_LABELS.get(runtime, runtime)}", 422)
    c.execute("INSERT INTO model_subscription_picks(scope,scope_id,runtime,subscription_id,set_by,updated) "
              "VALUES(?,?,?,?,?,?) ON CONFLICT(scope,scope_id,runtime) DO UPDATE SET "
              "subscription_id=excluded.subscription_id,set_by=excluded.set_by,updated=excluded.updated",
              (scope, scope_id, runtime, sub["id"], who.actor, H.now()))
    H.event(c, who.actor, "subscription.picked", sub["id"], {"scope": scope, "id": scope_id, "runtime": runtime})
    return picks_view(c, auth, who, scope, scope_id)


def _refusal(scope):
    return {"bot": "Only someone who manages this bot picks its subscription",
            "task": "Only someone who manages this task's bot picks its subscription",
            "team": "Only the team's lead, an owner or an admin picks its subscription"}[scope]


# ------------------------------------------------------------------------------------------------ pickers
def _default(c, settings, bot):
    """What a bot's runtime runs on with no subscription in effect: its computer's login when signed in, else the
    Gateway when the company's choice spends it, else nothing yet."""
    from . import models_preset, providers
    from .store import readiness_document
    placed = c.execute("SELECT r.* FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=? "
                       "AND r.revoked_at IS NULL", (bot,)).fetchone() if bot else None
    ready = readiness_document(placed["readiness_json"]) if placed else {}
    record = providers.load(c, settings) if settings is not None else {}
    gateway = models_preset.spends_gateway(record) and models_preset.gateway_on(record)

    def default(runtime):
        if placed and models_preset.signed_in(ready, runtime):
            return {"source": "computer", "computer": placed["label"]}
        return {"source": "gateway"} if gateway else {"source": "none"}
    return placed, default


def picks_view(c, auth, who, scope, scope_id, settings=None):
    """One level's pickers: per runtime its own pick, what is in effect for its turns and why, and the caller's own
    subscriptions that fit. A task and a bot show their bot's runtimes; a team shows every subscription runtime."""
    from . import providers
    from .shared_bots import follow
    task = H.task(c, scope_id) if scope == "task" else None
    bot = (H.actor_id(task["owner"]) if task and H.is_bot(task["owner"]) else None) if scope == "task" \
        else scope_id if scope == "bot" else None
    if bot:
        found = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
        record = providers.load(c, settings) if settings is not None else {}
        config = providers.fill(record, follow(c, bot, json.loads(found[0] or "{}") if found else {}))
        runtimes = turn_runtimes(config) or []
    else:
        runtimes = list(RUNTIMES)
    placed, default = _default(c, settings, bot)
    note = "" if not placed or runner_takes(placed) else f"Update {placed['label']} to use subscriptions"
    options = [brief(s) for s in H._rows(c.execute(
        "SELECT * FROM model_subscriptions WHERE owner=? ORDER BY lower(name), id", (who.actor,)))] if _present(c) else []
    rows = []
    for runtime in runtimes:
        own = H._one(c, "SELECT * FROM model_subscription_picks WHERE scope=? AND scope_id=? AND runtime=?",
                     (scope, scope_id, runtime)) if _present(c) else None
        own_sub = row(c, own["subscription_id"]) if own else None
        found = resolve(c, bot, runtime, task_id=scope_id if scope == "task" else None,
                        team=scope_id if scope == "team" else None)
        if found and found["subscription"]:
            sub = found["subscription"]
            owner = H.human(c, sub["owner"]) or {}
            effect = {"source": found["scope"], "scope_id": found["scope_id"],
                      "name": _scope_name(c, found["scope"], found["scope_id"]),
                      "subscription": {**brief(sub), "owner_name": owner.get("name") or owner.get("email") or ""}}
        else:
            effect = default(runtime) if bot else {"source": "none"}
        if note and effect.get("subscription"):
            effect["note"] = note
        effect["passed"] = (found or {}).get("passed") or []
        rows.append({"runtime": runtime, "label": RUNTIME_LABELS.get(runtime, runtime),
                     "pick": ({**brief(own_sub), "set_by": own["set_by"],
                               "mine": own_sub["owner"] == who.actor} if own_sub else None),
                     "effect": effect, "options": [o for o in options if o["runtime"] == runtime]})
    return {"scope": scope, "id": scope_id, "bot": bot, "can_pick": may_pick(c, auth, who, scope, scope_id),
            "runtimes": rows}


# ------------------------------------------------------------------------------------------------ claim and run
def for_claim(c, runner, bot, task_id, config):
    """The subscriptions a claimed turn runs on, [{runtime, subscription row}], for a runner that takes them."""
    if not runner_takes(runner) or not _any_picks(c):
        return []
    out = []
    for runtime in turn_runtimes(config):
        found = resolve(c, bot, runtime, task_id=task_id)
        if found and found["subscription"]:
            out.append({"runtime": runtime, "subscription": found["subscription"], "scope": found["scope"]})
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
                    "revision": sub["revision"], "billing": spec["billing"], "source": item["scope"],
                    **({"env": spec["env"]} if spec.get("env") else {"file": spec["file"]})})
    return out


def run_values(c, vault, who):
    """What `GET credential-runtime` adds for the bot run `who`: each subscription its claim handed it, with its value.
    Only the run's own attempt, only while it is live, and only the revision it was handed (a newer one waits for the
    next turn, so the runner's home and its write-back agree)."""
    if not who.attempt_id or not H._has_table(c, "attempt_subscriptions"):
        return []
    attempt = c.execute("SELECT runner_id,state,bot FROM attempts WHERE id=?", (who.attempt_id,)).fetchone()
    if not attempt or attempt["state"] not in ("leased", "running") or "bot:" + attempt["bot"] != who.actor:
        return []
    out = []
    for handed in c.execute("SELECT * FROM attempt_subscriptions WHERE attempt_id=? ORDER BY runtime", (who.attempt_id,)):
        sub = row(c, handed["subscription_id"])
        if not sub or sub["status"] == "needs_reconnect" or not _owner_ok(c, sub["owner"]):
            continue
        value = vault.cipher.unseal(c, _context(sub["id"]), sub["sealed"])
        spec = KINDS[sub["kind"]]
        out.append({"id": sub["id"], "runtime": handed["runtime"], "kind": sub["kind"], "name": sub["name"],
                    "revision": sub["revision"], "billing": spec["billing"], **delivered(sub["kind"], value)})
        H.event(c, who.actor, "subscription.sent_to_computer", sub["id"],
                {"attempt": who.attempt_id, "runner": attempt["runner_id"], "runtime": handed["runtime"]})
    return out


def refreshed(c, vault, who, body):
    """A runner sends back a ChatGPT login its CLI refreshed in a turn it ran on it. Kept only when the attempt was
    handed that subscription on this computer, the subscription is still at the revision the turn had, and the file is
    the same account; anything else is ignored (another computer refreshed first, or the person reconnected)."""
    if who.role != "runner":
        raise Problem("forbidden", "Only a registered computer may send this", 403)
    handed = c.execute("SELECT s.revision FROM attempt_subscriptions s JOIN attempts a ON a.id=s.attempt_id "
                       "WHERE s.attempt_id=? AND s.subscription_id=? AND a.runner_id=?",
                       (body.attempt, body.subscription, who.runner_id)).fetchone()
    sub = row(c, body.subscription)
    if not handed or not sub or sub["kind"] != "codex_login":
        raise Problem("forbidden", "That run was not handed this subscription on this computer", 403)
    if sub["revision"] != body.revision or handed["revision"] != body.revision:
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
    H.event(c, who.actor, "subscription.refreshed", sub["id"], {"attempt": body.attempt})
    return {"stored": True, "revision": sub["revision"] + 1}


def handed(c, attempt_id, runtime):
    return H._one(c, "SELECT * FROM attempt_subscriptions WHERE attempt_id=? AND runtime=?", (attempt_id, runtime)) \
        if H._has_table(c, "attempt_subscriptions") else None


def _status(c, sid, status, detail=""):
    c.execute("UPDATE model_subscriptions SET status=?,status_detail=?,status_at=? WHERE id=? AND status<>?",
              (status, detail[:300], H.now(), sid, status))


def turn_rejected(c, settings, attempt_id, bot, runtime, reason, task_id):
    """The provider refused the turn's sign-in for `runtime`. When the turn ran on a subscription for it, that
    subscription needs reconnecting and its picks are passed over from the next turn on (the next level, the
    computer's login or the Gateway takes the requeued work); the computer's own login is not touched and no card
    holds the task, since something else may run it. Its owner sees it in Settings > Subscriptions and the bot's
    status says so. Returns the subscription row, or None when the turn was on the computer's login."""
    found = handed(c, attempt_id, runtime)
    sub = row(c, found["subscription_id"]) if found else None
    if not sub:
        return None
    _status(c, sub["id"], "needs_reconnect", reason or "The provider refused it")
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


def assignment_fields(c, runner, bot, config):
    """What `runners/assignments` adds to a bot's entry: {"subscriptions": {runtime: kind}} when its bot or team pick
    covers a runtime, so the runner does not count that runtime's missing sign-in against the bot."""
    covered_runtimes = bot_runtimes(c, runner, bot, config)
    return {"subscriptions": covered_runtimes} if covered_runtimes else {}


# ------------------------------------------------------------------------------------------------ routes
def install(app, store, auth, mutate, settings):
    from fastapi import Request

    vault = app.state.vault

    @app.get("/api/v2/me/subscriptions")
    def list_mine(request: Request):
        who = request.state.identity
        auth.domain(who)
        _person(who)
        with store.read() as c:
            return {"subscriptions": mine(c, who), "kinds": kinds()}

    @app.post("/api/v2/me/subscriptions")
    def add(request: Request, body: SubscriptionWrite):
        who = request.state.identity
        _person(who, secret=True)
        return vault.change(who, request.url.path, request.headers.get("idempotency-key"), body,
                            lambda c, w: None, lambda c: save(c, vault, who, body))

    @app.post("/api/v2/me/subscriptions/{sid}")
    def change(request: Request, sid: str, body: SubscriptionWrite):
        who = request.state.identity
        _person(who, secret=True)
        return vault.change(who, request.url.path, request.headers.get("idempotency-key"), body,
                            lambda c, w: None, lambda c: save(c, vault, who, body, sid))

    @app.delete("/api/v2/me/subscriptions/{sid}")
    def delete(request: Request, sid: str):
        from .models import Empty
        who = request.state.identity

        def work(c):
            auth.domain(who)
            return remove(c, who, sid)
        return mutate(request, Empty(), work)

    @app.get("/api/v2/subscription-picks")
    def picks(request: Request, scope: str, id: str):
        who = request.state.identity
        auth.domain(who)
        if scope not in SCOPES:
            raise Problem("scope", "scope is task, bot or team", 422)
        with store.read() as c:
            if not _exists(c, scope, id):
                raise Problem("not_found", "Not found", 404)
            if scope == "bot":
                auth.require_see(c, who, id)
            elif scope == "task" and not H.task_private_readable(c, who.actor, H.task(c, id)):
                raise Problem("not_found", "Not found", 404)
            return picks_view(c, auth, who, scope, id, settings)

    @app.put("/api/v2/subscription-picks")
    def set_pick(request: Request, body: SubscriptionPick):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            pick(c, auth, who, body)
            return picks_view(c, auth, who, body.scope, body.id, settings)
        return mutate(request, body, work)

    @app.post("/api/v2/runner-subscription-refresh")
    def refresh(request: Request, body: SubscriptionRefresh):
        who = request.state.identity
        with store.transaction() as c:
            if who.role != "runner" or not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL",
                                                     (who.runner_id,)).fetchone():
                raise Problem("forbidden", "Only a registered computer may send this", 403)
            return refreshed(c, vault, who, body)
