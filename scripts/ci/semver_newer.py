"""Exit 0 when version A is newer than version B by semver (B empty or unreadable counts as older).

Used by release.yml to offer a release candidate's Recorder to installed Recorders only when it is newer than what
the latest release's manifest offers. `0.6.0-rc.20` > `0.6.0-rc.9` > `0.5.216`, and `0.6.0` > `0.6.0-rc.20`.
"""

import re
import sys

PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$")


def key(version):
    match = PATTERN.match(version.strip())
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    core = (int(major), int(minor), int(patch))
    if pre is None:
        return core, (1,)
    parts = tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in pre.split("."))
    return core, (0, parts)


def newer(a, b):
    left, right = key(a), key(b)
    if left is None:
        return False
    return right is None or left > right


if __name__ == "__main__":
    sys.exit(0 if newer(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "") else 1)
