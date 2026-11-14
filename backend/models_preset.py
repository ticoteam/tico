"""The company's models choice (Settings > Computers, the first-run step, TICO_MODELS_PRESET from the installer).

Four choices, kept on the providers record (backend/providers.py) as `preset`:
- `subscriptions-gateway` (recommended): bots' main work runs on the person's Claude and ChatGPT subscriptions; until
  a bot's runtime is signed in on its computer, its turns run on the Medium tier through the Tico AI Gateway, and a
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

from . import gateway_tiers
from . import bot_settings as BS

RECOMMENDED = "subscriptions-gateway"
PRESETS = (RECOMMENDED, "subscriptions", "gateway", "own-keys")
GATEWAY_PRESETS = frozenset((RECOMMENDED, "gateway"))
# What a bot's main work runs on before its subscription is signed in, and the Gateway fallback: Medium at the current
# version, pinned (backend/gateway_tiers.py `bot_model`), so a new price is a deliberate pick. A bot's `gateway:` block in
# bot.yaml (`level`, `fast`) changes the tier; the company-wide row names Medium.
WAITING_MODEL = "tico-medium-1.0"
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


def runs_gateway(ready):
    """Whether the computer whose readiness document is `ready` runs Tico models: it runs bots as their own user (only
    an explicit `isolated: true`), or it is a Tico cloud machine, one company's trust group (`trust`,
    runner-rs/src/cloud_machine.rs), whose turns reach the Gateway through the runner's relay as its one user."""
    return isinstance(ready, dict) and (ready.get("isolated") is True or ready.get("trust") == "cloud_machine")


def signed_in(ready, runtime):
    row = ((ready or {}).get("runtimes") or {}).get(runtime) or {}
    return row.get("authenticated") == SIGNED_IN


def gateway_on(record):
    from .providers import PROVIDER_BY_ID

    return any((PROVIDER_BY_ID.get(name) or {}).get("gateway") for name in record.get("enabled") or ())


def waiting(record, ready, config, plans_out=False):
    """Whether a bot on `config` (runtime and model filled in) runs its next turn on the Gateway because its
    subscription is not signed in on this computer yet: only under the recommended choice, with the Gateway connected,
    on an isolated computer, for a bot on a subscription runtime that names no Tico model of its own. `plans_out`
    (`plans_out` below): the computer's plans for it ran out and it does not fall back to the Gateway, so it waits."""
    from .gateway_connection import is_tico

    config = config if isinstance(config, dict) else {}
    runtime = str(config.get("runtime") or "")
    return (
        not plans_out
        and of(record) == RECOMMENDED
        and gateway_on(record)
        and runs_gateway(ready)
        and runtime in SUBSCRIPTION_RUNTIMES
        and not is_tico(config.get("model"))
        and not signed_in(ready, runtime)
    )


def computer_falls_back(c, runner_id):
    """Whether this computer's turns run on the Gateway when its plans run out: its own "Fall back to Tico Gateway"
    switch (Settings > Computers, `runners.gateway_fallback`), off unless an admin turned it on."""
    from .read_cache import one

    row = one(c, "SELECT gateway_fallback FROM runners WHERE id=?", (runner_id,)) if runner_id else None
    return bool(row and row[0])


def plans_out(c, runner_id, config, loads=None):
    """Whether a turn on `config` here waits for a plan instead of running on the Gateway: every plan given this
    computer for its runtime is passed over (at its limit, or needing reconnecting) and the computer does not fall
    back to the Gateway. Without it the turn would read as not signed in yet and `waiting` would send it there."""
    from . import model_subscriptions

    runtime = str((config or {}).get("runtime") or "")
    return (
        runtime in SUBSCRIPTION_RUNTIMES
        and model_subscriptions.passed_over(c, runner_id, runtime, loads)
        and not computer_falls_back(c, runner_id)
    )


def pick(model=None):
    """The Gateway model a waiting or fallback turn runs on: `model` (an id, or a function that finds it only when it
    is needed), else the company-wide Medium."""
    model = model() if callable(model) else model
    return model or gateway_tiers.default_model()


def model_for_bot(c, settings, bot, config):
    """The Gateway model for `bot` when nothing named one: Medium unless its bot.yaml `gateway:` names a level or Fast,
    at the current version. `c`, `settings` and `bot` are kept for the callers; the org chart no longer picks it."""
    return gateway_tiers.bot_model((config or {}).get("gateway"))


def on_gateway(config, model=None):
    """`config` moved to the Gateway for one turn. The bot's own choice is untouched: once it is signed in, the next
    claim runs it there again, with no restart."""
    return {
        **config,
        "runtime": GATEWAY_RUNTIME,
        "harness": GATEWAY_RUNTIME,
        "model": pick(model),
        "waiting_for_sign_in": str(config.get("runtime") or ""),
    }


def main_config(record, ready, config, model=None, plans_out=False):
    """What a claim runs a bot's turn on under the company's choice: `config` itself, or the Gateway while it waits."""
    return on_gateway(config, model) if waiting(record, ready, config, plans_out) else config


