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

from clients import harness_manifests as manifests
from clients import judge as J

from . import gateway_tiers, models_preset
from .batch_work import shared_check
from .harnesses import attach_harnesses, normalize_fallback
from .store import Problem

KEY = "providers"
# What a bot's config says when it has no opinion and follows the company default.
DEFAULT = "default"

# `runtime` is the host a runner starts (runner-rs/src/hosts/<runtime>.rs); `harness` names the tool
# manifest that installs it (runner/harnesses/<harness>.toml). `login`, what the person types on the runner's
# machine to sign that runtime in, comes from the manifest. Providers with no CLI of their own share the
# catch-all runtime (`catch_all`): pi, reached with an OpenRouter API key.
PROVIDERS = (
    {"id": "openai", "label": "OpenAI", "runtime": "codex", "harness": "codex",
     "detail": "Codex CLI, with a ChatGPT subscription or an OpenAI API key (OPENAI_API_KEY)"},
    {"id": "anthropic", "label": "Anthropic", "runtime": "claude", "harness": "claude-code",
     "detail": "Claude Code, with a Claude subscription or an Anthropic API key (ANTHROPIC_API_KEY)"},
    {"id": "google", "label": "Google", "runtime": "gemini", "harness": "gemini-cli",
     "detail": "Gemini CLI, with a Gemini API key in the bot's secrets"},
    {"id": "xai", "label": "xAI", "runtime": "grok", "harness": "grok",
     "detail": "Grok Build, xAI's own CLI, signed in with a Grok subscription or an xAI API key"},
    {"id": "cursor", "label": "Cursor", "runtime": "cursor", "harness": "cursor-agent",
     "detail": "Cursor's agent CLI, signed in with a Cursor subscription or a CURSOR_API_KEY"},
    {"id": "deepseek", "label": "DeepSeek", "runtime": "pi", "harness": "pi",
     "detail": "The pi agent, with an OpenRouter API key in the shared secrets"},
    {"id": "moonshot", "label": "Kimi (Moonshot)", "runtime": "pi", "harness": "pi",
     "detail": "The pi agent, with an OpenRouter API key in the shared secrets"},
    {"id": "meta", "label": "Meta", "runtime": "musecode", "harness": "muse-code",
     "detail": "Muse Code, Meta's CLI, signed in with a Meta account or a META_API_KEY"},
    {"id": "mistral", "label": "Mistral", "runtime": "pi", "harness": "pi",
     "detail": "The pi agent, with an OpenRouter API key in the shared secrets"},
    {"id": "openrouter", "label": "OpenRouter", "runtime": "pi", "harness": "pi",
     "catch_all": True, "detail": "The pi agent, with an OpenRouter API key in the shared secrets"},
    # Tico's own models through the Tico AI Gateway (clients/gateway.py): on only while the gateway connection is set
    # (backend/gateway_connection.py turns it on and off with the key); the pi agent reaches it with the gateway key.
    {"id": "tico", "label": "Tico AI Gateway", "runtime": "pi", "harness": "pi", "gateway": True,
     "detail": "Tico's own models through the Tico AI Gateway, with a gateway key from Tico"},
)
PROVIDERS = tuple({**row, "login": manifests.caps(row["runtime"]).get("terminal_login", "")} for row in PROVIDERS)
PROVIDER_BY_ID = {row["id"]: row for row in PROVIDERS}
# What a computer's model CLI is called, for the words on Health and in Settings.
RUNTIME_LABELS = {row["runtime"]: row["name"] for row in manifests.load().values() if row["id"] == row["runtime"]}
# The variables that sign a runtime's CLI in with no browser (`api_keys = "computer"`). A credential stored under one
# of these names can be given to every computer (backend/credentials.py `COMPUTERS`); a runner takes only the names it
# can sign in with.
MODEL_KEYS = {row["runtime"]: tuple(row["capabilities"]["api_key_env"]) for row in manifests.load().values()
              if row["capabilities"]["api_keys"] == "computer"}
