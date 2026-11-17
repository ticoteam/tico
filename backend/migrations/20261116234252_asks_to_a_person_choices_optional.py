"""The built-in rule "Asks to a person" stops requiring 2 to 4 choices (org_rules.ASKS_TO_A_PERSON): its tone stays
(plain words, why it matters in one line, no Markdown or file paths) and choices become optional; the ask's shape is a
check in code (backend/ask_shape.py).

The rule's text is stored per team, added once (registry_metadata `rules.asks_to_a_person`). Only a row still holding
the earlier built-in words is changed, with its version bumped so an editor open on the old text reads it again; a team
that edited the rule keeps its own words. It reads no other migration's schema, so it runs in any order, and running it
again changes nothing.
"""

from datetime import datetime, timezone

EARLIER = (
    "A bot's ask or task for a person says in plain words why it matters, gives 2 to 4 choices with the bot's "
    "recommendation, says what happens on each, and uses no Markdown or file paths. Otherwise it is refused, and the "
    "bot rewrites it."
)
NOW = (
    "A bot's ask or task for a person is in plain words, says in one line why it matters, and uses no Markdown or "
    "file paths. Choices are optional. Otherwise it is refused, and the bot rewrites it."
)


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='org_rules'").fetchone():
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    conn.execute(
        "UPDATE org_rules SET text=?, version=version+1, updated_at=? WHERE text=?",
        (NOW, now, EARLIER),
    )
