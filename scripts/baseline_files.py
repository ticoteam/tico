"""Plumbing shared by scripts/lint_baseline.py and scripts/pyright_baseline.py.

A per-file baseline is a folder of pieces: one JSON file per source file, at <folder>/<source path>.json. Two branches that
change the baselines of different source files change different pieces, so git merges them without a conflict and no
merge driver is needed (GitHub's merge queue runs none). The single file that each tool reads (ruff-baseline.toml,
.basedpyright/baseline.json) is generated from the pieces and git-ignored.

Every function takes the repository root, so tests can run them on a scratch repository.
"""

import subprocess


def git(root, *args, text=True):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=text, check=False)


def tracked(root, name):
    """True when git tracks `name` in the index."""
    return git(root, "ls-files", "--error-unmatch", "--", name).returncode == 0


def untrack(root, names):
    """Stop tracking the files `names` (they stay on disk, staged for removal). Returns the names git tracked."""
    names = [name for name in names if tracked(root, name)]
    if names:
        done = git(root, "rm", "-q", "--cached", "--", *names)
        if done.returncode != 0:
            raise SystemExit(f"git rm --cached failed: {done.stderr.strip()}")
    return names


def show_at(root, ref, name):
    """The text of `name` at git ref, or None when the ref does not have it."""
    done = git(root, "show", f"{ref}:{name}")
    return done.stdout if done.returncode == 0 else None


def blobs_at(root, ref, names):
    """The bytes of each of `names` at git ref, read with one `git cat-file --batch` call."""
    if not names:
        return []
    request = "".join(f"{ref}:{name}\n" for name in names).encode()
    out = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=root, input=request, capture_output=True, check=True
    ).stdout
    blobs, pos = [], 0
    for name in names:
        end = out.index(b"\n", pos)
        header = out[pos:end].split()
        if len(header) != 3:
            raise SystemExit(f"git cat-file: {ref}:{name} is not a file ({out[pos:end].decode(errors='replace')})")
        size = int(header[2])
        blobs.append(out[end + 1 : end + 1 + size])
        pos = end + 2 + size
    return blobs


def pieces_at(root, ref, folder):
    """The text of each piece under `folder` at git ref, in path order. Empty when the ref has no pieces there."""
    listed = git(root, "ls-tree", "-r", "-z", "--name-only", ref, "--", folder, text=False)
    if listed.returncode != 0:
        return []
    names = sorted(name for name in listed.stdout.decode().split("\0") if name.endswith(".json"))
    return [blob.decode("utf-8") for blob in blobs_at(root, ref, names)]


def pieces(folder):
    """The piece files in the working tree under `folder`, in path order."""
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.rglob("*.json") if path.is_file())


def write_pieces(folder, texts):
    """Make `folder` hold exactly `texts` ({piece path: text}): write each piece whose text changed, delete every other
    piece, and remove the folders that are left empty. A piece whose text is unchanged is not rewritten."""
    folder.mkdir(parents=True, exist_ok=True)
    for piece in pieces(folder):
        if piece not in texts:
            piece.unlink()
    for piece, text in texts.items():
        piece.parent.mkdir(parents=True, exist_ok=True)
        if not piece.exists() or piece.read_text(encoding="utf-8") != text:
            piece.write_text(text, encoding="utf-8")
    for sub in sorted(folder.rglob("*"), reverse=True):
        if sub.is_dir() and not any(sub.iterdir()):
            sub.rmdir()
