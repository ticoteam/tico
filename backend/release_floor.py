# pyright: strict
"""The oldest release Tico supports (v0.6.0), and the one check every place that takes a release to run, install or
update to makes. Standard library only: scripts/release_train.py loads this file directly. scripts/install.sh,
scripts/journey-test.sh, setup/cell.py and infra/windows/install-wsl.ps1 make the same check of their own."""

import re

FLOOR = (0, 6, 0)
VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?$")


def refused(value: object) -> str | None:
    """Why `value` is not a supported release, in plain words; None for a version at v0.6.0 or later by semver (a leading
    v is fine). A prerelease counts below its release: v0.6.0-rc.1 is refused, v0.6.259-rc.1 and v0.7.0-rc.1 are not.
    A malformed value is refused too."""
    text = str(value or "").strip()
    match = VERSION.match(text)
    if not match:
        return "%s is not a release version (like v0.6.1)" % (repr(text) if text else "An empty version")
    number = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if number < FLOOR or (number == FLOOR and match.group(4)):
        return "v%s is older than v0.6.0, and no release before v0.6.0 is supported" % text.lstrip("v")
    return None
