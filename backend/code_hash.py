"""One hash over the code an image runs and serves, so a release image with files copied over it can be told apart.

The image build stores it in release-manifest.json (`code`); the flight recorder (backend/flight.py) hashes the same files
again at start and records the image as `modified` when they differ. Standard library only: the build runs it before the
dependencies are installed (`python -m backend.code_hash`).
"""
import hashlib
import sys
from pathlib import Path

DIRS = ("backend", "clients", "runner_support", "harnesses", "runner-rs/shell_init", "ui")
SKIP = {"__pycache__", "node_modules", "tests"}


def digest(root):
    """SHA-256 over every file's path and contents under DIRS, in path order; compiled files and tests are left out."""
    root = Path(root)
    whole = hashlib.sha256()
    for name in DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            relative = path.relative_to(root)
            if SKIP.intersection(relative.parts[:-1]) or path.suffix == ".pyc":
                continue
            whole.update(relative.as_posix().encode() + b"\0")
            whole.update(hashlib.sha256(path.read_bytes()).digest())
    return whole.hexdigest()


if __name__ == "__main__":
    print(digest(sys.argv[1] if len(sys.argv) > 1 else "."))
