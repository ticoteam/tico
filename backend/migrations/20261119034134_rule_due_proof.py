"""Obligations with proof (Risk plan, slice 3; backend/rule_due.py).

- `rule_due_dates` gains what a date is and where it comes from: `title` (what is due, such as "Paiva records"), `basis`
  (law, order, agency, contract, ours, asked or theirs; '' for a date that says nothing, which keeps today's behaviour)
  and `task_id`, the one task (a matter) a date belongs to. A date with a task is shown only to that task's readers.
- `rule_due_status` gains how an occurrence was met: `outcome` (met, waived or missed), the proof (`proof_kind` file,
  email, link or bank, and `proof_ref`, the file's or sent email's id or the https link), a `note`, and
  `confirmed_by_id` / `confirmed_at` for a second person's confirmation (slice 9). Every row there today was met.

Every column is added with ALTER TABLE ... ADD COLUMN; no table is rebuilt. Running it again changes nothing.
"""

BASES = ("", "law", "order", "agency", "contract", "ours", "asked", "theirs")
OUTCOMES = ("met", "waived", "missed")
PROOF_KINDS = ("", "file", "email", "link", "bank")


def _within(values):
    return ",".join("'" + value + "'" for value in values)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    dates = _columns(conn, "rule_due_dates")
    if "title" not in dates:
        conn.execute("ALTER TABLE rule_due_dates ADD COLUMN title TEXT NOT NULL DEFAULT ''")
    if "basis" not in dates:
        conn.execute(
            f"ALTER TABLE rule_due_dates ADD COLUMN basis TEXT NOT NULL DEFAULT '' CHECK (basis IN ({_within(BASES)}))"
        )
    if "task_id" not in dates:
        conn.execute("ALTER TABLE rule_due_dates ADD COLUMN task_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS rule_due_dates_task ON rule_due_dates(task_id) WHERE task_id IS NOT NULL")
    marks = _columns(conn, "rule_due_status")
    if "outcome" not in marks:
        conn.execute(
            "ALTER TABLE rule_due_status ADD COLUMN outcome TEXT NOT NULL DEFAULT 'met' "
            f"CHECK (outcome IN ({_within(OUTCOMES)}))"
        )
    if "proof_kind" not in marks:
        conn.execute(
            "ALTER TABLE rule_due_status ADD COLUMN proof_kind TEXT NOT NULL DEFAULT '' "
            f"CHECK (proof_kind IN ({_within(PROOF_KINDS)}))"
        )
    for column, ddl in (
        ("proof_ref", "TEXT NOT NULL DEFAULT ''"),
        ("note", "TEXT NOT NULL DEFAULT ''"),
        ("confirmed_by_id", "TEXT"),
        ("confirmed_at", "TEXT"),
    ):
        if column not in marks:
            conn.execute(f"ALTER TABLE rule_due_status ADD COLUMN {column} {ddl}")