# The runtime that serves every provider with no CLI of its own (pi, through OpenRouter).
CATCH_ALL_RUNTIME = next(row["runtime"] for row in PROVIDERS if row.get("catch_all"))
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
    {"id": "claude-haiku-5-5", "label": "haiku 5.5", "runtime": "claude", "provider": "anthropic",
     "efforts": ("low", "medium", "high", "xhigh", "max"),
     "default_effort": "medium"},
    # Cursor picks the model itself on `auto`; any other model it offers is named in the bot's config.
    {"id": "cursor-auto", "label": "cursor auto", "runtime": "cursor", "provider": "cursor",
     "recommended": True, "efforts": ("as-configured",), "default_effort": "as-configured"},
    # Muse Code's catalog. Ids are what Meta's service accepts (a real `muse exec` turn per id, 1.4.8, 2026-10-14);
    # `muse model-profile show` resolves any name, so it is no check.
    {"id": "muse-spark-1.3", "label": "muse spark 1.3", "runtime": "musecode", "provider": "meta",
     "recommended": True, "efforts": ("low", "medium", "high", "xhigh", "max"), "default_effort": "medium"},
    {"id": "muse-wonder-1.0", "label": "muse wonder 1.0", "runtime": "musecode", "provider": "meta",
     "efforts": ("low", "medium", "high", "xhigh", "max"), "default_effort": "high"},
    {"id": "muse-wonder-full-1.0", "label": "muse wonder full 1.0", "runtime": "musecode", "provider": "meta",
     "efforts": ("low", "medium", "high", "xhigh", "max"), "default_effort": "high"},
    # Models with no CLI of their own run on pi through OpenRouter (runner-rs/src/hosts/pi.rs maps each
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
    # Llama stays on pi (OpenRouter) for the bots already on it; Muse Code is Meta's own CLI now.
    {"id": "llama-4-maverick", "label": "llama 4 maverick", "runtime": "pi", "provider": "meta",
     "efforts": ("low", "high"), "default_effort": "low"},
    {"id": "mistral-medium-3.5", "label": "mistral medium 3.5", "runtime": "pi", "provider": "mistral",
     "recommended": True, "efforts": ("low", "high"), "default_effort": "low"},
    {"id": "devstral-2512", "label": "devstral 2512", "runtime": "pi", "provider": "mistral",
     "efforts": ("low", "high"), "default_effort": "low"},
    # Inception's Mercury 3 (text only, tools, 1M context) through OpenRouter: the meeting model's next step on a computer
    # that cannot run the Gateway's (backend/live_meetings.py turn_config). A meeting model, never a bot's pick.
    {"id": "mercury-3", "label": "mercury 3", "runtime": "pi", "provider": "openrouter", "meeting": True,
     "efforts": ("low", "high"), "default_effort": "low"},
    # The Tico AI Gateway's lineup (backend/gateway_tiers.py): 60 department tiers at their current pinned version, then the
    # old lineup (retired: it keeps working at its old price, nothing new may pick it). Any other id of the lineup
    # (an auto id, an older version, one that arrived with HQ's catalog) is found through `MODEL_BY_ID`.
    *gateway_tiers.listed_rows(),
    # The meeting model (live-meeting replies): picked in Settings, never a bot's model (`meeting`, see provider_models).
    *gateway_tiers.meeting_rows(),
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


class _ById(dict):
    """`MODEL_BY_ID`: the listed rows, and any other id of the Gateway's lineup on lookup."""

    def __missing__(self, key):
        row = gateway_tiers.row_for(key) if isinstance(key, str) else None
        if row is None:
            raise KeyError(key)
        return row

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return dict.__contains__(self, key) or self.get(key) is not None


MODEL_BY_ID = _ById((row["id"], row) for row in MODEL_CATALOG)

