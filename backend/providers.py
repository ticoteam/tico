"""Which AI providers this company uses, and the one place that maps provider -> runtime -> model.

Tico never assumes a vendor. The owner enables providers and picks a default runtime and model
during setup (Settings edits them later); env vars only seed that choice on first boot. Every
bot resolves its runtime and model in this order, and never falls through to a hardcoded vendor:

    the bot's own config -> the company default -> the first enabled provider's recommended
    model -> an error that says where to fix it

The stored choice is one owner-level record with a revision, so a stale editor is refused the
way a stale bot definition is, and every change leaves an event with the before and after.
"""

import json

from .harnesses import attach_harnesses
from .store import Problem

KEY = "providers"
# What a bot's config says when it has no opinion and follows the company default.
DEFAULT = "default"

# `runtime` is the host a runner starts (runner/hosts/<runtime>.py); `harness` names the tool
# manifest that installs it (runner/harnesses/<harness>.toml). `login` is what the person types
# on the runner's machine to sign that runtime in. Providers with no CLI of their own share the
# catch-all runtime (`catch_all`): pi, reached with an OpenRouter API key.
PROVIDERS = (
    {"id": "openai", "label": "OpenAI", "runtime": "codex", "harness": "codex", "login": "codex login",
     "detail": "Codex CLI, with a ChatGPT subscription or an OpenAI API key (OPENAI_API_KEY)"},
    {"id": "anthropic", "label": "Anthropic", "runtime": "claude", "harness": "claude-code",
     "login": "claude auth login", "detail": "Claude Code, with a Claude subscription or an Anthropic API key (ANTHROPIC_API_KEY)"},
    {"id": "google", "label": "Google", "runtime": "gemini", "harness": "gemini-cli", "login": "",
     "detail": "Gemini CLI, with a Gemini API key in the bot's secrets"},
    {"id": "xai", "label": "xAI", "runtime": "grok", "harness": "grok", "login": "grok login",
     "detail": "Grok Build, xAI's own CLI, signed in with a Grok subscription or an xAI API key"},
    {"id": "cursor", "label": "Cursor", "runtime": "cursor", "harness": "cursor-agent", "login": "cursor-agent login",
     "detail": "Cursor's agent CLI, signed in with a Cursor subscription or a CURSOR_API_KEY"},
    {"id": "deepseek", "label": "DeepSeek", "runtime": "pi", "harness": "pi", "login": "",
     "detail": "The pi agent, with an OpenRouter API key in the shared secrets"},
    {"id": "moonshot", "label": "Kimi (Moonshot)", "runtime": "pi", "harness": "pi", "login": "",
     "detail": "The pi agent, with an OpenRouter API key in the shared secrets"},
    {"id": "meta", "label": "Meta Llama", "runtime": "pi", "harness": "pi", "login": "",
     "detail": "The pi agent, with an OpenRouter API key in the shared secrets"},
    {"id": "mistral", "label": "Mistral", "runtime": "pi", "harness": "pi", "login": "",
     "detail": "The pi agent, with an OpenRouter API key in the shared secrets"},
    {"id": "openrouter", "label": "OpenRouter", "runtime": "pi", "harness": "pi", "login": "",
     "catch_all": True, "detail": "The pi agent, with an OpenRouter API key in the shared secrets"},
)
PROVIDER_BY_ID = {row["id"]: row for row in PROVIDERS}
# What a computer's model CLI is called, for the words on Health and in Settings.
RUNTIME_LABELS = {"codex": "Codex", "claude": "Claude Code", "grok": "Grok", "cursor": "Cursor", "gemini": "Gemini", "pi": "pi"}
# The variables that sign a runtime's CLI in with no browser. A credential stored under one of these names can be
# given to every computer (backend/credentials.py `COMPUTERS`); a runner takes only the names it can sign in with.
MODEL_KEYS = {"codex": ("OPENAI_API_KEY",), "claude": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
              "cursor": ("CURSOR_API_KEY",)}
