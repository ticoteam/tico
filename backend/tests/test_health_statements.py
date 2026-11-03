"""The Health page reads in set queries: its statements do not grow with the failed runs, events or bots it summarises
(Nov 3: 384 statements and 1.2 s a call on the Tico Team hub, rising with the data)."""
import collections
import re
import uuid

from backend.store import H
from backend.tests.test_claim_statements import seed, traced
from backend.tests.test_api import api  # noqa: F401  (the fixture)
from backend.tests.test_api import headers


def history(api, per_bot):
    """`per_bot` failed and completed runs for every bot, spread over the last days."""
    store = api.app.state.store
    with store.transaction() as c:
        jobs = {r["bot_id"]: r["id"] for r in c.execute("SELECT id,bot_id FROM jobs")}
        runner_id = c.execute("SELECT id FROM runners LIMIT 1").fetchone()["id"]
        n = 0
        for bot, job in jobs.items():
            for i in range(per_bot):
                n += 1
                at = H.shift(H.now(), minutes=-(7 * n % 4000))
                state = ("failed", "completed", "expired", "interrupted")[i % 4]
                c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,"
                          "created_at,started_at,finished_at,result) VALUES(?,?,?,?,0,?,?,?,?,?,?,?)",
                          (uuid.uuid4().hex, job, bot, runner_id, uuid.uuid4().hex, state, at, at, at, at,
                           '{"fallback": "codex"}' if i % 3 == 0 else "{}"))


def health(api):
    with traced(api) as seen:
        response = api.get("/api/v2/health", headers=headers())
    assert response.status_code == 200, response.text
    return response.json(), seen


def shape(sql):
    return re.sub(r"\s+", " ", re.sub(r"'[^']*'|\b\d+\b|\?(,\?)*", "?", sql))[:150]


def test_health_statements_do_not_grow_with_history(api):
    seed(api, "few", 6)
    history(api, 3)
    health(api)                                   # first-time reads
    _, small = health(api)
    seed(api, "many", 34)
    history(api, 40)
    _, large = health(api)
    for sql, n in collections.Counter(shape(s) for s in large).most_common(25):
        print(n, sql)
    print("statements", len(small), len(large))
    assert len(large) <= len(small) + 12, (len(small), len(large))
