"""Home's Needs you, its unread card and the rail each read the threads a person is named in (threads.read_threads). One
request reads each conversation row once (H.conversation_rows): on TIDY (Nov 19) a person named in thousands of threads
read them three times, fifteen 500-row statements a Home read."""

from backend import threads
from backend.auth import Identity
from backend.store import H


def _conversation_reads(seen):
    return [sql for sql in seen if sql.startswith("SELECT * FROM conversations WHERE id")]


def test_threads_read_once_a_snapshot_with_the_same_rows(api):
    store = api.app.state.store
    with store.transaction() as c:
        ids = [
            threads.post(c, "human:ana", None, f"Topic {n} @ben", start={"title": f"T{n}", "audience": "company"})[
                "conversation_id"
            ]
            for n in range(3)
        ]
    who = Identity("human:ben", "human", "ben@acme.example")
    with store.read() as c:
        plain, plain_members = threads.read_threads(c, who, ids + ["missing"])  # no snapshot: read as before
    with store.read_transaction() as c:
        seen = []
        c.set_trace_callback(seen.append)
        try:
            H.prime_conversations(c, ids[:2])
            first, members = threads.read_threads(c, who, ids + ["missing"])
            again, _ = threads.read_threads(c, who, list(reversed(ids)))
        finally:
            c.set_trace_callback(None)
    assert len(_conversation_reads(seen)) == 2, seen  # the two primed, then the third alone; none again
    assert first == plain and members == plain_members and set(first) == set(ids)
    assert again == {cid: first[cid] for cid in reversed(ids)}