MODEL_KEY_NAMES = frozenset(name for names in MODEL_KEYS.values() for name in names)
# One provider speaks for a runtime when a bot names only the runtime: the catch-all row for a
# shared runtime, else the only one.
PROVIDER_BY_RUNTIME = {}
for _row in PROVIDERS:
    if _row["runtime"] not in PROVIDER_BY_RUNTIME or _row.get("catch_all"):
        PROVIDER_BY_RUNTIME[_row["runtime"]] = _row

# One row per model. `recommended` marks what a provider's bots get when nobody chose;
# `deprecated` rows stay so bots already on them render, but nothing new may pick them. Rows
# with no `provider` belong to external agents that bring their own model: they are never a
# default, and any company may create a bot on one.
MODEL_CATALOG = tuple(attach_harnesses(row) for row in (
    {"id": "gpt-6-astra", "label": "gpt 6 astra", "runtime": "codex", "provider": "openai",
     "efforts": ("low", "medium", "high", "xhigh", "max", "ultra"),
     "default_effort": "medium", "deprecated": True},
    {"id": "gpt-6.1-sol", "label": "gpt 6.1 sol", "runtime": "codex", "provider": "openai",
     "efforts": ("low", "medium", "high", "xhigh", "max"),
     "default_effort": "medium"},
    {"id": "gpt-6-sol", "label": "gpt 6 sol", "runtime": "codex", "provider": "openai",
     "efforts": ("low", "medium", "high", "xhigh", "max"),
     "default_effort": "medium", "deprecated": True},
    {"id": "gpt-6-luna", "label": "gpt 6 luna", "runtime": "codex", "provider": "openai",
     "recommended": True,
     "efforts": ("low", "medium", "high", "xhigh", "max"),
     "default_effort": "max"},
    {"id": "gpt-5.6-sol", "label": "gpt 5.6 sol", "runtime": "codex", "provider": "openai",
     "efforts": ("low", "medium", "high", "xhigh", "max", "ultra"),
     "default_effort": "low", "deprecated": True},
    {"id": "grok-4.6", "label": "grok 4.6", "runtime": "grok", "provider": "xai",
     "recommended": True,
     "efforts": ("low", "medium", "high", "xhigh"),
     "default_effort": "high"},
    {"id": "gemini-3.8-flash", "label": "gemini 3.8 flash", "runtime": "gemini", "provider": "google",
     "recommended": True,
     "efforts": ("low", "medium", "high"),
     "default_effort": "high"},
    {"id": "claude-opus-5-5", "label": "opus 5.5", "runtime": "claude", "provider": "anthropic",
     "recommended": True,
     "efforts": ("low", "medium", "high", "xhigh", "max"),
     "default_effort": "high"},
    {"id": "claude-opus-5", "label": "opus 5", "runtime": "claude", "provider": "anthropic",
     "deprecated": True,
     "efforts": ("low", "medium", "high", "xhigh", "max"),
     "default_effort": "high"},
    {"id": "claude-sonnet-5-5", "label": "sonnet 5.5", "runtime": "claude", "provider": "anthropic",
     "efforts": ("low", "medium", "high", "xhigh", "max"),
     "default_effort": "high"},
    {"id": "claude-fable-5-1", "label": "fable 5.1", "runtime": "claude", "provider": "anthropic",
     "efforts": ("low", "medium", "high", "xhigh", "max"),
     "default_effort": "high"},
    # Cursor picks the model itself on `auto`; any other model it offers is named in the bot's config.
    {"id": "cursor-auto", "label": "cursor auto", "runtime": "cursor", "provider": "cursor",
     "recommended": True, "efforts": ("as-configured",), "default_effort": "as-configured"},
    # Models with no CLI of their own run on pi through OpenRouter (runner/hosts/pi.py maps each
    # id here to its OpenRouter id). The `openrouter` provider serves all of them.
    {"id": "deepseek-v4.1-flash", "label": "deepseek v4.1 flash", "runtime": "pi",
     "provider": "deepseek", "recommended": True,
     "efforts": ("low", "high", "max"),
     "default_effort": "low"},
    {"id": "deepseek-v4-pro", "label": "deepseek v4 pro", "runtime": "pi", "provider": "deepseek",
     "efforts": ("low", "high", "max"), "default_effort": "low"},
    {"id": "kimi-k3", "label": "kimi k3", "runtime": "pi", "provider": "moonshot",
     "recommended": True, "efforts": ("low", "high", "max"), "default_effort": "low"},
    {"id": "kimi-k2.7-code", "label": "kimi k2.7 code", "runtime": "pi", "provider": "moonshot",
     "efforts": ("low", "high", "max"), "default_effort": "low"},
    {"id": "llama-4-maverick", "label": "llama 4 maverick", "runtime": "pi", "provider": "meta",
     "recommended": True, "efforts": ("low", "high"), "default_effort": "low"},
    {"id": "mistral-medium-3.5", "label": "mistral medium 3.5", "runtime": "pi", "provider": "mistral",
     "recommended": True, "efforts": ("low", "high"), "default_effort": "low"},
    {"id": "devstral-2512", "label": "devstral 2512", "runtime": "pi", "provider": "mistral",
     "efforts": ("low", "high"), "default_effort": "low"},
    # An external agent brings its own model; the profile's config decides, and the heartbeat
    # reports what it is. This row exists so the bot can be created and shown like any other.
    {"id": "hermes-profile", "label": "profile's own model", "runtime": "hermes", "provider": "",
     "efforts": ("as-configured",),
     "default_effort": "as-configured"},
    {"id": "openclaw-own", "label": "openclaw's own model", "runtime": "openclaw", "provider": "",
     "efforts": ("as-configured",),
     "default_effort": "as-configured"},
    {"id": "grokbot-own", "label": "grok bot's own model", "runtime": "grokbot", "provider": "",
     "efforts": ("as-configured",),
     "default_effort": "as-configured"},
    {"id": "dots-own", "label": "dots' own model", "runtime": "dots", "provider": "",
     "efforts": ("as-configured",),
     "default_effort": "as-configured"},
    {"id": "muse-own", "label": "muse's own model", "runtime": "muse", "provider": "",
     "efforts": ("as-configured",),
     "default_effort": "as-configured"},
))
MODEL_BY_ID = {row["id"]: row for row in MODEL_CATALOG}

