"""The company's models choice (Settings > Computers, the first-run step, TICO_MODELS_PRESET from the installer).

Four choices, kept on the providers record (backend/providers.py) as `preset`:
- `subscriptions-gateway` (recommended): bots' main work runs on the person's Claude and ChatGPT subscriptions; until
  a bot's runtime is signed in on its computer, its turns run on Tico Mid 1.0 through the Tico AI Gateway, and a
  used-up subscription falls back to the Gateway last.
- `subscriptions`: subscriptions only; the Gateway is never spent for a bot that does not name a Tico model.
- `gateway`: the Gateway only; a used-up login falls back to the Gateway last as well.
- `own-keys`: the company's own API keys; as `subscriptions` for the Gateway.
Unset is the choice as it was before presets: nothing here changes what a bot runs on.

A Tico model runs only on a computer that runs bots as their own user (runner-rs/src/gateway_relay.rs): the runner
reports it as `isolated` in its readiness. A runner that does not say so counts as not isolated, so no Gateway turn is
handed to it and the bot waits for a sign-in instead (it never fails for it).
"""
import json

RECOMMENDED = "subscriptions-gateway"
PRESETS = (RECOMMENDED, "subscriptions", "gateway", "own-keys")
GATEWAY_PRESETS = frozenset((RECOMMENDED, "gateway"))
# What a bot's main work runs on before its subscription is signed in, and the Gateway fallback (Head of Engineering,
# Oct 18: the versioned name, so a new lineup is a deliberate change).
WAITING_MODEL = "tico-mid-1.0"
GATEWAY_RUNTIME = "pi"
# The runtimes a person signs in to with a subscription (Settings > Computers "Sign in").
SUBSCRIPTION_RUNTIMES = frozenset(("claude", "codex"))
SIGNED_IN = "ready"


def normalize(value):
    """A stored or asked-for preset, or '' for none (an unknown value is none, never a guess)."""
    value = str(value or "").strip().lower()
    return value if value in PRESETS else ""


def of(record):
    return normalize((record or {}).get("preset"))


def spends_gateway(record):
    """Whether this choice lets a bot that names no Tico model run on the Gateway (before sign-in, or as the last
    fallback). Every other choice keeps the rule that such a bot never spends the company's gateway key."""
    return of(record) in GATEWAY_PRESETS


def isolated(ready):
    """Whether the computer whose readiness document is `ready` runs bots as their own user. Only an explicit `true`."""
    return isinstance(ready, dict) and ready.get("isolated") is True


def signed_in(ready, runtime):
    row = ((ready or {}).get("runtimes") or {}).get(runtime) or {}
    return row.get("authenticated") == SIGNED_IN


def gateway_on(record):
    from .providers import PROVIDER_BY_ID
    return any((PROVIDER_BY_ID.get(name) or {}).get("gateway") for name in record.get("enabled") or ())


def waiting(record, ready, config):
    """Whether a bot on `config` (runtime and model filled in) runs its next turn on the Gateway because its
    subscription is not signed in on this computer yet: only under the recommended choice, with the Gateway connected,
    on an isolated computer, for a bot on a subscription runtime that names no Tico model of its own."""
    from .gateway_connection import is_tico
    config = config if isinstance(config, dict) else {}
    runtime = str(config.get("runtime") or "")
    return (of(record) == RECOMMENDED and gateway_on(record) and isolated(ready)
            and runtime in SUBSCRIPTION_RUNTIMES and not is_tico(config.get("model"))
            and not signed_in(ready, runtime))


def on_gateway(config):
    """`config` moved to the Gateway for one turn. The bot's own choice is untouched: once it is signed in, the next
    claim runs it there again, with no restart."""
    return {**config, "runtime": GATEWAY_RUNTIME, "harness": GATEWAY_RUNTIME, "model": WAITING_MODEL,
            "waiting_for_sign_in": str(config.get("runtime") or "")}


def main_config(record, ready, config):
    """What a claim runs a bot's turn on under the company's choice: `config` itself, or the Gateway while it waits."""
    return on_gateway(config) if waiting(record, ready, config) else config


def with_gateway_fallback(record, ready, config):
    """`config` with the Gateway as the last fallback when the choice spends it, the computer can run it and the bot
    names no fallback of its own; the runner tries the company's other signed-in providers first."""
    from .gateway_connection import is_tico
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    if (not spends_gateway(record) or not record.get("auto_fallback") or not gateway_on(record)
            or not isolated(ready) or fallback or is_tico(config.get("model"))):
        return config
    return {**config, "gateway_fallback": WAITING_MODEL}


