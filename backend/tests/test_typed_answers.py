"""Typed answers on a stage (backend/answers.py, backend/task_requirements.py `typed` and `values`, docs/pipelines-v3.md
"The six requirement kinds", kind 6).

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company's two people and three bots, and a
two-stage pipeline saved through its settings record.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from backend import answers as A
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_requirements as R
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

ANA, BEN = X.ANA, "human:ben"
CONFIDENCE = {"name": "confidence", "type": "number", "min": 0, "max": 100, "question": "How sure are you?"}


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        for slug in ("builder", "reviewer", "manager"):
            X.bot(conn, slug)
        yield conn


@pytest.fixture
def eng(c):
    """Build asks its owner for Confidence; Review asks Ben how big it was, one of two choices."""
    saved = PS.save(
        c,
        ANA,
        {
            "name": "Engineering",
            "prefix": "EN",
            "manager_id": "bot:manager",
            "stages": [
                {
                    "name": "Build",
                    "owner": "bot:builder",
                    "requirements": [{"kind": "answer", "line": "Confidence", "settings": CONFIDENCE}],
                },
                {
                    "name": "Review",
                    "owner": "bot:reviewer",
                    "requirements": [
                        {
                            "kind": "answer",
                            "line": "Size",
                            "person_id": BEN,
                            "settings": {"name": "size", "type": "choice"},
                            "choices": ["Small", "Large"],
                        }
                    ],
                },
            ],
        },
    )
    build, review = saved["stages"]
    return {
        "id": saved["id"],
        "build": build["id"],
        "review": review["id"],
        "confidence": build["requirements"][0]["id"],
        "size": review["requirements"][0]["id"],
        "record": saved,
    }


def _refused(words):
    return pytest.raises(H.Refused, match=words)


def _task(c, eng):
    return X.new_task(c, ANA, "Build the importer", eng["id"])


def _version(c, task_id):
    return E.task(c, task_id)["version"]


def test_a_stage_asks_its_owner_and_it_is_their_turn_until_they_answer(c, eng):
    row = _task(c, eng)
    assert row["owner_id"] == "bot:builder"
    standing = R.standings(c, row)[0]
    assert (standing.state, standing.on, standing.line) == (R.NOT_MET, "bot:builder", "How sure are you?")
    turn = W.of(c, row)[0]
    assert (turn.who, turn.line, turn.ask) == ("bot:builder", "How sure are you?", False)


def test_a_number_is_whole_and_in_range_and_kept_in_one_form(c, eng):
    row = _task(c, eng)
    for wrong in ("120", "-1", "eighty", "80.5", "1e2", ""):
        with _refused("Confidence is a whole number from 0 to 100" if wrong else "Give an answer"):
            A.answer(c, row["id"], eng["confidence"], "bot:builder", wrong, seen_version=row["version"])
    assert c.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 0
    moved = A.answer(c, row["id"], eng["confidence"], "bot:builder", " 080 ", seen_version=row["version"])
    assert moved["stage"]["name"] == "Review"
    saved = H._one(c, "SELECT answer, name, value_type FROM answers WHERE id=?", (moved["answer_id"],))
    assert saved == {"answer": "80", "name": "confidence", "value_type": "number"}
    found = R.values(c, row["id"])["confidence"]
    assert (found["value"], found["type"], found["by"], found["stage_id"]) == (
        80,
        "number",
        "bot:builder",
        eng["build"],
    )


def test_the_owner_answers_by_name(c, eng):
    row = _task(c, eng)
    moved = H.task_answer(c, "bot:builder", row["id"], "Confidence", "75", seen_version=row["version"])
    assert moved["stage"]["name"] == "Review"
    assert R.values(c, row["id"])["confidence"]["value"] == 75
    with _refused("No such requirement"):  # Review asks for size, not confidence
        H.task_answer(c, BEN, row["id"], "confidence", "75")


def test_the_owner_the_manager_or_a_person_answers_the_owners_question(c, eng):
    row = _task(c, eng)
    with _refused("Only the owner, the manager or a person answers this"):
        A.answer(c, row["id"], eng["confidence"], "bot:reviewer", "50", seen_version=row["version"])
    assert A.answer(c, row["id"], eng["confidence"], "bot:manager", "50", seen_version=row["version"])
    other = _task(c, eng)
    assert A.answer(c, other["id"], eng["confidence"], BEN, "50")["stage"]["name"] == "Review"


def test_a_choice_is_one_of_its_choices_spelled_as_the_choice(c, eng):
    row = _task(c, eng)
    A.answer(c, row["id"], eng["confidence"], "bot:builder", "90", seen_version=row["version"])
    in_review = E.task(c, row["id"])
    standing = R.standings(c, in_review)[0]
    assert (standing.state, standing.on) == (R.WAITING, BEN)
    with _refused("Size is one of: Small, Large"):
        A.answer(c, row["id"], eng["size"], BEN, "Medium")
    with _refused("This is asked of"):
        A.answer(c, row["id"], eng["size"], "bot:reviewer", "Small", seen_version=in_review["version"])
    done = A.answer(c, row["id"], eng["size"], BEN, "small")
    assert done["status"] == "done"
    assert R.values(c, row["id"])["size"]["value"] == "Small"


def test_a_new_visit_asks_again_the_newest_answer_wins_and_undo_moves_it_back(c, eng):
    row = _task(c, eng)
    first = A.answer(c, row["id"], eng["confidence"], "bot:builder", "40", seen_version=row["version"])
    back = E.move(c, row["id"], eng["build"], ANA, "Not ready")
    assert back["stage"]["name"] == "Build"
    assert not R.standings(c, back)[0].met  # asked again
    assert R.values(c, row["id"])["confidence"]["value"] == 40  # until it is answered again
    again = A.answer(c, row["id"], eng["confidence"], "bot:builder", "70", seen_version=back["version"])
    assert again["stage"]["name"] == "Review"
    assert R.values(c, row["id"])["confidence"]["value"] == 70
    undone = A.undo(c, again["answer_id"], "bot:builder")
    assert undone["stage"]["name"] == "Build"
    assert R.values(c, row["id"])["confidence"]["value"] == 40
    assert first["answer_id"]


def test_the_task_read_carries_the_type_and_the_values(c, eng):
    row = _task(c, eng)
    view = H.task_view(c, row["id"], ANA)
    asked = view["to_leave"][0]
    assert asked["typed"] == {
        "name": "confidence",
        "type": "number",
        "min": 0,
        "max": 100,
        "question": CONFIDENCE["question"],
    }
    assert asked["standing"] == "How sure are you?"
    assert "answer" in asked["may"] and "give" not in asked["may"] and "thumbs" not in asked["may"]
    A.answer(c, row["id"], eng["confidence"], "bot:builder", "80", seen_version=row["version"])
    view = H.task_view(c, row["id"], BEN)
    assert view["values"]["confidence"]["value"] == 80
    size = view["to_leave"][0]
    assert size["typed"]["type"] == "choice" and size["choices"] == ["Small", "Large"]
    assert "answer" in size["may"] and "give" not in size["may"] and "thumbs" not in size["may"]


def test_a_stages_answer_takes_no_thumb_and_is_not_given_away(c, eng):
    row = _task(c, eng)
    A.answer(c, row["id"], eng["confidence"], "bot:builder", "80", seen_version=row["version"])
    with _refused("A stage's answer takes no thumb"):
        A.thumbs_down(c, row["id"], eng["size"], ANA, "Not needed")
    with _refused("Size is answered by who the stage names"):
        A.give_to(c, row["id"], eng["size"], BEN, ANA)


def test_the_settings_record_reads_back_an_answer_as_saved(c, eng):
    size = PS.read(c, eng["id"])["stages"][1]["requirements"][0]
    assert (size["kind"], size["person_id"], size["choices"]) == ("answer", BEN, ["Small", "Large"])
    assert size["settings"] == {"name": "size", "type": "choice"}


# ----------------------------------------------------------------------------- the migration
OLD_REQUIREMENTS = (
    "CREATE TABLE requirements (id TEXT PRIMARY KEY, stage_id TEXT REFERENCES stages(id), "
    "visit_id TEXT REFERENCES visits(id), "
    "kind TEXT NOT NULL CHECK (kind IN ('owner_done', 'ok', 'page', 'item', 'linked_done', 'answer')), source TEXT, "
    "condition TEXT, settings TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(settings)), "
    "can_push_past INTEGER NOT NULL DEFAULT 1, who_oks TEXT NOT NULL DEFAULT 'none', person_id TEXT, "
    "added_by_id TEXT, by_bot INTEGER NOT NULL DEFAULT 0, line TEXT NOT NULL DEFAULT '', shows TEXT, choices TEXT, "
    "position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, withdrawn_at TEXT, "
    "CHECK ((stage_id IS NULL) <> (visit_id IS NULL)), CHECK (kind <> 'answer' OR visit_id IS NOT NULL), "
    "CHECK ((kind = 'item') = (source IS NOT NULL AND condition IS NOT NULL)), "
    "CHECK (person_id IS NULL OR kind IN ('ok', 'page', 'answer')))"
)


def _migration():
    path = Path(__file__).resolve().parents[1] / "migrations" / "20261111130000_typed_answers.py"
    spec = importlib.util.spec_from_file_location("typed_answers_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_lets_a_stage_ask_and_keeps_every_row():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stages (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE visits (id TEXT PRIMARY KEY)")
    conn.execute(OLD_REQUIREMENTS)
    conn.execute(
        "CREATE TABLE answers (id TEXT PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id), "
        "requirement_id TEXT NOT NULL REFERENCES requirements(id), by_id TEXT NOT NULL, answer TEXT NOT NULL, "
        "notes TEXT, page_version INTEGER, created_at TEXT NOT NULL, undone_at TEXT)"
    )
    conn.execute("CREATE TABLE log (what TEXT)")
    conn.execute("CREATE TRIGGER answers_log AFTER INSERT ON answers BEGIN INSERT INTO log VALUES (NEW.answer); END")
    conn.execute("INSERT INTO stages VALUES ('s1')")
    conn.execute("INSERT INTO visits VALUES ('v1')")
    conn.executemany(
        "INSERT INTO requirements (id, stage_id, visit_id, kind, source, condition, choices, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            ("r1", "s1", None, "owner_done", None, None, None, "t1"),
            ("r2", "s1", None, "item", "github", "pr_open", None, "t2"),
            ("r3", None, "v1", "answer", None, None, '["Yes", "No"]', "t3"),
        ],
    )
    conn.execute("INSERT INTO answers VALUES ('a1', 'v1', 'r3', 'human:ana', 'Yes', NULL, NULL, 't4', NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO requirements (id, stage_id, kind, created_at) VALUES ('x', 's1', 'answer', 't')")
    before = conn.execute("SELECT * FROM requirements ORDER BY id").fetchall()
    migration = _migration()
    migration.up(conn)
    migration.up(conn)  # a second run changes nothing
    assert conn.execute("SELECT * FROM requirements ORDER BY id").fetchall() == before
    conn.execute("INSERT INTO requirements (id, stage_id, kind, created_at) VALUES ('r4', 's1', 'answer', 't5')")
    columns = [row[1] for row in conn.execute("PRAGMA table_info(answers)")]
    assert columns[-2:] == ["name", "value_type"]
    assert conn.execute("SELECT name, value_type FROM answers").fetchall() == [(None, None)]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, created_at, value_type) "
            "VALUES ('a2', 'v1', 'r4', 'bot:b', '8', 't6', 'date')"
        )
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"requirements_stage", "requirements_visit", "requirements_person", "requirements_one_per_bot"} <= indexes
    assert "answers_name" in indexes
    conn.execute(
        "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, created_at, name, value_type) "
        "VALUES ('a3', 'v1', 'r4', 'bot:b', '8', 't6', 'confidence', 'number')"
    )
    assert conn.execute("SELECT what FROM log").fetchall() == [("Yes",), ("8",)]  # the trigger is back