# What a model costs at the provider's public list price, in USD per 1M tokens: (input, cached input,
# output). Usage (backend/usage.py) multiplies a run's token counts by these to estimate spend, so a
# model missing here shows its tokens and no cost. Standard tier, prompts under the long-context
# threshold; batch, priority, regional and cache-write premiums are not modelled (a cache write is
# counted as input). A bot that names a model by another id than these gets no estimate.
# Sources, read 2026-09-29:
#   Anthropic  platform.claude.com/docs/en/about-claude/pricing (cache hit = "cache hits and refreshes")
#   OpenAI     developers.openai.com/api/docs/pricing (standard, short context)
#   Google     ai.google.dev/gemini-api/docs/pricing (paid standard; $0.75/$3.75 until 2026-12-31, doubling from 2027-01-01)
#   xAI        docs.x.ai/developers/models (prompts under 200k)
#   pi models  OpenRouter's public model list, openrouter.ai/api/v1/models (what the pi runtime is billed)
PRICES_AS_OF = "2026-09-29"
PRICES = {
    "claude-fable-5-1": (10.0, 0.25, 50.0),
    "claude-fable-5": (10.0, 1.0, 50.0),
    "claude-opus-5-5": (4.0, 0.20, 20.0),
    "claude-opus-5": (5.0, 0.50, 25.0),
    "claude-opus-4-8": (5.0, 0.50, 25.0),
    "claude-sonnet-5-5": (2.0, 0.20, 10.0),
    "claude-sonnet-5": (2.0, 0.20, 10.0),
    "claude-haiku-4-5": (1.0, 0.10, 5.0),
    "gpt-6-astra": (10.0, 1.0, 50.0),
    "gpt-6-sol": (2.0, 0.20, 10.0),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6.1-sol": (2.0, 0.10, 10.0),
    "gpt-5.6-sol": (4.0, 0.40, 20.0),
    "gpt-5.6-terra": (2.0, 0.20, 12.0),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gemini-3.8-flash": (0.75, 0.075, 3.75),
    "grok-4.6": (2.0, 0.50, 6.0),
    "deepseek-v4.1-flash": (0.30, 0.006, 1.20),
    "deepseek-v4-pro": (0.955, 0.080, 1.911),
    "kimi-k3": (3.0, 0.30, 15.0),
    "kimi-k2.7-code": (0.671, 0.18, 3.35),
    "llama-4-maverick": (0.1875, 0.1875, 0.6525),
    "mistral-medium-3.5": (1.5, 1.5, 7.5),
    "devstral-2512": (0.40, 0.04, 2.0),
}


