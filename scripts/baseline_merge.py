#!/usr/bin/env python3
"""Merge drivers for the three generated baselines, and the layout that keeps them mergeable.

git runs a driver when both sides changed one of these files (.gitattributes), with git's %O %A %B:
    python3 scripts/baseline_merge.py counts  BASE OURS THEIRS   ruff-baseline-counts.json
    python3 scripts/baseline_merge.py toml    BASE OURS THEIRS   ruff-baseline.toml
    python3 scripts/baseline_merge.py pyright BASE OURS THEIRS   .basedpyright/baseline.json
On a clean merge it writes the result to OURS and exits 0. When the sides change the same thing differently it leaves
OURS as it is, names the place on stderr and exits 1, which git reports as a conflict like any other. CONTRIBUTING.md
registers the drivers in each clone. Without them git merges these files as text, and GitHub's own merges (the merge
queue included) never run a driver at all: a server-side merge sees only the text.

Every count and every entry is a change against the base, so each merge applies both sides' changes to the base:
- counts: each (file, rule) count is ours + theirs - base, never below zero. Two sides that each fixed different
  findings of one rule add both fixes; a pair that either side removed stays removed.
- toml: a (file, rule) pair is kept when both sides keep it, or when only one side has it and the base did not. A pair
  either side removed stays removed. The pairs are the keys of the counts, so a merge that disagrees with them fails
  lint_baseline.py --check, and `lint_baseline.py --write` settles it.
- pyright: basedpyright matches each file's entries against its diagnostics in order, so an entry list is a sequence,
  and the merge is a three-way merge of that sequence. Two sides removing neighbouring entries merge, which git's text
  merge refuses to do; two sides changing the same entry differently are a conflict. The result is written in the
  layout basedpyright itself writes; `normalize` rewrites a file into it, and scripts/check_pyright_ratchet.py fails
  on any other layout. A layout with one entry per line was measured and conflicts more, not less, on git's text merge.
"""

import difflib
import json
import sys
from pathlib import Path

import lint_baseline

PYRIGHT_INDENT = 4  # basedpyright's JSON indent; an entry is the {code, range} it writes for one baselined diagnostic
REMEDY = {
    "counts": "python3 scripts/lint_baseline.py --write",
    "toml": "python3 scripts/lint_baseline.py --write",
    "pyright": "scripts/check_types.sh --writebaseline",
}


class Conflict(Exception):
    pass


def _counts(text):
    return lint_baseline.load_counts(text) if text.strip() else {}


def _pairs(text):
    return lint_baseline.baseline_pairs(text) if text.strip() else set()


def merge_counts(base_text, ours_text, theirs_text):
    base, ours, theirs = _counts(base_text), _counts(ours_text), _counts(theirs_text)
    merged = {}
    for pair in set(base) | set(ours) | set(theirs):
        count = ours.get(pair, 0) + theirs.get(pair, 0) - base.get(pair, 0)
        if count > 0:
            merged[pair] = count
    return lint_baseline.render_counts(merged)


def merge_toml(base_text, ours_text, theirs_text):
    base, ours, theirs = _pairs(base_text), _pairs(ours_text), _pairs(theirs_text)
    kept = {
        pair
        for pair in base | ours | theirs
        if (pair in ours and pair in theirs)
        or (pair in ours and pair not in base)
        or (pair in theirs and pair not in base)
    }
    return lint_baseline.render_toml(kept)


def parse_pyright(text):
    """The `files` object of a baseline: {path: [entry, ...]}, entries in basedpyright's order."""
    return json.loads(text).get("files", {}) if text.strip() else {}


def render_pyright(files):
    """basedpyright's layout: keys sorted, four-space indent, no empty lists, no newline at the end."""
    kept = {path: entries for path, entries in sorted(files.items()) if entries}
    return json.dumps({"files": kept}, indent=PYRIGHT_INDENT, ensure_ascii=False)


def _token(entry):
    return json.dumps(entry, sort_keys=True, ensure_ascii=False)


def _changes(base, other):
    """(start, end, replacement) for each run of `base` that `other` changed; `end == start` is an insertion."""
    matcher = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, other[j1:j2]) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def _clash(a, b):
    a1, a2, _ = a
    b1, b2, _ = b
    if a1 == a2 and b1 == b2:
        return a1 == b1
    if a1 == a2:
        return b1 <= a1 < b2
    if b1 == b2:
        return a1 <= b1 < a2
    return a1 < b2 and b1 < a2


def merge_sequences(base, ours, theirs):
    """Tokens after applying both sides' changes to `base`. Changes that touch the same run must be identical."""
    accepted = []
    for side in (_changes(base, ours), _changes(base, theirs)):
        for change in side:
            clashes = [prev for prev in accepted if _clash(prev, change)]
            if not clashes:
                accepted.append(change)
            elif not all(prev == change for prev in clashes):
                raise Conflict(f"both sides change the entry at base position {change[0]} differently")
    out, position = [], 0
    for start, end, replacement in sorted(accepted, key=lambda change: (change[0], change[1])):
        out.extend(base[position:start])
        out.extend(replacement)
        position = end
    out.extend(base[position:])
    return out


def merge_pyright(base_text, ours_text, theirs_text):
    sides = [parse_pyright(text) for text in (base_text, ours_text, theirs_text)]
    entries = {}
    for files in sides:
        for path_entries in files.values():
            for entry in path_entries:
                entries.setdefault(_token(entry), entry)
    merged = {}
    for path in sorted(set().union(*sides)):
        tokens = [[_token(entry) for entry in files.get(path, [])] for files in sides]
        try:
            merged[path] = [entries[token] for token in merge_sequences(*tokens)]
        except Conflict as conflict:
            raise Conflict(f"{path}: {conflict}") from None
    return render_pyright(merged)


MERGES = {"counts": merge_counts, "toml": merge_toml, "pyright": merge_pyright}


def normalize(path):
    text = Path(path).read_text(encoding="utf-8")
    rendered = render_pyright(parse_pyright(text))
    if rendered != text:
        Path(path).write_text(rendered, encoding="utf-8")
        print(f"normalized {path} to basedpyright's layout")
    return 0


def main(argv):
    if len(argv) == 2 and argv[0] == "normalize":
        return normalize(argv[1])
    if len(argv) != 4 or argv[0] not in MERGES:
        print(
            "usage: baseline_merge.py {counts,toml,pyright} BASE OURS THEIRS\n       baseline_merge.py normalize FILE",
            file=sys.stderr,
        )
        return 2
    mode, *paths = argv
    texts = [Path(path).read_text(encoding="utf-8") for path in paths]
    try:
        merged = MERGES[mode](*texts)
    except Conflict as conflict:
        print(f"baseline_merge {mode}: conflict: {conflict}; resolve it, then run {REMEDY[mode]}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"baseline_merge {mode}: cannot merge: {error}", file=sys.stderr)
        return 1
    Path(paths[1]).write_text(merged, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
