"""Meeting search narrows its candidates before loading transcripts, and answers exactly as it did before (I37)."""

import itertools
from pathlib import Path

import pytest

from backend import context_search, meetings
from backend.tests.helpers import meeting_search as fixture
from backend.tests.helpers.api import headers, setup_attempt

PEOPLE = ("ana-test", "ben-test", "cara-test")  # the company owner, a meeting owner, an attendee and listed reader


def search(api, token, **params):
    response = api.get("/api/v2/meetings/search", params=params, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def identity(api, token):
    return api.app.state.auth.authenticate({"authorization": "Bearer " + token}, "/api/v2/meetings/search", "GET")


@pytest.fixture
def archive(api):
    store = api.app.state.store
    _, _, attempt = setup_attempt(api)
    bot = attempt["token"]
    fixture.seed(store, 2 * len(fixture.CASES) + 3, bot=identity(api, bot).actor.removeprefix("bot:"))
    return store, bot


def test_results_match_the_unbounded_search_for_every_caller_and_filter(api, archive):
    store, bot = archive
    searches = [
        {"q": q, "person": person, "since": since, "until": until}
        for q, person, (since, until) in itertools.product(
            ("", "pricing", "Pricing renewal team", "zzz-no-match"),
            ("", "ben", "CARA@ACME", "Priya"),
            (
                (None, None),
                ("2026-02-14", None),
                (None, "2026-03-31"),
                ("2026-01-05", "2026-01-05"),
                ("2027-01-01", None),
            ),
        )
    ]
    pages = (
        {"limit": 20, "offset": 0},
        {"limit": 3, "offset": 0},
        {"limit": 3, "offset": 5},
        {"limit": 1, "offset": 40},
    )
    checked = set()
    for token in (*PEOPLE, bot):
        who = identity(api, token)
        for args, page in itertools.product(searches, pages):
            params = {k: v for k, v in {**args, **page}.items() if v not in (None, "")}
            expected = fixture.reference(store, who, **{**args, **page})
            assert search(api, token, **params) == expected, (token, params)
            checked.update(r["id"] for r in expected["results"])
    # The fixture reaches every readable case for someone, and some searches find nothing.
    assert len(checked) > 10


def test_a_search_outside_every_date_reads_no_transcript_and_few_statements(api, archive):
    store, bot = archive
    for token in ("ana-test", "ben-test", bot):
        with fixture.traced(store) as seen:
            assert search(api, token, since="2027-01-01")["results"] == []
        meeting_reads = [s for s in seen if "FROM meetings" in s]
        assert len(meeting_reads) == 1, meeting_reads
        assert "transcript" not in meeting_reads[0] and "SELECT *" not in meeting_reads[0]
        # The search's own statements, after the sign-in reads (a bot's credential checks take ten on their own).
        assert len(seen[seen.index("BEGIN") :]) <= 3, seen
        assert token == bot or len(seen) <= 10, seen


def test_a_search_that_finds_nothing_reads_meetings_per_batch_not_per_meeting(api):
    store = api.app.state.store
    fixture.seed(store, 300, turns=3)
    with fixture.traced(store) as seen:
        assert search(api, "ana-test", q="zzz-no-match")["results"] == []
    assert len(seen) <= 30, len(seen)
    assert not [s for s in seen if "meeting_deliveries" in s or "media_control WHERE meeting_id" in s]


def test_each_candidate_is_case_folded_once_for_a_three_word_search(api, archive, monkeypatch):
    store, _ = archive
    folded = []
    original = context_search.search_text

    def counted(row, body):
        folded.append(row["id"])
        return original(row, body)

    monkeypatch.setattr(context_search, "search_text", counted)
    result = search(api, "ana-test", q="pricing renewal zzz-no-match")
    assert result["results"] == []
    assert folded and len(folded) == len(set(folded))


def test_a_meeting_deleted_or_restricted_mid_search_is_judged_on_one_snapshot(api, archive, monkeypatch):
    store, _ = archive
    ids = [r["id"] for r in search(api, "cara-test", limit=50)["results"]]
    first, last = ids[0], ids[-1]
    original, changed = context_search.transcript_text, []

    def change_the_archive(row):
        if not changed:
            changed.append(row["id"])
            with store.transaction() as c:
                c.execute(
                    "INSERT INTO media_control(meeting_id,deleted_at) VALUES(?, '2026-06-01') "
                    "ON CONFLICT(meeting_id) DO UPDATE SET deleted_at=excluded.deleted_at",
                    (last,),
                )
                meta = meetings.get(ids[-2], c)["metadata"]
                meta.update(audience="participants", audience_emails=["dee@acme.example"])
                meetings.snapshot(meta, conn=c)
        return original(row)

    monkeypatch.setattr(context_search, "transcript_text", change_the_archive)
    during = [r["id"] for r in search(api, "cara-test", limit=50)["results"]]
    assert changed == [first]
    assert during == ids  # the search answers from the archive as it was when it started
    monkeypatch.setattr(context_search, "transcript_text", original)
    after = [r["id"] for r in search(api, "cara-test", limit=50)["results"]]
    assert last not in after and ids[-2] not in after


def test_candidates_walk_the_newest_index_and_the_day_index_without_sorting_the_archive(api, archive):
    store, bot = archive
    for token, params, index in (
        ("ana-test", {"q": "pricing"}, "meetings_newest"),
        ("ben-test", {}, "meetings_newest"),
        (bot, {"until": "2026-02-01"}, "meetings_recorded_day"),
        ("cara-test", {"since": "2026-01-01", "until": "2026-03-01"}, "meetings_recorded_day"),
    ):
        with fixture.traced(store) as seen:
            search(api, token, **params)
        [query] = [s for s in seen if s.startswith("SELECT m.id")]
        with store.read() as c:
            plan = " | ".join(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + query))
        assert f"USING INDEX {index}" in plan, plan
        if index == "meetings_newest":
            assert "TEMP B-TREE" not in plan, plan
        else:  # a seek on the day; a drifted expression or an extra OR term plans as a silent SCAN of the index
            assert f"SEARCH m USING INDEX {index}" in plan and "SCAN m" not in plan, plan


def test_the_day_index_is_on_the_expression_the_search_queries():
    # SQLite serves an expression index only for the same expression, and INDEXED BY fails without it.
    migration = Path(__file__).parents[1] / "migrations/20261112080700_meeting_search_indexes.sql"
    assert f"ON meetings(({context_search.RECORDED_DAY}))" in migration.read_text()
