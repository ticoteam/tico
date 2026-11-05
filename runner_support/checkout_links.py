"""Recognize copies of HEAD's link targets without treating local edits as disposable."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import posixpath
import subprocess


def inspect(root, run=subprocess.run):
    """Read only. A failed check never permits a repair; names come from NUL-delimited Git output."""
    result = {"flattened": [], "only_flattened": False}
    root = Path(root).resolve()

    def git(*args):
        done = run(["git", "-C", str(root), *args], capture_output=True, timeout=30,
                   stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if done.returncode:
            raise ValueError("Could not check checkout links")
        return done.stdout

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
            return result
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
        # Check the index separately too: a conflict, staged edit or staged mode change blocks every repair.
        result["only_flattened"] = (not staged and dirty == set(result["flattened"])
                                    and not git("diff", "--cached", "--name-only", "-z", "HEAD"))
    except (OSError, ValueError, subprocess.SubprocessError):
        result["only_flattened"] = False
    return result


def restore(root, run=subprocess.run):
    """Caller holds tico-update.lock. Restore the whole matching set, or leave every path alone."""
    found = inspect(root, run)
    found["restored"] = []
    if found["only_flattened"]:
        try:
            done = run(["git", "-C", str(root), "-c", "core.symlinks=true", "checkout", "--quiet", "HEAD", "--",
                        *(":(literal)" + name for name in found["flattened"])], capture_output=True,
                       timeout=30, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return found
        if not done.returncode:
            found["restored"] = found["flattened"]
    return found


def note(found):
    return f"restored {len(found['restored'])} flattened links" if found.get("restored") else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args()
    if args.repair:
        from .release_update import lock_path
        path = lock_path(args.root)
        if path is None:
            raise ValueError("Could not find the checkout update lock")
        with path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            found = restore(args.root)
    else:
        found = inspect(args.root)
    print(json.dumps({**found, "note": note(found)}))


if __name__ == "__main__":
    main()
