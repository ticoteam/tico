"""The harness manifests (runner/harnesses/*.toml) as the server and the clients read them: one row per harness,
keyed by the id bot configs use (`harness`, which is also the server's id), with its capabilities filled in.

The runner installs from the same files (runner-rs/src/harness_tools.rs); this module only reads what Tico does
differently per CLI, so code asks a capability instead of comparing CLI names. docs/harnesses.md describes each
capability and what an absent one means. Loading fails loudly on a bad `[capabilities]` table, naming the file.
"""

import tomllib
from functools import lru_cache
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[1] / "runner" / "harnesses"

# Every key `[capabilities]` may hold, with its type and the value an absent key means.
FIELDS = {
    "terminal_login": (list, []),
    "api_keys": (str, ""),
    "api_key_login": (list, []),
    "api_key_check": (list, []),
    "api_key_in_turn": (bool, False),
    "subscription_marker": (str, ""),
    "subscription_profiles": (bool, False),
    "profile_home": (str, ""),
    "profile_dirs": (list, []),
    "profile_home_overrides": (list, []),
    "own_config_home": (bool, False),
    "weekly_usage": (str, ""),
    "account": (str, ""),
    "tool_events": (bool, False),
    "mcp_transports": (list, []),
    "mcp_unsupported": (str, ""),
    "resume_after_interrupt": (bool, True),
    "steer": (bool, False),
    "persistent_session": (bool, False),
    "goal_runs_across_turns": (bool, False),
    "serial_cold_start": (bool, False),
    "one_turn_per_login": (bool, False),
    "isolated_user": (bool, False),
    "compaction": (bool, False),
    "compaction_events": (bool, False),
    "resume_command": (str, ""),
    "model_list": (str, ""),
    "model_list_args": (list, []),
    "readiness": (bool, True),
    "repo_permission_lint": (bool, False),
    "repo_commands": (bool, False),
    "sign_in_status": (dict, {}),
}
CHOICES = {"api_keys": ("", "computer", "bot"), "weekly_usage": ("", "refresh", "turns", "manual"),
           "account": ("", "status-json", "app-server"),
           "model_list": ("", "host", "command")}
CHECKS = ("none", "command", "json", "file", "bot-key")
TRANSPORTS = ("http", "sse")


class ManifestError(ValueError):
    pass


def capabilities(raw, where):
    """A `[capabilities]` table checked and filled with the defaults."""
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: [capabilities] must be a table")
    unknown = sorted(set(raw) - set(FIELDS))
    if unknown:
        raise ManifestError(f"{where}: unknown capability {', '.join(unknown)}")
    out = {}
    for key, (kind, default) in FIELDS.items():
        value = raw.get(key, default)
        if not isinstance(value, kind) or kind is list and not all(isinstance(v, str) and v for v in value):
            raise ManifestError(f"{where}: capabilities.{key} must be a {kind.__name__}")
        if value not in CHOICES.get(key, (value,)):
            raise ManifestError(f"{where}: capabilities.{key} is one of {', '.join(c for c in CHOICES[key] if c)}")
        out[key] = list(value) if kind is list else dict(value) if kind is dict else value
    if any(t not in TRANSPORTS for t in out["mcp_transports"]):
        raise ManifestError(f"{where}: capabilities.mcp_transports are http and sse")
    if out["sign_in_status"].get("check", "none") not in CHECKS:
        raise ManifestError(f"{where}: capabilities.sign_in_status.check is one of {', '.join(CHECKS)}")
    return out


def _row(harness, runtime, data, where, auth=None, login=None):
    caps = capabilities(data.get("capabilities", {}), where)
    executable = str(data.get("executable") or "")
    auth = auth or {}
    keys = list(auth.get("api_key_env") or []) if caps["api_keys"] or caps["api_key_in_turn"] else []
    relay = list((login or {}).get("command") or [])
    # What the API says: the manifest's own values plus the sign-in facts [login] and [auth] already hold.
    public = {**caps, "sign_in_status": caps["sign_in_status"].get("check", "none"),
              "browser_sign_in": bool(relay), "paste_code": bool((login or {}).get("paste_code")),
              # Sign out and Switch account from the app (backend/model_login.py): the CLI's own logout command.
              "sign_out": bool(relay and (login or {}).get("logout")),
              # Connect in Settings > Models: a command that signs a plan in for a company subscription.
              "subscription_connect": bool(relay and (login or {}).get("connect")),
              # Platforms where the login is in the system keychain, so Switch account cannot sign in on the side.
              "switch_unavailable_on": list((login or {}).get("keychain") or []) if relay and (login or {}).get("logout") else [],
              "terminal_login": " ".join([executable, *caps["terminal_login"]]) if caps["terminal_login"] else "",
              "container_login": " ".join([executable, *(relay or caps["terminal_login"])]) if relay or caps["terminal_login"] else "",
              "api_key_env": keys, "api_key_login": bool(caps["api_key_login"]),
              "api_key_check": bool(caps["api_key_check"]),
              "resume_command": caps["resume_command"] or f"{executable} --resume"}
    for private in ("profile_dirs", "model_list_args"):
        public.pop(private)
    return {"id": harness, "runtime": runtime, "name": str(data.get("name") or harness), "host": str(data.get("host") or ""),
            "executable": executable, "capabilities": public, "sign_in_status": caps["sign_in_status"],
            "model_list_args": caps["model_list_args"]}


def parse(data, source="manifest"):
    """The rows one manifest file gives: the harness itself, keyed by its `runtime`, and each `[variants.<id>]`."""
    where = f"{source} ({data.get('id', '?')})"
    runtime = data.get("runtime") or data.get("host")
    if not isinstance(runtime, str) or not runtime:
        raise ManifestError(f"{where}: runtime names what bot configs call this harness")
    rows = [_row(runtime, runtime, data, where, data.get("auth"), data.get("login"))]
    rows[0]["manifest"] = data.get("id")
    for harness, variant in (data.get("variants") or {}).items():
        if not isinstance(variant, dict):
            raise ManifestError(f"{where}: [variants.{harness}] must be a table")
        row = _row(harness, runtime, variant, f"{where} variant {harness}")
        row["manifest"] = data.get("id")
        rows.append(row)
    return rows


@lru_cache(maxsize=1)
def load(directory=DIRECTORY):
    """Every harness by its id. An empty directory (a client without the runner tree) gives no harnesses."""
    found = {}
    for path in sorted(Path(directory).glob("*.toml")):
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ManifestError(f"{path.name}: {exc}")
        for row in parse(data, path.name):
            if row["id"] in found:
                raise ManifestError(f"{path.name}: harness {row['id']} is already claimed by {found[row['id']]['manifest']}")
            found[row["id"]] = row
    return found


def get(harness):
    """One harness's row, or None for one with no manifest (an external agent, or a name nobody knows)."""
    return load().get(str(harness or ""))


def caps(harness):
    """One harness's capabilities; empty (unknown) for a harness with no manifest."""
    row = get(harness)
    return row["capabilities"] if row else {}


def of(config, runtime=None):
    """The capabilities of the harness a bot config runs: its `harness`, else its runtime (older configs)."""
    harness = str((config or {}).get("harness") or "").strip() or str(runtime or (config or {}).get("runtime") or "")
    return caps(harness)


def with_capability(name, value=True):
    """The harness ids whose capability `name` equals `value` (truthy when value is True)."""
    return tuple(h for h, row in load().items()
                 if (bool(row["capabilities"].get(name)) if value is True else row["capabilities"].get(name) == value))
