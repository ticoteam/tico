"""Returning freed pages to the disk (store.reclaim_free_pages): incremental mode only, bounded per step and per pass."""
import sqlite3

from backend.config import Settings
from backend.store import Store, reclaim_free_pages

ROW = b"x" * 4000     # one row is about one 4 KB page of its own


def store(path):
    s = Store(Settings(db_path=path))
    s.initialize(seed_market=False)
    return s


def pragma(s, name):
    with s.read() as c:
        return c.execute(f"PRAGMA {name}").fetchone()[0]


def big_freelist(s, rows=3000, keep=100):
    """A table of `rows` pages, then all but `keep` rows deleted: the freed pages sit on the freelist."""
    with s.transaction() as c:
        c.execute("CREATE TABLE bulk(id INTEGER PRIMARY KEY, v BLOB)")
        c.executemany("INSERT INTO bulk(v) VALUES(?)", [(ROW,)] * rows)
    with s.transaction() as c:
        c.execute("DELETE FROM bulk WHERE id>?", (keep,))


def test_a_pass_shrinks_the_file_and_leaves_the_floor(tmp_path):
    s = store(tmp_path / "hub.sqlite")
    big_freelist(s)
    before_pages, before_free = pragma(s, "page_count"), pragma(s, "freelist_count")
    assert before_free > 2500
    returned = reclaim_free_pages(s, step=500, floor=100)
    assert pragma(s, "freelist_count") == 100
    assert returned == before_free - 100
    assert pragma(s, "page_count") <= before_pages - returned


def test_the_cap_bounds_one_pass_and_the_next_takes_up_the_rest(tmp_path):
    s = store(tmp_path / "hub.sqlite")
    big_freelist(s)
    before_free = pragma(s, "freelist_count")
    page = pragma(s, "page_size")
    assert reclaim_free_pages(s, step=300, floor=100, most=1000 * page) == 1000
    assert pragma(s, "freelist_count") == before_free - 1000
    assert reclaim_free_pages(s, step=300, floor=100, most=1000 * page) == 1000
    assert pragma(s, "freelist_count") == before_free - 2000


def test_a_database_without_incremental_mode_is_untouched(tmp_path):
    path = tmp_path / "plain.sqlite"
    raw = sqlite3.connect(str(path), isolation_level=None)
    raw.execute("CREATE TABLE bulk(id INTEGER PRIMARY KEY, v BLOB)")
    raw.executemany("INSERT INTO bulk(v) VALUES(?)", [(ROW,)] * 3000)
    raw.execute("DELETE FROM bulk WHERE id>100")
    raw.close()
    s = Store(Settings(db_path=path))
    assert pragma(s, "auto_vacuum") == 0
    before = (pragma(s, "page_count"), pragma(s, "freelist_count"))
    assert before[1] > 2500
    assert reclaim_free_pages(s) == 0
    assert (pragma(s, "page_count"), pragma(s, "freelist_count")) == before


def test_a_fresh_store_is_created_with_incremental_auto_vacuum(tmp_path):
    s = store(tmp_path / "hub.sqlite")
    assert pragma(s, "auto_vacuum") == 2
