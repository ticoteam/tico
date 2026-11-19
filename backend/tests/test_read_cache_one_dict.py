"""hubdb._one (read_cache.one_dict): each caller gets a dict of its own, equal to the stored row, while the row's dict is
built once a snapshot. A write in the snapshot drops it; a row a page primed as a dict is copied as before."""

from backend import read_cache
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get  # noqa: F401  (the api fixture)

SQL = "SELECT * FROM bots WHERE id=?"


def test_each_caller_gets_its_own_dict_of_the_row_and_a_write_reads_it_again(api):
    store = api.app.state.store
    with store.transaction() as c:
        X.bot(c, "helper", computer=False)
    with store.read_transaction() as c:
        stored = dict(c.execute(SQL, ("helper",)).fetchone())
        first = H._one(c, SQL, ("helper",))
        assert first == stored
        first["display_name"] = "changed by a caller"
        second = H._one(c, SQL, ("helper",))
        assert second == stored and second is not first  # one caller's change is never another's row
        assert H._one(c, SQL, ("nobody",)) is None
    with store.transaction() as c:
        assert H._one(c, SQL, ("helper",)) == stored
        c.execute("UPDATE bots SET display_name='Renamed' WHERE id='helper'")
        assert H._one(c, SQL, ("helper",))["display_name"] == "Renamed"  # the write dropped the kept dict
    with store.read_transaction() as c:
        read_cache.prime(c, SQL, {("primed",): {"id": "primed", "tags": ["a"]}})
        primed = H._one(c, SQL, ("primed",))
        assert primed == {"id": "primed", "tags": ["a"]}
        primed["id"] = "x"
        assert H._one(c, SQL, ("primed",))["id"] == "primed"
    with store.read() as c:  # outside a snapshot: read each time
        assert H._one(c, SQL, ("helper",))["display_name"] == "Renamed"
