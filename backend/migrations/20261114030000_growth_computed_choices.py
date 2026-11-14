"""Growth computed retention part 4 (backend/growth.py `beside`): when a number typed as actual differs from the one
computed from customer facts, the channel's owner is asked once and chooses which stays.

`growth_computed_choices`: one row per cohort row and figure. `typed` is the typed number the ask was about; a new typed
number is a new question, so a row whose `typed` no longer matches is ignored. `asked_at`: when the channel's owner was
told; `keep` (typed or computed), `chosen_by` and `chosen_at` once they chose. A table of its own, so this migration does
not depend on the order other migrations alter growth_origins in."""


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_computed_choices ("
        "cohort_id TEXT NOT NULL, field TEXT NOT NULL, typed REAL NOT NULL, "
        "keep TEXT CHECK (keep IN ('typed','computed')), asked_at TEXT, chosen_by TEXT, chosen_at TEXT, "
        "PRIMARY KEY (cohort_id, field))"
    )
