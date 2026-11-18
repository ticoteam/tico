"""Growth channel read and alerts (backend/growth_brief.py, task #288).

- `growth_channels.input_kpi_id`: the KPI the channel moves, beside its guardrail. The Growth line adds the same
  column; whichever migration runs first adds it and the other leaves it. Its target stays on the goal it is linked to.
- `growth_alert_state`: the last state Tico saw per channel and kind (kpi: its colour; guardrail: over or under; source:
  failing or ok), so a crossing is a change of state, never a repeat.
- `growth_crossings`: every crossing, with what it was, the value and the line, and whom it told (`told`: task, comment
  or none) on which task. The channel read lists them under what changed; an open alert task is found from here.
- `growth_view_reads`: how many times each caller read a channel or segment view per day and run, for the 30-day read
  of the spec's first measure.
"""


def up(conn):
    if "input_kpi_id" not in {row[1] for row in conn.execute("PRAGMA table_info(growth_channels)")}:
        conn.execute("ALTER TABLE growth_channels ADD COLUMN input_kpi_id TEXT")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_alert_state ("
        "channel_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, at TEXT NOT NULL, "
        "PRIMARY KEY (channel_id, kind))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_crossings ("
        "id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, "
        "value REAL, line TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '', task_id TEXT, "
        "told TEXT NOT NULL CHECK (told IN ('task','comment','none')), actor TEXT, at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS growth_crossings_channel ON growth_crossings(channel_id, kind, at)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_view_reads ("
        "day TEXT NOT NULL, actor TEXT NOT NULL, run_id TEXT NOT NULL DEFAULT '', view TEXT NOT NULL, "
        "n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, actor, run_id, view))"
    )
