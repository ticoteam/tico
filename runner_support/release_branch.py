"""Read-only branch checks shared by the release updater and the runner's Health report."""
import argparse
import json
from pathlib import Path
import re

BOT_BRANCH = re.compile(r"(?:tico/[0-9a-f]{8}-[a-z0-9][a-z0-9-]*|tico-session/[A-Za-z0-9][A-Za-z0-9_-]*)\Z")
OPERATIONS = (("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
              ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
              ("sequencer", "cherry-pick or revert"), ("BISECT_START", "bisect"))


def blocker(root, g):
    """Fail closed if Git state cannot be read. Operation paths belong to this worktree's metadata."""
    for marker, operation in OPERATIONS:
        done = g("rev-parse", "--git-path", marker)
        if done.returncode or not done.stdout.strip():
            return "cannot inspect Git operations"
        path = Path(root) / done.stdout.strip()
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return "cannot inspect Git operations"
        return f"{operation} in progress; finish or abort it first"
    done = g("status", "--porcelain", "--untracked-files=no")
    if done.returncode:
        return "cannot inspect checkout changes"
    dirty = done.stdout.splitlines()
    if dirty:
        names = ", ".join(line[3:] for line in dirty[:3])
        return f"{len(dirty)} changed file{'s' if len(dirty) != 1 else ''}; commit or stash them first: {names}"
    return ""


def inspect(root, g, version=""):
    done = g("symbolic-ref", "--quiet", "--short", "HEAD")
    if done.returncode == 1:  # detached HEAD
        return {"branch": "HEAD", "recover": False, "error": ""}
    if done.returncode or not done.stdout.strip():
        return {"branch": "", "recover": False, "error": "cannot inspect the checkout branch"}
    branch = done.stdout.strip()
    if branch == "main":
        return {"branch": branch, "recover": False, "error": ""}
    prefix = f"the checkout is on branch {branch}; "
    if not BOT_BRANCH.fullmatch(branch):
        return {"branch": branch, "recover": False,
                "error": prefix + "switch it to main to follow releases"}
    reason = blocker(root, g)
    if not reason and version:
        target = g("rev-parse", "--verify", "--quiet", "v" + version.lstrip("v") + "^{commit}")
        if not target.returncode and target.stdout.strip():
            preview = g("read-tree", "--dry-run", "-m", "-u", "HEAD", target.stdout.strip())
            if preview.returncode:
                reason = "local files are in the way"
    return {"branch": branch, "recover": not reason,
            "error": prefix + ("cannot recover: " + reason if reason else
                               "will be recovered automatically on the next update")}


def main():
    from .release_update import git
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--version", default="")
    args = parser.parse_args()
    print(json.dumps(inspect(args.root, lambda *a: git(args.root, *a), args.version)))


if __name__ == "__main__":
    main()