def price_of(model):
    """(input, cached input, output) USD per 1M tokens for a model id, or None when it has no list price."""
    return PRICES.get(str(model or "").strip().lower())


def estimate_cost(model, input_tokens, cached_tokens, output_tokens):
    """The list-price cost in USD of one run's tokens, or None for a model with no price. `input_tokens`
    are the uncached ones; cached tokens are billed at the cached rate."""
    price = price_of(model)
    if not price:
        return None
    return (int(input_tokens or 0) * price[0] + int(cached_tokens or 0) * price[1]
            + int(output_tokens or 0) * price[2]) / 1_000_000


class ProviderError(Problem):
    """A choice that cannot be honoured; `detail` says what to do about it."""

    def __init__(self, code, detail, status=422):
        super().__init__(code, detail, status)


class NoProvider(ProviderError):
    def __init__(self, what="This bot"):
        super().__init__(
            "no_provider", f"{what} has no runtime or model and the company has no default. Choose "
            "which AI providers you use and a default model in Settings > AI providers "
            "(or run `tico env create ... --providers ...` / set TICO_ENABLED_PROVIDERS).", 409)


# ----------------------------------------------------------------------------- catalog
def runtime_provider(runtime):
    row = PROVIDER_BY_RUNTIME.get(str(runtime or ""))
    return row["id"] if row else ""


def serves(provider, row):
    """Whether enabling `provider` lets bots run this catalog model: the model's own provider, or
    the catch-all provider of the model's runtime (OpenRouter serves every pi model)."""
    named = PROVIDER_BY_ID.get(provider) or {}
    return bool(row["provider"] == provider or (named.get("catch_all") and named["runtime"] == row["runtime"]))


def provider_models(provider, *, current=True):
    return [row for row in MODEL_CATALOG
            if serves(provider, row) and not (current and row.get("deprecated"))]


def recommended(provider):
    """The model a provider's bots get when nobody chose one, or None for an unknown provider."""
    rows = provider_models(provider)
    return next((row for row in rows if row.get("recommended")), rows[0] if rows else None)


def runtime_enabled(runtime, enabled, model_row=None):
    """Whether the enabled providers can run `runtime` (and `model_row`, when one is chosen)."""
    if model_row:
        return any(serves(name, model_row) for name in enabled)
    return any(PROVIDER_BY_ID[name]["runtime"] == runtime for name in enabled)


def normalize_enabled(value):
    """A list of provider ids, in the order given, from a list or a comma-separated string."""
    items = value.split(",") if isinstance(value, str) else list(value or ())
    enabled = []
    for item in items:
        name = str(item).strip().lower()
        if not name:
            continue
        if name not in PROVIDER_BY_ID:
            raise ProviderError("provider", f"Unknown provider {name!r}; choose from "
                                + ", ".join(PROVIDER_BY_ID), 422)
        if name not in enabled:
            enabled.append(name)
    return enabled


