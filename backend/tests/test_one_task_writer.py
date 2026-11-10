"""One writer of a task's status, stage, owner and pipeline (docs/pipelines-v3.md, "One writer").

`backend/task_engine.py` is the only file that may hold an UPDATE of those columns on `tasks`. The database trigger
`tasks_move_only` refuses any other write at run time; this test refuses it at review time, before it ships.

Until the cut-over the writers of the engine before 0.6.0 are still here. `OLD_WRITERS` counts them per file. The list
only shrinks: a new writer anywhere fails, and so does a file whose count went down without its entry following. The
cut-over empties it.

The scan reads statements written out in the source. A statement whose column list is built at run time (the old
`task_update`) is not seen here; the trigger refuses those.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("backend", "clients", "connectors", "integrations", "scripts", "evals")
ENGINE = "backend/task_engine.py"
COLUMNS = ("status", "stage_id", "owner_id", "pipeline_id")
UPDATE = re.compile(r"UPDATE\s+(?:OR\s+\w+\s+)?tasks\s+SET\b(.*?)(?:\bWHERE\b|$)", re.IGNORECASE | re.DOTALL)
COLUMN = re.compile(r"(?<![\w.])(?:" + "|".join(COLUMNS) + r")\s*=")
# Python string pieces between the SQL words: quotes, an f prefix, line breaks and indentation.
JOINTS = re.compile(r"""["']\s*(?:\n\s*)?f?["']""")

# Writes made on purpose to prove the trigger refuses them (the fault rig's scenario 6: "A direct database write to a
# task's stage: refused by the trigger"). They never change a row.
REFUSED_ON_PURPOSE = {"evals/never_stuck/scenarios.py": 1}

OLD_WRITERS = {
    "backend/hubdb.py": 1,
    "backend/shared_bots.py": 1,
    "evals/factory/cases/source_outages.py": 1,
}


def writers(text):
    """How many UPDATEs of `tasks` in a file's text set one of the engine's columns."""
    joined = JOINTS.sub("", text)
    return sum(1 for found in UPDATE.finditer(joined) if COLUMN.search(found.group(1)[:1200]))


def scan():
    out = {}
    for top in SOURCES:
        for path in sorted((ROOT / top).rglob("*.py")):
            name = path.relative_to(ROOT).as_posix()
            if "/tests/" in name or "/migrations/" in name or name == ENGINE:
                continue
            count = writers(path.read_text(encoding="utf-8", errors="replace"))
            if count:
                out[name] = count
    return out


def test_the_scan_sees_a_write_however_the_statement_is_split():
    assert writers('c.execute("UPDATE tasks SET status=? WHERE id=?", (s, t))') == 1
    assert writers('c.execute(\n    "UPDATE tasks SET title=?, "\n    f"owner_id=? WHERE id=?"\n)') == 1
    assert writers('c.execute("UPDATE tasks SET title=?, checkpoint_state=? WHERE status=?")') == 0
    assert writers('c.execute("UPDATE task_links SET status=? WHERE id=?")') == 0


def test_only_the_engine_writes_a_tasks_status_stage_owner_and_pipeline():
    found = scan()
    new = {
        name: count
        for name, count in found.items()
        if count > OLD_WRITERS.get(name, 0) + REFUSED_ON_PURPOSE.get(name, 0)
    }
    assert not new, f"a task's status, stage, owner and pipeline change only through backend/task_engine.py: {new}"
    stale = {name: count for name, count in OLD_WRITERS.items() if found.get(name, 0) < count}
    assert not stale, f"OLD_WRITERS only shrinks; lower or remove these entries to what the scan finds: {stale}"


def test_the_engine_has_one_statement_that_writes_them():
    text = (ROOT / ENGINE).read_text(encoding="utf-8")
    assert len(re.findall(r"UPDATE\s+tasks\s+SET", text, re.IGNORECASE)) == 1
