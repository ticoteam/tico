"""How well each bot asks people (backend/ask_quality.py; Tasks spec "Bots ask people a question with choices", AC-12):
"Badly written" send-backs per bot over that bot's asks to people, in the Decide readout, with the guard that each
refused bot got an ask through within a day."""

from datetime import datetime, timedelta, timezone

from backend import ask_quality as Q
from backend import hubdb as H
from backend.tests.helpers.ask_ingest import gate  # noqa: F401
from backend.tests.helpers.tasks_board import api, get  # noqa: F401


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def ask(c, n):
    """A task of ops' own with one question to Ben on it; returns (task id, requirement id)."""
    made = H.task_create(c, "bot:ops", f"Pricing step {n}", "", "bot:ops", lint=False)
    added = H.task_ask(
        c,
        "bot:ops",
        made["id"],
        "human:ben",
        f"Which pricing page for step {n}?",
        choices=["A", "B"],
        seen_version=made["version"],
    )
    return made["id"], added["requirement_id"]


def refused(c, at, rule="task_ask_shape"):
    try:
        H.refuse(c, "bot:ops", rule, "An ask to a person needs: why it matters")
    except H.Refused:
        pass
    c.execute("UPDATE refusals SET created_at=? WHERE id=(SELECT id FROM refusals ORDER BY rowid DESC LIMIT 1)", (at,))


def test_badly_written_send_backs_are_counted_per_bot_over_its_asks_to_people(gate):  # noqa: F811
    api, _, _ = gate
    now = datetime.now(timezone.utc)
    with api.app.state.store.transaction() as c:
        asked = [ask(c, n) for n in range(4)]
        # Ben sends one back as badly written and one for another reason; only the first counts.
        H.task_thumbs(c, "human:ben", asked[0][0], asked[0][1], False, "Badly written: no why, no choices")
        H.task_thumbs(c, "human:ben", asked[1][0], asked[1][1], False, "Wrong person")
        refused(c, stamp(now - timedelta(days=3)))  # nothing got through in the day after: stuck
        refused(c, stamp(now - timedelta(hours=1)), "task_rule")  # the asks above followed it within the day
        refused(c, stamp(now - timedelta(hours=1)), "lint")  # not an ask's refusal
    with api.app.state.store.read() as c:
        [row] = Q.per_bot(c, stamp(now - timedelta(days=7)), stamp(now + timedelta(days=2)))
    assert (row["bot"], row["asks"], row["badly_written"], row["share"]) == ("bot:ops", 4, 1, 0.25)
    assert (row["refused"], row["stuck"]) == (2, 1)
    # A refusal less than a day before the window's end is too recent to tell.
    with api.app.state.store.read() as c:
        [recent] = Q.per_bot(c, stamp(now - timedelta(days=2)), stamp(now + timedelta(minutes=1)))
    assert (recent["refused"], recent["stuck"]) == (1, 0)


def test_the_decide_readout_names_each_bots_share_and_the_guard(gate):  # noqa: F811
    api, _, _ = gate
    with api.app.state.store.transaction() as c:
        first = ask(c, 1)
        H.task_thumbs(c, "human:ben", first[0], first[1], False, "Badly written")
    out = get(api, "judge/readout?days=21")
    [row] = out["asks_to_people"]
    assert (row["asks"], row["badly_written"], row["share"], row["stuck"]) == (1, 1, 1.0, 0)
    assert "badly written (100%)" in out["text"] and "Target: missed by" in out["text"]
    assert "Guard: every refused bot asked again within a day" in out["text"]


def test_a_thumbs_down_counts_in_the_window_its_badly_written_reason_was_given(gate):  # noqa: F811
    api, _, _ = gate
    now = datetime.now(timezone.utc)
    with api.app.state.store.transaction() as c:
        made, req = ask(c, 1)
        H.task_thumbs(c, "human:ben", made, req, False, "Badly written: no choices")
        # The 👎 was made ten days ago; its reason was set (or changed) to "Badly written" an hour ago.
        c.execute(
            "UPDATE feedbacks SET created_at=?, updated_at=? WHERE subject_type='ask'",
            (stamp(now - timedelta(days=10)), stamp(now - timedelta(hours=1))),
        )
        c.execute("UPDATE requirements SET created_at=? WHERE id=?", (stamp(now - timedelta(days=2)), req))
    with api.app.state.store.read() as c:
        [row] = Q.per_bot(c, stamp(now - timedelta(days=7)), stamp(now + timedelta(minutes=1)))
    assert (row["asks"], row["badly_written"]) == (1, 1)
