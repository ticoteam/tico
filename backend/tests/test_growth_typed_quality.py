"""A month typed without a quality mark is an estimate, never "actual" (Product task #292, spec
file-603ad4a3c04628384f981880 v2). Acme's agent records its months with `tico growth cohort set`; only a number it says
came from its books is actual. Each test names the spec's criterion it checks."""

import re
from pathlib import Path

from backend import growth_sync as GS
from backend import hubdb as H
from backend.tests.helpers.api import get, post, put

M = "2026-10"


def channel(api, slug="google-search"):
    post(api, "growth/channels", {"slug": slug, "name": slug.replace("-", " ").title()})


def cohort(api, slug, month=M):
    rows = get(api, f"growth/cohorts?channel={slug}&month={month}")["cohorts"]
    return rows[0] if rows else None


def marks(row):
    """(the month's mark, {number: origin · quality})."""
    return row["quality"], {k: f"{o['origin']}·{o['quality']}" for k, o in row["origins"].items()}


def stored(api, slug, month=M):
    """What the database holds, read past the API: the row's quality and each origin's."""
    with api.app.state.store.read() as c:
        row = c.execute(
            "SELECT g.id, g.quality FROM growth_cohorts g JOIN growth_channels ch ON ch.id=g.channel_id "
            "WHERE ch.slug=? AND g.month=?",
            (slug, month),
        ).fetchone()
        origins = dict(c.execute("SELECT field, quality FROM growth_origins WHERE cohort_id=?", (row[0],)).fetchall())
    return row[1], origins


def test_c1_an_unmarked_month_is_saved_as_an_estimate(api):
    """C1 (the API the command calls; test_hubcli shows the command sends no quality)."""
    channel(api)
    saved = put(api, "growth/cohorts", {"channel": "google-search", "month": M, "spend": 1800, "new_customers": 6})
    assert marks(saved) == ("estimate", {"spend": "typed·estimate", "new_customers": "typed·estimate"})
    assert marks(cohort(api, "google-search")) == marks(saved)


def test_c2_a_null_quality_is_an_estimate_too(api):
    """C2: the MCP tool may send quality null."""
    channel(api)
    body = {"channel": "google-search", "month": M, "spend": 1800, "new_customers": 6, "quality": None}
    assert put(api, "growth/cohorts", body)["quality"] == "estimate"
    assert cohort(api, "google-search")["quality"] == "estimate"


def test_c3_a_month_said_to_be_actual_is_actual(api):
    """C3."""
    channel(api)
    body = {"channel": "google-search", "month": M, "spend": 1800, "new_customers": 6, "quality": "actual"}
    assert marks(put(api, "growth/cohorts", body)) == (
        "actual",
        {"spend": "typed·actual", "new_customers": "typed·actual"},
    )


def test_c4_an_unmarked_number_leaves_the_other_numbers_marks_alone(api):
    """C4: spend typed as actual stays actual when an agent later sets new customers with no mark; the month then reads
    estimate because one of its numbers is."""
    channel(api)
    put(api, "growth/cohorts", {"channel": "google-search", "month": M, "spend": 1800, "quality": "actual"})
    later = put(api, "growth/cohorts", {"channel": "google-search", "month": M, "new_customers": 7})
    assert marks(later) == ("estimate", {"spend": "typed·actual", "new_customers": "typed·estimate"})
    assert stored(api, "google-search") == ("estimate", {"spend": "actual", "new_customers": "estimate"})


def test_c5_changing_only_where_a_number_came_from_keeps_the_month_actual(api):
    """C5."""
    channel(api)
    put(api, "growth/cohorts", {"channel": "google-search", "month": M, "spend": 1800, "quality": "actual"})
    noted = put(api, "growth/cohorts", {"channel": "google-search", "month": M, "sources": {"spend": "Google Ads"}})
    assert marks(noted) == ("actual", {"spend": "typed·actual"})
    assert noted["sources"] == {"spend": "Google Ads"}


def test_c6_a_synced_number_replaces_an_unmarked_typed_guess(api):
    """C6: an agent types spend 4000 with no mark; the source then syncs 4200. The source's number wins, with nothing
    held beside it for its owner."""
    channel(api, "search-ads")
    m = GS._month(GS._index(GS.this_month()) - 1)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": m, "spend": 4000})
    src = post(
        api,
        "growth/sources",
        {"truth_for": "spend", "system": "Ad spend sheet", "sync_kind": "file", "sync_map": {"fields": {"spend": "s"}}},
    )
    result = post(api, f"growth/sources/{src['id']}/rows", {"csv": f"month,channel,s\n{m},search-ads,4200\n"})
    assert (result["written"], result["held"]) == (1, 0)
    row = cohort(api, "search-ads", m)
    assert row["spend"] == 4200 and marks(row) == ("actual", {"spend": "synced·actual"})
    assert row["origins"]["spend"]["conflict"] is None


