"""Compact run steps for browsers (Perf I42): a stream that asks `output=compact` is sent, for each run step, which
step moved (its id, sequence, kind and size) and never its contents, however large; every other `runs` event, the
order, the change numbers and the resume stay as they were. A stream that does not ask keeps the whole step, and a
person who may read the run reads the whole step on demand (GET /api/v2/runs/{run_id}/steps/{seq}) under the checks the
stream applies. Fictional people only: ana (owner), cara (human)."""

import json

from backend import events as E
from backend import org_rule_check
from backend.tests.helpers.api import headers, post, setup_attempt  # noqa: F401  (fixture, helpers)
from backend.tests.helpers.thread_events import who

BIG = 512 * 1024  # the audit's tool output


def write(api, r, aid, *steps):
    """Run steps as a runner writes them: (seq, kind, payload)."""
    post(
        api,
        f"attempts/{aid}/events",
        {"events": [{"seq": seq, "kind": kind, "payload": payload} for seq, kind, payload in steps]},
        token=r["token"],
    )


def started_run(api):
    r, msg, attempt = setup_attempt(api)
    with api.app.state.store.read() as c:
        after = E.latest(c)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t1"}, token=r["token"])
    return r, msg, attempt["id"], after


def read(api, token, after, compact, snapshot=None, limit=E.BATCH):
    """`runs` events `token`'s person may be sent after `after`, as (seq, data, encoded bytes): every read a stream
    makes until it has caught up (a large step waits for a read of its own), or one read of `limit` rows."""
    got, more = [], True
    while more:
        events, after, more = read_once(api, token, after, compact, snapshot, limit)
        got += events
        if limit != E.BATCH:
            break
    return got, after


def read_once(api, token, after, compact, snapshot, limit):
    auth, person = api.app.state.auth, who(api, token)
    with api.app.state.store.read() as c:
        kwargs = dict(
            topics=("runs",),
            task_views=lambda rows, c, who, visible_sql="1": [],
            task_view=None,
            compact_runs=compact,
            limit=limit,
        )
        if snapshot is None:
            events, cursor, _, more = E.read(c, auth, person, after, **kwargs)
        else:
            c.execute("BEGIN")
            try:
                events, cursor, _, more = E.read_in(c, snapshot(c), auth, person, after, **kwargs)
            finally:
                c.rollback()
    return [(seq, data, data.encoded) for seq, topic, data in events if topic == "runs"], cursor, more


def test_a_browser_is_sent_a_compact_signal_for_a_half_megabyte_tool_output(api):
    """AC-1 and AC-5: under 2 KB and still output for the compact stream; the whole step for one that did not ask."""
    r, msg, aid, after = started_run(api)
    payload = {"name": "bash", "output": "x" * BIG}
    write(api, r, aid, (1, "tool", payload))
    compact, _ = read(api, "ana-test", after, True)
    full, _ = read(api, "ana-test", after, False)
    [(seq, small, encoded)] = [e for e in compact if "output" in e[1]]
    assert len(encoded.encode()) < 2048, len(encoded)
    assert small["output"] and small["output"]["compact"] is True and "payload" not in small["output"]
    assert small["run_id"] == aid and small["conversation_id"] == msg["conversation_id"] and small["bot_id"] == "ops"
    assert small["output"]["seq"] == 1 and small["output"]["kind"] == "tool" and small["output"]["id"]
    assert small["output"]["bytes"] > BIG
    [(full_seq, whole, full_encoded)] = [e for e in full if "output" in e[1]]
    assert full_seq == seq and whole["output"]["payload"] == payload and len(full_encoded) > BIG


def test_every_other_runs_event_order_and_seq_is_the_same_compact_or_not(api):
    """AC-3: start, finish and job state byte for byte; and the same events at the same change numbers in order."""
    r, msg, aid, after = started_run(api)
    write(api, r, aid, (1, "delta", {"text": "Working", "delta_kind": "text"}), (2, "tool", {"output": "y" * 70_000}))
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Done", "last_seq": 2}, token=r["token"])
    compact, _ = read(api, "ana-test", after, True)
    full, _ = read(api, "ana-test", after, False)
    assert [seq for seq, _, _ in compact] == [seq for seq, _, _ in full]
    states = [(seq, enc) for seq, data, enc in full if "output" not in data]
    assert states and states == [(seq, enc) for seq, data, enc in compact if "output" not in data]
    assert {data["output"]["seq"] for _, data, _ in compact if "output" in data} == {1, 2}


def test_the_shared_tail_and_the_sql_reader_send_the_same_compact_events(api):
    """AC-2: the same change rows served by either reader give the same compact events, in order, at the same seq."""
    r, msg, aid, after = started_run(api)
    write(api, r, aid, *[(n, "tool", {"output": str(n) * 5000}) for n in range(1, 6)])
    sql, _ = read(api, "ana-test", after, True, snapshot=lambda c: E._Snapshot(c))
    tail, _ = read(api, "ana-test", after, True, snapshot=lambda c: E._Snapshot(c, [E._Poll(None, None, after, ())]))
    assert sql and [(s, e) for s, _, e in sql] == [(s, e) for s, _, e in tail]


