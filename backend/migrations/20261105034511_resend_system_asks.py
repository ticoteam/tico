"""Re-sent unanswered system asks to their askers' reachable managers or the owner (before step 6c).

A no-op now: asks are gone, and 20261106131713_asks_to_question_tasks moves every open one, a system ask included,
at the next start (question_tasks.move_asks). Kept so installs that ran it keep its name in their history.
"""


def up(conn):
    return None
