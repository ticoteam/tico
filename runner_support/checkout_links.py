"""Recognize copies of HEAD's link targets without treating local edits as disposable."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import posixpath
import shutil
import subprocess
import tempfile
import uuid


def _git(root, run, *args):
    done = run(["git", "-C", str(root), *args], capture_output=True, timeout=30,
               stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if done.returncode:
        raise ValueError("Could not check checkout links")
    return done.stdout


def _inspect(root, run):
    result = {"flattened": [], "only_flattened": False}
    repairs = []
    root = Path(root).resolve()

    def git(*args):
        return _git(root, run, *args)

    try:
        entries = git("status", "--porcelain=v1", "-z", "--untracked-files=no").split(b"\0")
        dirty, staged = set(), False
        while entries and entries[0]:
            entry = entries.pop(0)
            xy, name = entry[:2], os.fsdecode(entry[3:])
            dirty.add(name)
            staged |= xy[:1] != b" "
            if b"R" in xy or b"C" in xy:
                dirty.add(os.fsdecode(entries.pop(0)))
        if not dirty:
            return result, repairs
        tree = {}
        for entry in git("ls-tree", "-r", "-z", "HEAD").split(b"\0"):
            if entry:
                info, name = entry.split(b"\t", 1)
                mode, kind, oid = info.split()
                tree[os.fsdecode(name)] = (mode, kind, oid.decode())
        for name in sorted(dirty):
            mode, kind, oid = tree.get(name, (None, None, None))
            path = root / name
            if mode != b"120000" or path.is_symlink() or not path.is_file():
                continue
            link = os.fsdecode(git("cat-file", "blob", oid))
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
            target_mode, target_kind, target_oid = tree.get(target, (None, None, None))
            if (posixpath.isabs(link) or target in dirty or target_mode not in (b"100644", b"100755")
                    or target_kind != b"blob"):
                continue
            source = root / target
            # Neither a replaced target nor a linked ancestor may lend another file's bytes to this check.
            if (path.parent.resolve() != path.parent or source.resolve() != source or not source.is_file()
                    or bool(source.stat().st_mode & 0o111) != (target_mode == b"100755")):
                continue
            content = git("cat-file", "blob", target_oid)
            if path.read_bytes() == content == source.read_bytes():
                result["flattened"].append(name)
                repairs.append((name, link, content))
        # Check the index separately too: a conflict, staged edit or staged mode change blocks every repair.
        result["only_flattened"] = (not staged and dirty == set(result["flattened"])
                                    and not git("diff", "--cached", "--name-only", "-z", "HEAD"))
    except (OSError, ValueError, subprocess.SubprocessError):
        result["only_flattened"] = False
    return result, repairs


def inspect(root, run=subprocess.run):
    """Read only. A failed check never permits a repair; names come from NUL-delimited Git output."""
    return _inspect(root, run)[0]


def _set_aside(path, saved):
    saved.parent.mkdir(parents=True, exist_ok=True)
    os.rename(path, saved)
    return saved


def _put_back(path, saved, link):
    # Keep an editor's replacement too; only our unchanged link can be discarded on rollback.
    try:
        displaced = _set_aside(path, saved.parent / f".tico-link-replacement-{uuid.uuid4().hex}")
    except FileNotFoundError:
        displaced = None
    try:
        os.link(saved, path, follow_symlinks=False)
    finally:
        if displaced is not None and displaced.is_symlink() and os.readlink(displaced) == link:
            displaced.unlink()


def _backup_folder(root, run):
    git_dir = Path(os.fsdecode(_git(root, run, "rev-parse", "--absolute-git-dir")).rstrip("\n"))
    parent = git_dir / "tico-flattened-links"
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ-")
    return Path(tempfile.mkdtemp(prefix=stamp, dir=parent))


def _prune_backups(current):
    # Keep this repair and the four newest older batches. Never prune the originals just validated.
    try:
        older = sorted((p for p in current.parent.iterdir()
                        if p != current and not p.is_symlink() and p.is_dir()), reverse=True)
    except OSError:
        return
    for old in older[4:]:
        shutil.rmtree(old, ignore_errors=True)


def restore(root, run=subprocess.run):
    """Caller holds tico-update.lock. Retain originals so later writes through open handles stay recoverable."""
    root = Path(root).resolve()
    found, repairs = _inspect(root, run)
    found["restored"] = []
    saved = []
    if found["only_flattened"]:
        try:
            folder = _backup_folder(root, run)
            found["backup"] = str(folder)
            for name, link, content in repairs:
                path = root / name
                backup = _set_aside(path, folder / name)
                saved.append((path, backup, link, content))
                os.symlink(link, path)
            # Git never writes the worktree or index here. Check staging after the swap, then the saved bytes,
            # including writes through a handle opened before the rename.
            if _git(root, run, "diff", "--cached", "--name-only", "-z", "HEAD"):
                raise ValueError("The index changed during link repair")
            for path, backup, link, content in saved:
                if (backup.is_symlink() or backup.read_bytes() != content
                        or not path.is_symlink() or os.readlink(path) != link):
                    raise ValueError("The file changed during link repair")
        except (OSError, ValueError, subprocess.SubprocessError):
            found["only_flattened"] = False
            for path, backup, link, _ in reversed(saved):
                try:
                    _put_back(path, backup, link)
                except OSError:
                    pass  # A failed rollback keeps the saved file, never deletes its bytes.
            return found
        found["restored"] = found["flattened"]
        _prune_backups(folder)
    return found


def note(found):
    return (f"restored {len(found['restored'])} flattened links; originals kept in {found['backup']}"
            if found.get("restored") else "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--repair", action="store_true")
    mode.add_argument("--repair-locked", action="store_true", help="repair while the caller holds tico-update.lock")
    args = parser.parse_args()
    if args.repair:
        from .release_update import lock_path
        from .regular_file import lock
        path = lock_path(args.root)
        if path is None:
            raise ValueError("Could not find the checkout update lock")
        with lock(path) as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            found = restore(args.root)
    elif args.repair_locked:
        found = restore(args.root)
    else:
        found = inspect(args.root)
    print(json.dumps({**found, "note": note(found)}))


if __name__ == "__main__":
    main()
