"""Subscription profiles: one directory on this machine per provider login.

Every bot used to run on whichever accounts the operator happened to be signed in to, because
the hosts inherit this process's `HOME` and `CODEX_HOME`. A profile is a named directory that
holds those homes instead, so two companies (or two bots of one company) on one Mac can use
different subscriptions. `share_operator` keeps the old behaviour: the operator's own `~/.codex`
and `$HOME`, which is what every registration made before profiles existed still gets.

The runner registration names them:

    "profiles": {"acme": {"dir": "~/.config/tico/environments/acme/profiles/acme",
                          "share_operator": false}},
    "default_profile": "acme",
    "bot_profiles": {"sales": "acme"}

Provider logins stay on the Computer. The server records profile names and sign-in state.
"""

import json
import os
import re
import stat
from pathlib import Path

RUNTIMES = ("codex", "claude", "gemini", "grok", "musecode")
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# The only relocation knob each CLI offers today. Codex reads CODEX_HOME; Claude Code and Grok
# Build key their login to HOME (grok 1.0.30 also honours GROK_HOME, but one
# rule for both keeps a profile a single directory). Gemini is not here: its host takes a home
# argument instead, so nothing in the turn's environment has to move. Muse Code keeps its login and
# settings in $XDG_CONFIG_HOME/muse.
HOME_VAR = {"codex": "CODEX_HOME", "claude": "HOME", "grok": "HOME", "musecode": "XDG_CONFIG_HOME"}
# Worst first: a runner row reports the profile that needs attention, not the healthy one.
SIGN_IN_ORDER = ("failed", "missing", "unknown", "ready")
LOGIN_COMMAND = {"codex": ["codex", "login"], "claude": ["claude", "auth", "login"],
                 "grok": ["grok", "login"], "musecode": ["muse", "login"]}


class SubscriptionUnavailable(RuntimeError):
    """An assigned subscription cannot run this turn on this Computer."""

    def __init__(self, problem, profile, runtime):
        super().__init__(problem)
        self.detail = {"profile": profile, "runtime": runtime, "problem": problem}


def covers(config):
    return (config.get("runtime") in RUNTIMES
            and config.get("harness") != "antigravity")


class Profile:
    """One subscription: the provider homes a bot's turns and readiness checks run against."""

    def __init__(self, name, directory, share_operator=False):
        self.name = name
        self.directory = Path(directory).expanduser()
        self.share_operator = bool(share_operator)

    def home(self, runtime):
        """Where this runtime keeps its login, or None when it uses the operator's own."""
        return None if self.share_operator else self.directory / runtime

    def environment(self, runtime, env=None):
        """`env` (this process's by default) with `runtime`'s home pointed at this profile."""
        env = dict(os.environ if env is None else env)
        home = self.home(runtime)
        if home and runtime in HOME_VAR:
            env[HOME_VAR[runtime]] = str(home)
        return env


def select(config, bot=None, requested=None):
    """The server-requested profile, or the local assignment and default when none is requested.

    None means the requested profile is unavailable, or no local profile is configured.
    Callers must block an unavailable server assignment.
    """
    profiles = config.get("profiles") or {}
    name = (config.get("bot_profiles") or {}).get(bot) if bot else None
    if requested:
        entry = profiles.get(requested)
        if not isinstance(entry, dict) or not entry.get("dir"):
            return None
        name = requested
    entry = profiles.get(name or config.get("default_profile") or "")
    if not isinstance(entry, dict) or not entry.get("dir"):
        return None
    return Profile(name or config["default_profile"], entry["dir"], entry.get("share_operator"))


def create(root, name, share_operator=False):
    """Make `<root>/<name>` with a home per runtime; returns its runner registration entry."""
    if not NAME_RE.fullmatch(name or "") or len(name) > 80:
        raise ValueError("A profile name is lowercase letters, digits, and single hyphens (up to 80 characters)")
    root = Path(root).expanduser()
    root.parent.mkdir(parents=True, exist_ok=True)
    root = root.parent.resolve() / root.name
    from . import isolation
    owner = isolation.identity()

    def directory_at(parent_fd, child, mode):
        try:
            os.mkdir(child, mode, dir_fd=parent_fd)
        except FileExistsError:
            pass
        info = os.stat(child, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Subscription profile paths must not be symbolic links")
        fd = os.open(child, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        if stat.S_IMODE(info.st_mode) != mode:
            os.fchmod(fd, mode)
        if owner and mode == 0o700:
            os.fchown(fd, *owner)
        return fd

    def write_new(fd, filename, content):
        try:
            target = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=fd)
        except FileExistsError:
            info = os.stat(filename, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Subscription profile paths must not be symbolic links")
            return
        if owner:
            os.fchown(target, *owner)
        with os.fdopen(target, "wb") as output:
            output.write(content)

    with_fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if owner:
            mode = stat.S_IMODE(os.fstat(with_fd).st_mode)
            if mode & 0o111 != 0o111:
                os.fchmod(with_fd, mode | 0o111)
        root_fd = directory_at(with_fd, root.name, 0o711)
        try:
            profile_fd = directory_at(root_fd, name, 0o700)
            try:
                write_new(profile_fd, "profile.json", json.dumps(
                    {"name": name, "share_operator": bool(share_operator)}, indent=2).encode())
                if not share_operator:
                    for runtime in RUNTIMES:
                        runtime_fd = directory_at(profile_fd, runtime, 0o700)
                        try:
                            if runtime == "claude":
                                claude_fd = directory_at(runtime_fd, ".claude", 0o700)
                                os.close(claude_fd)
                            source = Path.home() / ".gitconfig"
                            if runtime in ("claude", "grok") and source.is_file():
                                write_new(runtime_fd, ".gitconfig", source.read_bytes())
                        finally:
                            os.close(runtime_fd)
            finally:
                os.close(profile_fd)
        finally:
            os.close(root_fd)
    finally:
        os.close(with_fd)
    return {"dir": str(root / name), "share_operator": bool(share_operator)}


def missing(config, requested):
    entry = (config.get("profiles") or {}).get(requested)
    return f"profile {requested} not on this computer" if requested and (not isinstance(entry, dict) or not entry.get("dir")) else ""
