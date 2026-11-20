"""The built-in rule "Asks to a person" only warns (org_rules.ASKS_TO_A_PERSON): a style rule never stops a question
reaching its person. Held or escalated, a bot's questions piled up on one review task the person never saw, while the
bot believed it had asked. Its words drop "Otherwise it is refused, and the bot rewrites it."

The rule is stored per team. Only a row still holding a built-in wording (either earlier one, or the current one, matched
as 20261116234252_asks_to_a_person_choices_optional matches it: the whole text, exactly) is changed: it gets the current
words and `if_broken` "warn", with its version bumped so an editor open on it reads it again. A team that edited the
rule's words keeps its words and its setting. It reads no other migration's schema, so it runs in any order, and running
it again changes nothing.
"""

from datetime import datetime, timezone

FIRST = (
    "A bot's ask or task for a person says in plain words why it matters, gives 2 to 4 choices with the bot's "
    "recommendation, says what happens on each, and uses no Markdown or file paths. Otherwise it is refused, and the "
    "bot rewrites it."
)
EARLIER = (
    "A bot's ask or task for a person is in plain words, says in one line why it matters, and uses no Markdown or "
    "file paths. Choices are optional. Otherwise it is refused, and the bot rewrites it."
)
NOW = (
    "A bot's ask or task for a person is in plain words, says in one line why it matters, and uses no Markdown or "
    "file paths. Choices are optional."
)


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='org_rules'").fetchone():
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    conn.execute(
        "UPDATE org_rules SET text=?, if_broken='warn', version=version+1, updated_at=? "
        "WHERE text IN (?, ?, ?) AND NOT (text=? AND if_broken='warn')",
        (NOW, now, FIRST, EARLIER, NOW, NOW),
    )
