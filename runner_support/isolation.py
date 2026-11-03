"""Keep the runner's own credential away from the code a bot runs (Linux, Docker runner).

The runner's registration (runner.json) can claim any bot's work, so it must not sit where a turn
can read it. In the Docker image the supervisor is the same unprivileged user every earlier image
ran as (`ticorun`, 10002) and owns runner.json (0600), its state (0700) and the tools directory. It
holds a few capabilities as ambient ones (docker/runner.compose.yaml) so it can drop every process
that runs bot-controlled code to another user (`bot`, 10003, TICO_RUNNER_BOT_UID) with none: the model
CLI of a turn, the login relay, `git` in a bot's checkout, any model CLI the runner probes. `bot` shares
the group, so the workspace, the secrets the turns need and the model logins (all in HOME) work for both
users, and an older image can still run on the same volume. A turn gets its attempt token and the
environment the runner already passes, and asks for its GitHub token over a socket
(runner/credential_socket.py).

It stays off wherever TICO_RUNNER_BOT_UID is unset: a Mac runner is a trust group of one user
(SECURITY.md), and so is an image started the way it was before this. Bots still share the `bot` user.
"""
import os
import subprocess
from pathlib import Path

UID_ENV, GID_ENV = "TICO_RUNNER_BOT_UID", "TICO_RUNNER_BOT_GID"
SETPRIV = "/usr/bin/setpriv"


def identity():
    """(uid, gid) that bot code runs as, or None when this process is not separating them."""
    try:
        uid = int(os.environ[UID_ENV])
        gid = int(os.environ.get(GID_ENV) or uid)
    except (KeyError, ValueError):
        return None
    return (uid, gid) if uid > 0 and hasattr(os, "geteuid") and os.geteuid() != uid else None


def enabled():
    return identity() is not None


def wrap(args, kwargs):
    """(args, kwargs) that run `args` as the bot user with no capabilities: the supervisor keeps
    SETUID and friends as ambient capabilities (docker/runner-entrypoint.sh), and `setpriv` switches
    user and empties them before the bot's program starts. The bot's files are group-writable."""
    who = identity()
    if not who or not isinstance(args, (list, tuple)):
        return args, kwargs
    prefix = [SETPRIV, f"--reuid={who[0]}", f"--regid={who[1]}", "--clear-groups", "--inh-caps=-all", "--ambient-caps=-all"]
    return [*prefix, *args], {**kwargs, "umask": 0o002}


def run(args, **kwargs):
    args, kwargs = wrap(args, kwargs)
    return subprocess.run(args, **kwargs)


def popen(args, **kwargs):
    args, kwargs = wrap(args, kwargs)
    return subprocess.Popen(args, **kwargs)


def chown(path, *, recursive=False):
    """Hand a file the supervisor made to the bot user. A no-op when isolation is off."""
    who = identity()
    if not who:
        return
    path = Path(path)
    try:
        os.chown(path, *who, follow_symlinks=False)
        if recursive and path.is_dir() and not path.is_symlink():
            for root, dirs, files in os.walk(path):
                for name in (*dirs, *files):
                    os.chown(os.path.join(root, name), *who, follow_symlinks=False)
    except OSError:
        pass


def mkdir(path, mode=0o700):
    """`mkdir -p` where every directory it creates belongs to the bot user when isolation is on."""
    path = Path(path)
    missing = []
    for part in (path, *path.parents):
        if part.exists():
            break
        missing.append(part)
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    for part in missing:
        chown(part)
    return path


def bot_state(default):
    """Where per-turn host state lives: `default` (inside the runner's own state) unless isolated,
    where the supervisor's state directory is closed to bot code, so it is a bot-owned directory."""
    if not enabled():
        return Path(default)
    return mkdir(Path(os.environ.get("HOME") or "/home/runner") / ".tico-host")


def turn_dir():
    """A directory a turn can read files the supervisor prepared for it (vault files)."""
    base = Path(os.environ.get("TICO_RUNNER_TURN_DIR") or "/run/tico-runner/turn")
    base.mkdir(mode=0o755, parents=True, exist_ok=True)
    return base


def adopt(*paths):
    """Give the bot user anything under `paths` that root made: a secret file someone wrote with
    `docker exec` (root by default) or a login run as root. Cheap; never follows symlinks."""
    who = identity()
    if not who:
        return
    for path in paths:
        try:
            stats = os.lstat(path)
            if stats.st_uid != who[0]:
                os.chown(path, *who, follow_symlinks=False)
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            for root, dirs, files in os.walk(path):
                for name in (*dirs, *files):
                    child = os.path.join(root, name)
                    if os.lstat(child).st_uid != who[0]:
                        os.chown(child, *who, follow_symlinks=False)
        except OSError:
            pass
