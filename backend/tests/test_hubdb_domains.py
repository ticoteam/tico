"""The domains split out of hubdb.py: every moved name still reads as `H.<name>`, each domain module loads without
hubdb (so hubdb can import it at its top), and moved code still reaches hubdb's own helpers at call time, so a test
that patches `H.now` or `H.new_id` patches the moved writers too."""

import subprocess
import sys
from pathlib import Path

import pytest

from backend import hubdb as H
from backend import hubdb_history, hubdb_quiet_notes, hubdb_task_rooms
from backend.tests.helpers.hubdb import EMPLOYEES, PEOPLE

ROOT = Path(__file__).resolve().parents[2]

MOVED = {
    hubdb_history: (
        "HISTORY_SUBJECTS",
        "MARKET_SUBJECTS_SQL",
        "TASK_EVENT_COLUMNS",
        "history_event",
        "_task_event",
        "_strategy_changed",
        "task_history",
    ),
    hubdb_task_rooms: (
        "_room_person",
        "_room_reads",
        "_task_started",
        "task_from_chat",
        "_task_room",
        "give_task_room",
    ),
    hubdb_quiet_notes: (
        "NOTE_MAX",
        "NOTE_WAITING_SQL",
        "NOTE_CARRIER_SQL",
        "note_create",
        "note",
        "note_waiting",
        "note_cancel",
        "notes_waiting",
        "notes_settle",
        "notes_settle_catch_up",
        "notes_waiting_counts",
    ),
}


@pytest.mark.parametrize(("module", "name"), [(m, n) for m, names in MOVED.items() for n in names])
def test_each_moved_name_is_the_same_object_through_hubdb(module, name):
    assert getattr(H, name) is getattr(module, name)
    if callable(getattr(module, name)):
        assert getattr(module, name).__module__ == module.__name__


@pytest.mark.parametrize("module", sorted(m.__name__ for m in MOVED))
def test_a_domain_module_loads_without_hubdb(module):
    probe = f"import sys, {module}; print('backend.hubdb' in sys.modules)"
    done = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "False", done.stderr


@pytest.fixture
def conn(tmp_path):
    from backend.store import Store
    from backend.tests.helpers.api import api_settings

    Store(api_settings(tmp_path)).initialize()
    c = H.connect(tmp_path / "hub.db")
    H.sync_registry(c, EMPLOYEES, PEOPLE)
    yield c
    c.close()


def test_moved_writers_read_hubdb_now_and_new_id_when_they_run(conn, monkeypatch):
    monkeypatch.setattr(H, "now", lambda: "2026-11-27T06:00:00.000000Z")
    monkeypatch.setattr(H, "new_id", lambda: "fixed-id")
    note = H.note_create(conn, H.human_actor("ana"), "cmo", "Cancellations are flat.")
    assert (note["id"], note["created_at"]) == ("fixed-id", "2026-11-27T06:00:00.000000Z")
    monkeypatch.setattr(H, "new_id", lambda: "fixed-event")
    H.history_event(conn, "goal", "g1", H.human_actor("ana"), "title", "old", "new")
    row = conn.execute("SELECT id, created_at FROM history_events WHERE subject_id='g1'").fetchone()
    assert tuple(row) == ("fixed-event", "2026-11-27T06:00:00.000000Z")


def test_a_moved_writer_still_refuses_through_hubdb(conn):
    with pytest.raises(H.Refused) as got:
        H.note_create(conn, H.human_actor("ana"), "cmo", "")
    assert (got.value.rule, got.value.detail) == ("note", "the note is empty")
