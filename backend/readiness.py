"""Can this bot run this runtime on this computer: the one check behind a model change, a move and Health.

    can_run(c, bot, runtime, runner=None, harness="", model="") -> dict
    own_harness(c, bot, runtime) -> str       the bot's own harness for `runtime`, to pass as `harness`

`runner` is a computer id (default: the computer the bot is on). The answer is judged from that computer's last
heartbeat **for this bot**: its own subscription profile's sign-in when it has one, its own API-key Credential for the
harnesses that take a key per bot (`api_keys = "bot"`: Gemini CLI, pi), and otherwise the computer's shared sign-in for that runtime. Another
bot's missing key or another profile's sign-in never decides it.

    can_run      True, False, or None when nothing says (offline, an older computer, not checked yet, not applicable)
    applicable   False for a bot an external agent runs (Hermes, OpenClaw, Grok Bot): no computer check applies
    problem      one plain sentence when can_run is not True
    fix, link    what the person does and where (a relative `#/...` link), only when can_run is False or the bot has no
                 computer
    computer     {"id", "label"} or None; online
    reported     whether the bot's own readiness row already reflects this runtime (and model): after the next heartbeat
    bot_ready, bot_problems   that row's verdict, which describes the previous runtime until `reported` is true

Callers refuse only on can_run False; None never blocks.
"""
import json

from .harnesses import EXTERNAL_HARNESSES, bot_key, harness_caps, resolve_harness, runtime_of
from .store import H, readiness_document

ONLINE_SECONDS = 60
SIGNED_OUT = ("missing", "failed", "rejected")


def _config(c, bot):
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    return H._json(row["config_json"], {}) or {} if row else {}


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
    return {r["env"] for r in c.execute("SELECT id,env FROM credentials WHERE ciphertext IS NOT NULL AND env!=''")
            if r["id"] in held}


def can_run(c, bot, runtime, runner=None, harness="", model=""):
    config = _config(c, bot)
    if resolve_harness({**config, **({"harness": harness} if harness else {})}, runtime or None) in EXTERNAL_HARNESSES:
        return {"can_run": None, "applicable": False, "computer": None, "runtime": runtime,
                "problem": "An external agent runs this bot outside Tico, so no computer check applies"}
    if runner:
        row = c.execute("SELECT id,label,last_seen,revoked_at,readiness_json FROM runners WHERE id=?", (runner,)).fetchone()
    else:
        row = c.execute("SELECT r.id,r.label,r.last_seen,r.revoked_at,r.readiness_json FROM assignments a "
                        "JOIN runners r ON r.id=a.runner_id WHERE a.bot=?", (bot,)).fetchone()
    if not row or row["revoked_at"]:
        return {"can_run": None, "applicable": True, "computer": None, "runtime": runtime,
                "problem": "The bot is not on a computer", "fix": "Put it on a computer from its More tab",
                "link": "#/bot/" + bot + "/more"}
    label = row["label"] or "its computer"
    online = bool(row["last_seen"] and row["last_seen"] > H.shift(H.now(), seconds=-ONLINE_SECONDS))
    readiness = readiness_document(row["readiness_json"])
    detail = readiness.get("bots", {}).get(bot)
    detail = detail if isinstance(detail, dict) else {}
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
            return verdict(False, "The Tico AI Gateway is not connected", "Save the gateway key in Settings > AI providers",
                           "#/settings")
        return verdict(True)
    # A harness whose key each bot holds (`api_keys = "bot"`): the bot's own grant is its sign-in.
    own = harness or config.get("harness") or runtime
    name = bot_key({**config, "harness": own if runtime_of(own) == runtime else runtime}, runtime)
    if name:
        if name not in _held(c, bot):
            return verdict(False, "This bot has no " + name + " Credential", "Give the bot the " + name +
                           " Credential in Credentials", "#/credentials")
        return verdict(True)
    from .subscriptions import covers, effective
    profile = effective(c, bot)[0]
    if profile and covers({"runtime": runtime, "harness": harness}):
        stored = c.execute("SELECT runtimes_json FROM computer_profiles WHERE runner_id=? AND profile=?",
                           (row["id"], profile)).fetchone()
        signed = (json.loads(stored[0] or "{}") if stored else {}).get(runtime, {}).get("signed_in")
        if signed is None:
            signed = {"ready": True, **{k: False for k in SIGNED_OUT}}.get(
                ((state.get("profiles") or {}).get(profile) or {}).get("authenticated"))
        if signed is None:
            return verdict(None, "The " + profile + " subscription has not reported " + str(runtime) + " on " + label)
        return verdict(True) if signed else verdict(
            False, "The " + profile + " subscription is not signed in to " + str(runtime) + " on " + label,
            "Sign in the " + profile + " subscription to " + str(runtime) + " on " + label + " in Settings > Computers", sign_in=True)
    shared = (state.get("profiles") or {}).get("") or state
    signed = str(shared.get("authenticated") or "unknown")
    if signed == "ready":
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
