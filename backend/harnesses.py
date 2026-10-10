"""Harness catalog: the runner hosts a bot can run on, and the models each one accepts.

`runtime` stays on bot rows for compatibility. It is derived from harness
(`antigravity` → `gemini`). Older configs that omit `harness` read as `harness = runtime`.
"""

HARNESS_CATALOG = (
    {"id": "codex", "label": "Codex (OpenAI)", "runtime": "codex"},
    {"id": "claude", "label": "Claude Code (Anthropic)", "runtime": "claude"},
    {"id": "gemini", "label": "gemini", "runtime": "gemini"},
    {"id": "antigravity", "label": "antigravity", "runtime": "gemini"},
    {"id": "grok", "label": "Grok Build (xAI)", "runtime": "grok"},
    {"id": "pi", "label": "pi (openrouter)", "runtime": "pi"},
    {"id": "cursor", "label": "Cursor Agent", "runtime": "cursor"},
    # An external agent: it runs somewhere Tico does not manage, holds its own model and
    # provider, and reaches the hub with a bot credential over MCP or the `hub` CLI. No
    # runner claims work for it; it reads its inbox on its own schedule (docs/hermes-agents.md).
    {"id": "hermes", "label": "hermes", "runtime": "hermes", "external": True},
    # OpenClaw is the same kind of agent (docs/openclaw-agents.md): its own model, a standing credential,
    # a heartbeat, no computer.
    {"id": "openclaw", "label": "openclaw", "runtime": "openclaw", "external": True},
    # Bots on their maker's platform, synced in by the agent itself with its person's sign-in;
    # they never hold a credential of their own (backend/external_sync.py, docs/external-agent-sync.md).
    # A Grok Bot is one of many on a Grok account; Dots (ChatGPT) and Muse (Meta) are one always-on agent each.
    {"id": "grokbot", "label": "grok bot", "runtime": "grokbot", "external": True},
    {"id": "dots", "label": "dots", "runtime": "dots", "external": True},
    {"id": "muse", "label": "muse", "runtime": "muse", "external": True},
)
HARNESS_BY_ID = {row["id"]: row for row in HARNESS_CATALOG}
EXTERNAL_HARNESSES = tuple(row["id"] for row in HARNESS_CATALOG if row.get("external"))


# Only these hosts emit a `tool` event per tool call (runner/hosts/gemini.py and
# antigravity.py). claude, codex and grok stream text and never name a tool, so for those an
# attempt with no `tool` event means the hub has no evidence either way -- not that the turn
# kept its hands in its pockets.
TOOL_REPORTING_HARNESSES = ("gemini", "antigravity")


def reports_tool_calls(config, runtime=None):
    """True when this bot's host tells the hub about each tool call, so that the absence of one
    is a fact worth acting on."""
    return resolve_harness(config, runtime) in TOOL_REPORTING_HARNESSES


def is_external(config, runtime=None):
    """True when this bot is run by an external agent rather than a registered computer."""
    return resolve_harness(config, runtime) in EXTERNAL_HARNESSES


def harnesses_for_runtime(runtime):
    if runtime == "gemini":
        return ("gemini", "antigravity")
    return (runtime,) if runtime in HARNESS_BY_ID else ()


def runtime_of(harness):
    row = HARNESS_BY_ID.get(harness)
    return row["runtime"] if row else harness


def resolve_harness(config, runtime=None):
    """Harness from config, or runtime when older rows omitted it."""
    value = str((config or {}).get("harness") or "").strip()
    if value:
        return value
    return str(runtime or (config or {}).get("runtime") or "").strip()


def attach_harnesses(model):
    return {**model, "harnesses": harnesses_for_runtime(model["runtime"])}


def uses_gemini_cli(config):
    """True when this bot's primary or fallback harness is the Gemini CLI (API key)."""
    if resolve_harness(config) == "gemini":
        return True
    fallback = (config or {}).get("fallback")
    return isinstance(fallback, dict) and str(fallback.get("harness") or "") == "gemini"


def normalize_fallback(value):
    """A validated fallback dict, or None when unset/empty."""
    if not value or not isinstance(value, dict):
        return None
    harness = str(value.get("harness") or "").strip()
    model = str(value.get("model") or "").strip()
    if not harness or not model:
        return None
    effort = str(value.get("reasoning_effort") or value.get("effort") or "").strip()
    return {"harness": harness, "model": model, "reasoning_effort": effort,
            "runtime": runtime_of(harness)}


def backfill_config(config, slug=None, runtime=None):
    """Set missing `harness` from runtime; seed Tico's Gemini CLI fallback. Returns (config, changed)."""
    config = dict(config or {})
    changed = False
    harness = resolve_harness(config, runtime)
    if harness and config.get("harness") != harness:
        config["harness"] = harness
        changed = True
    if harness and not config.get("runtime"):
        config["runtime"] = runtime_of(harness)
        changed = True
    if slug == "coo" and resolve_harness(config) == "antigravity" and "fallback" not in config:
        from .providers import recommended
        config["fallback"] = {
            "harness": "gemini",
            "model": config.get("model") or recommended("google")["id"],
            "reasoning_effort": config.get("reasoning_effort") or config.get("effort") or "low",
        }
        changed = True
    return config, changed