def complete_choice(enabled, runtime="", model=""):
    """Validate an owner's choice and fill what they left out.

    A model implies its runtime; a runtime alone means that provider's recommended model; nothing
    at all means the first enabled provider's recommended model. Returns (runtime, model).
    """
    enabled = normalize_enabled(enabled)
    runtime, model = str(runtime or "").strip(), str(model or "").strip()
    if not enabled:
        if runtime or model:
            raise ProviderError("provider", "Enable at least one AI provider before choosing a default model", 422)
        return "", ""
    if model:
        row = MODEL_BY_ID.get(model)
        if not row or not row["provider"]:
            raise ProviderError("model", f"Unknown default model {model!r}", 422)
        if runtime and runtime != row["runtime"]:
            raise ProviderError("model", f"{model} runs on {row['runtime']}, not {runtime}", 422)
        if row.get("deprecated"):
            raise ProviderError("model", f"{row['label']} is retired; choose a current model", 422)
        runtime = row["runtime"]
    elif runtime:
        provider = PROVIDER_BY_RUNTIME.get(runtime)
        if not provider:
            raise ProviderError("runtime", f"Unknown runtime {runtime!r}; choose from "
                                + ", ".join(PROVIDER_BY_RUNTIME), 422)
        # The enabled provider that speaks for this runtime, so a company with only Kimi on
        # the shared pi runtime gets Kimi's model, not OpenRouter's pick.
        own = next((name for name in enabled if PROVIDER_BY_ID[name]["runtime"] == runtime), provider["id"])
        model = recommended(own)["id"]
    else:
        row = recommended(enabled[0])
        return row["runtime"], row["id"]
    if not runtime_enabled(runtime, enabled, MODEL_BY_ID.get(model)):
        raise ProviderError("provider", f"The default {runtime} runtime needs its provider "
                            f"({runtime_provider(runtime)}) to be enabled", 422)
    return runtime, model


# ----------------------------------------------------------------------------- stored choice
def _record(value):
    try:
        stored = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        stored = None
    stored = stored if isinstance(stored, dict) else {}
    return {"enabled": [p for p in stored.get("enabled") or [] if p in PROVIDER_BY_ID],
            "runtime": str(stored.get("runtime") or ""), "model": str(stored.get("model") or ""),
            "revision": int(stored.get("revision") or 0), "updated": str(stored.get("updated") or ""),
            "updated_by": str(stored.get("updated_by") or ""), "source": str(stored.get("source") or "")}


def env_seed(settings):
    """What the environment asks for on first boot; empty when it asks for nothing."""
    enabled = normalize_enabled(getattr(settings, "enabled_providers", ()))
    runtime, model = getattr(settings, "default_runtime", ""), getattr(settings, "default_model", "")
    if not enabled and (runtime or model):
        # A default without providers still means its own provider.
        row = MODEL_BY_ID.get(model) or PROVIDER_BY_RUNTIME.get(runtime) and {"provider": runtime_provider(runtime)}
        enabled = [row["provider"]] if row and row.get("provider") else []
    if not enabled:
        return None
    runtime, model = complete_choice(enabled, runtime, model)
    return {"enabled": enabled, "runtime": runtime, "model": model}


def load(c, settings=None):
    """The company's choice. Before the owner has saved one, the environment's seed stands in
    (`source: environment`), so a deployment that sets TICO_ENABLED_PROVIDERS works at once."""
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    if row:
        return _record(row[0])
    seed = env_seed(settings) if settings is not None else None
    return _record({**seed, "source": "environment"} if seed else {})


def configured(record):
    return bool(record["enabled"])


def seed_from_env(c, settings, now):
    """Persist the environment's seed once, so later edits are revisioned and the environment is
    never consulted again. Called at database initialization."""
    if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (KEY,)).fetchone():
        return False
    seed = env_seed(settings)
    if not seed:
        return False
    c.execute("INSERT INTO registry_metadata VALUES(?,?)",
              (KEY, json.dumps({**seed, "revision": 1, "updated": now, "updated_by": "environment",
                                "source": "environment"}, sort_keys=True)))
    return True


