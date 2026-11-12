"""Measures GET /api/v2/meetings/search on a fictional archive of 10, 100 and 1,000 meetings (I37).

    python3 -m pytest bench/perf-meeting-search.py -n 0 -s -q

The archive (backend/tests/helpers/meeting_search.py) mixes team, private, participants-only, pending-review,
dismissed, deleted, typed-note and bot-attached meetings, each with a transcript of 30 turns (about 6 KB). For the
company owner, a person and a bot it prints, per search, the statements one call runs on the store's read
connections (all of them, then the search's own after the sign-in reads) and the median wall time of RUNS calls,
and checks the answer against the search as it was before the candidates were bounded.
"""

import statistics
import time

from backend.tests.helpers import meeting_search as fixture
from backend.tests.helpers.api import headers, setup_attempt

SIZES = (10, 100, 1000)
RUNS = 15
SEARCHES = {
    "no-match": {"q": "zzz-no-match"},
    "date-excluded": {"since": "2027-01-01"},
    "page-1 match": {"q": "pricing renewal"},
}


def searched(seen):
    """The statements after the request's sign-in reads: from the search's snapshot on, or from its first meeting read."""
    start = seen.index("BEGIN") if "BEGIN" in seen else next(i for i, s in enumerate(seen) if "FROM meetings" in s)
    return len(seen) - start


def test_meeting_search_statements_and_time_by_archive_size(api):
    store = api.app.state.store
    _, _, attempt = setup_attempt(api)
    callers = {"owner": "ana-test", "person": "ben-test", "bot": attempt["token"]}
    auth = api.app.state.auth
    who = {
        name: auth.authenticate({"authorization": "Bearer " + t}, "/api/v2/meetings/search", "GET")
        for name, t in callers.items()
    }
    seeded = 0
    for size in SIZES:
        fixture.seed(store, size - seeded, bot=who["bot"].actor.removeprefix("bot:"), prefix=f"s{size}-")
        seeded = size
        for caller, token in callers.items():
            for label, params in SEARCHES.items():
                expected = fixture.reference(store, who[caller], **params)
                with fixture.traced(store) as seen:
                    first = api.get("/api/v2/meetings/search", params=params, headers=headers(token))
                assert first.status_code == 200, first.text
                assert first.json() == expected, (caller, label)
                walls = []
                for _ in range(RUNS):
                    start = time.perf_counter()
                    api.get("/api/v2/meetings/search", params=params, headers=headers(token))
                    walls.append(time.perf_counter() - start)
                print(
                    f"meetings={size:<5} {caller:<7} {label:<14} statements={len(seen):<5} search={searched(seen):<5} "
                    f"median_ms={statistics.median(walls) * 1000:8.2f} results={len(expected['results'])}",
                    flush=True,
                )