# What a model costs at the provider's public list price, in USD per 1M tokens: (input, cached input,
# output). Usage (backend/usage.py) multiplies a run's token counts by these to estimate spend, so a
# model missing here shows its tokens and no cost. Standard tier, prompts under the long-context
# threshold; batch, priority, regional and cache-write premiums are not modelled (a cache write is
# counted as input). A bot that names a model by another id than these gets no estimate.
# Sources, read 2026-09-29 (Anthropic re-read 2026-10-17):
#   Anthropic  platform.claude.com/docs/en/about-claude/pricing (cache hit = "cache hits and refreshes";
#              0.025x input on Fable 5.1, 0.05x on Opus 5.5 and Sonnet 5.5, 0.1x on the rest)
#              claude-haiku-5-5 read 2026-10-17: the rate for prompts up to 100,000 tokens (LONG_PROMPT below).
#   OpenAI     developers.openai.com/api/docs/pricing (standard, short context)
#   Google     ai.google.dev/gemini-api/docs/pricing (paid standard; $0.75/$3.75 until 2026-12-31, doubling from 2027-01-01)
#   xAI        docs.x.ai/developers/models (prompts under 200k)
#   pi models  OpenRouter's public model list, openrouter.ai/api/v1/models (what the pi runtime is billed)
#              mercury-3 (2026-10-29): $0.20 in / $0.70 out, the list price the Gateway's meeting row marks up; cache
#              reads at 10% of input, as the Gateway prices it
PRICES_AS_OF = "2026-09-29"
class _Priced(dict):
    """A price table whose lookups also find the Gateway's ids (`gateway_tiers`): a tier, a pinned version, an old id."""

    def __init__(self, rows, find):
        super().__init__(rows)
        self.find = find

    def get(self, key, default=None):
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        found = self.find(key) if isinstance(key, str) and key.startswith(gateway_tiers.PREFIX) else None
        return default if found is None else found

    def __missing__(self, key):
        found = self.get(key)
        if found is None:
            raise KeyError(key)
        return found

    def __contains__(self, key):
        return self.get(key) is not None


PRICES = _Priced({
    "claude-fable-5-1": (10.0, 0.25, 50.0),
    "claude-fable-5": (10.0, 1.0, 50.0),
    "claude-opus-5-5": (4.0, 0.20, 20.0),
    "claude-opus-5": (5.0, 0.50, 25.0),
    "claude-opus-4-8": (5.0, 0.50, 25.0),
    "claude-sonnet-5-5": (2.0, 0.10, 10.0),
    "claude-sonnet-5": (2.0, 0.20, 10.0),
    "claude-haiku-5-5": (0.10, 0.01, 0.50),
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
    "mercury-3": (0.20, 0.02, 0.70),
}, gateway_tiers.price_for)    # the Gateway's ids are priced in backend/gateway_tiers.py (cached input at 10% of input)


# Models whose every rate goes up for a request with a long prompt: (prompt tokens it may have, the rates
# above that) in the same units as PRICES. A request's prompt is all its input, cache reads and cache writes
# included, and each request is priced on its own. Anthropic's pricing page ("Long context pricing"), read
# 2026-10-17: only Claude Haiku 5.5; Claude 4.6 and later models bill their whole context at the standard rate.
# The Gateway's (Low tiers, Cheap 1.1) come from its own price (backend/gateway_tiers.py), not Anthropic's page.
LONG_PROMPT_AS_OF = "2026-10-17"
LONG_PROMPT = _Priced({
    "claude-haiku-5-5": (100_000, (0.50, 0.05, 2.50)),
}, gateway_tiers.long_prompt_for)


def price_of(model):
    """(input, cached input, output) USD per 1M tokens for a model id, or None when it has no list price."""
    return PRICES.get(str(model or "").strip().lower())


def estimate(model, input_tokens, cached_tokens, output_tokens, requests=None):
    """(USD, may be low) for one run's tokens at list price; USD is None for a model with no price.
    `input_tokens` are the uncached ones; cached tokens are billed at the cached rate. On a model with a
    LONG_PROMPT rate each of `requests` ((input, cached, output) per model request, as the runner counted
    them) pays its own tier. Tokens no request accounts for pay the lower rate, and the estimate may be low."""
    price = price_of(model)
    if not price:
        return None, False
    totals = [int(input_tokens or 0), int(cached_tokens or 0), int(output_tokens or 0)]
    long = LONG_PROMPT.get(str(model or "").strip().lower())
    if not long:
        return sum(n * rate for n, rate in zip(totals, price)) / 1_000_000, False
    limit, high = long
    cost, rest = 0.0, totals
    for request in requests or ():
        counts = [int(n or 0) for n in request[:3]]      # a fourth, whose request it was, is not a count
        cost += sum(n * rate for n, rate in zip(counts, high if counts[0] + counts[1] > limit else price))
        rest = [r - n for r, n in zip(rest, counts)]
    rest = [max(r, 0) for r in rest]
    return (cost + sum(n * rate for n, rate in zip(rest, price))) / 1_000_000, any(rest)


def estimate_cost(model, input_tokens, cached_tokens, output_tokens, requests=None):
    """The list-price cost in USD of one run's tokens (`estimate`), or None for a model with no price."""
    return estimate(model, input_tokens, cached_tokens, output_tokens, requests)[0]


