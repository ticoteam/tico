"""Print one version's section of CHANGELOG.md, the body of its GitHub release."""
import re
import sys
from pathlib import Path


def section(text, version):
    """A version's notes; a missing prerelease section may use only its matching base release."""
    lines, out, on = text.splitlines(), [], False
    for line in lines:
        if line.startswith("## "):
            if on:
                break
            on = re.match(r"## \[?" + re.escape(version.lstrip("v")) + r"(?=\]|\s|$)", line) is not None
            continue
        if on and not re.match(r"\[[^\]]+\]: ", line):
            out.append(line)
    if not on:
        base = re.fullmatch(r"(\d+\.\d+\.\d+)-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*", version.lstrip("v"))
        if base:
            return section(text, base.group(1))
    return "\n".join(out).strip()


if __name__ == "__main__":
    notes = section(Path(sys.argv[2] if len(sys.argv) > 2 else "CHANGELOG.md").read_text(), sys.argv[1])
    if not notes:
        sys.exit("CHANGELOG.md has no notes for " + sys.argv[1])
    print(notes)
