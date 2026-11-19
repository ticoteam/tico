"""GET /api/v2/runner-credential-grants reads a computer's bots in one snapshot (the company-wide tool, key, grant and
settings reads once, not once per bot) and answers byte for byte what each bot read on its own answered, names in the
same order; and the batch `tool_settings.names_each` answers what `names` answers for each subject alone."""

import collections
import contextlib
import random

from fastapi.responses import JSONResponse

from backend import default_read, tool_settings
from backend.auth import Identity
from backend.credentials import granted
from backend.tests.helpers.api import headers
from backend.tests.helpers.runner_grants import reference, seed

BOTS = ["tools-owner"] + [f"bot{n:02d}" for n in range(47)]


@contextlib.contextmanager
def statements(store):
    """Every statement the store's read connections run meanwhile."""
    seen, opened = [], store.read

    @contextlib.contextmanager
    def traced():
        with opened() as c:
            c.set_trace_callback(seen.append)
            try:
                yield c
            finally:
                c.set_trace_callback(None)

    store.read = traced
    try:
        yield seen
    finally:
        store.read = opened


def answer(api, runner_id):
    token = "runner-" + runner_id
    api.app.state.store.settings.test_identities[token] = Identity("runner:" + runner_id, "runner", runner_id=runner_id)
    r = api.get("/api/v2/runner-credential-grants", headers=headers(token))
    assert r.status_code == 200, r.text
    return r


def test_one_pass_answers_byte_for_byte_what_each_bot_read_alone_answered(api):
    store = api.app.state.store
    for seed_value in (3, 11):
        rng = random.Random(seed_value)
        with store.transaction() as c:
            c.execute("DELETE FROM credential_grants")
            c.execute("DELETE FROM tool_settings")
            c.execute("DELETE FROM credentials")
            c.execute("DELETE FROM tools")
            unsealed, held_by_someone, logins = seed(c, BOTS, rng, tools=40, runners=4)
        assert unsealed and held_by_someone and logins  # the fixture has the keys a bot never gets
        held_any = settings_any = 0
        for runner_id in ("r0", "r1", "r2", "r3"):
            expected = reference(store, runner_id)
            with statements(store) as seen:
                r = answer(api, runner_id)
            assert r.content == JSONResponse(expected).body  # the same bytes: bots and names in the same order
            held_any += sum(bool(n) for n in expected["bots"].values())
            settings_any += sum(
                any(n.endswith(("_HOST", "_REGION", "_ID")) for n in v) for v in expected["bots"].values()
            )
            # Once a request, whatever the number of bots: the company-wide reads and the bots' grants.
            counts = collections.Counter(seen)
            assert len(expected["bots"]) == 12
            assert counts[default_read.CANDIDATES_SQL] == 1
            assert counts[tool_settings.KEY_TOOLS_SQL] == 1
            assert sum(k for s, k in counts.items() if "FROM credential_grants g" in s) == 1
            assert sum(k for s, k in counts.items() if "FROM tool_settings s" in s) <= 1
        assert held_any > 20 and settings_any > 5  # the fixture exercises keys and settings, not empty answers

        # The batch answers what the per-subject helper answers, for every kind of subject and for none.
        with store.read() as c:
            subjects = ["bot:" + b for b in BOTS] + ["human:ana", "human:ben", "computers"]
            helds = [granted(c, s) for s in subjects] + [frozenset(), frozenset({"nothing"})]
            alone = [tool_settings.names(c, h) for h in helds]
            assert tool_settings.names_each(c, helds) == alone
            assert tool_settings.names_each(c, [frozenset()]) == [[]] and tool_settings.names_each(c, []) == []
