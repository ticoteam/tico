"""Carry the previous release's page files into a new web build, so a tab opened before the release keeps working.

A tab on an earlier release asks for that release's files under /next/assets/ when it opens a page it had not loaded.
The server serves the build's assets/ folder as it is (backend/web_routes.py), so the image build copies the previous
release's assets/ into the new one: the files it built and the ones it carried itself. A file is carried for KEEP_DAYS
after the last release that built it; CARRIED, beside assets/, records that time for each carried file, so the next
build knows when to drop it. A file's name is its content, so a carried file never replaces one the build made.

Past that, the file is the 404 it was before and the tab shows "Tico couldn't finish loading".

    python3 scripts/carry_web_assets.py <previous release's build/web> <new build/web> [--out DIR] [--now SECONDS]

The Dockerfile runs it in the web build, with the previous release's image named by PREVIOUS_RELEASE.
"""

import argparse
import json
import os
import shutil
import time
from pathlib import Path

# A tab left open overnight keeps working. Each release changes about 3 MB of these files, and there can be about 20
# releases a day on a busy company, so each kept day adds about 60 MB (uncompressed) to the server image.
KEEP_DAYS = 2
CARRIED = "carried-assets.json"


def _files(folder: Path) -> list[os.DirEntry[str]]:
    """The regular files in `folder`, never a link or a hidden file; none when it is missing."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    return [e for e in entries if not e.name.startswith(".") and e.is_file(follow_symlinks=False)]


def carry(previous: Path, new: Path, now: float, out: Path | None = None) -> dict[str, float]:
    """Copy the previous build's files that `new` lacks into out/assets (default: new's own), except those last built
    more than KEEP_DAYS before `now`, and write out/CARRIED. Returns what was carried: each name and when a release
    last built it. The image build writes them apart (`out`), so only the server image gets them, as its own layer."""
    out = new if out is None else out
    (out / "assets").mkdir(parents=True, exist_ok=True)
    try:
        last_built = json.loads((previous / CARRIED).read_text()).get("carried", {})
    except (OSError, ValueError, AttributeError):
        last_built = {}  # a release from before this step: everything in it is its own
    built = {e.name for e in _files(new / "assets")}
    oldest = now - KEEP_DAYS * 86400
    carried: dict[str, float] = {}
    for entry in sorted(_files(previous / "assets"), key=lambda e: e.name):
        if entry.name in built:
            continue
        when = last_built.get(entry.name)
        if not isinstance(when, (int, float)):
            when = now  # the previous release built it: it ships until this one replaces it
        if when < oldest:
            continue
        shutil.copyfile(entry.path, out / "assets" / entry.name)
        carried[entry.name] = float(when)
    (out / CARRIED).write_text(
        json.dumps({"keep_days": KEEP_DAYS, "carried": carried}, indent=1, sort_keys=True) + "\n"
    )
    return carried


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("previous", type=Path, help="the previous release's build/web (empty or missing: none)")
    parser.add_argument("new", type=Path, help="the new build/web, already built")
    parser.add_argument("--out", type=Path, default=None, help="write the carried files here (default: the new build)")
    parser.add_argument("--now", type=float, default=None, help="seconds since the epoch (default: now)")
    args = parser.parse_args(argv)
    if not (args.new / "assets").is_dir():
        parser.error(f"{args.new}/assets is not a folder: build the web app first")
    carried = carry(args.previous, args.new, time.time() if args.now is None else args.now, args.out)
    print(f"carry_web_assets: {len(carried)} files carried from the previous release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
