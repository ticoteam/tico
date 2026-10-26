"""Descriptive task trigger metadata. This catalog never participates in rule evaluation.

Hard-coded checks and event effects live alongside wake rules in their existing listing;
job metadata is attached to the live routine rows, retaining their schedule and permissions.
"""

# Entries describe the existing hubdb/task creation paths, not independently switchable rules.
BUILTINS = (
    ("dedupe", "created", "Refuse it if an unfinished task (of any type) with the same title already exists between the same people (same parent)."),
    ("ceo_limit", "created", "Limit the CEO Bot to five ordinary items a day (UTC) in one person's Needs you; urgent asks go through."),
    ("review_pr", "created", "Refuse a Review 1 or Review 2 subtask with no linked pull request, failing checks, an unreadable current version, or no passing test evidence for the current code."),
    ("review_duplicate", "created", "Refuse a second open Review 1, Review 2 or QA subtask of the same kind for the same pull request head. Security subtasks are not checked."),
    ("private_default", "created", 'Make it private if the requester\'s or owner\'s "private tasks" default is on.'),
    ("public_text", "created", "Refuse a bot's new task that carries public roadmap text while bots need a person's approval to publish. The bot must file it without that text, then ask for publication."),
    ("ask_check", "created", "Check the title and text for asks a person must answer first; note any problem on the task."),
    ("qa_tag", "created", 'Add the "qa" tag when its changed files need QA (Dev and Engineering types).'),
    ("conversation", "created", 'Open its own conversation for a bot owner, and say "Started task" in the chat it came from.'),
    ("queue", "created", 'Put it at the bottom of its owner\'s queue and of its step (top, if filed with "top").'),
    ("created_wake", "created", 'Wake its owner: "New task from …". A bot\'s own task waits until the turn that filed it ends.'),
    ("human_wait", "created", "If a bot's task gets a subtask for a person, say once in that bot's main chat that it waits on them. A top-level task for a person says nothing."),
    ("roadmap", "step", "Move the linked Product roadmap item: Building, Ready to ship, Shipped (and back); only Engineering and Dev types linked to a roadmap item."),
    ("review_gate", "step", "Before it enters Review 1 or Review 2 with a linked pull request, refuse the move if its checks are failing or the pull request cannot be read, and require a passing test receipt for the current code. Checks still running do not refuse it; a failed test at that code always does."),
    ("questions", "finished", 'Drop its open questions to people (and its subtasks\'), except the requester\'s "is this done?" question.'),
    ("approvals", "finished", "Drop any yes/no approval it was waiting on."),
    ("completion", "finished", "Tell the bot that asked for it: at once if it is waiting on this result (Wakes); otherwise in its next batch of finished tasks (Quiet). Closes it accepted itself, and its own tasks, send nothing."),
)

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
    "propose_demotions": ("open", "Suggest turning noisy wake rules into notes; changes nothing itself."),
    "routine_timer": ("open", "Start each bot routine (these file tasks). Edit them in Tasks → Routines."),
    "stale_review": ("after", "Suggest what to do with open tasks that look finished or forgotten."),
}

NON_TASK = frozenset(("routine", "schedule", "next_run", "background_done", "*", "digest", "feed", "backlog", "release_now"))
# Delivery filters and engine fallbacks decide how news reaches a bot; they do nothing to a task, so the card
# leaves them out. A filter with its own task words (RULE_WORDS) still shows.
ENGINE_GROUPS = frozenset(("filter", "system"))

# These replace the generic engine description with the limits of the task path it describes.
RULE_WORDS = {
    "assignment": "Reassigned or handed back: wake the new owner.",
    "awaited_blocker_done": "Its last blocker finished: wake its bot if the task is still open (privacy checked). Links to a roadmap item it delivers stay.",
    "awaited_subtask": 'A subtask moved or finished: wake the parent\'s bot; say "All subtasks done" after the last.',
    "bot_to_bot": "A plain message from another bot, or status news: note it for the bot's next run.",
}


def trigger(event):
    if event in NON_TASK or "." in event:
        return None
    if event in ("task_done", "task_declined", "batch_completion"):
        return "finished"
    if event == "step_entered":
        return "step"
    return "open"


def describe_rule(row):
    out = {**row, "task_trigger": trigger(row["event"])}
    if row.get("scope_kind") == "builtin" and row.get("group") in ENGINE_GROUPS and row["id"] not in RULE_WORDS:
        out["task_trigger"] = None
    if row.get("scope_kind") == "builtin" and row["id"] == "step_entered":
        out["task_trigger"] = "open"
    if row.get("scope_kind") == "builtin" and row["id"] in RULE_WORDS:
        out["task_action"] = RULE_WORDS[row["id"]]
    return out


def builtins(c):
    engineering = [r[0] for r in c.execute("SELECT id FROM task_types WHERE role='engineering' OR name='Dev tickets'")]
    return [{"id": "task-setting-" + key, "task_trigger": section, "task_action": words,
             "scope_kind": "builtin", "built_in": True, "enabled": True, "can_change": False,
             "then": "wake" if key == "created_wake" else "note",
             **({"task_types": engineering} if key in ("qa_tag", "roadmap", "review_pr", "review_duplicate", "review_gate") else {}),
             **({"when": {"step": ["Review 1", "Review 2"]}} if key == "review_gate" else {}),
             **({"task_effect": "Wakes / Quiet"} if key == "completion" else {}),
             "usage_available": False}
            for key, section, words in BUILTINS]


def describe_job(row):
    if row.get("kind") == "task_rule":
        hours = " or ".join(str(t["hours"]) for t in row.get("triggers", []))
        return {**row, "task_trigger": "after", "task_effect": "Quiet", "task_action":
                f"Close a done task after {hours} hours untouched; skips tasks waiting on a person, snoozed, pinned, "
                "or with an open question or approval; at most 50 a pass."}
    metadata = JOBS.get(row["key"])
    wakes = {"assigned_tasks", "stall_checks", "waiting_checks", "due_reminders", "snooze_ends", "ask_deadlines", "github_sync", "repository_waits", "routine_timer"}
    return {**row, "task_trigger": metadata[0] if metadata else None,
            "task_effect": "Wakes" if row["key"] in wakes else "Quiet",
            "task_action": metadata[1] if metadata else None}
