"""Bot files never belong in the Tico checkout running this code."""
from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def _git_identity(path):
    # Git resolves .git files, commondir and linked installs; inherited turn overrides must not redirect it.
    existing = path
    while not existing.exists():
        existing = existing.parent
    if not existing.is_dir():
        existing = existing.parent
    try:
        # Ownership checks require the worktree root, even when the requested directory already exists.
        for root in (existing, *existing.parents):
            try:
                (root / ".git").lstat()
            except FileNotFoundError:
                if not ((root / "HEAD").exists() and (root / "objects").is_dir()):
                    continue
            break
        else:
            return ()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        done = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                               "-c", "safe.directory=" + str(root), "-C", str(root),
                               "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"],
                              env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"Cannot check repository identity: {path}") from exc
    if done.returncode:
        # Broken Git metadata must not look like a safe clone or a confirmed install match.
        raise ValueError(f"Cannot check repository identity: {path}")
    names = done.stdout.splitlines()
    if len(names) != 2 or any(not Path(name).is_absolute() for name in names):
        raise ValueError(f"Cannot check repository identity: {path}")
    return tuple(_resolve(Path(name)) for name in names)


def _same_path(left, right):
    if left == right:
        return True
    try:
        return left.samefile(right)
    except OSError:
        return False


def _resolve(path):
    path = Path(path).expanduser().absolute()
    try:
        return path.resolve(strict=True)
    except FileNotFoundError as err:
        tail = []
        parent = path
        while True:
            try:
                parent.lstat()
                break
            except FileNotFoundError:
                tail.append(parent.name)
                parent = parent.parent
        # An unresolved '..' can hide a later link into the install; do not append it unchecked.
        if ".." in tail:
            raise ValueError(f"Cannot check repository path: {path}") from err
        real = _resolve(parent.parent / parent.readlink()) if parent.is_symlink() else parent.resolve(strict=True)
        return real.joinpath(*reversed(tail))


def outside_installation(path):
    try:
        resolved = _resolve(path)
        installation = _resolve(ROOT)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"Cannot check repository path: {path}") from exc
    inside = resolved.is_relative_to(installation)
    # Path.resolve on a case-insensitive Mac volume can keep the caller's spelling.
    if not inside:
        for parent in (resolved, *resolved.parents):
            try:
                if parent.samefile(installation):
                    inside = True
                    break
            except OSError:
                continue
    if inside:
        raise ValueError(f"Repository path is inside the Tico install: {path}")
    installation_git = _git_identity(installation)
    repository_git = _git_identity(resolved)
    if any(_same_path(left, right) for left in repository_git for right in installation_git):
        raise ValueError(f"Repository shares Git metadata with the Tico install: {path}")
    return resolved


def contained_file(root, path):
    """Check both the destination and its parents, including a dangling link's target."""
    # These checks prevent accidents, not a hostile bot with full access to the install.
    root, path = _resolve(root), Path(path)
    resolved = outside_installation(path)
    parent = _resolve(path.parent)
    if not resolved.is_relative_to(root) or not parent.is_relative_to(root):
        raise ValueError(f"File path is outside the bot repo: {path}")
    # Replacing this entry must not traverse the final link, even when that link stays in the repo.
    return parent / path.name
