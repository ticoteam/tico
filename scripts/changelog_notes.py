"""Print one version's section of the release notes, the body of its GitHub release.

The current minor's notes are in CHANGELOG.md; each older minor is in docs/history/CHANGELOG-<minor>.md.
"""
import re
import sys
from pathlib import Path


def section(text, version):
    """A version's notes; a missing prerelease section may use only its matching base release.

    The notes end at the next `## ` heading or at the link references, so the pointer line after them is not notes.
    """
    lines, out, on = text.splitlines(), [], False
    for line in lines:
        if line.startswith("## "):
            if on:
                break
            on = re.match(r"## \[?" + re.escape(version.lstrip("v")) + r"(?=\]|\s|$)", line) is not None
            continue
        if on and re.match(r"\[[^\]]+\]: ", line):
            break
        if on:
            out.append(line)
    if not on:
        base = re.fullmatch(r"(\d+\.\d+\.\d+)-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*", version.lstrip("v"))
        if base:
            return section(text, base.group(1))
    return "\n".join(out).strip()


def notes_files(root=Path(".")):
    """Every file that holds release notes, newest first: CHANGELOG.md, then docs/history/CHANGELOG-<minor>.md."""
    def minor(path):
        return tuple(int(part) for part in path.stem.removeprefix("CHANGELOG-").split(".") if part.isdigit())
    history = sorted((root / "docs" / "history").glob("CHANGELOG-*.md"), key=minor, reverse=True)
    return [root / "CHANGELOG.md", *history]


def first_notes(texts, version):
    """The notes for `version` in the first text that has them, or ""."""
    return next((notes for notes in (section(text, version) for text in texts) if notes), "")


if __name__ == "__main__":
    paths = [Path(sys.argv[2])] if len(sys.argv) > 2 else notes_files()
    notes = first_notes([path.read_text() for path in paths if path.is_file()], sys.argv[1])
    if not notes:
        sys.exit("CHANGELOG.md has no notes for " + sys.argv[1])
    print(notes)
