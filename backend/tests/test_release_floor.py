"""The v0.6.0 floor every place that takes a release to run, install or update to checks (backend/release_floor.py)."""

import pytest

from backend.release_floor import refused


@pytest.mark.parametrize("version", ["v0.6.0", "0.6.0", "v0.6.264", "v0.6.259-rc.1", "v0.7.0-rc.1", "v1.0.0"])
def test_v0_6_0_and_later_are_accepted_prereleases_above_the_floor_too(version):
    assert refused(version) is None


@pytest.mark.parametrize("version", ["v0.6.0-rc.1", "v0.5.216", "0.2.5"])
def test_a_release_below_v0_6_0_is_refused_and_v0_6_0_rc_counts_below_it(version):
    assert "older than v0.6.0" in refused(version)


@pytest.mark.parametrize("version", ["", None, "latest", "main", "v0.6", "v0.6.1-", "v0.6.1 rc", "vv0.6.1"])
def test_a_malformed_version_is_refused(version):
    assert "is not a release version" in refused(version)