def save(c, actor, body, now):
    """Replace the choice. `body` carries enabled, runtime, model and the revision it was read at."""
    before = load(c)
    if before["revision"] != int(body.get("expected_revision") or 0):
        raise ProviderError("conflict", "The provider choice changed since you opened it; reload and try again", 409)
    enabled = normalize_enabled(body.get("enabled"))
    runtime, model = complete_choice(enabled, body.get("runtime"), body.get("model"))
    after = {"enabled": enabled, "runtime": runtime, "model": model, "revision": before["revision"] + 1,
             "updated": now, "updated_by": actor, "source": "owner"}
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (KEY, json.dumps(after, sort_keys=True)))
    return before, after


def view(record):
    """What a client renders: every provider with whether it is on, and the current default."""
    return {"configured": configured(record), "enabled": record["enabled"],
            "default": {"runtime": record["runtime"], "model": record["model"]},
            "revision": record["revision"], "source": record["source"],
            "updated": record["updated"], "updated_by": record["updated_by"],
            "providers": [{**row, "enabled": row["id"] in record["enabled"],
                           "recommended": (recommended(row["id"]) or {}).get("id", "")}
                          for row in PROVIDERS]}


# ----------------------------------------------------------------------------- resolution
def _named(value):
    value = str(value or "").strip()
    return "" if value == DEFAULT else value


def resolve(record, config=None, *, what="This bot"):
    """The runtime and model a bot runs on. Raises NoProvider when nothing is configured.

    A bot that names only a model runs that model's runtime; one that names only a runtime gets
    the company default when it is on that runtime, else that provider's recommended model.
    """
    config = config or {}
    runtime, model = _named(config.get("runtime")), _named(config.get("model"))
    if model and not runtime:
        runtime = (MODEL_BY_ID.get(model) or {}).get("runtime", "")
    if runtime and model:
        return runtime, model
    if runtime:
        if record["runtime"] == runtime and record["model"]:
            return runtime, record["model"]
        row = recommended(runtime_provider(runtime))
        if row:
            return runtime, row["id"]
        raise ProviderError("model", f"Choose a model for the {runtime} runtime in Settings", 409)
    if record["runtime"] and record["model"]:
        return record["runtime"], record["model"]
    if record["enabled"]:
        row = recommended(record["enabled"][0])
        return row["runtime"], row["id"]
    raise NoProvider(what)


def fill(record, config):
    """`config` with its runtime and model made concrete, or unchanged when nothing resolves."""
    try:
        runtime, model = resolve(record, config)
    except ProviderError:
        return config
    return {**config, "runtime": runtime, "model": model}


def bot_choice(c, settings, config):
    """(runtime, model) for a bot's stored config, or ('', '') when nothing resolves: for the
    places that only record or display what a bot runs on and must not fail."""
    try:
        return resolve(load(c, settings), config)
    except ProviderError:
        return "", ""


def wanted_runtimes(record):
    """The runtimes a signed-in model must exist for: the company's default, else any of its
    providers'. Empty when the company has not chosen a provider yet."""
    if record["runtime"]:
        return {record["runtime"]}
    return {row["runtime"] for row in PROVIDERS if row["id"] in record["enabled"]}


def runtimes_needed(c, settings):
    """{runner id: runtimes} each computer must have: the company's, plus those of the active bots
    assigned to it. What a computer has beyond this is not a problem, so a missing one is not red."""
    record = load(c, settings)
    wanted = wanted_runtimes(record)
    need = {}
    for row in c.execute("SELECT a.runner_id, bc.config_json FROM assignments a JOIN bots b ON b.slug=a.bot "
                         "AND b.state='active' LEFT JOIN bot_config bc ON bc.bot=a.bot"):
        try:
            config = json.loads(row["config_json"] or "{}")
        except ValueError:
            config = {}
        runtime = bot_choice(c, settings, config)[0]
        need.setdefault(row["runner_id"], set()).update({runtime} if runtime else ())
    return wanted, need
