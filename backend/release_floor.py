"""The oldest release Tico supports (v0.6.0), and the one check every place that takes a release to run, install or
update to makes. Standard library only: scripts/release_train.py loads this file directly. scripts/install.sh and
scripts/journey-test.sh make the same check in shell."""

import re

FLOOR = (0, 6, 0)
STABLE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def refused(value):
    """Why `value` is not a supported release, in plain words; None for a stable X.Y.Z (a leading v is fine) at v0.6.0
    or later. A prerelease (rc) or a malformed value is refused the same way as an older release."""
    text = str(value or "").strip()
    match = STABLE.match(text)
    if not match:
        return "%s is not a release version (like v0.6.1)" % (repr(text) if text else "An empty version")
    if tuple(int(part) for part in match.groups()) < FLOOR:
        return "v%s is older than v0.6.0, and no release before v0.6.0 is supported" % text.lstrip("v")
    return None
