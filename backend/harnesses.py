"""Harness catalog: the runner hosts a bot can run on, and the models each one accepts.

`runtime` stays on bot rows for compatibility. It is derived from harness
(`antigravity` → `gemini`). Older configs that omit `harness` read as `harness = runtime`.
"""

from clients import harness_manifests as manifests

# `offered: False`: a harness the team does not offer for new picks (Settings' model picker leaves it out); a bot already
# on one keeps it.
HARNESS_CATALOG = (
    {"id": "codex", "label": "Codex (OpenAI)", "runtime": "codex"},
    {"id": "claude", "label": "Claude Code (Anthropic)", "runtime": "claude"},
    {"id": "gemini", "label": "gemini", "runtime": "gemini", "offered": False},
    {"id": "antigravity", "label": "antigravity", "runtime": "gemini"},
    {"id": "grok", "label": "Grok Build (xAI)", "runtime": "grok"},
    {"id": "pi", "label": "pi (openrouter)", "runtime": "pi"},
    {"id": "cursor", "label": "Cursor Agent", "runtime": "cursor", "offered": False},
    {"id": "musecode", "label": "Muse Code (Meta)", "runtime": "musecode"},
    # An external agent: it runs somewhere Tico does not manage, holds its own model and
    # provider, and reaches the hub with a bot credential over MCP or the `tico` CLI. No
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
# Each CLI's short name and capabilities come from its manifest (runner-rs/harnesses/*.toml); an external agent has
# none, so every capability reads as unknown for it.
HARNESS_CATALOG = tuple(
    {
        **row,
        "name": (manifests.get(row["id"]) or {}).get("name", row["label"]),
        "capabilities": dict(manifests.caps(row["id"])),
    }
    for row in HARNESS_CATALOG
)
# External agents a person syncs in (backend/external_sync.py): they read only their own chat, never a task.
SYNCED_HARNESSES = ("grokbot", "dots", "muse")
SYNCED_SQL = ",".join(f"'{h}'" for h in SYNCED_HARNESSES)
EXTERNAL_HARNESSES = tuple(row["id"] for row in HARNESS_CATALOG if row.get("external"))


def reports_tool_calls(config, runtime=None):
    """True when this bot's host tells the hub about each tool call (`tool_events`), so that the absence of one
    is a fact worth acting on. A host that streams text and never names a tool leaves no evidence either way."""
    return bool(manifests.caps(resolve_harness(config, runtime)).get("tool_events"))


def reports_skill_uses(config, runtime=None):
    """True when this bot's host reports each skill its model uses (`skill_events`), so that a bot with no use is a
    bot that used none. A host that reports only some (Codex: the skills its input names) proves nothing by silence."""
    return bool(manifests.caps(resolve_harness(config, runtime)).get("skill_events"))


def steers(config, runtime=None):
    """True when this bot's host writes a message sent mid-turn into that turn (`steer`); otherwise the message
    waits for the next turn. The runner also needs a Claude Code that echoes its input, which the hub cannot see."""
    return bool(manifests.caps(resolve_harness(config, runtime)).get("steer"))


def is_external(config, runtime=None):
    """True when this bot is run by an external agent rather than a registered computer."""
    return resolve_harness(config, runtime) in EXTERNAL_HARNESSES


def harnesses_for_runtime(runtime):
    """Every harness that runs `runtime`: the CLI first, then its variants (Gemini's Antigravity)."""
    return tuple(row["id"] for row in HARNESS_CATALOG if row["runtime"] == runtime)


def runtime_of(harness):
    row = HARNESS_BY_ID.get(harness)
    return row["runtime"] if row else harness


def harness_caps(harness, runtime=None):
    """The capabilities of `harness`, else of `runtime`'s own CLI; empty when neither has a manifest."""
    return manifests.caps(harness) or manifests.caps(runtime)


def bot_keyed(runtime):
    """Whether `runtime` signs each bot in with that bot's own key (`api_keys = "bot"`: pi and the Gemini CLI). A
    provider's refusal of such a key is that bot's alone, as the runner's signin.bot_keyed decides it."""
    return bool(runtime) and any(
        row["runtime"] == runtime and row["capabilities"].get("api_keys") == "bot" for row in HARNESS_CATALOG
    )


def bot_key(config, runtime=None):
    """The API key the bot must hold as its own Credential: the first key of its primary or fallback harness whose
    keys belong to each bot (`api_keys = "bot"`, the Gemini CLI and pi), else ''. With `runtime`, only the
    harnesses that run it count."""
    config = config or {}
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    for harness in (resolve_harness(config), str(fallback.get("harness") or "")):
        if runtime and runtime_of(harness) != runtime:
            continue
        caps = manifests.caps(harness)
        if caps.get("api_keys") == "bot" and caps.get("api_key_env"):
            return caps["api_key_env"][0]
    return ""


def resolve_harness(config, runtime=None):
    """Harness from config, or runtime when older rows omitted it."""
    value = str((config or {}).get("harness") or "").strip()
    if value:
        return value
    return str(runtime or (config or {}).get("runtime") or "").strip()


def attach_harnesses(model):
    harnesses = tuple(model["harnesses"]) if "harnesses" in model else harnesses_for_runtime(model["runtime"])
    return {**model, "harnesses": harnesses}


def normalize_fallback(value):
    """A validated fallback dict, or None when unset/empty."""
    if not value or not isinstance(value, dict):
        return None
    harness = str(value.get("harness") or "").strip()
    model = str(value.get("model") or "").strip()
    if not harness or not model:
        return None
    effort = str(value.get("reasoning_effort") or value.get("effort") or "").strip()
    out = {"harness": harness, "model": model, "reasoning_effort": effort, "runtime": runtime_of(harness)}
    compact = value.get("compact_at")
    if isinstance(compact, int) and not isinstance(compact, bool) and compact > 0:
        # Where it compacts while on the fallback; absent is "Same as bot".
        out["compact_at"] = compact
    return out


# Claude Code's built-in output styles, its `outputStyle` setting (backend/models.py OutputStyle); none saved is its own
# Default. Codex's `model_verbosity` levels; none saved is the model's own. A bot keeps either on any harness, and the
# runner passes each only to its own harness (runner-rs/src/hosts/claude.rs and codex.rs).
OUTPUT_STYLES = ("Concise", "Explanatory", "Learning", "Proactive")
VERBOSITIES = ("low", "medium", "high")


def output_style(config):
    """The bot's saved Claude Code output style, or None for Default (nothing saved, or a value not offered)."""
    value = (config or {}).get("output_style")
    return value if value in OUTPUT_STYLES else None


def verbosity(config):
    """The bot's saved Codex verbosity, or None for the model's own."""
    value = (config or {}).get("verbosity")
    return value if value in VERBOSITIES else None