def test_c7_months_saved_before_this_release_keep_their_marks_through_the_migrations(tmp_path):
    """C7: months saved the old way (an unmarked row took the column default 'actual') keep every mark when the
    database is opened again and its migrations run, and no migration writes a growth quality. The reopen guards the
    schema code that runs on every start; the scan of every migration statement and file is what catches a future
    migration that rewrites a quality (this release adds none)."""
    path = tmp_path / "hub.db"
    conn = H.connect(path)
    now = "2026-10-01T00:00:00Z"
    conn.execute(
        "INSERT INTO growth_cohorts (id, month, spend, updated_at) VALUES ('old-default', '2026-08', 900, ?)", (now,)
    )
    conn.execute(
        "INSERT INTO growth_cohorts (id, month, spend, quality, updated_at) VALUES ('old-est', '2026-09', 700, 'estimate', ?)",
        (now,),
    )
    for cid, quality in (("old-default", "actual"), ("old-est", "estimate")):
        conn.execute(
            "INSERT INTO growth_origins (cohort_id, field, origin, quality, by, at) VALUES (?, 'spend', 'typed', ?, 'bot:ops', ?)",
            (cid, quality, now),
        )
    before = (
        [tuple(r) for r in conn.execute("SELECT id, quality FROM growth_cohorts ORDER BY id")],
        [
            tuple(r)
            for r in conn.execute("SELECT cohort_id, field, origin, quality FROM growth_origins ORDER BY cohort_id")
        ],
    )
    assert before[0] == [("old-default", "actual"), ("old-est", "estimate")]
    conn.close()
    again = H.connect(path)
    after = (
        [tuple(r) for r in again.execute("SELECT id, quality FROM growth_cohorts ORDER BY id")],
        [
            tuple(r)
            for r in again.execute("SELECT cohort_id, field, origin, quality FROM growth_origins ORDER BY cohort_id")
        ],
    )
    again.close()
    assert after == before
    writes = re.compile(r"UPDATE\s+growth_(cohorts|origins)\s+SET[^;]*quality", re.I | re.S)
    assert not [i for i, m in enumerate(H.MIGRATIONS) if writes.search(m)]
    folder = Path(H.__file__).parent / "migrations"
    assert not [p.name for p in folder.glob("*.py") if writes.search(p.read_text())]


def test_c9_the_docs_say_a_month_is_an_estimate_unless_marked_actual():
    """C9 (the docs; test_growth_setup checks the first-run step, test_hubcli the command's help and the MCP tool)."""
    docs = (Path(H.__file__).parent.parent / "docs" / "growth.md").read_text()
    assert "is saved as an `estimate`" in docs and "pass `--quality actual`" in docs


def test_c10_no_route_saves_an_unmarked_month_as_actual(api):
    """C10: the column default is still 'actual'; every way of saving a month without a mark (no quality, quality
    null, a new month with only where its numbers came from) stores estimate, row and origins, read from the
    database."""
    for slug in ("missing", "null", "sources-only"):
        channel(api, slug)
    put(api, "growth/cohorts", {"channel": "missing", "month": M, "spend": 10, "new_customers": 1})
    put(api, "growth/cohorts", {"channel": "null", "month": M, "spend": 10, "new_customers": 1, "quality": None})
    put(api, "growth/cohorts", {"channel": "sources-only", "month": M, "sources": {"spend": "a guess"}})
    assert stored(api, "missing") == ("estimate", {"spend": "estimate", "new_customers": "estimate"})
    assert stored(api, "null") == ("estimate", {"spend": "estimate", "new_customers": "estimate"})
    assert stored(api, "sources-only") == ("estimate", {})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM growth_cohorts WHERE quality='actual'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM growth_origins WHERE quality='actual'").fetchone()[0] == 0


def test_c5_a_null_mark_on_an_existing_actual_month_keeps_it_actual(api):
    """C5 with quality null (the MCP tool may send it): only sources change, the month stays actual."""
    channel(api)
    put(api, "growth/cohorts", {"channel": "google-search", "month": M, "spend": 1800, "quality": "actual"})
    body = {"channel": "google-search", "month": M, "sources": {"spend": "Google Ads"}, "quality": None}
    assert marks(put(api, "growth/cohorts", body)) == ("actual", {"spend": "typed·actual"})
    assert stored(api, "google-search") == ("actual", {"spend": "actual"})


def test_c4_a_number_saved_before_origins_were_kept_keeps_its_actual_mark(api):
    """C4 for a month saved before per-number origins were kept (no origin rows): an unmarked new number makes the
    month an estimate, but the old spend stays actual, and clearing the new number makes the month actual again."""
    channel(api)
    put(api, "growth/cohorts", {"channel": "google-search", "month": M, "spend": 1800, "quality": "actual"})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM growth_origins")
    later = put(api, "growth/cohorts", {"channel": "google-search", "month": M, "new_customers": 7})
    assert marks(later) == ("estimate", {"spend": "typed·actual", "new_customers": "typed·estimate"})
    cleared = put(api, "growth/cohorts", {"channel": "google-search", "month": M, "new_customers": None})
    assert marks(cleared) == ("actual", {"spend": "typed·actual"})


def test_c4_an_old_estimate_keeps_its_estimate_mark(api):
    """C4's other side for a month saved before origins were kept: an old estimate stays an estimate, so clearing the
    new unmarked number does not turn the month actual."""
    channel(api)
    put(api, "growth/cohorts", {"channel": "google-search", "month": M, "spend": 1800, "quality": "estimate"})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM growth_origins")
    put(api, "growth/cohorts", {"channel": "google-search", "month": M, "new_customers": 7})
    cleared = put(api, "growth/cohorts", {"channel": "google-search", "month": M, "new_customers": None})
    assert marks(cleared) == ("estimate", {"spend": "typed·estimate"})
