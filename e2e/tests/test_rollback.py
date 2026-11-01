"""Rollback (e2e/README.md "rollback"): a backup-API copy of a populated database boots and serves the same scenario.

TODO: the real rollback boots the snapshot with the previous release's server (`--old-server-ref`, a git ref). That
needs the old release's checkout and its own migrations path, so until it is built this kind only checks that the
current code boots and serves a copy. The option is passed through as TICO_E2E_OLD_SERVER_REF and makes the test skip."""
import os

import pytest

from backend.tests.test_api import api  # noqa: F401  (the fixture)
from e2e import scenario
from e2e.normalize import normalize
from e2e.server import serve, settings_for, snapshot


@pytest.mark.slow
def test_a_copy_taken_with_the_backup_api_boots_and_serves_the_scenario(api, tmp_path):  # noqa: F811
    old_ref = os.environ.get("TICO_E2E_OLD_SERVER_REF")
    if old_ref:
        pytest.skip("TODO: boot the snapshot with the server at %s (not built yet)" % old_ref)
    ids = scenario.populate(api)
    source = api.app.state.store.settings.db_path
    scenario.populate_direct(source)
    names = {k: v for k, v in ids.items() if not k.startswith("_")}
    # The fixture seeds rows after its own boot: one boot settles them, and the expected answers come from that state.
    raw = tmp_path / "raw" / "hub.sqlite"
    snapshot(source, raw)
    with serve(settings_for(tmp_path / "settle", raw)) as client:
        before = {name: normalize(entry, names) for name, entry in scenario.capture(client, ids).items()}
    copy = tmp_path / "rollback" / "hub.sqlite"
    snapshot(raw, copy)
    with serve(settings_for(tmp_path / "rollback-settings", copy)) as client:
        assert client.get("/healthz").status_code == 200
        after = {name: normalize(entry, names) for name, entry in scenario.capture(client, ids).items()}
    assert after == before
