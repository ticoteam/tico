"""A bot's files under their new and old names, read in one place.

Tico reads both for one release (the new name wins), and a template refresh renames the old ones:

  bot.yaml     <- employee.yaml            the bot's manifest
  routines:    <- schedules:               inside the manifest
  tools:       <- access:                  inside the manifest (the bot's declared tools)
  bot-<slug>   <- emp-<slug>               the bot's repository folder on a computer (the server records it:
                                           folder_dir, since a rename keeps it)
  groups.yaml  <- departments.yaml         the template catalog's groups
  group:       <- department:              a catalog card's group

Nothing else here: no I/O beyond checking which name exists.
"""
from pathlib import Path

MANIFEST = "bot.yaml"
OLD_MANIFEST = "employee.yaml"
GROUPS_FILE = "groups.yaml"
OLD_GROUPS_FILE = "departments.yaml"
REPO_PREFIX = "bot-"                 # new bot repositories
OLD_REPO_PREFIX = "emp-"             # existing ones keep their names


def manifest_path(folder):
    """`bot.yaml` in the folder if it is there, else `employee.yaml` if that is, else the new name (to create)."""
    folder = Path(folder)
    for name in (MANIFEST, OLD_MANIFEST):
        if (folder / name).is_file():
            return folder / name
    return folder / MANIFEST


def pick(mapping, new, old):
    """The value under the new key, or the old key when the new one is absent."""
    if not isinstance(mapping, dict):
        return None
    return mapping[new] if new in mapping else mapping.get(old)


def routines_of(manifest):
    """The manifest's declared routines: `routines:`, else the older `schedules:`."""
    return pick(manifest, "routines", "schedules")


def tools_of(manifest):
    """The manifest's declared tools: `tools:`, else the older `access:`."""
    return pick(manifest, "tools", "access")


def group_of(card):
    """A catalog card's group: `group:`, else the older `department:`."""
    return pick(card, "group", "department")


def repo_dir(root, slug):
    """A bot's repository folder under a workspace: `bot-<slug>` if it exists, else `emp-<slug>` if that does,
    else `bot-<slug>` (where a new one goes). Never used when the bot's recorded repo is known."""
    root = Path(root)
    for prefix in (REPO_PREFIX, OLD_REPO_PREFIX):
        if (root / (prefix + slug)).exists():
            return root / (prefix + slug)
    return root / (REPO_PREFIX + slug)


def folder_dir(root, folder, slug=""):
    """A bot's repository folder under a workspace by the folder the server recorded for it (its `folder`: named once,
    so a rename keeps it), else by its slug (repo_dir) for a server that does not say. An older bot's recorded
    `bot-<id>` finds its `emp-<id>` folder too. A value that is not one plain folder name is never a path."""
    folder = str(folder or "").strip()
    if not folder or folder in (".", "..") or not all((ch.isascii() and ch.isalnum()) or ch in "-_." for ch in folder):
        return repo_dir(root, slug) if slug else None
    return repo_dir(root, folder[len(REPO_PREFIX):]) if folder.startswith(REPO_PREFIX) else Path(root) / folder


def bot_slug_of_repo(name):
    """`bot-x` or `emp-x` -> `x`; anything else unchanged."""
    for prefix in (REPO_PREFIX, OLD_REPO_PREFIX):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def hub_bot(environ):
    """The bot a process runs as: HUB_BOT, else the older HUB_EMPLOYEE."""
    return (environ.get("HUB_BOT") or environ.get("HUB_EMPLOYEE") or "").strip()