class ProviderError(Problem):
    """A choice that cannot be honoured; `detail` says what to do about it."""

    def __init__(self, code, detail, status=422):
        super().__init__(code, detail, status)


class NoProvider(ProviderError):
    def __init__(self, what="This bot"):
        super().__init__(
            "no_provider", f"{what} has no runtime or model and the company has no default. Choose "
            "which AI providers you use and a default model in Settings > Computers "
            "(or run `tico env create ... --providers ...` / set TICO_ENABLED_PROVIDERS).", 409)


# ----------------------------------------------------------------------------- catalog
def runtime_provider(runtime):
    row = PROVIDER_BY_RUNTIME.get(str(runtime or ""))
    return row["id"] if row else ""


def serves(provider, row):
    """Whether enabling `provider` lets bots run this catalog model: the model's own provider, or
    the catch-all provider of the model's runtime (OpenRouter serves every pi model)."""
    named = PROVIDER_BY_ID.get(provider) or {}
    # OpenRouter serves the pi runtime's models, but never the Gateway's: those need the gateway key.
    gateway = (PROVIDER_BY_ID.get(row["provider"]) or {}).get("gateway")
    return bool(row["provider"] == provider
                or (named.get("catch_all") and named["runtime"] == row["runtime"] and not gateway))


def provider_models(provider, *, current=True, meeting=False):
    """The catalog models a provider serves. A bot's picks leave out the meeting models unless `meeting` asks for them."""
    return [row for row in MODEL_CATALOG
            if serves(provider, row) and not (current and row.get("deprecated"))
            and bool(row.get("meeting")) == meeting]


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


