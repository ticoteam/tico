"""The status line under a chat (thread_bot_status._step) says the same word as a full replay of the run's newest events
(turns.reply_parts), without decoding a big output (backend/step_briefs.py)."""

import json
import random

import pytest

from backend import step_briefs, thread_bot_status
from backend.tests.helpers.api import assign, ready, runner  # noqa: F401  (fixture, helpers)
from backend.tests.helpers.run_view import events, task_attempt
from backend.tests.helpers.step_briefs import add, database, replayed

BIG = 512 * 1024


class Decoded:
    """Counts the characters thread_bot_status hands to json.loads."""

    def __init__(self, monkeypatch):
        self.chars = 0
        real = json.loads

        def loads(text, *a, **k):
            self.chars += len(text) if isinstance(text, (str, bytes)) else 0
            return real(text, *a, **k)

        monkeypatch.setattr(thread_bot_status.json, "loads", loads)


def tool(n, status="completed", output_size=0, **more):
    return {"tool": "Bash", "summary": f"npm test --shard {n}", "status": status, "item_id": f"t{n}",
            "output": "x" * output_size, **more}  # fmt: skip


def test_sixty_big_outputs_read_the_same_word_from_a_few_kilobytes(monkeypatch):
    c = database()
    for n in range(59):
        add(c, n + 1, "tool", tool(n, output_size=BIG))
    add(c, 60, "tool", tool(59, status="started", output_size=BIG))
    word = replayed(c, "a")
    decoded = Decoded(monkeypatch)
    assert thread_bot_status._step(c, "a") == word == "Ran Bash"
    assert decoded.chars <= 64 * 1024  # it was 60 × 512 KiB, about 31.5 MB, before briefs


@pytest.mark.parametrize(
    "rows, word",
    [
        ([], thread_bot_status.STARTING),
        ([("tool", tool(1, status="started")), ("delta", {"delta_kind": "thought", "text": "hm"})], "Thinking"),
        ([("delta", {"text": "Hel"}), ("delta", {"text": "lo"})], "Writing a reply"),
        ([("tool", tool(1, status="started", summary="x " * 200))], None),
        # A closing message that repeats a piece typed across several briefed deltas: the deltas are read again and
        # joined as typed, so the repeat adds nothing and the tool call stays the step.
        (
            [
                ("delta", {"text": "a" * 1000}),
                ("delta", {"text": "b"}),
                ("tool", tool(1, status="started")),
                ("message", {"text": "a" * 1000 + "b"}),
            ],
            "Ran Bash",
        ),
    ],
)
def test_each_word_is_as_before(monkeypatch, rows, word):
    monkeypatch.setattr(step_briefs, "SMALL", 0)  # every event has a brief
    c = database()
    for n, (kind, payload) in enumerate(rows):
        add(c, n + 1, kind, payload)
    assert thread_bot_status._step(c, "a") == replayed(c, "a")
    if word:
        assert thread_bot_status._step(c, "a") == word


TEXTS = ["", " ", "\n", "Hello", "Hello ", " Hello", "world", "x" * 1500, " " * 1200, "y" * 1500 + " ", " "]
STATUSES = ["started", "running", "completed", "failed", "", "in_progress", "Error"]


def random_run(rng, length):
    rows = []
    for _ in range(length):
        roll = rng.random()
        if roll < 0.45:
            payload = {"text": rng.choice(TEXTS)}
            if rng.random() < 0.15:
                payload["delta_kind"] = "thought"
            if rng.random() < 0.03:
                payload = {"text": rng.choice([None, 7, {"a": 1}])}
            rows.append(("delta", payload))
        elif roll < 0.7:
            # A closing message often repeats what was typed before it, or an earlier piece.
            earlier = [p.get("text") for k, p in rows if k in ("delta", "message") and isinstance(p.get("text"), str)]
            text = rng.choice(earlier + TEXTS) if earlier and rng.random() < 0.5 else rng.choice(TEXTS)
            rows.append(("message", {"text": text}))
        else:
            key = rng.choice(["t1", "t2", "t3", None, "", 4, f"u{rng.random()}"])
            payload = {
                "tool": rng.choice(["Bash", "Read", "mcp__tico__tico_task_show", "  "]),
                "summary": rng.choice(["git status", "npm test", "", "x " * 300]),
                rng.choice(["status", "state", "phase"]): rng.choice(STATUSES),
                "item_id": key,
                "output": "o" * rng.choice([0, 10, 2000]),
            }
            if rng.random() < 0.1:
                payload["parent_id"] = "a1"
            rows.append(("tool", payload))
    return rows


@pytest.mark.parametrize("seed", range(40))
def test_every_window_of_a_random_run_reads_as_the_replay(monkeypatch, seed):
    rng = random.Random(seed)
    monkeypatch.setattr(step_briefs, "SMALL", rng.choice([0, 0, 900]))
    c = database()
    for n, (kind, payload) in enumerate(random_run(rng, rng.choice([5, 30, 90]))):
        # Some events predate briefs (no brief kept); others carry a big text or output.
        add(c, n + 1, kind, payload, brief=rng.random() < 0.8)
        assert thread_bot_status._step(c, "a") == replayed(c, "a"), (seed, n)


def test_a_rewritten_or_deleted_event_drops_its_brief(monkeypatch):
    monkeypatch.setattr(step_briefs, "SMALL", 0)
    c = database()
    add(c, 1, "message", {"text": "Here is the secret plan " + "x" * 1000})
    add(c, 2, "tool", tool(2, status="started", output_size=2000))
    assert c.execute("SELECT count(*) FROM attempt_event_briefs").fetchone()[0] == 2
    # A held reply wiped, or a credential scrubbed: the brief goes with what it described.
    c.execute("UPDATE attempt_events SET payload=? WHERE seq=2", (json.dumps(tool(2, status="completed")),))
    assert c.execute("SELECT seq FROM attempt_event_briefs").fetchall() == [(1,)]
    assert thread_bot_status._step(c, "a") == replayed(c, "a") == "Writing a reply"
    c.execute("DELETE FROM attempt_events WHERE seq=1")
    assert c.execute("SELECT count(*) FROM attempt_event_briefs").fetchone()[0] == 0
    assert thread_bot_status._step(c, "a") == replayed(c, "a") == thread_bot_status.STARTING


def test_a_runner_s_big_output_is_kept_with_its_brief(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    _, attempt = task_attempt(api, machine)
    aid = attempt["id"]
    events(
        api,
        machine,
        aid,
        [
            ("delta", {"text": "Let me run the tests"}),
            ("tool", tool(1, status="started", output_size=BIG)),
            ("tool", tool(1, status="completed", output_size=BIG)),
        ],
    )
    with api.app.state.store.read() as c:
        # A new file gets the triggers too: the migration runs before the store makes attempt_events.
        assert {
            r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE tbl_name='attempt_events' AND type='trigger'")
        } >= {
            "attempt_event_briefs_rewritten",
            "attempt_event_briefs_deleted",
        }
        kept = c.execute(
            "SELECT seq, brief FROM attempt_event_briefs WHERE attempt_id=? ORDER BY seq", (aid,)
        ).fetchall()
        assert [(seq, json.loads(brief)) for seq, brief in kept] == [
            (2, {"s": "started", "i": "t1", "l": "Ran Bash"}),
            (3, {"s": "completed", "i": "t1", "l": "Ran Bash"}),
        ]
        assert thread_bot_status._step(c, aid) == replayed(c, aid) == "Ran Bash"
