"""What each system job does to tasks, shown beside it in Settings › System. It never decides anything."""

JOBS = {
    "assigned_tasks": ("open", 'Start assigned tasks on a free computer, up to each bot\'s "Keep N going".'),
    "stall_checks": ("open", "Wake the owner when nothing moves on a task (at most 3 a day); drop queued work the bot can no longer read."),
    "waiting_checks": ("open", "Send a bot task waiting on nothing back to work after 2 hours; turn a bot's plain ask to a person into a small task."),
    "due_reminders": ("open", "Tell a bot its task is due. People get no reminder."),
    "snooze_ends": ("open", "Bring snoozed tasks back on their date."),
    "still_wanted": ("open", "Ask the task's person once whether a task idle for a week is still wanted."),
    "ask_deadlines": ("open", "When a bot question passes its deadline, go ahead on its default answer, or let it expire."),
    "approval_expiry": ("open", "Remove approvals and asks nobody will act on from Needs you."),
    "still_needed": ("open", "Weekday mornings, send questions unanswered for 2 business days back to their bots."),
    "github_sync": ("open", "Catch missed pull request, merge and release notices."),
    "repository_waits": ("open", "Finish tasks waiting on a repository or worktree."),
    "task_manager_report": ("open", "Task Manager: per department, list quiet tasks, overdue asks, review tickets without a PR and merged work whose step did not move."),
    "stall_triage": ("open", "Ask Decide what to do with a quiet task: set a wait, close it, or wake once."),
    "stale_review": ("after", "Suggest what to do with open tasks that look finished or forgotten."),
}

def describe_job(row):
    if row.get("kind") == "task_rule":
        hours = " or ".join(str(t["hours"]) for t in row.get("triggers", []))
        return {**row, "task_trigger": "after", "task_effect": "Quiet", "task_action":
                f"Close a done task after {hours} hours untouched; skips tasks waiting on a person, snoozed, pinned, "
                "or with an open question or approval; at most 50 a pass."}
    metadata = JOBS.get(row["key"])
    wakes = {"assigned_tasks", "stall_checks", "waiting_checks", "due_reminders", "snooze_ends", "ask_deadlines", "github_sync", "repository_waits"}
    return {**row, "task_trigger": metadata[0] if metadata else None,
            "task_effect": "Wakes" if row["key"] in wakes else "Quiet",
            "task_action": metadata[1] if metadata else None}
