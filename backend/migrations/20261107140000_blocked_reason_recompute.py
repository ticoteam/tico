"""The Blocked reason follows the holds again (task 08a47dd5, under the Blocked label task 8701fc5d).

A waiting task kept "Waiting on <person>" after the question, child or blocker it waited on had cleared: a clear that wrote
nothing else returned before the reason was made again (hubdb.task_update), and a waiting flag cleared by hand left the
reason as it was. This runs once per database and makes each open hold's reason again from the holds the task has now
(task_holds.recompute_reasons): a dependency hold, and a hold set by hand whose reason names a person. Only the reason
text changes: no hold is cleared, no task is woken or messaged, and no approval is touched; a hold a keeper kept stays
held with its owner, status and clock.
"""

from backend import task_holds


def up(conn):
    task_holds.recompute_reasons(conn)
