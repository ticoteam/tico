"""Task commands: tasks and replays."""

import argparse
from clients.hubcli_core import (
    ASKED,
    TASK_STATUSES,
    TASK_UPDATE_STATUSES,
    delivery_flags,
    task_update_status,
)


def add_tasks(sub):
    task = sub.add_parser("task", aliases=["tasks"]).add_subparsers(dest="sub")
    s = task.add_parser("child", help="create a subtask")
    s.add_argument("parent")
    s.add_argument("--owner", help="who does it; a --check-model subtask is your own (`me`)")
    s.add_argument("--title", required=True)
    s.add_argument("--body", default="")
    s.add_argument(
        "--check-model",
        metavar="RUNTIME[:MODEL]",
        nargs="?",
        const="auto",
        help="run this subtask of yours on another model, in its own session (e.g. grok, codex:gpt-6.1-sol); "
        "with no value (put the flag after the parent id), the company's check model, from a provider other "
        "than the parent's authors when one can run it",
    )
    s.add_argument(
        "--role",
        dest="check_role",
        choices=("review", "check"),
        help="review files a review child; with --check-model, what that model is asked for (default check)",
    )
    s.set_defaults(fn="task child create")
    s = task.add_parser("tree", help="show nested subtasks")
    s.add_argument("id")
    s.set_defaults(fn="task tree")
    worktree = task.add_parser("worktree", help="create or attach this task's worktree").add_subparsers(
        dest="worktree_sub"
    )
    for operation, argument in (("add", "repo"), ("attach", "path"), ("setup", "repo")):
        s = worktree.add_parser(operation)
        s.add_argument(argument)
        s.add_argument("--task", help="task id; defaults to this run's task")
        s.set_defaults(fn="task worktree " + operation)
    s = task.add_parser(
        "caps",
        help="the task caps: today's count per pair, the tasks closed before the "
        "receiver acted, and the changes bots asked for",
    )
    s.set_defaults(fn="task caps")
    cap = task.add_parser(
        "cap", help="set a task cap: how many tasks sender may create for receiver a day"
    ).add_subparsers(dest="cap_sub")
    s = cap.add_parser(
        "set", help="sender receiver --per-day N; 0 blocks the pair, and with no --per-day the cap is removed"
    )
    s.add_argument("sender", help="bot:<slug> or person:<id>")
    s.add_argument("receiver", help="bot:<slug> or person:<id>, a manager above the sender")
    s.add_argument("--per-day", dest="per_day", type=int, help="tasks a day; 0 blocks the pair")
    s.set_defaults(fn="task cap set")
    s = task.add_parser("create")
    s.add_argument("--visibility", choices=["public", "private"])
    s.add_argument("--public-title", dest="public_title", help="public roadmap title (needed with --visibility public)")
    s.add_argument("--public-summary", dest="public_summary", help="public roadmap summary")
    s.add_argument("--private", action="store_true", default=None, help="only its requester, owner and readers see it")
    s.add_argument(
        "--reader",
        dest="readers",
        action="append",
        help="with --private: someone else who sees it, a person, bot or group:<id> (repeat for more)",
    )
    s.add_argument(
        "--follow", action="store_true", default=None, help="follow the task you file (its owner follows anyway)"
    )
    s.add_argument(
        "--from-private",
        dest="from_private",
        action="store_true",
        help="from a run on your private task: a company-visible task for a bot, in your own words (no "
        "12 words copied from the private work, no secret-shaped value, no file or link of it)",
    )
    s.add_argument("--owner", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--body", default="")
    s.add_argument("--body-file", dest="body_file")
    s.add_argument("--due")
    s.add_argument("--check-back-at", help="ISO timestamp or duration such as 45m")
    s.add_argument("--check-back-note", help="what result to inspect")
    s.add_argument("--parent", help="file it as a subtask of this task")
    s.add_argument("--label", action="append", help="a label (repeat, or comma-separate); a project is a label")
    s.add_argument(
        "--change-path", dest="change_paths", action="append", help="changed path for automatic QA classification"
    )
    s.add_argument("--qa", choices=["yes", "no"], help="explicit QA override")
    s.add_argument("--qa-not-checkable-live", action="store_true", default=None)
    s.add_argument(
        "--blocker",
        nargs=3,
        metavar=("REPO", "STATUS", "ENDPOINT"),
        help="what refused, for a task to DevOps or a person: e.g. ticoteam/tico 403 workflow_dispatch. "
        "An open ask about the same three joins this task instead of a second ask",
    )
    s.add_argument("--top", action="store_true", help="put it at the top of the owner's queue")
    s.add_argument("--link", action="append", help="a URL to attach (a pull request, an issue, a document)")
    s.add_argument("--goal", help="the goal this task serves (hub goal list); optional")
    ways = delivery_flags(s, steer=False)
    # The older words for --defer, kept: a deferred task with the default deadline.
    ways.add_argument("--next-run", dest="next_run", action="store_true", help="the older name for --defer")
    ways.add_argument("--quiet", dest="next_run", action="store_true", help=argparse.SUPPRESS)
    s.add_argument(
        "--dry-run", dest="dry_run", action="store_true", help="print the checks a create would fail and write nothing"
    )
    s.add_argument(
        "--request-id",
        dest="request_id",
        help="the person's message this carries: handing BotOps something a person asked you for, their "
        "message id or `turn` (BotOps acts with their rights, or puts it to them as a Confirm card)",
    )
    s.add_argument("--type", help="task type id or name; defaults to General")
    s.add_argument("--step", help="step id or name in the type")
    s.add_argument("--number", type=int, help="movers: keep an imported ticket's number")
    s.add_argument(
        "--model",
        help="the model this bot-owned task's runs use, from `hub bot model`'s list; leave out "
        "for the bot's task model",
    )
    s.add_argument("--effort", help="the reasoning effort that model takes")
    s.add_argument("--harness", help="the harness that runs that model")
    s.set_defaults(fn="task create")
    types = task.add_parser("type", help="manage task types (movers only)").add_subparsers(dest="type_sub")
    bots_help = (
        "what every bot may do with the type's tasks: parties (only the bots on each task), "
        "read (read and comment on all of them) or work (also change them)"
    )
    steps_only_help = (
        "its tasks change status only by moving to a step, never by closing, declining, reopening or finishing"
    )
    s = types.add_parser("create")
    s.add_argument("--default-visibility", dest="default_visibility", choices=["public", "private"])
    s.add_argument("--name", required=True)
    s.add_argument("--steps-file", dest="steps_file")
    s.add_argument("--bots", choices=["parties", "read", "work"], help=bots_help)
    s.add_argument("--numbered", action="store_true", default=None, help="number each task on the type")
    s.add_argument("--steps-only", dest="steps_only", action="store_true", default=None, help=steps_only_help)
    s.set_defaults(fn="task type create")
    s = types.add_parser("update")
    s.add_argument("--default-visibility", dest="default_visibility", choices=["public", "private"])
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--steps-file", dest="steps_file")
    s.add_argument("--bots", choices=["parties", "read", "work"], help=bots_help)
    s.add_argument(
        "--numbered",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="number each task created on or moved onto the type",
    )
    s.add_argument(
        "--steps-only", dest="steps_only", action=argparse.BooleanOptionalAction, default=None, help=steps_only_help
    )
    s.set_defaults(fn="task type update")
    s = types.add_parser("delete")
    s.add_argument("id")
    s.set_defaults(fn="task type delete")
    s = task.add_parser("types", help="list or manage task types and steps")
    s.add_argument("id", nargs="?", help="type id or name")
    s.add_argument("--name", help="create a type, or rename the selected type")
    s.add_argument("--steps-file", dest="steps_file", help="JSON array of steps; keep ids when editing")
    s.add_argument("--delete", action="store_true", help="delete an unused type")
    s.set_defaults(fn="task types")
    s = task.add_parser(
        "question", help="put one decision to a person as a task they own: a pick or Yes / No; their answer closes it"
    )
    s.add_argument("question")
    s.add_argument("--on", required=True, help="the person who decides")
    s.add_argument(
        "--option",
        action="append",
        help="\"LABEL -> WHAT HAPPENS\" or \"LABEL=FILE -> WHAT HAPPENS\" (an image shows with it); repeat, 2 to 6; "
        "none for Yes / No. The what-happens line is required",
    )
    s.add_argument("--why", help="one plain line on why it matters, shown under the question")
    s.add_argument(
        "--multi",
        action="store_true",
        default=None,
        help="several picks: the person ticks the ones they want; unticked ones come back as \"not this one\"",
    )
    s.add_argument("--recommend", action="append", metavar="LABEL", help="an option you recommend (pre-ticked); repeat")
    s.add_argument("--title")
    s.add_argument("--body")
    s.add_argument("--parent")
    s.add_argument(
        "--topic",
        help="a short key for what this decides (backup-model): your open question to them "
        "on it is updated instead of a second one opened",
    )
    s.add_argument("--private", action="store_true", default=None, help="only you and the person may read it")
    s.add_argument(
        "--if-no-answer",
        dest="if_no_answer",
        help="what you will do if they don't answer: an option (\"Yes\") or one plain sentence",
    )
    s.add_argument(
        "--default-kind",
        dest="default_kind",
        choices=["reversible", "send", "spend", "delete"],
        help="what that step is, required with --if-no-answer; only reversible (internal, undoable) goes "
        "ahead on its own: a send, spending or a delete waits for an answer",
    )
    s.add_argument("--by", help="ISO-8601 date-time: go ahead then, instead of after the team's days (default 3)")
    s.add_argument(
        "--review",
        action="append",
        metavar="PATH|FILE[@N]",
        help="a file version for them to review, shown on the card: a path here (published first, as "
        "hub file publish does) or a file id; repeat. Without --option: Approve / Request changes",
    )
    s.add_argument(
        "--pick",
        action="store_true",
        default=None,
        help="each --review file is its own option (2 to 5), plus Request changes",
    )
    s.add_argument(
        "--spend",
        metavar="LABEL=AMOUNT:WHAT",
        help="picking LABEL also approves spending AMOUNT on WHAT: a spend approval you consume once",
    )
    s.add_argument("--spend-account", dest="spend_account", help="the account --spend comes from (default gateway)")
    s.set_defaults(fn="task question")
    s = task.add_parser(
        "update",
        description="Move your task; to reopen, set doing or open; the requester closes. "
        "Putting it back to open requires a note. Automatic starts wait 60 minutes unless it gets "
        "a comment, a new link or a blocker finishes.",
    )
    s.add_argument("--visibility", choices=['public', 'private'])
    s.add_argument("--public-title", dest="public_title")
    s.add_argument("--public-summary", dest="public_summary")
    s.add_argument("--approval-id", dest="approval_id")
    s.add_argument("--size", type=float)
    visibility = s.add_mutually_exclusive_group()
    visibility.add_argument("--private", dest="private", action="store_true", default=None)
    visibility.add_argument(
        "--company", dest="private", action="store_false", help="human requester publishes the task"
    )
    s.add_argument("id")
    s.add_argument("--title", help="a new title, checked as a new task's title would be")
    s.add_argument("--status", choices=list(TASK_UPDATE_STATUSES), type=task_update_status)
    s.add_argument("--note")
    s.add_argument("--owner")
    s.add_argument("--due")
    s.add_argument(
        "--on",
        dest="waiting_on",
        help="with --status waiting: the person you need. It files them a question task under this one, "
        "and this task opens again with their answer. --note is your question: one or two plain "
        "sentences saying what you need and what happens after, at most 40 words, with no ids, hashes, "
        "file paths or tool names (\"PR #1555\" is fine). Not a bot: ask a bot in a DM (hub message "
        "send <slug> ... --needs-action), or wait on its task (hub task wait <id> --on task:<its task>)",
    )
    s.add_argument("--goal", help='the goal this task serves; "" takes it off')
    s.add_argument(
        "--brief", action="store_true", help="keep the note on the task; chat hears only the title and status"
    )
    s.add_argument("--quiet", dest="brief", action="store_true", help=argparse.SUPPRESS)  # brief's older name
    s.add_argument("--type", help="task type id or name")
    s.add_argument("--step", help="step id or name; sets status; an empty string clears it")
    s.add_argument("--step-rank", dest="step_rank", type=float, help="its place within its step, lower first")
    s.add_argument("--number", type=int, help="movers: the number of a task that has none")
    s.add_argument(
        "--role",
        action="append",
        help="who is on it by role, NAME=person,person (repeat for more roles; NAME= clears one)",
    )
    s.add_argument(
        "--verdict",
        choices=["approve", "changes", "pass", "fail"],
        help="a review or QA subtask's verdict; the task that asked for it is woken with it",
    )
    s.add_argument("--head", dest="verdict_head", help="the pull request head (commit sha) the verdict judged")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task update")
    s = task.add_parser(
        "done",
        help="finish a task you own with its result; a review or QA subtask adds its --verdict "
        "and the --head it judged",
    )
    s.add_argument("id")
    s.add_argument("--note", help="the result, in a few plain sentences")
    s.add_argument("--verdict", choices=["approve", "changes", "pass", "fail"])
    s.add_argument("--head", dest="verdict_head", help="the pull request head (commit sha) the verdict judged")
    s.add_argument(
        "--brief", action="store_true", help="keep the note on the task; chat hears only the title and status"
    )
    s.add_argument("--quiet", dest="brief", action="store_true", help=argparse.SUPPRESS)  # brief's older name
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task done")
    s = task.add_parser("comment", help="leave a comment on a task, on the record with your name")
    s.add_argument("--public", action="store_true", default=None)
    s.add_argument("--approval-id", dest="approval_id")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--attach", action="append", help="file to attach; repeat for more files")
    s.add_argument(
        "--file",
        action="append",
        metavar="ID@VERSION",
        help="a file already in Tico: one on this task, or (a person) a chat file you can open, as ID@1",
    )
    s.add_argument(
        "--visual",
        action="append",
        metavar="PATH",
        help="a picture, SVG, HTML page, video or audio to show with it (a file in your checkout, or ID@VERSION "
        "already in Tico); repeat for more",
    )
    s.add_argument(
        "--title",
        action="append",
        dest="visual_title",
        metavar="TITLE",
        help="the caption for each --visual, in the same order",
    )
    delivery_flags(s, goal=True)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.add_argument(
        "--needs-action",
        dest="needs_action",
        action="store_true",
        help="a bot's message to a bot waits for that bot's next run; this wakes it now",
    )
    s.set_defaults(fn="task comment")
    s = task.add_parser("comment-moderate", help="hide, show again or delete a public comment on a task you manage")
    s.add_argument("id")
    s.add_argument("comment_id")
    s.add_argument("action", choices=["hide", "show", "delete"])
    s.set_defaults(fn="task comment-moderate")
    s = task.add_parser("requesters", help="a task's requesters: internal contacts and counts, never tokens")
    s.add_argument("id")
    s.add_argument("--limit", type=int)
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="task requesters")
    s = task.add_parser(
        "requester-save", help="add a requester to a task, or update one with --requester-id; nothing is sent"
    )
    s.add_argument("id")
    s.add_argument("--requester-id", dest="requester_id", help="the requester to update; omit to add one")
    s.add_argument("--actor", help="a teammate (verified identity); omit for someone outside")
    for field in ("name", "email", "phone", "other", "organization", "note"):
        s.add_argument("--" + field)
    s.add_argument("--source", choices=["public_form", "inbox", "meeting", "manual"])
    s.add_argument("--important", action=argparse.BooleanOptionalAction, default=None)
    s.set_defaults(fn="task requester-save")
    s = task.add_parser("requester-remove", help="take a requester off a task")
    s.add_argument("id")
    s.add_argument("requester_id")
    s.set_defaults(fn="task requester-remove")
    s = task.add_parser(
        "publication-preview",
        help="the exact payload a publish approval needs for public text, a label change or a public reply",
    )
    s.add_argument("id")
    s.add_argument("--visibility", choices=["public", "private"])
    s.add_argument("--public-title", dest="public_title")
    s.add_argument("--public-summary", dest="public_summary")
    s.add_argument(
        "--label", dest="labels", action="append", help="the task's labels after the change; repeat for more"
    )
    s.add_argument("--comment", help="the public reply's text")
    s.set_defaults(fn="task publication-preview")
    s = task.add_parser("comment-edit", help="change the text of a comment you wrote; it wakes nobody")
    s.add_argument("--approval-id", dest="approval_id")
    s.add_argument("id")
    s.add_argument("comment_id", help="the comment's id (its id in the task's comments)")
    s.add_argument("text")
    s.set_defaults(fn="task comment-edit")
    s = task.add_parser("comment-delete", help="take back a comment you wrote")
    s.add_argument("id")
    s.add_argument("comment_id", help="the comment's id (its id in the task's comments)")
    s.set_defaults(fn="task comment-delete")
    s = task.add_parser("delete", help="delete a task made by mistake: its requester or a mover, never a bot")
    s.add_argument("id")
    s.set_defaults(fn="task delete")
    s = task.add_parser("deleted", help="deleted tasks you may restore, newest first")
    s.set_defaults(fn="task deleted")
    s = task.add_parser("restore", help="put a deleted task back: whoever deleted it, its requester or a mover")
    s.add_argument("id")
    s.set_defaults(fn="task restore")
    s = task.add_parser("link", help="attach a link: the pull request you opened, an issue, a document, a session")
    s.add_argument("id")
    s.add_argument("url", help="the URL; with --session also a resume command or a reference")
    s.add_argument("--title")
    s.add_argument(
        "--session",
        action="store_true",
        help="another session working this task, tracked only: task chat goes to the owning bot",
    )
    s.add_argument(
        "--wake-once",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="a pull request: wake this task once when its checks finish (pass or fail), it is approved or "
        "gets changes requested, or a release ships it; never changes the task's status. "
        "--no-wake-once disarms it",
    )
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task link")
    readers = task.add_parser("readers", help="who can see a private task: list, add, remove").add_subparsers(
        dest="subsub"
    )
    s = readers.add_parser("list", help="its requester, owner and readers")
    s.add_argument("id")
    s.set_defaults(fn="task readers list")
    for change, words in (
        ("add", "let them see it: people, bots or group:<id>s"),
        ("remove", "take them off: they stop seeing it"),
    ):
        s = readers.add_parser(change, help=words)
        s.add_argument("id")
        s.add_argument("readers", nargs="+", metavar="X", help="human:<id>, bot:<slug> or group:<id>")
        s.set_defaults(fn="task readers " + change)
    s = task.add_parser("followers", help="who follows a task, each at their level")
    s.add_argument("id")
    s.set_defaults(fn="task followers")
    for change, words in (("follow", "follow a task, or set your level"), ("unfollow", "stop following a task")):
        s = task.add_parser(change, help=words)
        s.add_argument("id")
        if change == "follow":
            s.add_argument("--level", choices=("live", "mentions", "digest", "muted"))
        s.add_argument(
            "--who", help="someone else who sees it (human:<id> or bot:<slug>), when you may change the task"
        )
        s.set_defaults(
            fn="task follow", **({"unfollow": True} if change == "unfollow" else {})
        )  # one tool: hub_task_follow
    s = task.add_parser("relate", help="relate another task to this one: <id> <kind> <task>")
    s.add_argument("id")
    s.add_argument("task")
    s.add_argument(
        "--kind",
        default="related",
        choices=["parent", "blocks", "blocked_by", "related", "duplicate_of", "follow_up"],
        help="parent: task is its parent; blocks / blocked_by; related (default); duplicate_of; "
        "follow_up: it was split off or followed up from task",
    )
    s.add_argument("--remove", action="store_true", help="take it off instead")
    s.set_defaults(fn="task relate")
    s = task.add_parser(
        "wait",
        help="make a task wait on another task, a pull request merging or a release being "
        "published; prints what was stored",
    )
    s.add_argument("id")
    s.add_argument(
        "--on",
        dest="wait_on",
        required=True,
        help="task:<id> (blocked_by), pr:<url> or pr:<task> (until that pull request merges), "
        "release:<tag> (until that release is published). The task it waits on keeps its own step.",
    )
    s.add_argument(
        "--note", help="what to check when the pr: or release: wait fires, in the wake; one or two sentences"
    )
    s.add_argument("--remove", action="store_true", help="take the wait off instead")
    s.set_defaults(fn="task wait")
    s = task.add_parser("label", help="add or remove labels on a task")
    s.add_argument("id")
    s.add_argument("--add", action="append")
    s.add_argument("--remove", action="append")
    s.add_argument("--approval-id", dest="approval_id", help="an approved publish approval, for a public roadmap label")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task label")
    s = task.add_parser("close")
    s.add_argument("id")
    s.add_argument("--note")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task close")
    s = task.add_parser(
        "bulk",
        help="one change to every task a filter selects (at most 200): step, status, owner, "
        "tags or close; --dry-run counts first. Each task changes as its own update would",
    )
    s.add_argument("action", choices=["step", "status", "owner", "tags", "close"])
    s.add_argument("value", nargs="?", help="the step, status or owner to set (none for tags or close)")
    s.add_argument("--add", action="append", help="with tags: a tag to add; repeat for more")
    s.add_argument("--remove", action="append", help="with tags: a tag to take off; repeat for more")
    s.add_argument("--note", help="what changed, or why; goes with each task's change")
    s.add_argument("--dry-run", dest="dry_run", action="store_true", help="count what would change; change nothing")
    for name, word in (
        ("owner", "owner (a bot slug, a human id or me)"),
        ("requester", "requester"),
        ("label", "tag"),
        ("type", "type (id or name)"),
        ("step", "step (id or name)"),
        ("updated-since", "changed after this ISO-8601 time"),
        ("member", "person or bot on it by role"),
        ("role", "role, with --where-member"),
    ):
        s.add_argument("--where-" + name, dest="where_" + name.replace("-", "_"), help="only tasks with this " + word)
    s.add_argument(
        "--where-status",
        dest="where_status",
        action="append",
        choices=list(TASK_STATUSES),
        help="only tasks with this status; repeat for more",
    )
    s.add_argument("--where-lane", dest="where_lane", choices=["company", "product"])
    s.add_argument("--where-number", dest="where_number", type=int, help="only this task number")
    s.add_argument("--where-snoozed", dest="where_snoozed", choices=["hide", "only", "all"])
    s.add_argument(
        "--where-id", dest="where_ids", action="append", help="only this task (id or number); repeat for more"
    )
    s.set_defaults(fn="task bulk")
    s = task.add_parser("attach", help="attach a file to a task; prints the link for your note")
    s.add_argument("id")
    s.add_argument("file")
    s.add_argument("--name", help="the name people see; defaults to the file's own")
    s.add_argument("--poster", help="a PNG or JPEG preview")
    s.add_argument("--note", help="short note on this version (at most 500 characters)")
    s.set_defaults(fn="task attach")
    s = task.add_parser("answers", help="list the task's structured answers, oldest first")
    s.add_argument("id")
    s.set_defaults(fn="task answers")
    s = task.add_parser("context", help="readable page and meeting references for a task")
    s.add_argument("id")
    s.set_defaults(fn="task context")
    s = task.add_parser("meetings", help="meetings linked to a task")
    s.add_argument("id")
    s.add_argument("--limit", type=int)
    s.add_argument("--cursor")
    s.set_defaults(fn="task meetings")
    s = task.add_parser("list")
    s.add_argument("--q", help="title words or #N")
    s.add_argument("--owner")
    s.add_argument("--requester")
    s.add_argument("--status", action="append", choices=list(TASK_STATUSES))
    s.add_argument("--lane", choices=["company", "product"])
    s.add_argument("--label")
    s.add_argument("--type", help="only tasks of this type (id or name)")
    s.add_argument("--step", help="only tasks in this step (id or name)")
    s.add_argument(
        "--sort", choices=["queue", "finished", "step"], help="step: in step order, then each one's place in it"
    )
    s.add_argument("--number", type=int, help="only this task number")
    s.add_argument("--updated-since", help="only tasks changed after this ISO-8601 time with a timezone")
    s.add_argument("--member", help="only tasks this person or bot is on in some role (me, a human id, a bot slug)")
    s.add_argument("--role", help="with --member: only tasks they are on in this role")
    s.add_argument("--brief", action="store_true", help="leave out bodies and acceptance criteria")
    s.add_argument(
        "--all", action="store_true", help="the board: every task and every bot you may see, as {tasks, bots}"
    )
    s.add_argument(
        "--stuck",
        action="store_true",
        help="only open work untouched for --hours that waits on nobody (BotOps's sweep)",
    )
    s.add_argument("--hours", type=int, default=24, help="with --stuck: untouched for at least this many hours")
    s.add_argument(
        "--snoozed",
        choices=["only", "all"],
        help="snoozed tasks are left out: only lists just them, all lists them with the rest",
    )
    s.set_defaults(fn="task list")
    s = task.add_parser(
        "show", help="one task with its history and conversation; several ids: their fields in one read"
    )
    s.add_argument("id", nargs="+")
    s.set_defaults(fn="task show")
    s = task.add_parser("run", help="start a bot's task now, as the task")
    s.add_argument("id")
    s.set_defaults(fn="task run")
    s = task.add_parser(
        "model",
        help="the model this task's runs use, with its effort and harness; --clear puts it "
        "back on the bot's task model",
    )
    s.add_argument("id")
    s.add_argument("model", nargs="?", help="a model id or name; leave out to change only --effort or --harness")
    s.add_argument("--effort")
    s.add_argument("--harness")
    s.add_argument(
        "--clear", action="store_true", help="clear the task's own model: its runs use the bot's task model again"
    )
    s.set_defaults(fn="task model")
    s = task.add_parser("check-back", help="schedule the next follow-up on bot work")
    s.add_argument("id")
    timing = s.add_mutually_exclusive_group(required=True)
    timing.add_argument("--at", help="ISO date-time with timezone")
    timing.add_argument("--in", dest="in_duration", help="duration such as 45m or 2h")
    s.add_argument("--for", dest="check_back_note", help="the result or handoff to inspect")
    s.set_defaults(fn="task check-back")
    s = task.add_parser(
        "snooze",
        help="set a task aside until a date: off the lists and Needs you until then, when its owner bot is woken once",
    )
    s.add_argument("id")
    s.add_argument(
        "--until", required=True, help="ISO-8601 date (the start of that day, Pacific) or date-time with a zone"
    )
    s.add_argument(
        "--note",
        help="what to check when the snooze ends, in the owner's wake: what you are waiting for and "
        "where to look (at most 500 characters)",
    )
    s.set_defaults(fn="task snooze")
    # `defer` is its older name: a date makes it a snooze-like hold, not a delivery (backend/delivery.py).
    s = task.add_parser(
        "later",
        aliases=["defer"],
        help="a deferred task carried into this run that you will not do now: say when it is "
        "picked up again (it gets one run of its own then); with none, your next run is handed it again",
    )
    s.add_argument("id")
    when = s.add_mutually_exclusive_group(required=True)
    when.add_argument("--after", help="when this task is done or closed (an id or number)")
    when.add_argument("--at", help="at this time: ISO-8601 date-time with a zone, or a date (start of day, Pacific)")
    when.add_argument("--next-run", action="store_true", help="with your next run, whatever starts it")
    when.add_argument("--handled", action="store_true", help="this run is done with it for now (nothing to resume)")
    s.set_defaults(fn="task later")
    s = task.add_parser("unsnooze", help="bring a snoozed task back now; its owner bot is woken once")
    s.add_argument("id")
    s.set_defaults(fn="task unsnooze")
    s = task.add_parser(
        "waive-qa",
        help="a person waives a Dev ticket's current In QA step for one exact pull "
        "request head, with a reason; recorded as a waiver, never as a QA pass",
    )
    s.add_argument("id")
    s.add_argument("--head", required=True, help="the pull request's current head: the full 40-character sha")
    s.add_argument("--reason", required=True, help="why QA is waived, in plain words")
    s.set_defaults(fn="task waive-qa")

    replay = sub.add_parser("replay", help="what happened in a window, as you may see it").add_subparsers(dest="sub")
    s = replay.add_parser("show", help="what happened in a window, in time order, as you may see it (JSON)")
    s.add_argument("--from", dest="from", help="start, ISO 8601 with a zone (default 24 hours before --to)")
    s.add_argument("--to", help="end (default now); at most 31 days after --from")
    scope = s.add_mutually_exclusive_group()
    scope.add_argument("--bot", help="only what involves this bot")
    scope.add_argument("--person", help="only what involves this person")
    scope.add_argument("--team", help="only what involves this team")
    s.set_defaults(fn="replay show")
