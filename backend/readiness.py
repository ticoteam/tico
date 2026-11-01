"""Can this bot run this runtime on this computer: the one check behind a model change, a move and Health.

    can_run(c, bot, runtime, runner=None, harness="", model="") -> dict
    own_harness(c, bot, runtime) -> str       the bot's own harness for `runtime`, to pass as `harness`

`runner` is a computer id (default: the computer the bot is on). The answer is judged from that computer's last
heartbeat **for this bot**: its own subscription profile's sign-in when it has one, its own API-key Credential for the
harnesses that take a key per bot (`api_keys = "bot"`: Gemini CLI, pi), the company subscription the computer is given
for that runtime while one is in effect there (backend/model_subscriptions.py), and otherwise the computer's shared
sign-in for that runtime. Another bot's missing key or another profile's sign-in never decides it.

    can_run      True, False, or None when nothing says (offline, an older computer, not checked yet, not applicable)
    applicable   False for a bot an external agent runs (Hermes, OpenClaw, Grok Bot): no computer check applies
    problem      one plain sentence when can_run is not True
    warning      an API-key computer login bills separately from a subscription; can_run stays True
    fix, link    what the person does and where (a relative `#/...` link), only when can_run is False or the bot has no
                 computer
    computer     {"id", "label"} or None; online
    reported     whether the bot's own readiness row already reflects this runtime (and model): after the next heartbeat
    bot_ready, bot_problems   that row's verdict, which describes the previous runtime until `reported` is true

Callers refuse only on can_run False; None never blocks.
"""

from . import models_preset, read_cache
from .harnesses import EXTERNAL_HARNESSES, bot_key, harness_caps, resolve_harness, runtime_of
from .store import H, readiness_document

ONLINE_SECONDS = 60
SIGNED_OUT = ("missing", "failed", "rejected")


CONFIG_SQL = "SELECT config FROM bot_config WHERE bot=?"
# One computer's row, and the row of the computer a bot is on: read through read_cache, so a page that judges the same
# bot for many harnesses and moves (backend/health.py runtime_choice) reads each once per snapshot, not once per check.
RUNNER_SQL = "SELECT id,label,last_seen_at,revoked_at,readiness,capabilities FROM runners WHERE id=?"
PLACED_SQL = ("SELECT r.id,r.label,r.last_seen_at,r.revoked_at,r.readiness,r.capabilities FROM assignments a "
              "JOIN runners r ON r.id=a.runner_id WHERE a.bot=?")
HELD_SQL = "SELECT id,env FROM credentials WHERE ciphertext IS NOT NULL AND env!=''"


def _config(c, bot):
    from .read_cache import one
    row = one(c, CONFIG_SQL, (bot,))
    return H._json(row["config"], {}) or {} if row else {}


def computer_login(readiness, runtime):
    """The physical default's report, never a dormant subscription's aggregate verdict."""
    state = (readiness.get("runtimes") or {}).get(runtime) or {}
    profile = (state.get("profiles") or {}).get(readiness.get("computer_profile") or "") or {}
    # Account/key metadata lives on the runtime row; per-home rows carry only the sign-in check.
    return {**state, **profile}


def login_warning(readiness, runtime, harness=""):
    """An API login has separate billing; a missing plan reading says nothing about its API credit."""
    caps = harness_caps(harness, runtime)
    if not caps.get("subscription_profiles") or caps.get("api_keys") != "computer":
        return ""
    state = computer_login(readiness, runtime)
    if state.get("authenticated") != "ready":
        return ""
    if ((state.get("account") or {}).get("kind") == "api_key" or state.get("key")
            or "api key" in str(state.get("detail") or "").lower()):
        return ("This computer uses an API key, not a subscription allowance. API credit is not checked here. "
                "Check the provider's API billing, or switch this computer to the intended subscription "
                "in Settings > Computers before running its bots.")
    return ""