def complete_choice(enabled, runtime="", model="", kept=""):
    """Validate an owner's choice and fill what they left out.

    A model implies its runtime; a runtime alone means that provider's recommended model; nothing
    at all means the first enabled provider's recommended model. Returns (runtime, model). `kept` is the
    model already saved: it may stay after it retires, so a save about something else never forces a move.
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
        if row.get("deprecated") and model != kept:
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
            "revision": int(stored.get("revision") or 0), "updated_at": str(stored.get("updated_at") or ""),
            "updated_by": str(stored.get("updated_by") or ""), "source": str(stored.get("source") or ""),
            # On unless the owner turned it off: a bot out of quota runs on another provider (default_fallbacks).
            "auto_fallback": stored.get("auto_fallback") is not False,
            "check_models": [str(entry) for entry in stored.get("check_models") or [] if isinstance(entry, str)],
            # The models choice (backend/models_preset.py); '' is the choice as it was before presets.
            "preset": models_preset.normalize(stored.get("preset")),
            # How hard the Tico AI Gateway thinks on a decision (clients/judge.py DECIDE_LEVELS); Low is today's.
            "decide_level": J.normalize_level(stored.get("decide_level")),
            # The model live-meeting replies run on (backend/live_meetings.py): None is unset (the Gateway's default
            # while the Gateway is on), '' the bot's own model, else a model id.
            "meeting_model": None if stored.get("meeting_model") is None else str(stored.get("meeting_model") or "")}


def env_seed(settings):
    """What the environment asks for on first boot; empty when it asks for nothing."""
    enabled = normalize_enabled(getattr(settings, "enabled_providers", ()))
    runtime, model = getattr(settings, "default_runtime", ""), getattr(settings, "default_model", "")
    preset = models_preset.normalize(getattr(settings, "models_preset", ""))
    if not enabled and not (runtime or model) and preset in (models_preset.RECOMMENDED, "subscriptions"):
        # The installer's subscription choices mean Claude and ChatGPT (scripts/install.sh --recommended).
        enabled = ["anthropic", "openai"]
    if not enabled and (runtime or model):
        # A default without providers still means its own provider.
        row = MODEL_BY_ID.get(model) or PROVIDER_BY_RUNTIME.get(runtime) and {"provider": runtime_provider(runtime)}
        enabled = [row["provider"]] if row and row.get("provider") else []
    if not enabled:
        return {"enabled": [], "runtime": "", "model": "", "preset": preset} if preset else None
    runtime, model = complete_choice(enabled, runtime, model)
    return {"enabled": enabled, "runtime": runtime, "model": model, **({"preset": preset} if preset else {})}


# The model live-meeting replies run on when the company has not chosen one and the Tico AI Gateway is on.
MEETING_MODEL = "tico-meeting-1.0"
# The same model through OpenRouter, for a computer that cannot run the Gateway's (a Mac runner).
OPENROUTER_MEETING_MODEL = "mercury-3"


def with_gateway(record, connected):
    """`record` with the Tico AI Gateway's provider on exactly while its connection has a key: it is never the owner's
    to tick, so it follows Settings > Computers > Tico AI Gateway (backend/gateway_connection.py). An unset meeting
    model is the Gateway's meeting model while the Gateway is on, else the bot's own model ('')."""
    enabled = [name for name in record["enabled"] if not PROVIDER_BY_ID[name].get("gateway")]
    if connected:
        enabled += [row["id"] for row in PROVIDERS if row.get("gateway")]
    meeting = record.get("meeting_model")
    meeting = (MEETING_MODEL if connected else "") if meeting is None else meeting
    return {**record, "enabled": enabled, "meeting_model": meeting}


def meeting_choice(record, model):
    """A live-meeting model as a request names it: '' for the bot's own model, else a current model one of the enabled
    providers can run. Raises ProviderError."""
    model = str(model or "").strip()
    if not model:
        return ""
    row = MODEL_BY_ID.get(model)
    if not row or not row["provider"] or row.get("deprecated") or not runtime_enabled(row["runtime"], record["enabled"], row):
        raise ProviderError("meeting_model", "Choose one of the models this company can run for meetings", 422)
    return model


def load(c, settings=None):
    """The company's choice. Before the owner has saved one, the environment's seed stands in
    (`source: environment`), so a deployment that sets TICO_ENABLED_PROVIDERS works at once."""
    from . import gateway_connection, read_cache
    # The company's choice is the team's state whichever bot asks: an error reading it fails the claim for every bot,
    # so it is marked team-wide (shared_check) rather than passing one bot over.
    with shared_check("team"):
        row = read_cache.metadata(c, KEY)
        if row:
            record = _record(row[0])
        else:
            seed = env_seed(settings) if settings is not None else None
            record = _record({**seed, "source": "environment"} if seed else {})
        return with_gateway(record, gateway_connection.connected(c, settings))


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


def save(c, actor, body, now, settings=None):
    """Replace the choice. `body` carries enabled, runtime, model and the revision it was read at. `settings` is read
    the way `load` reads it, so the revision and the Gateway's provider match what the page read."""
    before = load(c, settings)
    if before["revision"] != int(body.get("expected_revision") or 0):
        raise ProviderError("conflict", "The provider choice changed since you opened it; reload and try again", 409)
    gateway = [name for name in before["enabled"] if PROVIDER_BY_ID[name].get("gateway")]
    enabled = with_gateway({"enabled": normalize_enabled(body.get("enabled"))}, bool(gateway))["enabled"]
    runtime, model = complete_choice(enabled, body.get("runtime"), body.get("model"), kept=before["model"])
    auto, checks = body.get("auto_fallback"), body.get("check_models")
    preset = before["preset"] if body.get("preset") is None else models_preset.normalize(body.get("preset"))
    if body.get("preset") not in (None, "") and not preset:
        raise ProviderError("preset", "Choose one of the four models choices", 422)
    level = body.get("decide_level")
    if level not in (None, "") and level not in J.DECIDE_LEVELS:
        raise ProviderError("decide_level", "Choose Low, Medium or High for decisions", 422)
    # Kept as saved (None while unset, so the Gateway's default follows the connection) unless the owner names one.
    from . import read_cache
    saved = read_cache.metadata(c, KEY)
    kept = _record(saved[0])["meeting_model"] if saved else None
    meeting = kept if body.get("meeting_model") is None else meeting_choice({"enabled": enabled}, body.get("meeting_model"))
    after = {"enabled": enabled, "runtime": runtime, "model": model, "revision": before["revision"] + 1,
             "updated_at": now, "updated_by": actor, "source": "owner",
             "auto_fallback": before["auto_fallback"] if auto is None else bool(auto),
             "check_models": (before["check_models"] if checks is None
                              else normalize_check_models(checks, {"enabled": enabled, "runtime": runtime,
                                                                   "model": model}, before["check_models"])),
             "preset": preset,
             "decide_level": before["decide_level"] if level is None else J.normalize_level(level),
             "meeting_model": before["meeting_model"] if body.get("meeting_model") is None else meeting}
    # The Gateway's provider is never stored: it follows the connection (`with_gateway`).
    stored = {**after, "enabled": [name for name in enabled if name not in gateway], "meeting_model": meeting}
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, json.dumps(stored, sort_keys=True)))
    return before, after


