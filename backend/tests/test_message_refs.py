"""The typed refs keys a message is routed, queued and fenced by (`message_refs`, migration 0051)."""
import pytest

from backend.enqueue import _sql_zero
from backend.store import H
from backend.tests.test_api import api  # noqa: F401


def _typed(api, refs):
    with api.app.state.store.transaction() as c:
        mid = H.new_id()
        c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
                  "VALUES(?,NULL,'human:ana','human:ben','say','words',?,?)", (mid, refs, H.now()))
        row = c.execute("SELECT ref_task_id, ref_wake, ref_quiet, ref_command, ref_live_kind FROM message_refs "
                        "WHERE id=?", (mid,)).fetchone()
        assert c.execute("SELECT count(*) FROM message_refs WHERE id=?", (mid,)).fetchone()[0] == 1
    return tuple(row)


@pytest.mark.parametrize("refs, want", [
    (None, (None, None, 0, 0, None)),
    ("{}", (None, None, 0, 0, None)),
    ('{"task":"t1","wake":"status_check"}', ("t1", "status_check", 0, 0, None)),
    ('{"task":["t2","t3"]}', ("t2", None, 0, 0, None)),            # a list names its first task, as _task_ref
    ('{"task_id":"t4"}', (None, None, 0, 0, None)),                # only `task` is the message's own task key
    ('{"command":true,"live":{"kind":"transcript"}}', (None, None, 0, 1, "transcript")),
    ('{"quiet":true}', (None, None, 1, 0, None)),
    ("[1,2]", (None, None, 0, 0, None)),
])
def test_message_refs_types_the_routing_keys(api, refs, want):
    assert _typed(api, refs) == want


@pytest.mark.parametrize("quiet", ["true", "false", "0", "0.0", "1", '""', '"no"', "[]", "{}", "null"])
def test_ref_quiet_is_the_queue_s_own_rule(api, quiet):
    import json
    assert _typed(api, '{"quiet":' + quiet + "}")[2] == (0 if _sql_zero(json.loads(quiet)) else 1)


def test_a_messages_row_reads_as_it_always_did(api):
    """The typed keys are on the view only: `SELECT *` on messages, which the API and the runner wire serve, is
    unchanged, and every `messages` column is on the view under its own name."""
    with api.app.state.store.read() as c:
        stored = [d[0] for d in c.execute("SELECT * FROM messages LIMIT 0").description]
        typed = [d[0] for d in c.execute("SELECT * FROM message_refs LIMIT 0").description]
    assert not [name for name in stored if name.startswith("ref_")]
    assert typed == stored + ["ref_task_id", "ref_wake", "ref_quiet", "ref_command", "ref_live_kind"]
