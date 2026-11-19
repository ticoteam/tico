"""No structured questions (Flow simplification, step 2): a question to a person is a message marked ❓
(backend/question_marks.py), so the `answer` requirement kind, its choices and strategy merges are gone.

- Each question still open on an open task (not withdrawn, not answered, on a visit still in its stage) is copied to
  `old_questions` first: the next start takes it back with a note and wakes the bot that asked it to ask again with a
  ❓ (backend/old_questions.py, which runs with the serving code after every migration), and deletes the row.
- Every `requirements` row of kind `answer` is then deleted, with the rows that hang off it: its answers, Flow opinions
  and snoozes, pages, OK grants and shows, related-input rows and Flow suggestions. Deleted, not kept: the kind is out
  of the table's check, and turning an old question into an `ok` would read as an approval nobody gave. What was asked
  and answered stays in each task's history (history_events) and its thread. A ❓ is a `question_marks` row, never a
  requirement, so nothing here touches one. A stage's own typed answer (the pipeline editor's "An answer…") goes too.
- `requirements` is rebuilt without the `choices` column and without `answer` in either kind check. Every other column,
  row and index is kept. The `changes_requirements_*` triggers named `choices`; they are left out, and the change log
  (backend/events.py ensure) makes them again from the table's columns at start.
- `strategy_merges` is dropped: nothing merges a bot's strategy question into another any more.

`feedbacks` keeps its old 'ask' subject rows (👎 "badly written" on an ask), which backend/ask_quality.py counts for the
weeks they cover; nothing new is written with that subject. `answers.name` and `value_type` stay, unwritten.

Running it again changes nothing.
"""

import re

FOREIGN_KEYS_OFF = True

# The tables that name a requirement by `requirement_id`.
DEPENDENTS = (
    "answers",
    "ask_opinions",
    "ask_snoozes",
    "requirement_pages",
    "ok_grants",
    "ok_shown",
    "related_inputs",
    "flow_suggestions",
    "flow_suggestion_runs",
)
ANSWER = "SELECT id FROM requirements WHERE kind='answer'"
OLD_QUESTIONS = """
CREATE TABLE IF NOT EXISTS old_questions (
    id TEXT PRIMARY KEY,
    visit_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    added_by_id TEXT,
    line TEXT NOT NULL DEFAULT '',
    choices TEXT,
    created_at TEXT NOT NULL
)
"""
# Each open question: its answers are read by their visit first (answers_visit leads with visit_id), never a scan of
# every answer ever given.
KEEP_OPEN = (
    "INSERT OR IGNORE INTO old_questions (id, visit_id, task_id, added_by_id, line, choices, created_at) "
    "SELECT r.id, r.visit_id, v.task_id, r.added_by_id, coalesce(r.line, ''), r.choices, r.created_at "
    "FROM requirements r JOIN visits v ON v.id=r.visit_id JOIN tasks t ON t.id=v.task_id "
    "WHERE r.kind='answer' AND r.withdrawn_at IS NULL AND v.left_at IS NULL AND t.status='open' "
    "AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.visit_id=v.id AND a.requirement_id=r.id AND a.undone_at IS NULL)"
)
CHOICES = re.compile(r",\s*choices\s+TEXT\b[^,]*?json_valid\(choices\)\s*\)", re.IGNORECASE)
KIND_LIST = re.compile(r"(kind\s+IN\s*\()([^)]*)(\))", re.IGNORECASE)


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _without_answer(match):
    kinds = [kind.strip() for kind in match.group(2).split(",")]
    return match.group(1) + ", ".join(kind for kind in kinds if kind != "'answer'") + match.group(3)


def _delete(conn, tables):
    for table in DEPENDENTS:
        if table in tables and "requirement_id" in _columns(conn, table):
            conn.execute(f"DELETE FROM {table} WHERE requirement_id IN ({ANSWER})")
    conn.execute("DELETE FROM requirements WHERE kind='answer'")


def _aside(conn):
    """A rebuild re-checks every trigger and view, so they step aside and go back as they were, except a trigger that
    names the dropped column (it would fail on the next write)."""
    aside = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _table, _sql in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    return [
        sql
        for kind, _name, table, sql in aside
        if not (kind == "trigger" and table == "requirements" and re.search(r"\bchoices\b", sql))
    ]


def _rebuild(conn):
    found = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='requirements'").fetchone()
    if not found:
        return
    sql = str(found[0])
    columns = [column for column in _columns(conn, "requirements") if column != "choices"]
    new_sql = KIND_LIST.sub(_without_answer, CHOICES.sub("", sql))
    if new_sql == sql:
        return
    if "choices" in _columns(conn, "requirements") and "choices" in new_sql:
        raise RuntimeError("requirements: the choices column was not found in its CREATE TABLE")
    new_sql = re.sub(
        r"^\s*CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?\"?requirements\"?", "CREATE TABLE requirements_new", new_sql
    )
    indexes = [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='requirements' AND sql IS NOT NULL"
        )
    ]
    back = _aside(conn)
    names = ", ".join(f'"{column}"' for column in columns)
    conn.execute(new_sql)
    conn.execute(f"INSERT INTO requirements_new ({names}) SELECT {names} FROM requirements")
    conn.execute("DROP TABLE requirements")
    conn.execute("ALTER TABLE requirements_new RENAME TO requirements")
    for statement in indexes:
        conn.execute(statement)
    for statement in back:
        conn.execute(statement)


def _keep_open(conn, tables):
    conn.execute(OLD_QUESTIONS)
    needed = {"visits", "tasks", "answers"}
    if needed <= tables and "choices" in _columns(conn, "requirements"):
        conn.execute(KEEP_OPEN)


def up(conn):
    tables = _tables(conn)
    if "requirements" in tables:
        _keep_open(conn, tables)
        _delete(conn, tables)
        _rebuild(conn)
    conn.execute("DROP TABLE IF EXISTS strategy_merges")
