"""Meeting task references use the task's complete read check, including bot access."""
from .store import Problem


def readable(c, auth, who, task_id):
    if not task_id or auth is None or who is None:
        return None
    try:
        return auth.task(c, who, task_id)
    except Problem as exc:
        if exc.status not in (403, 404):
            raise
        return None


def redact(c, auth, who, value):
    """Keep the item, but remove task ids this viewer cannot follow, including stale receipts."""
    value = dict(value)
    for field in ('proposed_task_id', 'result_ref'):
        if value.get(field) and not readable(c, auth, who, value[field]):
            value[field] = None
    return value
