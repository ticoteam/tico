"""The employee registry as the local tools read it: `registry/employees.yaml` merged with each
repo's `bot.yaml` (older: `employee.yaml`), and the small rules that hang off an entry.

  load_registry()                 -> (defaults, [entry, ...])    the raw file
  load_people()                   -> dict                        the raw registry/people.yaml
  merge_employee(entry, defaults) -> dict                        defaults < entry < bot.yaml
  declared_env_keys(emp)          -> [KEY, ...]                  what the `tools:` block (older: `access:`) needs
  grok_model(emp), grok_effort(emp)                              what `runtime: grok` means

The cloud keeps its own copy of every entry (`backend/settings_admin.py`); this module is for
the tools that run on a Mac beside the repos, such as `clients/preflight.py`.
"""

import os
import re
from pathlib import Path

import yaml

from clients.manifest import manifest_path, repo_dir, tools_of

HUB_DIR = Path(__file__).resolve().parents[1]
# An environment keeps its own registry; TICO_REGISTRY_DIR names it (backend/config.py does the same).
REGISTRY_DIR = Path(os.environ.get("TICO_REGISTRY_DIR") or HUB_DIR / "registry")
# Employees are siblings of the hub checkout. TICO_PROJECTS names the folder when this
# checkout is somewhere else (a worktree), the way the runner's --projects does.
ROOT = Path(os.environ.get("TICO_PROJECTS") or HUB_DIR.parent).expanduser()
CREDENTIAL_PROFILE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
GROK_MODEL = "grok-4.6"  # what `runtime: grok` means: Grok 4.6 through Grok Build (`grok`)
# Grok 4.6 supports low | medium | high | xhigh. Unknown values retain the safe
# provider default instead of being passed through as an invalid command-line option.
GROK_EFFORTS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh"}
GROK_DEFAULT_EFFORT = "high"


def load_registry(path=None):
    """The raw registry file: (defaults, [entry, ...]). Entries exist whether or not the repo does."""
    doc = yaml.safe_load((path or REGISTRY_DIR / "employees.yaml").read_text())
    return doc.get("defaults") or {}, doc["employees"]


def load_people(path=None):
    """The raw people roster (`registry/people.yaml`); `backend.people.load` normalises it."""
    return yaml.safe_load((path or REGISTRY_DIR / "people.yaml").read_text()) or {}


def merge_employee(entry, defaults, root=None):
    """One employee as the tools see it: defaults < registry entry < the repo's bot.yaml (older: employee.yaml)."""
    d = repo_dir(root or ROOT, entry["name"])
    m = {}
    mp = manifest_path(d)
    if mp.exists():
        m = yaml.safe_load(mp.read_text()) or {}
    return {**defaults, **entry, **m, "dir": d}


def declared_env_keys(emp):
    """Secret names the employee's `tools:` block (older: `access:`) says it needs, in order, deduped."""
    keys = []
    for a in tools_of(emp) or []:
        k = a.get("env") if isinstance(a, dict) else None
        if k and k not in keys:
            keys.append(str(k))
    return keys


def grok_model(emp, default_model=None):
    """The Grok Build model for this employee.

    `defaults` in the registry name a Codex model, and an employee that only says
    `runtime: grok` inherits it; Grok Build would not know what to do with it. So an unset model,
    "default", the registry's own default model, any Codex id (`gpt-…`) and any leftover Cursor
    name (`cursor-grok-4.6-high-fast`) all mean Grok 4.6.
    """
    m = str(emp.get("model") or "").strip()
    if not m or m == "default":
        return GROK_MODEL
    if default_model is None:
        try:
            default_model = str((load_registry()[0] or {}).get("model") or "")
        except Exception:
            default_model = ""
    if m == default_model or m.lower().startswith(("gpt-", "cursor-")):
        return GROK_MODEL
    return m


def grok_effort(emp):
    """Validated `--reasoning-effort` for this Grok 4.6 employee."""
    return GROK_EFFORTS.get(str(emp.get("reasoning_effort") or "").strip().lower(), GROK_DEFAULT_EFFORT)


# The runtimes whose registry model and effort mean something else to their CLI: (model, effort) for an employee. An
# adapter table, like the runner's host factory; every other runtime takes the registry's values as they are.
CLI_SETTINGS = {"grok": (grok_model, grok_effort)}
