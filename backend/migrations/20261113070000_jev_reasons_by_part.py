"""Jev's verdicts as it says them now: one line naming the part that is missing, never a probability or the whole check
(backend/pipeline_judges.py, task #351).

- A page an open task still waits on, whose verdict is unsure, is owed again (judged once more, now by its parts): its
  verdict, p and reason are cleared and its tries start over, so the Jev step judges it on its next beat. Nothing else is
  judged again.
- Every other verdict that still reads as the old template ("Jev found it meets the check (p 0.91): <the whole rubric>")
  keeps its verdict and gets the one line the template says now, with no p and no rubric.

`page_verdicts` and `requirement_pages` do not exist yet on a new file: there is nothing to change.
"""

OLD = (
    ("met", "Jev found it meets the check (p ", "Meets the check."),
    ("not_met", "Jev found it does not meet the check (p ", "Doesn't meet the check."),
    ("unsure", "Jev could not tell whether it meets the check (p ", "Can't tell if it meets the check."),
)

# The verdict each open visit's current page is read by (task_requirements._page): the page attached and not replaced.
WAITED_ON = (
    "SELECT v.id FROM page_verdicts v JOIN requirement_pages p ON p.file_id = v.file_id AND p.version = v.version "
    "AND p.rubric_hash = v.rubric_hash AND p.replaced_at IS NULL JOIN visits s ON s.id = p.visit_id "
    "AND s.left_at IS NULL JOIN tasks t ON t.id = s.task_id AND t.status = 'open' WHERE v.verdict = 'unsure'"
)


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def up(conn):
    if not {"page_verdicts", "requirement_pages", "visits", "tasks"} <= _tables(conn):
        return
    now = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
    owed = [row[0] for row in conn.execute(WAITED_ON)]
    for verdict_id in owed:
        conn.execute(
            "UPDATE page_verdicts SET verdict=NULL, p=NULL, reasons=NULL, tries=0, last_error=NULL, next_try_at=?, "
            "updated_at=? WHERE id=?",
            (now, now, verdict_id),
        )
    for verdict, start, line in OLD:
        conn.execute(
            "UPDATE page_verdicts SET reasons=? WHERE verdict=? AND substr(reasons, 1, ?) = ?",
            (line, verdict, len(start), start),
        )