def view(record):
    """What a client renders: every provider with whether it is on, and the current default."""
    return {"configured": configured(record), "enabled": record["enabled"],
            "default": {"runtime": record["runtime"], "model": record["model"]},
            "revision": record["revision"], "source": record["source"],
            "updated_at": record["updated_at"], "updated_by": record["updated_by"],
            "auto_fallback": record["auto_fallback"],
            "check_models": record["check_models"], "check_order": check_order(record),
            "preset": record.get("preset", ""), "presets": list(models_preset.PRESETS),
            "decide_level": record.get("decide_level", J.DECIDE_LEVELS[0]), "decide_levels": list(J.DECIDE_LEVELS),
            "meeting_model": record["meeting_model"],
            "providers": [{**row, "enabled": row["id"] in record["enabled"],
                           "recommended": (recommended(row["id"]) or {}).get("id", "")}
                          for row in PROVIDERS]}


def gateway_model(model):
    """Whether `model` is served by a Gateway provider (the Tico models), which only runs with the gateway key."""
    provider = (MODEL_BY_ID.get(str(model or "")) or {}).get("provider")
    return bool(provider and (PROVIDER_BY_ID.get(provider) or {}).get("gateway"))


def default_fallbacks(record):
    """What a runner may run a turn on when the bot's own subscription hits its limit and the bot names no
    fallback: one model per enabled provider's runtime, in the owner's order (the company default on its own
    runtime, else the provider's recommended model). The runner takes the first other runtime signed in on
    its computer (runner-rs/src/fallback.rs). Empty when the owner turned it off."""
    if not record["auto_fallback"]:
        return []
    rows, seen = [], set()
    for name in record["enabled"]:
        if PROVIDER_BY_ID[name].get("gateway"):
            continue                # a fallback the bot did not name never spends the company's gateway key
        runtime = PROVIDER_BY_ID[name]["runtime"]
        # Nor does a company default on a Gateway model reach it through another provider on the same runtime.
        own = record["model"] if record["runtime"] == runtime and not gateway_model(record["model"]) else ""
        model = own or (recommended(name) or {}).get("id")
        if runtime in seen or not model:
            continue
        seen.add(runtime)
        rows.append({"runtime": runtime, "harness": runtime, "model": model})
    # Except under the two choices that include the Gateway: it goes last, after every signed-in provider
    # (backend/models_preset.py). A runner without a relay for the turn leaves it out (gateway_relay.rs admit).
    gateway = models_preset.fallback_row(record)
    if gateway:
        rows.append(gateway)
    return rows


# ----------------------------------------------------------------------------- check model
def _check_row(choice, record):
    """One check-model entry, `runtime`, `runtime:model` or a model id, as {runtime, model, provider}. A runtime
    alone runs the company default when that is on the same runtime, else its provider's recommended model, so it
    follows the catalog rather than pinning the model current when it was saved."""
    text = str(choice or "").strip()
    runtime, model = text.split(":", 1) if ":" in text else ("", text) if text in MODEL_BY_ID else (text, "")
    runtime, model = runtime.strip(), model.strip()
    if not runtime and not model or ":" in text and not (runtime and model):
        raise ProviderError("check_model", f"Check model {text!r}: name a runtime such as grok, or runtime:model", 422)
    if (runtime and not model and record["runtime"] == runtime and record["model"]
            and not (MODEL_BY_ID.get(record["model"]) or {}).get("deprecated")):
        model = record["model"]
    named = MODEL_BY_ID.get(model) or {}
    if (runtime in PROVIDER_BY_RUNTIME or named.get("provider")) and not runtime_enabled(
            runtime or named["runtime"], record["enabled"], named or None):
        raise ProviderError("check_model", f"Check model {text!r}: its provider "
                            f"({named.get('provider') or runtime_provider(runtime)}) is not enabled", 422)
    try:
        runtime, model = complete_choice(record["enabled"], runtime, model)
    except ProviderError as error:
        raise ProviderError("check_model", f"Check model {text!r}: {error.detail}", 422) from None
    if not runtime:
        raise ProviderError("check_model", "Enable at least one AI provider before choosing a check model", 422)
    return {"runtime": runtime, "model": model, "provider": MODEL_BY_ID[model]["provider"]}