def with_gateway_fallback(record, ready, config, model=None, falls_back=False):
    """`config` with the Gateway as the last fallback when the choice spends it, the computer can run it and falls
    back to it (`falls_back`, its own switch: computer_falls_back), and the bot names no fallback of its own; the
    runner tries the company's other signed-in providers first."""
    from .gateway_connection import is_tico

    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    if (
        not falls_back
        or not spends_gateway(record)
        or not record.get("auto_fallback")
        or not gateway_on(record)
        or not runs_gateway(ready)
        or fallback
        or is_tico(config.get("model"))
    ):
        return config
    return {**config, "gateway_fallback": pick(model)}


def with_gateway_inputs(config):
    """`config` with `gateway_inputs`, what each Gateway model of the turn reads: {model id: ["text", "image", "pdf"]} for
    its model, the fallback it names and `gateway_fallback`, from the catalog in use (backend/gateway_tiers.py).
    The runner tells pi from it which models take images (runner-rs/src/hosts/pi.rs); a model it names no inputs for
    is text only. Nothing is added when the turn runs no Gateway model."""
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    seen = {}
    for model in (config.get("model"), fallback.get("model"), config.get("gateway_fallback")):
        found = gateway_tiers.resolve(model)
        if found and found["kind"] == "chat":
            seen[str(model).strip()] = found["inputs"]
    return {**config, "gateway_inputs": seen} if seen else config


def fallback_row(record):
    """The Gateway's row at the end of the company's fallback list (providers.default_fallbacks), or None."""
    if spends_gateway(record) and gateway_on(record):
        return {"runtime": GATEWAY_RUNTIME, "harness": GATEWAY_RUNTIME, "model": pick()}
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
        row = c.execute(BS.ONE_SQL, (bot,)).fetchone()
        config = fill(record, json.loads(row[0] or "{}") if row else {})
    if not waiting(record, ready, config):
        return ""
    # Its plans on its computer ran out and the computer does not fall back to the Gateway: the claim holds the turn.
    row = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    if row and plans_out(c, row[0], config):
        return ""
    return (
        "Using Tico AI Gateway; sign in to "
        + LOGIN_NAMES.get(config["runtime"], config["runtime"])
        + " to use your subscription"
    )


def as_served(c, bot, ready, check, settings=None):
    """The bot's readiness row from its computer (`check`) as a person reads it: while the Gateway runs its turns
    because its subscription is not signed in there, the missing sign-in is not a problem and `gateway` says what
    runs instead. What the claim still holds such a turn for (its checkout, its settings, the Gateway's own runtime)
    keeps the row as reported. The route itself is chosen at the claim, never here."""
    if (
        not isinstance(check, dict)
        or check.get("ready") is True
        or check.get("repository_present") is not True
        or check.get("configuration_valid") is not True
    ):
        return check
    line = gateway_line(c, bot, ready, settings)
    return {**check, "ready": True, "problems": [], "gateway": line} if line else check


def computer_of(c, bot):
    """The readiness document of the computer `bot` is on, or {}."""
    from .store import readiness_document

    row = c.execute(
        "SELECT r.readiness FROM assignments a JOIN runners_with_state r ON r.id=a.runner_id "
        "WHERE a.bot=? AND r.revoked_at IS NULL",
        (bot,),
    ).fetchone()
    return readiness_document(row[0]) if row else {}


def runnable_fallbacks(record, ready, rows):
    """The company's fallback rows a bot on the computer `ready` can use: the Gateway row only where it can run it."""
    return [
        row
        for row in rows
        if not gateway_tiers.resolve(row.get("model")) or row.get("runtime") != GATEWAY_RUNTIME or runs_gateway(ready)
    ]


SIGN_IN_TITLE = "Sign in to Claude and ChatGPT, or add API keys"
SIGN_IN_LINE = (
    "Sign in with your own Claude and ChatGPT accounts in Settings > Computers. Don't share one login across your team."
)
NOT_ISOLATED_LINE = (
    "This computer can't run Gateway work without the Docker runner; sign in to Claude and ChatGPT "
    "or add API keys. Until then its bots wait."
)


def card(c, settings=None):
    """The one Needs you card for the company's owner while the recommended choice waits for a sign-in: Claude or
    ChatGPT is signed in (by subscription or API key) on no computer. One card, never one per bot or per computer.
    It says so plainly when no computer can run Gateway work meanwhile, so bots that wait are not silently stuck."""
    from .providers import load
    from .store import readiness_document

    record = load(c, settings)
    if of(record) != RECOMMENDED:
        return None
    documents = [
        readiness_document(row[0])
        for row in c.execute("SELECT readiness FROM runners_with_state WHERE revoked_at IS NULL ORDER BY id").fetchall()
    ]
    if all(any(signed_in(d, runtime) for d in documents) for runtime in SUBSCRIPTION_RUNTIMES):
        return None
    lines = [SIGN_IN_LINE] + ([] if any(runs_gateway(d) for d in documents) else [NOT_ISOLATED_LINE])
    return {
        "kind": "setup",
        "id": "setup:sign-in",
        "title": SIGN_IN_TITLE,
        "first_line": " ".join(lines),
        "created_at": record.get("updated_at") or "",
        "requester_id": None,
        "origin_actor": None,
        "from_person": None,
        "link": "#/settings",
        "link_label": "Open Settings > Computers",
    }
