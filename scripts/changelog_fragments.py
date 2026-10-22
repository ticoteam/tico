"""Changelog fragments: each pull request adds its own notes file; the release commit compiles them into CHANGELOG.md.

A pull request never edits CHANGELOG.md, so two of them never conflict there and a release cut never moves their
lines. Each one adds `changes/unreleased/<name>.md` with Keep a Changelog headings and its entries, exactly as they
will read in the release notes:

    ### Fixed
    - **Short lead.** What changed for the person using Tico,
      wrapped at 120 characters with a two-space indent.

    python scripts/changelog_fragments.py add Fixed "**Short lead.** What changed."   # names it after the branch
    python scripts/changelog_fragments.py adopt     # a branch that wrote its line in CHANGELOG.md moves it here
    python scripts/changelog_fragments.py check     # every fragment parses
    python scripts/changelog_fragments.py compile 0.3.74 [--date 2026-10-23]          # the release commit only

`compile` writes the `## [X.Y.Z] - date` section under an empty `## [Unreleased]` (newest fragment first in each
heading), moves the compare links on and deletes the fragments it used. Commit all of it as the release commit, with
the `Changelog-Release: vX.Y.Z` trailer.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAGMENTS = Path("changes") / "unreleased"
HEADINGS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")


class FragmentError(ValueError):
    pass


def parse(text, where="fragment"):
    """[(heading, entry text)] in order; an entry is its `- ` line plus the indented lines that continue it."""
    out, heading, entry = [], None, None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            entry = None
        elif line.startswith("### "):
            heading, entry = line[4:].strip(), None
            if heading not in HEADINGS:
                raise FragmentError("%s line %d: ### %s is not one of %s" % (where, number, heading, ", ".join(HEADINGS)))
        elif line.startswith("- "):
            if heading is None:
                raise FragmentError("%s line %d: put the entry under a heading, e.g. ### Fixed" % (where, number))
            entry = [heading, line]
            out.append(entry)
        elif line[:1] in " \t" and entry is not None:
            entry[1] += "\n" + line.rstrip()
        else:
            raise FragmentError("%s line %d: expected ### <heading>, a `- ` entry or its indented continuation" % (where, number))
    if not out:
        raise FragmentError("%s has no entries" % where)
    return [(h, e.rstrip()) for h, e in out]


def render(entries):
    """Entries grouped under their headings in Keep a Changelog order, each heading once."""
    blocks = []
    for heading in HEADINGS:
        lines = [e for h, e in entries if h == heading]
        if lines:
            blocks.append("### %s\n%s\n" % (heading, "\n".join(lines)))
    return "\n".join(blocks)


def fragment_paths(root):
    return sorted((Path(root) / FRAGMENTS).glob("*.md"))


def check(root=ROOT):
    """Each fragment's problem, as `path: message`; empty when every fragment parses."""
    problems = []
    folder = Path(root) / FRAGMENTS.parent
    allowed = {folder / "README.md", *fragment_paths(root)}
    for path in sorted(folder.rglob("*")) if folder.is_dir() else []:
        if path.is_file() and path not in allowed and path.name != ".gitkeep":
            problems.append("%s: compile reads only %s/*.md" % (path.relative_to(root), FRAGMENTS))
    for path in fragment_paths(root):
        try:
            parse(path.read_text(), str(path.relative_to(root)))
        except FragmentError as e:
            problems.append(str(e))
    return problems


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def added_at(root, path):
    """When the fragment was first committed (Unix time); one not committed yet is the newest."""
    out = git(root, "log", "--diff-filter=A", "--format=%ct", "-1", "--", str(path.relative_to(root))).stdout.split()
    return int(out[0]) if out else float("inf")


def unreleased_bounds(text):
    """(start, end) of the lines under `## [Unreleased]`, between its heading and the next `## ` heading."""
    match = re.search(r"^## \[Unreleased\][^\n]*\n", text, re.M)
    if not match:
        raise FragmentError("CHANGELOG.md has no ## [Unreleased] heading")
    following = re.compile(r"^## ", re.M).search(text, match.end())
    end = following.start() if following else len(text)
    link = re.compile(r"^\[[^\]]+\]: ", re.M).search(text, match.end(), end)
    return match.end(), link.start() if link else end


def versions(text):
    return [tuple(map(int, v.split("."))) for v in re.findall(r"^## \[?(\d+\.\d+\.\d+)\b", text, re.M)]


def tags_without_notes(root, text):
    """Release tags newer than every section here: their notes are not merged back yet, so a cut would repeat them."""
    newest = max(versions(text), default=(0, 0, 0))
    tags = git(root, "tag", "--list", "v[0-9]*").stdout.split()
    late = [t for t in tags if re.match(r"^v\d+\.\d+\.\d+$", t) and tuple(map(int, t[1:].split("."))) > newest]
    return sorted(late, key=lambda t: tuple(map(int, t[1:].split("."))))


