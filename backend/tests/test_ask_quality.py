"""How well each bot asks people (backend/ask_quality.py; Tasks spec "Bots ask people a question with choices", AC-12):
"Badly written" send-backs per bot over that bot's asks to people, in the Decide readout, with the guard that each
refused bot got an ask through within a day."""

from datetime import datetime, timedelta, timezone

from backend import ask_quality as Q
from backend import hubdb as H
from backend.tests.helpers import engine as X
from backend.tests.helpers.ask_ingest import gate  # noqa: F401
from backend.tests.helpers.tasks_board import api, get  # noqa: F401


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def ask(c, n):
    """A task of ops' own with one ask to Ben on it, an OK of the page it shows (written without the ask's checks:
    what is counted is the row); returns (task id, requirement id)."""
    made = H.task_create(c, "bot:ops", f"Pricing step {n}", "", "bot:ops", lint=False)
    added = X.ok_ask(
        c,
        made["id"],
        "bot:ops",
        "human:ben",
        f"OK the pricing page for step {n}?",
        shows=X.shown(c, f"Pricing step {n}", bot="ops"),
    )
    return made["id"], added["requirement_id"]


def thumbs_down(c, task_id, requirement_id, reason):
    """Ben's 👎 on the ask, as a row from before asks lost their thumbs: a `feedbacks` row of subject `ask`
    (`<visit id>:<requirement id>`) that stays as history."""
    visit = c.execute("SELECT visit_id FROM requirements WHERE id=?", (requirement_id,)).fetchone()[0]
    at = H.now()
    c.execute(
        "INSERT INTO feedbacks(id,person_id,subject_type,subject_id,bot_id,rating,reason,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (H.new_id(), "human:ben", "ask", f"{visit}:{requirement_id}", "bot:ops", "down", reason, at, at),
    )


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
        thumbs_down(c, asked[0][0], asked[0][1], "Badly written: no why")
        thumbs_down(c, asked[1][0], asked[1][1], "Wrong person")
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
        thumbs_down(c, first[0], first[1], "Badly written")
    out = get(api, "decisions/readout?days=21")
    [row] = out["asks_to_people"]
    assert (row["asks"], row["badly_written"], row["share"], row["stuck"]) == (1, 1, 1.0, 0)
    assert "badly written (100%)" in out["text"] and "Target: missed by" in out["text"]
    assert "Guard: every refused bot asked again within a day" in out["text"]


def test_a_thumbs_down_counts_in_the_window_its_badly_written_reason_was_given(gate):  # noqa: F811
    api, _, _ = gate
    now = datetime.now(timezone.utc)
    with api.app.state.store.transaction() as c:
        made, req = ask(c, 1)
        thumbs_down(c, made, req, "Badly written: unclear")
        # The 👎 was made ten days ago; its reason was set (or changed) to "Badly written" an hour ago.
        c.execute(
            "UPDATE feedbacks SET created_at=?, updated_at=? WHERE subject_type='ask'",
            (stamp(now - timedelta(days=10)), stamp(now - timedelta(hours=1))),
        )
        c.execute("UPDATE requirements SET created_at=? WHERE id=?", (stamp(now - timedelta(days=2)), req))
    with api.app.state.store.read() as c:
        [row] = Q.per_bot(c, stamp(now - timedelta(days=7)), stamp(now + timedelta(minutes=1)))
    assert (row["asks"], row["badly_written"]) == (1, 1)


def test_a_question_mark_is_an_ask_and_one_after_a_refusal_means_the_bot_is_not_stuck(gate):  # noqa: F811
    """A question is a message marked ❓ now (backend/question_marks.py): it counts as the bot's ask, and as the ask that
    got through after a refusal."""
    from backend import question_marks as QM

    api, _, _ = gate
    now = datetime.now(timezone.utc)
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, "bot:ops", "Launch note", "", "bot:ops", lint=False)
        refused(c, stamp(now - timedelta(days=2)))
        mark = QM.ask(c, "bot:ops", made["id"], "human:ben", "Which tone should the launch note use?")
        c.execute(
            "UPDATE question_marks SET created_at=? WHERE id=?", (stamp(now - timedelta(days=2, hours=-1)), mark["id"])
        )
    with api.app.state.store.read() as c:
        [row] = Q.per_bot(c, stamp(now - timedelta(days=3)), stamp(now + timedelta(hours=1)))
    assert (row["bot"], row["asks"], row["refused"], row["stuck"]) == ("bot:ops", 1, 1, 0)
