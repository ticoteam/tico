"""The migration that ends structured questions (backend/migrations/20261117094959_no_structured_questions.py): on a
file migrated to just before it, each question still open is kept in `old_questions` for the next start to take back
(backend/old_questions.py, tested in backend/tests/test_question_writers.py), every `answer` requirement goes with the
rows that hang off it, an OK and a ❓'s snooze stay, `requirements` loses `choices` and the `answer` kind,
`strategy_merges` goes, and running it again changes nothing."""

import importlib
import shutil
import sqlite3

import pytest

from backend import events
from backend import migrations

NAME = "20261117094959_no_structured_questions"
MIGRATION = importlib.import_module(f"backend.migrations.{NAME}")
NOW = "2026-11-17T00:00:00.000000Z"


def _directory(directory, *, through):
    """This code's migrations up to and including `through`, with the baseline and the floor."""
    directory.mkdir(exist_ok=True)
    for migration in migrations.files():
        if migration.version <= through:
            shutil.copy(migration.path, directory / migration.path.name)
    for name in (migrations.BASELINE, migrations.RETIRED_LIST):
        shutil.copy(migrations.DIRECTORY / name, directory / name)


def _requirement(c, rid, kind, *, visit="v1", stage=None, person="human:ana", choices=None, withdrawn=None):
    row = {
        "id": rid,
        "stage_id": stage,
        "visit_id": None if stage else visit,
        "kind": kind,
        "who_oks": "people_with_exceptions",
        "person_id": person,
        "added_by_id": "bot:builder",
        "by_bot": 1,
        "line": "Which?",
        "created_at": NOW,
        "withdrawn_at": withdrawn,
    }
    if choices:
        row["choices"] = choices
    c.execute(f"INSERT INTO requirements ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))


def _ids(c, sql):
    return sorted(row[0] for row in c.execute(sql))


def _just_before(tmp_path):
    directory = tmp_path / "migrations"
    before = max(m.version for m in migrations.files() if m.version < NAME)
    _directory(directory, through=before)
    conn = sqlite3.connect(tmp_path / "hub.sqlite", isolation_level=None)
    migrations.apply(conn, directory)
    assert "choices" in [row[1] for row in conn.execute("PRAGMA table_info(requirements)")]
    return conn, directory


def _task(c, task_id, visit, *, status="open", left=None):
    c.execute("INSERT INTO tasks (id, title, owner_id) VALUES (?, 'Write the note', 'bot:builder')", (task_id,))
    if status != "open":  # only move() changes a task's status: its guard lets this one write through
        c.execute("UPDATE task_move_guards SET open=1, task_id=? WHERE id=1", (task_id,))
        c.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
        c.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")
    c.execute(
        "INSERT INTO visits (id, task_id, stage_id, entered_at, entered_how, left_at, how) "
        "VALUES (?, ?, 'general-todo', ?, 'new', ?, ?)",
        (visit, task_id, NOW, left, "met" if left else None),
    )


def test_each_open_question_is_kept_for_the_next_start_to_carry_over(tmp_path):
    conn, directory = _just_before(tmp_path)
    for n, (status, left) in enumerate(
        (("open", None), ("open", None), ("closed", None), ("open", NOW), ("open", None))
    ):
        _task(conn, f"t{n}", f"v{n}", status=status, left=left)
    _requirement(conn, "q-open", "answer", visit="v0", choices='["A","B"]')
    _requirement(conn, "q-answered", "answer", visit="v1")
    conn.execute(
        "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, created_at) "
        "VALUES ('a1','v1','q-answered','human:ana','A',?)",
        (NOW,),
    )
    _requirement(conn, "q-closed", "answer", visit="v2")  # its task is closed
    _requirement(conn, "q-left", "answer", visit="v3")  # its task left that stage
    _requirement(conn, "q-withdrawn", "answer", visit="v4", withdrawn=NOW)
    _requirement(conn, "ok-1", "ok", visit="v4")

    _directory(directory, through=NAME)
    migrations.apply(conn, directory)

    kept = conn.execute("SELECT id, visit_id, task_id, added_by_id, line, choices FROM old_questions")
    assert [tuple(row) for row in kept] == [("q-open", "v0", "t0", "bot:builder", "Which?", '["A","B"]')]
    assert _ids(conn, "SELECT id FROM requirements") == ["ok-1"]
    MIGRATION.up(conn)
    assert _ids(conn, "SELECT id FROM old_questions") == ["q-open"], "a second run keeps nothing twice"


def test_the_open_questions_read_their_answers_by_visit(tmp_path):
    conn, _ = _just_before(tmp_path)
    conn.execute(MIGRATION.OLD_QUESTIONS)
    plan = " ".join(str(row[-1]) for row in conn.execute("EXPLAIN QUERY PLAN " + MIGRATION.KEEP_OPEN))
    assert "answers_visit" in plan and "SCAN a" not in plan, plan


def test_answer_rows_go_with_what_hangs_off_them_and_oks_stay(tmp_path):
    conn, directory = _just_before(tmp_path)

    # Questions the carry-over has withdrawn or that were answered, a stage's own typed answer, and a person's OK
    # beside them.
    _requirement(conn, "q-open", "answer", choices='["A","B"]', withdrawn=NOW)
    _requirement(conn, "q-done", "answer", visit="v2")
    _requirement(conn, "q-stage", "answer", stage="s1", person=None)
    _requirement(conn, "ok-1", "ok", visit="v3")
    conn.execute(
        "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, created_at) VALUES "
        "('a1','v2','q-done','human:ana','A',?), ('a2','v3','ok-1','human:ana','ok',?)",
        (NOW, NOW),
    )
    for rid, visit in (("q-done", "v2"), ("ok-1", "v3")):
        conn.execute(
            "INSERT INTO ask_opinions (visit_id, requirement_id, task_id, ask_version, state, started_at, created_at, "
            "updated_at) VALUES (?,?,'t1','x','ready',?,?,?)",
            (visit, rid, NOW, NOW, NOW),
        )
    for rid, visit in (("q-open", "v1"), ("mark-1", "qmark")):
        conn.execute(
            "INSERT INTO ask_snoozes (person_id, visit_id, requirement_id, task_id, asked_at, ask_version, "
            "snoozed_until, created_at, updated_at) VALUES ('human:ana',?,?,'t1',?,'x',?,?,?)",
            (visit, rid, NOW, NOW, NOW, NOW),
        )
    conn.execute(
        "INSERT INTO ok_grants (requirement_id, asked_by_id, subject, content_hash, created_at) "
        "VALUES ('ok-1','bot:builder','merge','h',?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO strategy_merges (merged_into, original, person, asker, created_at) "
        "VALUES ('g1','g2','human:ana','bot:strategy',?)",
        (NOW,),
    )
    history = conn.execute("SELECT count(*) FROM history_events").fetchone()[0]

    _directory(directory, through=NAME)
    migrations.apply(conn, directory)

    assert _ids(conn, "SELECT id FROM requirements") == ["ok-1"]
    assert _ids(conn, "SELECT id FROM answers") == ["a2"]
    assert _ids(conn, "SELECT requirement_id FROM ask_opinions") == ["ok-1"]
    assert _ids(conn, "SELECT requirement_id FROM ask_snoozes") == ["mark-1"]  # a ❓'s snooze is not a requirement's
    assert _ids(conn, "SELECT requirement_id FROM ok_grants") == ["ok-1"]
    assert conn.execute("SELECT count(*) FROM history_events").fetchone()[0] == history, "history stays"
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "strategy_merges" not in tables and "requirements_new" not in tables
    assert "choices" not in [row[1] for row in conn.execute("PRAGMA table_info(requirements)")]
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='requirements'").fetchone()[0]
    assert "'answer'" not in sql and "'related_input'" in sql
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"requirements_stage", "requirements_visit", "requirements_one_per_bot"} <= indexes

    # The change log makes its requirements triggers again from the table, and a write goes through them.
    events.ensure(conn)
    conn.execute("UPDATE requirements SET line='OK the note' WHERE id='ok-1'")
    with pytest.raises(sqlite3.IntegrityError):  # the answer kind is out of the check
        _requirement(conn, "q-new", "answer")

    schema = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    MIGRATION.up(conn)
    assert conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall() == schema
    assert _ids(conn, "SELECT id FROM requirements") == ["ok-1"]
