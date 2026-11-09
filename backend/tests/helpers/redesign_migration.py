"""Helpers that other tests share, moved out of test_redesign_migration.py so they import no test file."""

from backend import redesign_migration as M


def db(api):
    return api.app.state.store


def first_start(api):
    """This release's first start on the data above: the cutover has no record yet."""
    with db(api).transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (M.CUTOVER,))
    return M.cutover(db(api))