def compile_release(root, version, date):
    """Write `## [version] - date` from the fragments (and anything still under Unreleased), delete them, return them."""
    root = Path(root)
    if not VERSION.match(version):
        raise FragmentError("the version is X.Y.Z, without a v: %r" % version)
    changelog = root / "CHANGELOG.md"
    text = changelog.read_text()
    if re.search(r"^## \[?%s\b" % re.escape(version), text, re.M):
        raise FragmentError("CHANGELOG.md already has a [%s] section" % version)
    if not git(root, "tag", "--list", "v[0-9]*").stdout.split():
        print("warning: no release tags in this checkout, so a newer release whose notes are not merged back cannot be "
              "found; fetch the tags (git fetch --tags) to have that checked", file=sys.stderr)
    late = tags_without_notes(root, text)
    if late and late != ["v" + version]:
        raise FragmentError("%s is tagged but its notes are not in this CHANGELOG.md; merge them back first, or this "
                            "release repeats their lines" % ", ".join(late))
    paths = fragment_paths(root)
    # Newest first within a heading, as the changelog has always read; same-commit fragments by name.
    paths.sort(key=lambda p: (-added_at(root, p), p.name))
    entries = []
    for path in paths:
        entries += parse(path.read_text(), str(path.relative_to(root)))
    start, end = unreleased_bounds(text)
    left = text[start:end].strip()
    if left:  # lines written in CHANGELOG.md before fragments, or by hand: older than any fragment
        entries += parse(left, "CHANGELOG.md [Unreleased]")
    if not entries:
        raise FragmentError("nothing to release: no fragments in %s and nothing under [Unreleased]" % FRAGMENTS)
    section = "\n## [%s] - %s\n\n%s\n" % (version, date, render(entries))
    text = text[:start] + section + text[end:].lstrip("\n")
    previous = re.search(r"^\[Unreleased\]: (\S+)/compare/(v[^.\s]+\.\d+\.\d+)\.\.\.HEAD$", text, re.M)
    if previous:
        base, tag = previous.groups()
        text = text.replace(previous.group(0), "[Unreleased]: %s/compare/v%s...HEAD\n[%s]: %s/compare/%s...v%s" % (
            base, version, version, base, tag, version), 1)
    changelog.write_text(text)
    for path in paths:
        path.unlink()
    return paths


def branch_name(root):
    name = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    name = re.sub(r"^(tico|fix|feat|feature)/", "", name)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")[:60].rstrip("-.")
    if not name or name == "HEAD":
        raise FragmentError("no branch name to name the fragment after; pass --name")
    return name


def add(root, heading, line, name=None):
    heading = heading.strip().capitalize()
    path = Path(root) / FRAGMENTS / ("%s.md" % (name or branch_name(root)))
    entries = parse(path.read_text(), str(path)) if path.exists() else []
    entries += parse("### %s\n- %s\n" % (heading, line.strip().removeprefix("- ")), "the new entry")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(entries))
    return path


def adopt(root, main="origin/main", name=None):
    """Move the entries this branch added under [Unreleased] into its fragment and give CHANGELOG.md back main's copy."""
    root = Path(root)
    base = git(root, "merge-base", main, "HEAD").stdout.strip()
    if not base:
        raise FragmentError("no merge base with %s; fetch it first" % main)

    def unreleased(text):
        start, end = unreleased_bounds(text)
        body = text[start:end].strip()
        return parse(body, "CHANGELOG.md [Unreleased]") if body else []

    before = set(unreleased(git(root, "show", base + ":CHANGELOG.md").stdout))
    # Mid-merge with main, CHANGELOG.md holds conflict markers: this branch's own copy is the one at HEAD.
    unmerged = git(root, "ls-files", "--unmerged", "--", "CHANGELOG.md").stdout.strip()
    ours = git(root, "show", "HEAD:CHANGELOG.md").stdout if unmerged else (root / "CHANGELOG.md").read_text()
    mine = [e for e in unreleased(ours) if e not in before]
    if not mine:
        raise FragmentError("this branch adds nothing under [Unreleased]; if a main merge moved your line into a "
                            "released section, undo that merge, run adopt, commit, then merge main again")
    path = Path(root) / FRAGMENTS / ("%s.md" % (name or branch_name(root)))
    entries = (parse(path.read_text(), str(path)) if path.exists() else []) + mine
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(entries))
    shown = git(root, "show", main + ":CHANGELOG.md")
    if shown.returncode:
        raise FragmentError("cannot read CHANGELOG.md on %s" % main)
    (root / "CHANGELOG.md").write_text(shown.stdout)
    return path, mine


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", default=str(ROOT))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("add", help="add an entry to this branch's fragment")
    p.add_argument("heading", choices=[h.lower() for h in HEADINGS] + list(HEADINGS))
    p.add_argument("line")
    p.add_argument("--name")
    p = sub.add_parser("adopt", help="move this branch's CHANGELOG.md lines into its fragment")
    p.add_argument("--main", default="origin/main")
    p.add_argument("--name")
    sub.add_parser("check", help="check every fragment parses")
    p = sub.add_parser("compile", help="the release commit: fragments into the new version section")
    p.add_argument("version")
    p.add_argument("--date", help="YYYY-MM-DD, default today (UTC)")
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        if args.command == "add":
            print(add(root, args.heading, args.line, args.name).relative_to(root))
        elif args.command == "adopt":
            path, moved = adopt(root, args.main, args.name)
            print("moved %d entr%s to %s; CHANGELOG.md is main's again" % (
                len(moved), "y" if len(moved) == 1 else "ies", path.relative_to(root)))
        elif args.command == "check":
            problems = check(root)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1 if problems else 0
        else:
            import datetime
            date = args.date or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
            used = compile_release(root, args.version.lstrip("v"), date)
            print("[%s] written from %d fragment%s; commit CHANGELOG.md and the deletions with the trailer "
                  "`Changelog-Release: v%s`" % (args.version.lstrip("v"), len(used), "" if len(used) == 1 else "s",
                                               args.version.lstrip("v")))
    except FragmentError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