def normalize_check_models(value, record, saved=()):
    """The owner's check-model order as typed, from a list or a comma-separated string. A new entry must be one the
    enabled providers can run; one already saved is kept as it is, so turning a provider off or a model retiring
    never makes an unrelated save fail (check_order skips it until it works again)."""
    items = value.split(",") if isinstance(value, str) else list(value or ())
    entries = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in entries:
            continue
        if text not in saved:
            _check_row(text, record)
        entries.append(text)
    return entries


def check_order(record):
    """The models a review or check subtask may run on, best first: the owner's check-model order, then one model
    per other enabled provider in the owner's order (the company default for its own provider). An entry whose
    provider is off or whose model is retired is skipped."""
    rows = []
    for entry in record["check_models"]:
        try:
            rows.append(_check_row(entry, record))
        except ProviderError:
            continue
    for name in record["enabled"]:
        default = MODEL_BY_ID.get(record["model"])
        row = (default if default and default["provider"] == name and not default.get("deprecated")
               else recommended(name))
        if row and row.get("provider"):
            rows.append({"runtime": row["runtime"], "model": row["id"], "provider": row["provider"]})
    order, seen = [], set()
    for row in rows:
        if row["model"] not in seen and not MODEL_BY_ID[row["model"]].get("deprecated"):
            seen.add(row["model"])
            order.append(row)
    return order


def author_provider(author):
    """The model maker behind `author`: a provider id, a model id or a runtime; '' when it names none."""
    author = str(author or "").strip()
    if author in PROVIDER_BY_ID:
        return author
    row = MODEL_BY_ID.get(author)
    if row:
        return row["provider"] or ""
    return runtime_provider(author)


def check_choice(record, author, available=None):
    """The check model for work written on `author` (a provider, model or runtime): the first in check_order on a
    runtime in `available` (the signed-in runtimes) when that is given, from another provider when one qualifies. A
    second opinion from another provider is preferred; with none, the author's own provider still checks the work."""
    own = author_provider(author)
    rows = [row for row in check_order(record) if available is None or row["runtime"] in available]
    for row in [r for r in rows if r["provider"] != own] + [r for r in rows if r["provider"] == own]:
        return row
    raise ProviderError("check_model", "No check model can run: enable or sign in an AI provider in "
                        "Settings > Computers, or name one with --check-model", 409)


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


def default_runtime(record):
    """The one runtime a computer with no bots installs and is signed in on first: the company's default, else the
    first of its providers' runtimes by name. Empty before a provider is chosen."""
    return next(iter(sorted(wanted_runtimes(record))), "")


def runtimes_run_on(c, settings):
    """{runner id: runtimes} the bots on each computer run on, including task models and fallbacks: what that computer
    installs for them (runner-rs/src/commands.rs harness_runtimes), so the owner cannot turn one off there."""
    from .shared_bots import follow
    from .task_models import of as task_model_of
    record, found = load(c, settings), {}
    for row in c.execute("SELECT a.runner_id, a.bot, bc.config FROM assignments a "
                         "LEFT JOIN bot_config bc ON bc.bot=a.bot"):
        try:
            config = follow(c, row["bot"], json.loads(row["config"] or "{}"))
        except ValueError:
            config = {}
        try:
            runtime = resolve(record, config)[0]
        except ProviderError:
            runtime = ""
        fallback = normalize_fallback(config.get("fallback")) or {}
        task_model = task_model_of(config) or {}
        found.setdefault(row["runner_id"], set()).update(
            {runtime, fallback.get("runtime"), task_model.get("runtime")} - {None, ""})
    return found


def runtimes_needed(c, settings):
    """{runner id: runtimes} each computer must have: the company's, plus those of the active bots
    assigned to it. What a computer has beyond this is not a problem, so a missing one is not red."""
    record = load(c, settings)
    wanted = wanted_runtimes(record)
    need = {}
    for row in c.execute("SELECT a.runner_id, bc.config FROM assignments a JOIN bots b ON b.slug=a.bot "
                         "AND b.state='active' LEFT JOIN bot_config bc ON bc.bot=a.bot"):
        try:
            config = json.loads(row["config"] or "{}")
        except ValueError:
            config = {}
        runtime = bot_choice(c, settings, config)[0]
        need.setdefault(row["runner_id"], set()).update({runtime} if runtime else ())
    return wanted, need
