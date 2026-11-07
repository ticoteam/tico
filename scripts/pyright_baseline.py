#!/usr/bin/env python3
"""The basedpyright baseline, one piece per Python file: baselines/pyright/<path>.json holds that file's entries.

basedpyright reads .basedpyright/baseline.json, its default baseline path. That file is generated from the pieces and
git-ignored, so the pieces are the only baseline git tracks. Two PRs that change the entries of different Python files
change different pieces, and git merges them with no conflict (GitHub's merge queue runs no merge drivers, so the layout
is what keeps the merge clean).

    python3 scripts/pyright_baseline.py --generate   write .basedpyright/baseline.json from the pieces (check_types.sh runs it)
    python3 scripts/pyright_baseline.py --split      write the pieces from .basedpyright/baseline.json, which
                                                     `scripts/check_types.sh --writebaseline` wrote
    python3 scripts/pyright_baseline.py --migrate    --split the single file of before the split, then untrack it

A file with no entries has no piece. A piece and the generated file use basedpyright's own layout: keys sorted, a
four-space indent, no empty list, and no newline at the end.
"""

import argparse
import json
import sys
from pathlib import Path

import baseline_files

ROOT = Path(__file__).resolve().parents[1]
BASELINE_NAME = ".basedpyright/baseline.json"  # basedpyright's default baseline path: generated, git-ignored
LAYOUT = "baselines/pyright"
PYRIGHT_INDENT = 4  # basedpyright's JSON indent


def layout_dir():
    return ROOT / LAYOUT


def baseline_file():
    return ROOT / BASELINE_NAME


def render(files):
    """basedpyright's layout for a `files` object: keys sorted, no empty lists, no newline at the end."""
    kept = {path: entries for path, entries in sorted(files.items()) if entries}
    return json.dumps({"files": kept}, indent=PYRIGHT_INDENT, ensure_ascii=False)


def parse(text):
    """The `files` object of a baseline text: {key: [entry, ...]}, entries in basedpyright's order."""
    return json.loads(text).get("files", {}) if text.strip() else {}


def rel_of(key):
    """basedpyright's key for a file ("./backend/a.py") as the repository path ("backend/a.py")."""
    return key[2:] if key.startswith("./") else key


def piece_file(key):
    return layout_dir() / (rel_of(key) + ".json")


def load_layout():
    """{key: entries} from the pieces in the working tree."""
    files = {}
    for piece in baseline_files.pieces(layout_dir()):
        files.update(parse(piece.read_text(encoding="utf-8")))
    return files


def layout_at(ref):
    """{key: entries} at git ref, in either layout: the single file of before the split, else the pieces. None when the
    ref has neither (the ref is older than the baselines)."""
    single = baseline_files.show_at(ROOT, ref, BASELINE_NAME)
    if single is not None:
        return parse(single)
    texts = baseline_files.pieces_at(ROOT, ref, LAYOUT)
    if not texts:
        return None
    files = {}
    for text in texts:
        files.update(parse(text))
    return files


def layout_problems():
    """Pieces that are not in the layout split() writes: a piece holding other than one file, an empty piece, a piece at
    the wrong path, or text other than what render() gives. A hand edit or a bad merge shows up here."""
    problems = []
    for piece in baseline_files.pieces(layout_dir()):
        where = piece.relative_to(ROOT).as_posix()
        text = piece.read_text(encoding="utf-8")
        files = parse(text)
        if len(files) != 1:
            problems.append(f"{where} holds {len(files)} files; a piece holds one")
            continue
        key, entries = next(iter(files.items()))
        if not entries:
            problems.append(f"{where} has no entries; delete the piece")
        elif piece != piece_file(key) or text != render({key: entries}):
            problems.append(
                f"{where} is not in the layout scripts/pyright_baseline.py writes; run scripts/check_types.sh --writebaseline"
            )
    return problems


def generate():
    """Write .basedpyright/baseline.json from the pieces. Refuses while git tracks that file: the single file of before the
    split, which writing over would make differ from the pieces."""
    if baseline_files.tracked(ROOT, BASELINE_NAME):
        sys.exit(
            f"{BASELINE_NAME} is tracked by git, the single-file baseline from before the split. "
            "Run: python3 scripts/pyright_baseline.py --migrate"
        )
    files = load_layout()
    baseline_file().parent.mkdir(parents=True, exist_ok=True)
    baseline_file().write_text(render(files), encoding="utf-8")
    return files


def split():
    """Write the pieces from .basedpyright/baseline.json, which basedpyright wrote, and rewrite that file in render()'s
    layout (basedpyright's own output, normalized)."""
    path = baseline_file()
    if not path.exists():
        sys.exit(f"{BASELINE_NAME} is missing: basedpyright writes it (scripts/check_types.sh --writebaseline)")
    files = parse(path.read_text(encoding="utf-8"))
    texts = {piece_file(key): render({key: entries}) for key, entries in files.items() if entries}
    baseline_files.write_pieces(layout_dir(), texts)
    path.write_text(render(files), encoding="utf-8")
    return files


def migrate():
    """Turn the single file of before the split into pieces, then untrack it. A branch from before the split runs this
    after its rebase, on the branch's own copy of the file."""
    if not baseline_files.tracked(ROOT, BASELINE_NAME):
        print(f"{BASELINE_NAME} is not tracked by git: nothing to migrate")
        return 0
    files = split()
    baseline_files.untrack(ROOT, [BASELINE_NAME])
    print(
        f"migrated {BASELINE_NAME} to {LAYOUT}: {sum(len(e) for e in files.values())} entries in "
        f"{sum(1 for e in files.values() if e)} files; {BASELINE_NAME} is generated and git-ignored now"
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--generate", action="store_true", help=f"write {BASELINE_NAME} from {LAYOUT}")
    mode.add_argument(
        "--split", action="store_true", help=f"write {LAYOUT} from {BASELINE_NAME} (after --writebaseline)"
    )
    mode.add_argument(
        "--migrate", action="store_true", help="--split the single file of before the split, then untrack it"
    )
    args = parser.parse_args(argv)
    if args.generate:
        files = generate()
        print(
            f"wrote {BASELINE_NAME} from {LAYOUT}: {sum(len(e) for e in files.values())} entries in {len(files)} files"
        )
        return 0
    if args.split:
        files = split()
        print(
            f"wrote {LAYOUT}: {sum(len(e) for e in files.values())} entries in {sum(1 for e in files.values() if e)} files"
        )
        return 0
    return migrate()


if __name__ == "__main__":
    sys.exit(main())
