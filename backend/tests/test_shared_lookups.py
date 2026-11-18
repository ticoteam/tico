"""A bot's owners, asked per bot by the "can this person manage it" check on the hot pages, come from one read of
bot_config per snapshot (read_cache.keyed): the same rows as reading each bot alone, a write in the snapshot seen at
once, and a statement count that does not grow with the bots."""

import contextlib
import json

from backend import cloud_access, read_cache
from backend import cloud_machines as CM
from backend.store import H
from backend.tests.helpers import api as T
from backend.tests.helpers import engine as X
from backend.tests.helpers.cloud_access import signals
from backend.tests.helpers.statement_scope import measuring, recorder


def cloud_bots(api, slugs):
    """A Tico Cloud company whose bots wait on cloud machines: most on the shared one, every third on its own, one of
    those with a DELETE sent. Odd bots belong to Cara, who is no admin."""
    store = api.app.state.store
    cloud_access.apply(store, cloud_access.parse(signals("open")))
    with store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (CM.SIZES_KEY, json.dumps({"sizes": [{"name": "default", "cpu": 2, "memory_gb": 4}], "caps": {}})),
        )
        c.execute(
            "INSERT OR IGNORE INTO cloud_machines(bot,mode,placement,machine_id,state,changed_by,updated_at) "
            "VALUES(?,'cloud','shared','m-shared','running','tico',?)",
            (CM.SHARED, H.now()),
        )
        for n, slug in enumerate(slugs):
            X.bot(c, slug, computer=False)
            c.execute("UPDATE bot_config SET owner_id=? WHERE bot=?", ("cara" if n % 2 else "ana", slug))
            own = n % 3 == 0
            c.execute(
                "INSERT INTO cloud_machines(bot,mode,placement,machine_id,state,changed_by,updated_at) "
                "VALUES(?,'cloud',?,?,'stopped','human:ana',?)",
                (slug, "own" if own else "shared", f"m-{slug}" if own else None, H.now()),
            )
            H.task_create(c, "human:ana", f"Work for {slug}", "Do it.", "bot:" + slug)
        c.execute(
            "INSERT INTO cloud_machine_deletions(machine_id,bot,reason,created_at) VALUES(?,?,'manual',?)",
            (f"m-{slugs[0]}", slugs[0], H.now()),
        )


def alone(c, sql, key):
    return c.execute(sql, (key,)).fetchone()


def test_each_lookup_reads_the_row_one_bot_alone_reads(api):
    """In a snapshot every bot, a missing one included, gets the row its own query gives: the same columns by name and
    by position, nulls kept."""
    slugs = [f"s{n:02d}" for n in range(12)]  # past the one-row reads: the last bots come from the whole read
    cloud_bots(api, slugs)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET bot_owners=NULL WHERE bot=?", (slugs[11],))
        c.execute("UPDATE bot_config SET bot_owners=? WHERE bot=?", (json.dumps(["ben"]), slugs[10]))
    asked = [*slugs, "nobody"]
    with store.read() as c:
        expected = {bot: c.execute(read_cache.OWNERS_SQL, (bot,)).fetchone() for bot in asked}
    assert expected["nobody"] is None and expected[slugs[11]]["bot_owners"] is None
    assert expected[slugs[10]]["bot_owners"] == json.dumps(["ben"])
    with store.read_transaction() as c:
        for bot in asked:
            got, want = read_cache.bot_owners(c, bot), expected[bot]
            assert (got is None) == (want is None), bot
            if want is not None:
                assert got.keys() == want.keys() and tuple(got) == tuple(want), bot
        assert ("keyed", read_cache.OWNERS_SQL) in read_cache.cache(c)


def test_a_write_in_the_snapshot_is_read_back(api):
    """Once a snapshot holds the whole read, a row changed inside the transaction is what the next lookup answers,
    from the table read again after the write."""
    slugs = [f"w{n:02d}" for n in range(12)]
    cloud_bots(api, slugs)
    store = api.app.state.store
    with store.transaction() as c:
        for bot in slugs:
            read_cache.bot_owners(c, bot)
        assert ("keyed", read_cache.OWNERS_SQL) in read_cache.cache(c)
        assert read_cache.bot_owners(c, slugs[11])["owner_id"] == "cara"
        c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot=?", (slugs[11],))
        assert read_cache.bot_owners(c, slugs[11])["owner_id"] == "ben"
        for bot in slugs:
            read_cache.bot_owners(c, bot)
        assert read_cache.bot_owners(c, slugs[11])["owner_id"] == "ben"
    with store.read_transaction() as c:
        for bot in slugs:
            read_cache.bot_owners(c, bot)
        assert read_cache.bot_owners(c, slugs[11])["owner_id"] == "ben"


SHAPES = {
    "owners": "SELECT owner_id,bot_owners FROM bot_config WHERE bot=",
    "owners whole": read_cache.ALL_OWNERS_SQL,
    "owners keys": read_cache.ALL_OWNERS_KEYS_SQL,
}


def counted(api, path, token):
    """The statements of one GET, by the lookups' shapes."""
    store, seen = api.app.state.store, []
    originals = {name: getattr(store, name) for name in ("read", "read_transaction")}

    def traced(opened):
        @contextlib.contextmanager
        def borrowed():
            with opened() as c:
                c.set_trace_callback(recorder(seen))
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        return borrowed

    try:
        for name, opened in originals.items():
            setattr(store, name, traced(opened))
        with measuring(seen):
            response = api.get(path, headers=T.headers(token))
    finally:
        for name, opened in originals.items():
            setattr(store, name, opened)
    assert response.status_code == 200, response.text
    return {name: sum(1 for s in seen if s.startswith(prefix)) for name, prefix in SHAPES.items()}, response.json()


def test_the_lookups_do_not_grow_with_bots_and_answer_as_before(tmp_path, monkeypatch):
    """The bot list, the floor and the employees page run the same owner reads, one-row and whole, for 40 bots as for 20,
    and give the JSON they give with the snapshot read cache off (each bot's rows read alone)."""
    from backend.tests.helpers.api_app import api_client

    measured = {}
    for count in (20, 40):
        (tmp_path / str(count)).mkdir()
        install = api_client(tmp_path / str(count), None)
        try:
            api = next(install)
            cloud_bots(api, [f"b{n:02d}" for n in range(count)])
            for path in ("/api/v2/bots", "/api/v2/company/floor", "/api/employees"):
                for token in ("ana-test", "cara-test"):
                    api.get(path, headers=T.headers(token))  # warm what any first call fills
                    measured[count, path, token], cached = counted(api, path, token)
                    with monkeypatch.context() as off:
                        off.setattr(read_cache, "cache", lambda c: None)
                        alone_answer = api.get(path, headers=T.headers(token)).json()
                    assert cached == alone_answer, (path, token)
        finally:
            install.close()
    for (count, path, token), few in measured.items():
        if count != 20:
            continue
        many = measured[40, path, token]
        assert many == few, (path, token, few, many)
        assert many["owners"] <= 16, (path, token, many)