def fallback_row(record):
    """The Gateway's row at the end of the company's fallback list (providers.default_fallbacks), or None."""
    if spends_gateway(record) and gateway_on(record):
        return {"runtime": GATEWAY_RUNTIME, "harness": GATEWAY_RUNTIME, "model": WAITING_MODEL}
    return None


# What the person calls a subscription login, in Health's plain wording under the two Gateway choices.
LOGIN_NAMES = {"claude": "Claude", "codex": "ChatGPT"}


def gateway_line(c, bot, ready, settings=None, config=None):
    """What runs `bot`'s turns while its subscription is not signed in on the computer `ready` describes, in a
    person's words, or '' when the Gateway does not take them (`waiting`, the claim's own rule:
    backend/execution.py claim_refusal). `config` is the runtime and model asked about (the model picker's); the
    bot's own, filled from the company's choice, when not given. A computer without the Gateway's own runtime holds
    the turn (as the claim does)."""
    from .providers import fill, load
    if ((ready.get("runtimes") or {}).get(GATEWAY_RUNTIME) or {}).get("installed") is not True:
        return ""
    record = load(c, settings)
    if config is None:
        row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
        config = fill(record, json.loads(row[0] or "{}") if row else {})
    if not waiting(record, ready, config):
        return ""
    return "Using Tico AI Gateway; sign in to " + LOGIN_NAMES.get(config["runtime"], config["runtime"]) \
        + " to use your subscription"


def as_served(c, bot, ready, check, settings=None):
    """The bot's readiness row from its computer (`check`) as a person reads it: while the Gateway runs its turns
    because its subscription is not signed in there, the missing sign-in is not a problem and `gateway` says what
    runs instead. What the claim still holds such a turn for (its checkout, its settings, the Gateway's own runtime)
    keeps the row as reported. The route itself is chosen at the claim, never here."""
    if (not isinstance(check, dict) or check.get("ready") is True or check.get("repository_present") is not True
            or check.get("configuration_valid") is not True):
        return check
    line = gateway_line(c, bot, ready, settings)
    return {**check, "ready": True, "problems": [], "gateway": line} if line else check


def computer_of(c, bot):
    """The readiness document of the computer `bot` is on, or {}."""
    from .store import readiness_document
    row = c.execute("SELECT r.readiness_json FROM assignments a JOIN runners r ON r.id=a.runner_id "
                    "WHERE a.bot=? AND r.revoked_at IS NULL", (bot,)).fetchone()
    return readiness_document(row[0]) if row else {}


def runnable_fallbacks(record, ready, rows):
    """The company's fallback rows a bot on the computer `ready` can use: the Gateway row only where it can run it."""
    return [row for row in rows if row.get("model") != WAITING_MODEL or row.get("runtime") != GATEWAY_RUNTIME
            or isolated(ready)]


SIGN_IN_TITLE = "Sign in to Claude and ChatGPT, or add API keys"
SIGN_IN_LINE = ("Sign in with your own Claude and ChatGPT accounts in Settings > Computers. "
                "Don't share one login across your team.")
NOT_ISOLATED_LINE = ("This computer can't run Gateway work without the Docker runner; sign in to Claude and ChatGPT "
                     "or add API keys. Until then its bots wait.")


def card(c, settings=None):
    """The one Needs you card for the company's owner while the recommended choice waits for a sign-in: Claude or
    ChatGPT is signed in (by subscription or API key) on no computer. One card, never one per bot or per computer.
    It says so plainly when no computer can run Gateway work meanwhile, so bots that wait are not silently stuck."""
    from .providers import load
    from .store import readiness_document
    record = load(c, settings)
    if of(record) != RECOMMENDED:
        return None
    documents = [readiness_document(row[0]) for row in
                 c.execute("SELECT readiness_json FROM runners WHERE revoked_at IS NULL ORDER BY id").fetchall()]
    if all(any(signed_in(d, runtime) for d in documents) for runtime in SUBSCRIPTION_RUNTIMES):
        return None
    lines = [SIGN_IN_LINE] + ([] if any(isolated(d) for d in documents) else [NOT_ISOLATED_LINE])
    return {"kind": "setup", "id": "setup:sign-in", "title": SIGN_IN_TITLE, "first_line": " ".join(lines),
            "created": record.get("updated") or "", "requester": None, "origin_actor": None, "from_person": None,
            "link": "#/settings", "link_label": "Open Settings > Computers"}
