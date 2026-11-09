"""What each system job does to tasks, shown beside it in Automations › System jobs. It never decides anything."""

JOBS = {
    "heartbeat": (
        "open",
        "Every beat: tell a bot its task is due, bring snoozed tasks back, catch missed GitHub notices, "
        "finish repository waits; hourly, settle past-deadline bot questions and expire approvals nobody "
        "will act on; every minute, drop queued work the bot can no longer read.",
    ),
    "assigned_tasks": ("open", 'Start assigned tasks on a free computer, up to each bot\'s "Keep N going".'),
    "task_roundup": (
        "open",
        "One list to each bot of its tasks with a missed update, gone quiet, or a follow-up time come. Runs at 6am, noon and 6pm company time by default.",
    ),
    "waiting_checks": ("open", "A bot's task waiting on nothing tracked goes back to open after 2 hours."),
    "stranded_resume": (
        "open",
        "A bot's task in progress with no run and nothing to wait on gets one resume wake; three with no progress and "
        "it goes to the bot's manager's roundup.",
    ),
    "still_needed": ("open", "Weekday mornings, send questions unanswered for 2 business days back to their bots."),
    "task_manager_report": (
        "open",
        "Task Manager: per department, list quiet tasks, overdue asks, review tickets without a PR and merged work whose step did not move.",
    ),
    "bookkeeper_check": (
        "open",
        "Bookkeeper: read its Finance work queue; open one Books check task only when something is new.",
    ),
    "stale_review": ("after", "Suggest what to do with open tasks that look finished or forgotten."),
}


def describe_job(row):
    if row.get("kind") == "task_rule":
        hours = " or ".join(str(t["hours"]) for t in row.get("triggers", []))
        return {
            **row,
            "task_trigger": "after",
            "task_effect": "Quiet",
            "task_action": f"Close a done task after {hours} hours untouched; skips tasks waiting on a person, snoozed, pinned, "
            "or with an open question or approval; at most 50 a pass.",
        }
    metadata = JOBS.get(row["key"])
    wakes = {"assigned_tasks", "task_roundup", "heartbeat", "stranded_resume"}
    return {
        **row,
        "task_trigger": metadata[0] if metadata else None,
        "task_effect": "Wakes" if row["key"] in wakes else "Quiet",
        "task_action": metadata[1] if metadata else None,
    }