def test_a_stream_that_resumes_gets_the_same_compact_events_as_one_that_never_dropped(api):
    """AC-4: resuming from a change number sends what an unbroken stream sent after it, compact."""
    r, msg, aid, after = started_run(api)
    write(api, r, aid, *[(n, "tool", {"output": "z" * 3000}) for n in range(1, 4)])
    first, cursor = read(api, "ana-test", after, True, limit=2)
    write(api, r, aid, *[(n, "tool", {"output": "z" * 3000}) for n in range(4, 7)])
    rest, _ = read(api, "ana-test", cursor, True)
    whole, _ = read(api, "ana-test", after, True)
    assert [(s, e) for s, _, e in first + rest] == [(s, e) for s, _, e in whole]
    assert all(data["output"]["compact"] for _, data, _ in whole if "output" in data)


def test_a_reader_of_the_run_reads_a_step_whole_on_demand_and_nobody_else_does(api):
    """AC-6: the whole stored step, unchanged, for whom the stream would send it; nothing for anyone else."""
    r, msg, aid, after = started_run(api)
    payload = {"name": "bash", "output": "w" * BIG}
    write(api, r, aid, (1, "tool", payload))
    got = api.get(f"/api/v2/runs/{aid}/steps/1", headers=headers())
    assert got.status_code == 200, got.text
    full, _ = read(api, "ana-test", after, False)
    streamed = json.loads(json.dumps([d for _, d, _ in full if "output" in d][0]["output"]))
    step = got.json()["output"]  # the public API names a run `run_id` (runs.public_json); the stream keeps the column
    assert step.pop("run_id") == streamed.pop("attempt_id") == aid
    assert step == streamed and step["payload"] == payload
    # Cara may not read ana's room with ops, and no step of it reaches her by either door.
    assert api.get(f"/api/v2/runs/{aid}/steps/1", headers=headers("cara-test")).status_code == 404
    assert not [d for _, d, _ in read(api, "cara-test", after, True)[0] if "output" in d]
    # A bot's own run token is not a person's page; a step that does not exist is not found.
    assert api.get(f"/api/v2/runs/{aid}/steps/1", headers=headers(r["token"])).status_code == 403
    assert api.get(f"/api/v2/runs/{aid}/steps/9", headers=headers()).status_code == 404
    assert api.get("/api/v2/runs/no-such-run/steps/1", headers=headers()).status_code == 404


def test_a_reply_of_a_bot_under_org_rules_is_sent_and_read_by_nobody_until_delivered(api, monkeypatch):
    """AC-7 and AC-6: no event for its reply step, compact or full, and no raw read; its tool steps still go."""
    r, msg, aid, after = started_run(api)
    write(api, r, aid, (1, "delta", {"text": "Secret draft", "delta_kind": "text"}), (2, "tool", {"output": "ok"}))
    monkeypatch.setattr(org_rule_check, "watched", lambda c, bot: True)
    for compact in (True, False):
        steps = [d["output"]["seq"] for _, d, _ in read(api, "ana-test", after, compact)[0] if "output" in d]
        assert steps == [2], (compact, steps)
    assert api.get(f"/api/v2/runs/{aid}/steps/1", headers=headers()).status_code == 404
    assert api.get(f"/api/v2/runs/{aid}/steps/2", headers=headers()).status_code == 200


def test_the_stream_takes_output_full_or_compact_only(api):
    assert api.get("/api/v2/events?output=everything", headers=headers()).status_code == 422


def test_a_compact_read_loads_no_more_stored_output_than_one_read_carries(api):
    """A compact stream is sent none of a step's payload, but its read still loads and checks each one: a stream behind
    on large outputs takes them a READ_BYTES budget at a time, as a full stream does, not all in one read."""
    r, msg, aid, after = started_run(api)
    write(api, r, aid, *[(n, "tool", {"output": "v" * 200_000}) for n in range(1, 6)])
    for compact in (True, False):
        events, cursor, more = read_once(api, "ana-test", after, compact, None, E.BATCH)
        steps = [d["output"]["seq"] for _, d, _ in events if "output" in d]
        assert more and 1 <= len(steps) < 5, (compact, steps)
    caught_up, _ = read(api, "ana-test", after, True)  # the later reads bring the rest, in order
    assert [d["output"]["seq"] for _, d, _ in caught_up if "output" in d] == [1, 2, 3, 4, 5]


def test_a_compact_step_counts_its_size_in_utf8_bytes(api):
    r, msg, aid, after = started_run(api)
    write(api, r, aid, (1, "tool", {"output": "é" * 1000}))
    [small] = [d for _, d, _ in read(api, "ana-test", after, True)[0] if "output" in d]
    with api.app.state.store.read() as c:
        stored = c.execute("SELECT payload FROM attempt_events WHERE attempt_id=? AND seq=1", (aid,)).fetchone()[0]
    assert small["output"]["bytes"] == len(stored.encode()) > len(stored)