def own_harness(c, bot, runtime):
    """The bot's own harness when it runs `runtime`, else '': what a check of the runtime the bot already runs passes
    as `harness`, the way the model picker passes the harness it offers. Without it an Antigravity bot is judged as
    Gemini CLI and gets a Gemini sign-in hint that is not its own."""
    harness = resolve_harness(_config(c, bot), runtime)
    return harness if runtime and runtime_of(harness) == runtime else ""


def _held(c, bot):
    """The variable names of the stored Credentials granted to `bot` (what its computer is handed)."""
    from .credentials import granted
    held = granted(c, "bot:" + bot)
    return {r["env"] for r in read_cache.rows(c, HELD_SQL) if r["id"] in held}


def can_run(c, bot, runtime, runner=None, harness="", model=""):
    config = _config(c, bot)
    if resolve_harness({**config, **({"harness": harness} if harness else {})}, runtime or None) in EXTERNAL_HARNESSES:
        return {"can_run": None, "applicable": False, "computer": None, "runtime": runtime,
                "problem": "An external agent runs this bot outside Tico, so no computer check applies"}
    if runner:
        row = read_cache.one(c, RUNNER_SQL, (runner,))
    else:
        row = read_cache.one(c, PLACED_SQL, (bot,))
    if not row or row["revoked_at"]:
        return {"can_run": None, "applicable": True, "computer": None, "runtime": runtime,
                "problem": "The bot is not on a computer", "fix": "Put it on a computer from its More tab",
                "link": "#/bot/" + bot + "/more"}
    label = row["label"] or "its computer"
    online = bool(row["last_seen_at"] and row["last_seen_at"] > H.shift(H.now(), seconds=-ONLINE_SECONDS))
    readiness = readiness_document(row["readiness"])
    detail = readiness.get("bots", {}).get(bot)
    detail = models_preset.as_served(c, bot, readiness, detail) if isinstance(detail, dict) else {}
    out = {"applicable": True, "computer": {"id": row["id"], "label": row["label"]}, "online": online, "runtime": runtime,
           "bot_ready": detail.get("ready") is True, "bot_problems": list(detail.get("problems") or [])[:5],
           "reported": (str(detail.get("runtime") or "") == str(runtime or "")
                        and (not model or str(detail.get("model") or "") == str(model)))}
    state = (readiness.get("runtimes") or {}).get(runtime) or {}

    def verdict(can, problem="", fix="", link="#/settings", sign_in=False):
        out.update(can_run=can)
        if sign_in:
            out["sign_in"] = True     # the model is not signed in on this computer: Settings > Computers fixes it
        if can is not True:
            out["problem"] = problem
        if can is False:
            out.update(fix=fix, link=link)
        return out

    if not online:
        return verdict(None, label + " is offline, so it has not checked " + str(runtime))
    from .subscriptions import login_upgrade
    if problem := login_upgrade(row, readiness, bot=bot):
        return verdict(False, problem, "Update the runner in Settings > Computers")
    # A harness the runner does not report on (`readiness = false`, Antigravity) has no row to judge.
    if readiness.get("schema_version") != 1 or harness_caps(harness, runtime).get("readiness") is False:
        return verdict(None, label + " does not report its AI tools; update it to check")
    if not state.get("installed"):
        # A computer installs a bot's harness when the bot is put there (runner-rs/src/harness_tools.rs), so a harness it
        # can install is no reason to refuse the move or the model: it says so while it installs.
        tool = next((t for t in (readiness.get("harnesses") or {}).values()
                     if isinstance(t, dict) and t.get("runtime") == runtime), None)
        if tool and tool.get("state") == "failed":
            return verdict(False, (tool.get("name") or str(runtime)) + " could not be installed on " + label
                           + (": " + str(tool["detail"])[:200] if tool.get("detail") else ""),
                           "Check " + label + " in Settings > Computers, or choose a model it already runs")
        if tool:
            return verdict(None, (tool.get("name") or str(runtime)) + " is installing on " + label)
        return verdict(False, str(runtime) + " is not installed on " + label,
                       "Install " + str(runtime) + " on " + label + ", or choose a model it already runs")
    # A Tico model is signed in by the company's Tico AI Gateway connection, which its run is handed; no grant of its own.
    from . import gateway_connection
    if gateway_connection.is_tico(model or config.get("model")):
        if not gateway_connection.connected(c):
            return verdict(False, "The Tico AI Gateway is not connected", "Save the gateway key in Settings > Computers",
                           "#/settings")
        # Only a computer that says it runs bots as their own user, or a cloud machine's trust group, runs a Tico turn
        # (runner-rs/src/gateway_relay.rs, models_preset.runs_gateway); the claim holds the work anywhere else
        # (execution.claim_refusal). One that says it does not (a Mac, a server without Tico's Docker runner) is
        # refused plainly, and an older runner that does not say is asked to update.
        if models_preset.runs_gateway(readiness):
            return verdict(True)
        if readiness.get("isolated") is False:
            return verdict(False, label + " can't run Tico Gateway work (it needs Tico's Docker runner)",
                           "Sign in to Claude or ChatGPT in Settings > Computers, or add a Docker runner", "#/settings")
        return verdict(False, label + " hasn't said it can run Tico Gateway work",
                       "Update its runner in Settings > Computers", "#/settings")
    # A harness whose key each bot holds (`api_keys = "bot"`): the bot's own grant is its sign-in.
    own = harness or config.get("harness") or runtime
    name = bot_key({**config, "harness": own if runtime_of(own) == runtime else runtime}, runtime)
    if name:
        if name not in _held(c, bot):
            return verdict(False, "This bot has no " + name + " Credential", "Give the bot the " + name +
                           " Credential in Credentials", "#/credentials")
        return verdict(True)
    # A company subscription given to this computer for the runtime signs its CLI turns in, whatever the computer's own
    # login says (backend/model_subscriptions.py). An older runner is never given one (in_effect), so nothing changes
    # there; nor for a bot delivered through a desktop app, which signs in its own way.
    from . import model_subscriptions
    if not config.get("app_delivery") and model_subscriptions.in_effect(c, row["id"], runtime):
        return verdict(True)
    # Every bot runs each tool on its computer's one login for it: a bot has no sign-in of its own.
    shared = computer_login(readiness, runtime)
    signed = str(shared.get("authenticated") or "unknown")
    if signed == "ready":
        if warning := login_warning(readiness, runtime, harness):
            out["warning"] = warning
        return verdict(True)
    key_caps = harness_caps(own if runtime_of(own) == runtime else runtime, runtime)
    if signed != "rejected" and key_caps.get("api_key_in_turn") and set(key_caps.get("api_key_env") or ()) & _held(c, bot):
        # A granted per-turn key does not depend on the computer's subscription login.
        return verdict(True)
    if signed in SIGNED_OUT and (line := models_preset.gateway_line(
            c, bot, readiness, config={"runtime": runtime, "model": model or config.get("model")})):
        # Its turns run on the Gateway until it is signed in (backend/models_preset.py): say so, never "login required".
        out["gateway"] = line
        return verdict(True)
    if signed in SIGNED_OUT:
        return verdict(False, shared.get("detail") or (str(runtime) + " is not signed in on " + label),
                       "Sign in " + str(runtime) + " on " + label + " in Settings > Computers, or choose a model it "
                       "already runs", sign_in=True)
    return verdict(None, label + " has not checked the " + str(runtime) + " sign-in yet")


def absolute(answer, base=""):
    """`answer` with a relative `#/...` link made absolute under the team's address, when it has one."""
    link = (answer or {}).get("link") or ""
    if base and link.startswith("#/"):
        return {**answer, "link": base.rstrip("/") + "/" + link}
    return answer
