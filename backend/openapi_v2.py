"""The stable v2 contract for a team's own frontend: `GET /api/v2/openapi.json`.

FastAPI would describe every route, runner and bot endpoint included. A frontend team needs the
resources a person's app is built from, so this module keeps only those (STABLE below), names
each operation, groups them by resource and describes the answers a person gets. Handlers return
plain dictionaries rather than response models, so the answer shapes are declared here; a test
(backend/tests/test_openapi_v2.py) checks them against live responses, and that the committed copy
`docs/openapi/v2.json` matches. Regenerate it with `python -m backend.openapi_v2`.

Anything not listed is internal and may change in any release. See docs/api.md.
"""

import copy
import json
import sys
from pathlib import Path

VERSION = "2.0.0"
COPY = Path(__file__).resolve().parents[1] / "docs" / "openapi" / "v2.json"

TAGS = {
    "Session": "Who is calling, and how a frontend signs in (docs/custom-frontend.md).",
    "Team": "This installation's names and settings.",
    "Repositories": "Team repositories, product-repository creation (the Owner, or a bot with Create product repositories), "
                    "and bot repository access.",
    "Subscriptions": "Provider logins on Computers. Every bot runs each AI tool on its Computer's one login for it.",
    "Team chart": "Humans and bots, and who reports to whom.",
    "Bots": "The bots a person can see, and their live status.",
    "Computers": "The computers that run bots: what each one runs at once.",
    "Conversations": "Chats with bots: send, list, and stream replies.",
    "Tasks": "Work assigned to people and bots.",
    "Updates": "Daily and weekly updates bots post to people.",
    "Changelog": "Shipped Tico releases, product announcements and the signed-in person's read state (docs/updates.md).",
    "Needs you": "What is waiting on the signed-in person: questions, tasks, approvals.",
    "Meetings": "Recorded meetings.",
    "Calls": "Tico Meet calls (docs/meet.md): audio and screen share in a thread, a live transcript posted to it when it "
             "ends, and bots that listen. Joining hands back a token for audio and screen share only, never a camera.",
    "Files": "What a bot creates, revises or delivers, listed on its page (docs/files.md).",
    "Pages": "HTML pages bots publish and keep current (docs/pages.md): a file with a cadence, a data date and a status line.",
    "Docs": "The team's docs (docs/docs.md): internal docs written or imported in Tico, with history and locks; external sources, including indexed public GitHub docs; and search across them.",
    "Assistant": "The signed-in person's own private Assistant chat (docs/assistant.md): ask, and confirm what it proposes.",
    "Goals": "What every person and bot is for (docs/goals-and-kpis.md): "
             "goals with a colour the Strategy Bot works out from their KPIs, "
             "unless a person set one; check-ins in the owner's words; proposals the owner confirms.",
    "KPIs": "Measures that stand on their own: a goal links to them and carries the target. Readings are facts with a period, "
            "evidence and a quality, never edited; a correction supersedes the old one.",
    "Usage": "Estimated model spend per bot (docs/usage.md): tokens counted by each run's computer, priced at list price.",
    "Health": "Whether the installation is working.",
    "Live events": "One server-sent event stream for a person's pages (docs/custom-frontend.md, Live events): tasks, "
                   "messages, runs, bots and Needs you as they change, resumable from a change number.",
}

EVENTS_DESCRIPTION = (
    "text/event-stream. Each change is `id: <change number>`, `event: <topic>` and JSON `data` with `seq`, `actor` "
    "(who made it, when the viewer may know) and `at`:\n"
    "- `tasks`: `id` and `task` (as `GET /api/v2/tasks` shows it to the viewer), or `gone: true` for a deleted task.\n"
    "- `messages`: `id`, `conversation_id` and `message` (as a conversation page shows it), or `deleted: true`; "
    "a chat goal set, paused, met or stopped is `goal_id` and `goal`.\n"
    "- `runs`: `attempt_id`, `conversation_id`, `bot`, and either `output` (one step of the run: `kind`, `payload`) or "
    "`state` (`job_id`, and the job's or attempt's state).\n"
    "- `bots`: `bot` and `status` (as `GET /api/v2/status` lists it).\n"
    "- `needs`: the viewer's own Needs-you `count` and `items` (as `GET /api/v2/needs-you`), whenever anything on it may have moved. "
    "With `needs=changes`: `count`, `version`, each bot's count (`bots`), and the ids `changed` and `removed` "
    "since `base` (null when more than 100 moved: read the list whole); read the changed items with "
    "`GET /api/v2/needs-you?ids=`, and the whole list (or its first pages) when the version held is not "
    "`base`. `ready` then carries `needs_version`.\n"
    "Also `event: ready` (`seq`: where the log stands), `event: cursor` (the change number to resume from, past changes "
    "the viewer may not see), `event: reset` (the client was further behind than the log keeps, 24 hours or more than 5,000 changes: read in full), "
    "`event: expired` (sign-in no longer valid; sign in and reconnect), and a `: keepalive` comment every 15 seconds. "
    "Only what the viewer may read is sent. A stream ends after about fifteen minutes; reconnect with `after` (or the "
    "browser's own `Last-Event-ID`). People only: a bot is refused with 403."
)

# Path, method, tag, operationId, summary, name of the 200 answer in ANSWERS.
STABLE = [
    ("/api/v2/subscriptions", "get", "Subscriptions", "listSubscriptions", "Subscription logins reported by each Computer", "SubscriptionList"),
    ("/api/v2/subscriptions/name", "put", "Subscriptions", "renameSubscription", "Rename a subscription without changing its local login", "SubscriptionIdentity"),
    ("/api/v2/subscriptions/weekly", "put", "Subscriptions", "recordSubscriptionWeekly", "Record or clear a manual weekly allowance observation", "SubscriptionWeeklyResult"),
    ("/api/v2/subscriptions/refresh", "post", "Subscriptions", "refreshSubscriptionWeekly", "Request a bounded weekly allowance read on its computer", "SubscriptionRefreshResult"),
    ("/api/v2/bots/{bot}/keep-tasks-going", "post", "Bots", "setKeepTasksGoing",
     "Set automatic assigned-task starts: null is unlimited, 0 is off, N caps active work; reviews have no cap", None),
    ("/api/v2/bots/{bot}/turns-at-once", "post", "Bots", "setBotTurnsAtOnce",
     "Set the most turns the bot runs at once on every computer: null is no limit, 1 to 256 otherwise", None),
    ("/api/v2/bots/{bot}/manages-reports", "post", "Bots", "setBotManagesReports",
     "Whether the bot's manager bot may change its direct reports: true is on (the default), false is off; the owner's team rule overrides it", None),
    ("/api/v2/computers/{rid}/turns-at-once", "post", "Computers", "setComputerTurnsAtOnce",
     "Set the most turns a computer runs at once: null is automatic (its own capacity)", None),
    ("/api/v2/computers/{rid}/browser-exit", "get", "Computers", "getComputerBrowserExit",
     "Whether this computer shares its internet with bots, which bots, and its daily cap in bytes", "BrowserExit"),
    ("/api/v2/computers/{rid}/browser-exit", "post", "Computers", "setComputerBrowserExit",
     "Share this computer's internet with chosen bots, or stop: its owner only; an admin may turn it off", None),
    ("/api/v2/computers/{rid}/browser-exit/usage", "get", "Computers", "listComputerBrowserExitUsage",
     "Bytes and connections through this computer per day, bot and hostname (30 days); its owner or an admin",
     "BrowserExitUsage"),
    ("/api/v2/bots/{bot}/browser-exit", "get", "Bots", "getBotBrowserExit",
     "The computer the bot's browser reaches the web through (null: its own) and the ones it may pick", "BotBrowserExitView"),
    ("/api/v2/bots/{bot}/browser-exit", "post", "Bots", "setBotBrowserExit",
     "Browse through a computer that shares its internet with this bot, or null for its own", "BotBrowserExitView"),
    ("/api/v2/bots/{bot}/subscription", "get", "Subscriptions", "getBotSubscription", "The Computer login a bot runs on", "BotSubscription"),
    ("/api/v2/repositories", "get", "Repositories", "listRepositories", "Team repositories", "RepositoryList"),
    ("/api/v2/repositories/refresh", "post", "Repositories", "refreshRepositories", "Refresh from GitHub", "RepositoryList"),
    ("/api/v2/github/product-repos/preview", "get", "Repositories", "previewProductRepository", "Preview an empty private product repository", "ProductRepositoryPreview"),
    ("/api/v2/github/product-repos", "post", "Repositories", "createProductRepository", "Create the confirmed product repository", "ProductRepositoryResult"),
    ("/api/v2/repositories/settings", "put", "Repositories", "setRepositoryDefaults", "New bot repository default", None),
    ("/api/v2/repositories/{owner}/{repo}", "put", "Repositories", "updateRepository", "Tick or edit a repository", None),
    ("/api/v2/bots/{bot}/repositories", "get", "Repositories", "getBotRepositories", "Bot repository access", "BotRepositories"),
    ("/api/v2/bots/{bot}/repositories", "put", "Repositories", "setBotRepositories", "Set bot repository access", "BotRepositories"),
    ("/api/v2/bots/{bot}/github-repos", "get", "Repositories", "getExtraRepositories", "Chosen write repositories (legacy alias)", None),
    ("/api/v2/bots/{bot}/github-repos", "put", "Repositories", "setExtraRepositories", "Set chosen write repositories (legacy alias)", None),
    ("/api/v2/me", "get", "Session", "getMe", "The signed-in caller", "Me"),
    ("/auth/login", "get", "Session", "startSignIn",
     "Start browser sign-in (redirects); a frontend passes next and code_challenge", None),
    ("/auth/token", "post", "Session", "exchangeCode", "Exchange the one-time sign-in code for a bearer session", "Token"),
    ("/auth/token/revoke", "post", "Session", "revokeSession", "Sign the bearer session out", "Revoked"),
    ("/api/v2/me/tokens", "get", "Session", "listMyTokens", "The caller's personal API tokens (never the secret)", None),
    ("/api/v2/me/tokens", "post", "Session", "createMyToken",
     "Mint a personal API token for server-to-server use (any person unless the owner limits it to admins; cookie sessions only)", None),
    ("/api/v2/me/tokens/{token_id}/revoke", "post", "Session", "revokeMyToken",
     "Revoke a personal API token: your own; the owner and admins revoke anyone's", None),
    ("/api/v2/service-keys", "get", "Session", "listServiceKeys", "Service keys, never the secret (owner and admins)", None),
    ("/api/v2/service-keys", "post", "Session", "createServiceKey",
     "Make a key another system uses to file, update and close tasks (scope tasks), or to update this install (scope update, "
     "owner only), and nothing else; shown once (owner and admins; a key made with a personal token ends with it)", None),
    ("/api/v2/service-keys/{key_id}/revoke", "post", "Session", "revokeServiceKey", "Revoke a service key", None),
    ("/api/v2/openapi.json", "get", "Session", "getOpenApi", "This document", None),
    ("/api/v2/config", "get", "Team", "getConfig", "Team and app names, version, setup state", "Config"),
    ("/api/v2/org", "get", "Team chart", "getOrg",
     "Humans, the bots the caller can see and reports_to; ?can=read|write keeps the bots they can read or write to", "Org"),
    ("/api/v2/org-models", "get", "Team chart", "listOrgModels",
     "Published Tico starter org charts used for re-org recommendations; contains no customer org data", "OrgModels"),
    ("/api/v2/humans/{pid}", "post", "Team chart", "updatePerson", "Edit a person's profile or reports_to", None),
    ("/api/v2/humans/{pid}", "delete", "Team chart", "removePerson",
     "Remove a left person with no history for good (owner only); anyone with history stays as left", None),
    ("/api/v2/humans/{pid}/access", "get", "Team chart", "getPersonAccess",
     "Who may see, read and write to a person (themselves, whoever they report up to, admins; docs/permissions.md)",
     "TeammateAccessView"),
    ("/api/v2/humans/{pid}/access", "put", "Team chart", "setPersonAccess",
     "Set who may see, read and write to a person; send the revision you read (409 version_conflict otherwise)",
     "TeammateAccessView"),
    ("/api/v2/seats", "get", "Team chart", "listSeats",
     "The Recruiting seats: roles on the chart with a job description and reporting line, and no bot. A seat holds no "
     "work: a task for it is refused and its role's work stays with its manager", "SeatList"),
    ("/api/v2/seats", "post", "Team chart", "createSeat",
     "Add a Recruiting seat: the owner anywhere, a person under themselves or someone below them", "Seat"),
    ("/api/v2/seats/batch", "post", "Team chart", "createSeats",
     "Add a group of Recruiting seats in one transaction; the owner anywhere, others only under themselves or below", "SeatBatch"),
    ("/api/v2/seats/{seat_id}", "patch", "Team chart", "updateSeat",
     "Change a seat's title, job description, reporting line or group (a person it reports up to, or the owner)", "Seat"),
    ("/api/v2/seats/{seat_id}/close", "post", "Team chart", "closeSeat", "Take a seat off the chart; its row is kept",
     "SeatClosed"),
    ("/api/v2/seats/draft", "post", "Team chart", "draftSeat",
     "A job description for a job not posted yet, from its title: composed from the template catalog at once, or "
     "with `redraft` rewritten once by the model when the server has a key. Stores nothing", "SeatDrafted"),
    ("/api/v2/seats/{seat_id}", "get", "Team chart", "getSeat",
     "One seat, with whose words its job description is and whether the caller may edit it", "SeatDetail"),
    ("/api/v2/seats/{seat_id}/recruit", "post", "Team chart", "recruitSeat",
     "Up to eight Tico templates for the seat at once, best fit first, each with one line, what it needs, how well it "
     "matches and a suggested model, and the models one may choose instead. Local: nothing leaves the server",
     "SeatCandidates"),
    ("/api/v2/seats/{seat_id}/hire", "post", "Team chart", "hireSeat",
     "Pick a candidate: the bot is created in Onboarding in the seat's place, then its repository", "SeatHired"),
    ("/api/v2/groups", "get", "Team chart", "listGroups",
     "The groups, nested by `parent`, each with its humans and the bots the caller can see", "GroupList"),
    ("/api/v2/groups", "post", "Team chart", "createGroup",
     "Add a group: a name, an optional parent group and teammates to start with (owners and admins)", "Group"),
    ("/api/v2/groups/{gid}", "patch", "Team chart", "updateGroup",
     "Rename a group, move it under another (`parent`, empty for the top) and add or remove teammates (owners and admins)", "Group"),
    ("/api/v2/groups/{gid}", "delete", "Team chart", "deleteGroup",
     "Remove a group; the groups and teammates in it move up to its parent (owners and admins)", None),
    ("/api/v2/groups/{gid}/access", "get", "Team chart", "getGroupAccess",
     "Who may see, read and write to a group (its leads and admins; docs/permissions.md)", "TeammateAccessView"),
    ("/api/v2/groups/{gid}/access", "put", "Team chart", "setGroupAccess",
     "Set who may see, read and write to a group; send the revision you read (409 version_conflict otherwise)",
     "TeammateAccessView"),
    ("/api/v2/org/order/{manager}", "get", "Team chart", "getReportOrder",
     "A manager's direct reports (a bot's slug or a human's id), humans and the bots the caller can see, in the "
     "chart's order", "ReportOrderView"),
    ("/api/v2/org/order/{manager}", "put", "Team chart", "setReportOrder",
     "Put a manager's reports in this order; the ones left out keep their places (whoever may put reports under "
     "that manager, and a bot for its own)", "ReportOrderView"),
    ("/api/v2/bots", "get", "Bots", "listBots",
     "Bots the caller can see, each with the caller's own access; ?can=read|write keeps the ones they can read or write to",
     "BotList"),
    ("/api/v2/bots/{bot}", "get", "Bots", "getBot",
     "One bot: its profile for anyone who can see it; status, computer, queue and goals too for anyone who can read it",
     "BotDetail"),
    ("/api/v2/bots/{bot}/pause-state", "get", "Bots", "getBotPauseState",
     "Pause state and recorded person/time, readable open-task count and company billing pause; no billing amounts", None),
    ("/api/v2/bots/{bot}/access", "get", "Bots", "getBotAccess",
     "Who may see, read and write to a bot (its managers only; docs/permissions.md)", "BotAccessView"),
    ("/api/v2/bots/{bot}/access", "put", "Bots", "setBotAccess",
     "Set who may see, read and write to a bot; send the revision you read (409 version_conflict otherwise)", "BotAccessView"),
    ("/api/v2/bots/{bot}/instructions", "get", "Bots", "getBotInstructions",
     "Latest Instructions snapshot published by the Computer; requires Read, published is false until content arrives",
     "BotInstructions"),
    ("/api/v2/bots/{bot}/onboarded", "post", "Bots", "markBotOnboarded",
     "Mark onboarding complete, by a person above the bot on the org chart (its manager, their managers or the owner; "
     "403 for anyone else): its `stage` goes from onboarding to active (`onboarding_state` `needs_setup` to "
     "`onboarded`) and its automations start. The bot's own call is its \"I'm ready\" with a `summary` and leaves it "
     "onboarding. Repeating it changes nothing; 409 bot_limit for a member's bot over their limit",
     "BotOnboarded"),
    ("/api/v2/bots/{bot}/tools", "get", "Bots", "listBotTools",
     "The tools a bot uses, for the row at the top of its page: its model and harness, its repository and each "
     "declared `access:` entry, with its status; names and verbs, never a secret", "BotTools"),
    ("/api/v2/bots/{bot}/tools", "post", "Bots", "registerBotTool",
     "Register a tool for a bot you manage: validated, kept as a pending request and handed to BotOps as a task with the "
     "exact `access:` entry. Never a credential: `env` is a variable's name", "BotToolRegistered"),
    ("/api/v2/bots/{bot}/tools/{tool_id}", "delete", "Bots", "removeBotTool",
     "Ask BotOps to remove a declared tool, or withdraw a pending request (bot managers)", "BotToolRemoval"),
    ("/api/v2/bots/{bot}/tools/{tool_id}/delete", "post", "Bots", "removeBotToolPost",
     "The same as DELETE, for clients that only send GET and POST", "BotToolRemoval"),
    ("/api/v2/bots/{bot}/tools/{tool_id}/update", "post", "Bots", "updateBotTool",
     "Change a declared tool's `can`, `scope` or `note` in place (bot managers): one BotOps task with the changed entry; "
     "the tool shows `pending: update` until the computer reports it", "BotToolUpdated"),
    ("/api/v2/models", "get", "Bots", "listModels", "Models a bot can be set to", None),
    ("/api/v2/me/recent", "get", "Bots", "listRecentBots", "Bots the caller worked with lately", "Recent"),
    ("/api/v2/bots/{bot}/updates", "get", "Updates", "getBotUpdateSettings", "A bot's daily and weekly update settings", None),
    ("/api/v2/conversations", "get", "Conversations", "listConversations",
     "The caller's conversations; chat_with=<bot> for that bot's chat", "ConversationList"),
    ("/api/v2/conversations", "post", "Conversations", "createConversation", "Open a conversation", None),
    ("/api/v2/conversations/{cid}/messages", "get", "Conversations", "listMessages",
     "The newest messages (up to 200, or limit=); before=<message id> pages back, since=<timestamp> only newer, "
     "max_bytes= ends the page at that many bytes of messages", "MessagePage"),
    ("/api/v2/conversations/{cid}/messages", "post", "Conversations", "replyInConversation",
     "Send a message in a conversation", "MessageResult"),
    ("/api/v2/conversations/{cid}/snapshot", "get", "Conversations", "getConversationSnapshot",
     "The newest messages and the bot's current run (its newest 40 reply pieces), in one read; limit= and max_bytes= "
     "as for the messages", "Snapshot"),
    ("/api/v2/chat/{bot}", "post", "Conversations", "chatWithBot", "Send a message to a bot (opens the chat if needed)", "ChatResult"),
    ("/api/v2/chat/{bot}/new", "post", "Conversations", "startNewChat", "Archive the current personal chat and start fresh", None),
    ("/api/v2/conversations/{cid}/goal", "get", "Conversations", "getChatGoal", "Read the pinned goal and commands", "ChatGoalResult"),
    ("/api/v2/conversations/{cid}/goal", "post", "Conversations", "setChatGoal", "Set, edit, pause, resume or clear a native goal", "ChatGoalResult"),
    ("/api/v2/messages", "post", "Conversations", "sendMessage", "Send a message to a person or bot", "Message"),
    ("/api/v2/messages/{mid}", "get", "Conversations", "getMessage", "One message", "Message"),
    ("/api/v2/task-types", "get", "Tasks", "listTaskTypes", "Task types and their ordered steps", "TaskTypeList"),
    ("/api/v2/task-types", "post", "Tasks", "createTaskType", "Create a task type (movers only)", "TaskTypeResult"),
    ("/api/v2/task-types/{type_id}", "get", "Tasks", "getTaskType", "One task type and its steps", "TaskTypeResult"),
    ("/api/v2/task-types/{type_id}", "post", "Tasks", "updateTaskType", "Edit a type and replace its steps, keeping retained ids (movers only)", "TaskTypeResult"),
    ("/api/v2/task-types/{type_id}", "delete", "Tasks", "deleteTaskType", "Delete an unused task type (movers only)", "TaskTypeResult"),
    ("/api/v2/task-types/{type_id}/delete", "post", "Tasks", "deleteTaskTypePost", "Delete an unused type for clients using POST", "TaskTypeResult"),
    ("/api/v2/tasks", "get", "Tasks", "listTasks",
     "Tasks the caller can see; type, step, number and updated_since filter, member=<actor> keeps the tasks that "
     "person or bot is on in some role (role=<name> narrows it to one), sort=step orders a board's columns, "
     "brief=true leaves out bodies. Snoozed tasks are left out unless snoozed=only|all or asked for by ids or "
     "number. Each answer carries a `cursor`; changed_after=<cursor> with the same filters "
     "answers only the tasks changed since (`tasks`) and the ids that left (`gone`), or `reset: true` to read in "
     "full. A full read has an ETag: If-None-Match answers 304 when nothing changed", "TaskList"),
    ("/api/v2/tasks", "post", "Tasks", "createTask", "Create a task", "TaskResult"),
    ("/api/v2/tasks/dry-run", "post", "Tasks", "checkTask", "The checks a create would fail; writes nothing", None),
    ("/api/v2/task-bulk", "post", "Tasks", "bulkChangeTasks",
     "One change to every task a filter selects (filter takes the listTasks filters, and ids); action is step, status, "
     "owner, tags (add, remove) or close. Each task changes as updateTask would change it: a task the caller may not "
     "change is skipped with its reason, the rest still change. At most 200 tasks: a filter matching more is refused "
     "(422 too_many). dry_run makes every change and undoes it, so its counts are the real ones", "TaskBulkResult"),
    ("/api/v2/tasks/labels", "get", "Tasks", "listTaskLabels", "Labels in use (ETag: If-None-Match answers 304 when unchanged)", None),
    ("/api/v2/tasks/{tid}", "get", "Tasks", "getTask", "A task with its history, comments and messages", "TaskDetail"),
    ("/api/v2/tasks/{tid}", "post", "Tasks", "updateTask",
     "Change a task; send the version you read (409 version_conflict otherwise). status closed is close:true; closing and editing are separate operations", "TaskResult"),
    ("/api/v2/tasks/{tid}/files", "get", "Tasks", "listTaskFiles", "Files and versions on a task", "TaskFileList"),
    ("/api/v2/tasks/{tid}/files", "post", "Tasks", "attachTaskFile", "Attach a file; the same name adds a version", "TaskFileResult"),
    ("/api/v2/tasks/{tid}/comments", "get", "Tasks", "listTaskComments", "Comments and questions with their answers", "TaskComments"),
    ("/api/v2/tasks/{tid}/answers", "get", "Tasks", "listTaskAnswers", "Structured answers, oldest first", "TaskAnswers"),
    ("/api/v2/tasks/{tid}/answers", "post", "Tasks", "answerTaskQuestion", "Answer or dismiss a comment or file version question", "TaskAnswerResult"),
    ("/api/v2/tasks/{tid}/comments", "post", "Tasks", "commentOnTask", "Comment on a task", "CommentResult"),
    ("/api/v2/inbound/tasks", "post", "Tasks", "upsertInboundTask",
     "Another system's work as it is now, by its own key: files, updates, closes or reopens one task (service key only; "
     "docs/service-keys.md)", "InboundTaskResult"),

    ("/api/v2/tasks/{tid}/comments/{mid}", "post", "Tasks", "editTaskComment",
     "Change the text of a comment you wrote; it wakes nobody and is marked edited_at", "CommentResult"),
    ("/api/v2/tasks/{tid}/comments/{mid}/delete", "post", "Tasks", "deleteTaskComment",
     "Delete a comment you wrote from future comment reads and bot context; existing delivered copies remain", "CommentResult"),
    ("/api/v2/tasks/{tid}/comments/{mid}/resolve", "post", "Tasks", "resolveTaskQuestion",
     "Settle an open question answered elsewhere ({quote}: its asker or the task's owner bot), or withdraw it ({}: its "
     "asker); it leaves every Needs you list and wakes nobody", "CommentResult"),
    ("/api/v2/tasks/{tid}/relations", "post", "Tasks", "relateTask",
     "Relate another task to this one ({task, kind}: parent, blocks, blocked_by, related, duplicate_of, "
     "follow_up), or take the relation off with remove", "TaskResult"),
    ("/api/v2/tasks/{tid}/delete", "post", "Tasks", "deleteTask",
     "Delete a task made by mistake, with its conversation, to the trash: its human requester or a mover, signed in "
     "as themselves, whatever bots did on it: that work stays in place, hidden until a restore", None),
    ("/api/v2/tasks/{tid}/model", "post", "Tasks", "setTaskModel",
     "Set the model this task's runs use, with its effort and harness (backend/task_run_models.py): an absent `model` "
     "keeps it and changes only what is named, and a null `model` clears it so the bot's task model runs the task "
     "again. Whoever may change the bot's model may change this; a bot never does. Its `readiness` says whether the bot's "
     "computer can run it now", "TaskRunModelResult"),
    ("/api/v2/tasks/{tid}/snooze", "post", "Tasks", "snoozeTask",
     "Set a task aside until `until` (an ISO-8601 date or date-time): off lists and Needs you, and quiet, until "
     "then, when its owner bot is woken once. Its requester, owner, anyone on it by role, or a mover", "TaskResult"),
    ("/api/v2/tasks/{tid}/unsnooze", "post", "Tasks", "unsnoozeTask",
     "Bring a snoozed task back now; its owner bot is woken once", "TaskResult"),
    ("/api/v2/tasks/{tid}/restore", "post", "Tasks", "restoreTask",
     "Put a deleted task back with its conversation, comments, links and number: whoever deleted it, its "
     "requester or a mover, signed in as themselves", None),
    ("/api/v2/deleted-tasks", "get", "Tasks", "listDeletedTasks",
     "Deleted tasks this person may restore, newest first", None),
    ("/api/v2/updates", "get", "Updates", "listUpdates", "Daily and weekly updates", "UpdateList"),
    ("/api/v2/changelog", "get", "Changelog", "listChangelog",
     "Shipped changes with your read state; kind=product|activity|all (default all), unread=true, q search, "
     "since_version for releases newer than a version, and limit/offset paging. Only installed releases or earlier appear. "
     "unread_count counts all unread product entries, independently of filters; reading does not acknowledge them.", "ChangelogList"),
    ("/api/v2/changelog/read", "post", "Changelog", "markChangelogRead",
     "Acknowledge only supplied product entry ids for the signed-in person. Other people's read state and unseen entries stay unchanged.",
     "ChangelogReadResult"),
    ("/api/v2/updates/unread", "get", "Updates", "countUnreadUpdates", "How many updates are unread", "Unread"),
    ("/api/v2/updates/read", "post", "Updates", "markUpdatesRead", "Mark updates read or unread", None),
    ("/api/v2/updates/archive", "post", "Updates", "archiveUpdates",
     "Archive or restore updates for the signed-in person", "UpdateArchiveResult"),
    ("/api/v2/updates/{uid}", "get", "Updates", "getUpdate", "One update and the replies to it", None),
    ("/api/v2/updates/{uid}/reply", "post", "Updates", "replyToUpdate", "Reply to an update (goes to the bot)", None),
    ("/api/v2/updates/redo", "post", "Updates", "redoUpdates",
     "Ask the bots again for a past day's update in the current shape: the owner", None),
    ("/api/v2/events", "get", "Live events", "streamEvents",
     "Server-sent events: tasks, messages, runs, bots and Needs you as they change. topics=<comma list> (default all), "
     "after=<change number> resumes, conversation=<ids> narrows messages and runs, bot=<slugs> narrows runs and bots, "
     "task=<ids> and party=<actors> narrow tasks to those and to the ones those actors own or asked for, needs=changes sends Needs you as changed ids", None),
    ("/api/v2/needs-you", "get", "Needs you", "getNeedsYou",
     "What waits on the caller, with its `version` and `count`; count=true for the number alone, version=<held> "
     "answers `unchanged` without items while it holds, ids=<comma list> only those items and their `positions`, "
     "limit=<n> (at most 200) a page with `next` to pass as cursor=<next> for the one after it (a cursor from an older version "
     "starts again from the top, with `restart`); without limit or cursor, the whole list", "NeedsYou"),
    ("/api/v2/messages/{mid}/answer", "post", "Needs you", "answerMessage", "Answer a question a bot asked", None),
    ("/api/v2/needs-you/recommended", "post", "Needs you", "takeRecommendation",
     "Do what the bot recommends: answer its ask with what it said it would do, and close the card", None),
    ("/api/v2/needs-you/clarify", "post", "Needs you", "clarifyAsk",
     "Ask the bot behind a stale question whether it still matters, in clearer words", None),
    ("/api/v2/stale-review/accept-all", "post", "Needs you", "acceptAllStaleSuggestions",
     "Accept the suggestions your Stale tickets card showed (their ids and task versions), each as your own change; "
     "a row whose task changed since is refused on its own and a row added since is left", None),
    ("/api/v2/stale-review/{suggestion_id}", "post", "Needs you", "decideStaleSuggestion",
     "Accept or skip one suggestion on your Stale tickets card; accept sends the task version it showed "
     "(409 version_conflict otherwise)", None),
    ("/api/v2/approvals/{aid}", "get", "Needs you", "getApproval", "One approval request", None),
    ("/api/v2/approvals/{aid}", "post", "Needs you", "decideApproval", "Approve or reject", None),
    ("/api/v2/approvals/{aid}/undo", "post", "Needs you", "undoApproval",
     "Take back a decision your Assistant made for you, while nothing has acted on it", None),
    ("/api/v2/meetings", "get", "Meetings", "listMeetings", "Meetings; review=pending reads your own queue", "MeetingReviewList"),
    ("/api/v2/meetings/import", "post", "Meetings", "importMeeting", "Import into your Pending queue unless shared explicitly or automatically", None),
    ("/api/v2/meetings/settings", "get", "Meetings", "getMeetingReviewSettings", "Your meeting review preference and Team default", "MeetingReviewPreferences"),
    ("/api/v2/meetings/settings", "post", "Meetings", "setMeetingReviewSettings", "Set auto-share; only the owner sets the Team default", "MeetingReviewPreferences"),
    ("/api/v2/meetings/review", "post", "Meetings", "reviewMeetings", "Share or dismiss your Pending meetings", "MeetingReviewList"),
    ("/api/v2/meetings/{id}/review", "post", "Meetings", "reviewMeeting", "Approve, dismiss or restore your own meeting", "ReviewedMeeting"),
    ("/api/v2/meetings/{id}", "get", "Meetings", "getMeeting", "Read a meeting with its notes and transcripts", "ReviewedMeeting"),
    ("/api/v2/meetings/granola", "get", "Meetings", "getGranolaStatus", "Your Granola connection", "GranolaStatus"),
    ("/api/v2/meetings/granola/connect", "post", "Meetings", "connectGranola", "Start personal Granola browser sign-in", "GranolaDevice"),
    ("/api/v2/meetings/granola/connect/status", "get", "Meetings", "pollGranolaSignIn", "Poll personal sign-in at the provider interval", "GranolaSignIn"),
    ("/api/v2/meetings/granola/connect", "delete", "Meetings", "disconnectGranola", "Delete your encrypted Granola tokens", "GranolaDisconnect"),
    ("/api/v2/meetings/granola/sync", "post", "Meetings", "syncGranola", "Start a background sync; reuse the last two minutes", "GranolaSync"),
    ("/api/v2/meetings/search", "get", "Meetings", "searchMeetings", "Search or list recorded meetings", "MeetingSearch"),
    ("/api/v2/meetings/transcript", "get", "Meetings", "getMeetingTranscript", "A meeting's transcript", None),
    ("/api/v2/bots/{bot}/files", "get", "Files", "listBotFiles",
     "A bot's files the caller may see, newest activity first, with the visible total; limit and cursor page", "BotFileList"),
    ("/api/v2/files/uploads", "post", "Files", "uploadBotFile",
     "A bot (or its computer) publishes a file: raw bytes with the fields in the query, or JSON text/content_base64", "BotFileResult"),
    ("/api/v2/files/links", "post", "Files", "addFileLink",
     "Register or touch an https document (Google, Notion, Figma, any site); Tico keeps the address only", "BotFileResult"),
    ("/api/v2/files/imports", "post", "Files", "importFile",
     "Bytes the bot's computer copied from an S3 object; a changed etag is a new version", "BotFileResult"),
    ("/api/v2/files/{fid}", "patch", "Files", "editBotFile",
     "Change a file's title or task, remove it from the list (archive) or promote it bot-wide (owner and bot administrators)",
     "BotFileResult"),
    ("/api/v2/files/{fid}/activity", "get", "Files", "listFileActivity", "A file's append-only activity, newest first", "FileActivity"),
    ("/api/v2/files/{fid}/versions", "get", "Files", "listFileVersions", "A file's versions, newest first", "FileVersions"),
    ("/api/v2/files/{fid}/versions/{number}", "patch", "Files", "editFileVersion", "Edit a version's note or question as its author", "VersionReview"),
    ("/api/v2/files/{fid}/versions/{number}", "get", "Files", "getFileVersion",
     "The bytes of one version (a download, never a storage address)", None),
    ("/api/v2/docs", "get", "Docs", "listInternalDocs",
     "Internal docs by path, without their text; path_prefix narrows to a folder, limit and cursor page", "InternalDocList"),
    ("/api/v2/docs", "post", "Docs", "createInternalDoc",
     "Write a new internal doc (Markdown). The path defaults to a slug of the title; a chosen path that is taken is a 409", "InternalDocResult"),
    ("/api/v2/docs/search", "get", "Docs", "searchInternalAndLinkedDocs",
     "Search team and manual sections by relevance, with excerpts, and linked docs by title, note and address; each result has a type",
     "DocsSearchResults"),
    ("/api/v2/doc-sources", "get", "Docs", "listDocSources", "External sources and indexing state; limit/cursor page", "DocSourceList"),
    ("/api/v2/doc-sources", "post", "Docs", "addDocSource", "Save a URL and queue public GitHub indexing; duplicate URLs open the existing source", "DocSourceResult"),
    ("/api/v2/doc-sources/{source_id}", "patch", "Docs", "updateDocSource", "Edit or remove a source; follows linked-doc permissions", None),
    ("/api/v2/doc-sources/{source_id}/refresh", "post", "Docs", "refreshDocSource", "Queue one coalesced refresh without waiting for GitHub", "DocSourceResult"),
    ("/api/v2/doc-sources/{source_id}/documents", "get", "Docs", "listSourceDocuments", "Included file metadata, limit/cursor page", "SourceDocumentList"),
    ("/api/v2/docs/external/{document_id}", "get", "Docs", "getExternalDoc", "Untrusted external text and provenance; optional observed revision", "ExternalDocResult"),
    ("/api/v2/docs/import", "post", "Docs", "importInternalDoc",
     "A file (.md .markdown .txt .html .htm .docx .pdf, up to 20 MB) converted to Markdown and saved as a new internal doc",
     "InternalDocResult"),
    ("/api/v2/docs/{doc_id}", "get", "Docs", "getInternalDoc", "One internal doc with its text and version", "InternalDocResult"),
    ("/api/v2/docs/{doc_id}", "patch", "Docs", "updateInternalDoc",
     "Change a doc's title, text or path, archive it, or lock it (owner and bot administrators). `version` must be the "
     "current one or the answer is 409 version_conflict; a locked doc answers 403 locked to everyone else", "InternalDocResult"),
    ("/api/v2/docs/{doc_id}/versions", "get", "Docs", "listInternalDocVersions",
     "A doc's versions, newest first, without their text", "InternalDocVersions"),
    ("/api/v2/docs/{doc_id}/versions/{number}", "get", "Docs", "getInternalDocVersion", "One version with its text",
     "InternalDocVersionResult"),
    ("/api/v2/docs/{doc_id}/restore", "post", "Docs", "restoreInternalDoc",
     "Save an earlier version as a new version (the history is never rewritten)", "InternalDocResult"),
    ("/api/v2/linked-docs", "get", "Docs", "listLinkedDocs", "External link identities and metadata; public GitHub content is indexed separately", "LinkedDocList"),
    ("/api/v2/linked-docs", "post", "Docs", "addLinkedDoc",
     "Link a doc by its address; the kind is detected and the title defaults to the host and path. Anyone may add one", "LinkedDocResult"),
    ("/api/v2/linked-docs/{link_id}", "patch", "Docs", "updateLinkedDoc",
     "Change or remove (archived) a linked doc: whoever added it, owners and bot administrators", "LinkedDocResult"),
    ("/api/v2/context/search", "get", "Docs", "searchDocs", "Search team documents", "DocSearch"),
    ("/api/v2/context/document", "get", "Docs", "getDocument", "One document", None),
    ("/api/v2/docs/ask", "post", "Docs", "askDocs",
     "Ask the Librarian a question about the team's docs. It goes to the caller's own private docs conversation; "
     "`results` is the instant search (same shape as docs/search) and the answer streams on "
     "GET /api/v2/events?topics=messages,runs&conversation={cid}", "DocsAsked"),
    ("/api/v2/librarian", "get", "Docs", "getLibrarian", "Whether the Librarian is on, and whether the caller can turn it on",
     "Librarian"),
    ("/api/v2/librarian/conversations", "get", "Docs", "listDocsConversations",
     "The caller's private Docs conversations, including archived ones; limit and offset page back", "DocsConversations"),
    ("/api/v2/librarian/conversations/{cid}/reopen", "post", "Docs", "reopenDocsConversation",
     "Make the caller's own Docs conversation current, archiving their current one; 409 busy while it is answering",
     "DocsReopened"),
    ("/api/v2/librarian/research", "post", "Docs", "requestLibrarianResearch",
     "Ask the Librarian to research the web and add what it finds, cited, to one doc, market page or market entity "
     "(`target`), optionally one `section` of it; it files one task to the Librarian", "LibrarianResearchFiled"),
    ("/api/v2/librarian/setup", "get", "Docs", "getLibrarianSetup",
     "The Librarian's first-run setup for the caller: whether Docs asks where the docs are, and the owner's market "
     "answers so far", "LibrarianSetup"),
    ("/api/v2/librarian/setup/docs", "post", "Docs", "setUpDocs",
     "Add the places the caller's docs are as sources and file one \"Set up Docs\" task to the Librarian; "
     "`skip` hides the first-visit question for the caller only", None),
    ("/api/v2/librarian/setup/market", "post", "Docs", "setUpMarket",
     "Owner only: keep the answers to the Librarian's four market questions (`save`, `skip`), or `finish` and file "
     "\"Set up the market map\", which builds the map and sets what Market tracks", "LibrarianMarketSetup"),
    ("/api/v2/librarian/turn-on", "post", "Docs", "turnOnLibrarian",
     "Owner only: add the Librarian from the catalog (or wake a planned one) and activate it", None),
    ("/api/v2/assistant", "get", "Assistant", "getAssistant",
     "The caller's Assistant (the Strategy Bot, for the owner): their private room id, "
     "whether it is on, the recent messages "
     "and what waits for their OK",
     "Assistant"),
    ("/api/v2/assistant/messages", "post", "Assistant", "sendAssistantMessage",
     "Say something to the Assistant. A lookup is answered at once (fast: true, with the reply); anything else "
     "is a turn of the assistant bot, shown as `execution` on GET /api/v2/assistant. `place: updates` (the Updates "
     "chat) always takes a turn, handed the recent updates the caller may read", "AssistantSent"),
    ("/api/v2/assistant/turn-on", "post", "Assistant", "turnOnAssistant",
     "Owner only: restore the archived assistant, or add it from the catalog, and activate it", None),
    ("/api/v2/assistant/actions", "post", "Assistant", "proposeAssistantAction",
     "Propose one operation for the caller to confirm (what the assistant's `hub assistant propose` calls)", "AssistantActionResult"),
    ("/api/v2/assistant/actions/{aid}", "get", "Assistant", "getAssistantAction", "One proposal and how it ended",
     "AssistantActionResult"),
    ("/api/v2/assistant/actions/{aid}/confirm", "post", "Assistant", "confirmAssistantAction",
     "The person's own click: runs the proposal as them, once. Never callable by the assistant or a personal token",
     "AssistantActionResult"),
    ("/api/v2/assistant/actions/{aid}/cancel", "post", "Assistant", "cancelAssistantAction",
     "Drop a proposal; it never runs", "AssistantActionResult"),
    ("/api/v2/assistant/settings", "get", "Assistant", "getAssistantSettings",
     "Your Assistant settings: whether it decides approvals for you, and whether the team allows it", None),
    ("/api/v2/assistant/settings", "post", "Assistant", "setAssistantSettings",
     "Let your Assistant decide approvals addressed to you, or stop it. Only you, signed in to the app", None),
    ("/api/v2/assistant/decisions", "get", "Assistant", "listAssistantDecisions",
     "The approvals your Assistant decided for you, with your words and whether each can still be undone", None),
    ("/api/v2/assistant/read-back", "post", "Assistant", "readBackAssistantApprovals",
     "The Assistant only: post the list of approvals it is about to approve for you in your chat; only those may then "
     "be approved on your yes", None),
    # Goals and KPIs (docs/goals-and-kpis.md)
    ("/api/v2/strategy-bot", "get", "Goals", "getStrategyBot",
     "The Strategy Bot's name, whether its regular checks run, how much it does (its level, "
     "with each level's sentence and "
     "who changed it when), and how many items it put in each person's Needs you today against its cap of 5 "
     "(everyone's for the owner, admins and the Strategy Bot; your own for anyone else)", "StrategyBotView"),
    ("/api/v2/strategy-bot/level", "post", "Goals", "setStrategyBotLevel",
     "Settings > Strategy Bot, \"How much your Strategy Bot does\": the owner picks Passive (1), "
     "Suggests actions (2) or Acts on "
     "its own (3) on their own click, logged with who and when. No admin or bot may, the Strategy Bot included",
     "StrategyBotView"),
    ("/api/v2/strategy-bot/checks", "post", "Goals", "setStrategyBotChecks",
     "Settings > Strategy Bot, \"Regular checks\": the owner turns the KPI pass and the "
     "automatic goal colours on or off, "
     "on their own click. Off, neither runs until they are turned back on. No admin or bot may", "StrategyBotView"),
    ("/api/v2/strategy-bot/merge", "post", "Goals", "mergeStrategyBotAsks",
     "The Strategy Bot only, at level 2 or 3: one item in a person's Needs you for 2 to 10 "
     "tasks where bots ask them the "
     "same thing. The originals leave their Needs you while it is open; their one answer goes to each bot that asked, "
     "and an item that ends unanswered puts the originals back. Counts toward its 5 a day", "StrategyBotMergeResult"),
    ("/api/v2/strategy-bot/level3-offer", "post", "Goals", "recordStrategyBotLevel3Offer",
     "The Strategy Bot only: record that its Monday note offered level 3. Refused (422) unless level3_offer.due: 10 "
     "recoveries approved in a row with its pick unchanged, and no offer in 30 days. It never raises its own level",
     "StrategyBotLevel3OfferResult"),
    ("/api/v2/strategy-bot/decisions", "post", "Goals", "makeStrategyBotDecision",
     "The Strategy Bot only, at level 3: one decision made alone, with its reason: "
     "bot tasks created, moved to the top of "
     "their queue, given to another bot or closed, under one action id in the Strategy Bot log. A person's task, a bot "
     "offline or a refusal becomes one proposal in the owner's "
     "Needs you instead, never dropped", "StrategyBotLogEntry"),
    ("/api/v2/strategy-bot/log", "get", "Goals", "listStrategyBotLog",
     "The Strategy Bot log, newest first: each decision it made alone and each thing it proposed because it stays the "
     "owner's (cost, publishing, outside sends, adding or removing bots, goal and KPI targets, the vision, a bot's "
     "instructions, a person's task), with the reason. The owner only; anyone else is refused (403)", "StrategyBotLog"),
    ("/api/v2/strategy-bot/log/{action_id}/undo", "post", "Goals", "undoStrategyBotDecision",
     "The owner, on their own click, undoes one decision the Strategy Bot made alone: each task goes back where nobody "
     "changed it since, a later change is kept and named, a task it made is closed, and each bot is told",
     "StrategyBotLogEntry"),
    ("/api/v2/company", "get", "Goals", "getCompany",
     "The team's Vision and Description (the Company section at the top of Goals), and whether the caller may edit "
     "them. The Vision is the strategy's: the draft's for the owner, the published one for everyone else",
     "CompanyResult"),
    ("/api/v2/company", "post", "Goals", "updateCompany",
     "The owner, on their own click: change the Vision (written to the strategy draft) and/or the Description; never "
     "an admin or a bot, the Strategy Bot included. Each change is kept with who and when", "CompanyResult"),
    ("/api/v2/company/history", "get", "Goals", "getCompanyHistory",
     "Every change to the Vision and the Description, newest first, with who made it and when", "CompanyHistory"),
    ("/api/v2/goal-manager/turn-on", "post", "Goals", "turnOnGoalManager",
     "Owner only: add the Strategy Bot from the catalog or restore it, and activate it", None),
    ("/api/v2/goals", "get", "Goals", "listGoals",
     "The caller's own goals, the chain above them and their reports' goals; ?all=true is every goal the caller may read "
     "(?status=red,gray filters); ?owner= names someone else", "GoalList"),
    ("/api/v2/goals", "post", "Goals", "createGoal",
     "Set a goal for yourself, under a goal you own, or for someone below you; the team owner sets team goals", "GoalResult"),
    ("/api/v2/goals/tree", "get", "Goals", "getGoalTree",
     "Every goal with its owner, its KPIs (each with its target and colour), the KPIs no goal uses and the pending proposals: "
     "what the Goals page draws", "GoalTree"),
    ("/api/v2/goals/needs-you", "get", "Goals", "getGoalsNeedsYou",
     "Red KPIs on goals the caller owns, stale KPIs they own, and definitions or targets they are asked to confirm; for a person "
     "each is a Strategy Bot task (`task`), the one Tasks and Decide show", "GoalsNeedsYou"),
    ("/api/v2/goals/refresh", "post", "Goals", "refreshGoalStatuses",
     "The Strategy Bot's status pass (or the owner's): work automatic colours out again. "
     "A colour a person set only gets a suggestion",
     "GoalRefreshView"),
    ("/api/v2/goals/read-back", "post", "Goals", "readBackGoalChanges",
     "The Strategy Bot only, in the owner's own chat: Tico writes the read-back of up to 20 "
     "changes to goals, measures and "
     "suggestions as its reply and stores the exact saves. The owner's yes to it (a plain yes, or a yes to some by number) "
     "within 30 minutes lets exactly those saves through (each step's method, path and body_json, sent as it is), cited "
     "with X-Tico-On-Behalf-Of; anything else is 403 not_agreed. "
     "A colour, done or dropped, archiving, a reading or the Vision is refused here: it stays a click", None),
    ("/api/v2/goals/read-back/{mid}", "get", "Goals", "getGoalReadBack",
     "One goal read-back: each change with its exact saves, whether it is saved and, in the turn the owner's reply "
     "started, whether that reply says yes to it and why not", None),
    ("/api/v2/goals/{gid}", "get", "Goals", "getGoal",
     "One goal with its KPIs, the goals under and above it, tasks, history, recent check-ins and pending proposals", "GoalResult"),
    ("/api/v2/goals/{gid}", "post", "Goals", "updateGoal",
     "Change a goal's title, body, parent, owner or place in its owner's order", "GoalResult"),
    ("/api/v2/goals/{gid}/status", "post", "Goals", "setGoalStatus",
     "Set the colour by hand with one sentence (red, yellow, green) or end the goal (done, dropped). It sticks, with the setter's "
     "name, until a person hands it back", "GoalResult"),
    ("/api/v2/goals/{gid}/reopen", "post", "Goals", "reopenGoal",
     "Bring a done or dropped goal back with a colour and one line why, or auto for the colour it had; whoever may set "
     "its colour", "GoalResult"),
    ("/api/v2/goals/{gid}/status/auto", "post", "Goals", "handBackGoalStatus",
     "\"Let Strategy Bot set it\": end a colour set by hand; the automatic colour is worked out at once", "GoalResult"),
    ("/api/v2/goals/{gid}/why", "post", "Goals", "setGoalWhy",
     "A person who may edit the goal writes its Why (one line tying it to the vision) or clears it; a bot proposes one "
     "instead (kind goal_why)", "GoalResult"),
    ("/api/v2/goals/{gid}/checkins", "get", "Goals", "listGoalCheckins", "A goal's check-ins, newest first", "GoalCheckins"),
    ("/api/v2/goals/{gid}/checkins", "post", "Goals", "addGoalCheckin",
     "The owner's own words on how it is going, with an optional signal; it colours a goal that has no KPI", "GoalCheckinResult"),
    ("/api/v2/goals/{gid}/kpis", "post", "Goals", "linkGoalKpi",
     "Link a goal to a KPI (kpi_id) or make a new one and link it (name), with the target on the link; linking again replaces the target",
     "GoalKpiResult"),
    ("/api/v2/goals/{gid}/kpis/{kid}", "post", "Goals", "setGoalKpiTarget",
     "Change the target on a goal's link to a KPI: an improvement (baseline, target, deadline), a range (min, max) or none", "GoalKpiResult"),
    ("/api/v2/goals/{gid}/kpis/{kid}/unlink", "post", "Goals", "unlinkGoalKpi", "Take a KPI off a goal; the KPI and its readings stay", "GoalResult"),
    ("/api/v2/proposals", "get", "Goals", "listGoalProposals",
     "Proposals (pending by default; status=confirmed|rejected|all), each saying whether the caller may decide it", "GoalProposalList"),
    ("/api/v2/proposals", "post", "Goals", "createGoalProposal",
     "Propose a change the caller may not make: goal wording, a KPI for a goal, a definition, a target, or a flag; "
     "the Strategy Bot at level 2 or 3, a recovery for a red or stalled goal (evidence, "
     "options of bot-task changes, its pick)",
     "GoalProposalResult"),
    ("/api/v2/proposals/{pid}/decide", "post", "Goals", "decideGoalProposal",
     "A person's own decision, made by whoever owns the goal or KPI: confirm makes the change as them, reject drops it. "
     "An Add a KPI suggestion may be confirmed with a `target` for the link (as a goal's KPI target), used in place of its own. "
     "A recovery applies exactly the chosen option (`option`, else the Strategy Bot's pick) in "
     "the Strategy Bot's name; what "
     "can't be done comes back as a new recovery saying why", "GoalProposalResult"),
    ("/api/v2/proposals/{pid}/note", "post", "Goals", "addGoalProposalReason",
     "After a reject, the person who turned the suggestion down adds one line on why (at most 280 characters), once, "
     "while it has none; a pending or confirmed suggestion, anyone else, a second reason or an empty one is refused. "
     "The Strategy Bot reads it before suggesting again", "GoalProposalResult"),
    ("/api/v2/kpis", "get", "KPIs", "listKpis",
     "The KPIs the caller may see; archived KPIs are omitted unless include_archived=true; goal_id, owner, unlinked=true narrow it; auto_for=<bot> is that bot's five automatic KPIs", "KpiList"),
    ("/api/v2/kpis", "post", "KPIs", "createKpi",
     "Make a KPI (definition, unit, direction, cadence, owner, source note); with goal_id it is linked, the target fields on the link", "KpiDetail"),
    ("/api/v2/kpis/{kid}", "get", "KPIs", "getKpi",
     "One KPI: definition and its versions, the goals using it with each target and colour, every reading, check-ins, pending proposals", "KpiDetail"),
    ("/api/v2/kpis/{kid}", "post", "KPIs", "updateKpi",
     "Change a KPI; a change to what it measures is a new definition version", "KpiDetail"),
    ("/api/v2/kpis/{kid}/archive", "post", "KPIs", "archiveKpi",
     "Hide a KPI from active views; only its owner or someone above them; definitions, links, readings and audit history stay", "KpiDetail"),
    ("/api/v2/kpis/{kid}/restore", "post", "KPIs", "restoreKpi",
     "Restore an archived KPI to active views; only its owner or someone above them; history stays", "KpiDetail"),
    ("/api/v2/kpis/{kid}/readings", "get", "KPIs", "listKpiReadings",
     "Every reading oldest first, each with the reading that superseded it; effective=true leaves out the corrected ones", "KpiReadings"),
    ("/api/v2/kpis/{kid}/readings", "post", "KPIs", "addKpiReading",
     "Log a reading: a value with its period, evidence and quality. Never edited; supersedes names the reading it corrects", "KpiReadingResult"),
    ("/api/v2/bots/{bot}/kpis", "get", "KPIs", "listBotKpis",
     "A bot's five automatic KPIs, computed from Tico's own data (Read on the bot)", "BotKpis"),
    ("/api/v2/usage", "get", "Usage", "getUsage",
     "Estimated spend over from..to (UTC dates, default the last 7 days), grouped by bot (default), day, routine, harness, model, effort or subscription, "
     "filterable by those run dimensions and department (__unknown__ selects missing values). The owner and bot administrators see every bot; anyone else sees the bots they run "
     "or own. ?bot=<slug> is that bot's daily series and top routines instead", "Usage"),
    ("/api/v2/usage/limits", "get", "Usage", "getUsageLimits",
     "The team default limit and each bot's hourly, daily, weekly and monthly limits with this period's spend, for the bots "
     "the caller may see usage for; for the owner and bot administrators also the team's and each group's combined limits (a "
     "lead who is not an administrator gets the groups it leads and those below)", "UsageLimits"),
    ("/api/v2/usage/limits", "put", "Usage", "setUsageDefault",
     "The default limit for bots with none of their own, in estimated USD (empty is no limit), and whether subscription runs "
     "count toward every limit (owner and bot administrators)", "UsageDefaultView"),
    ("/api/v2/usage/limits/{bot}", "put", "Usage", "setBotUsageLimit",
     "A bot's own 30-minute, daily, weekly and monthly limit in estimated USD; empty "
     "follows the team default. Owner and administrators, the person who runs the bot within the team default, and a group "
     "lead for the bots in the group and those below. A bot over a limit takes no new job until the period turns over or the "
     "limit is raised", "BotUsageLimit"),
    ("/api/v2/usage/pools/company", "put", "Usage", "setTeamUsageLimit",
     "A limit on every bot's estimated spend together, per hour, day, week and month; null removes a field, one left out stays. Over it, no bot "
     "takes a new job (owner and bot administrators)", "UsagePoolLimit"),
    ("/api/v2/usage/pools/groups/{group}", "put", "Usage", "setGroupUsageLimit",
     "A limit on the estimated spend of a group's bots together, nested groups included; null removes a field, one left out stays. Over it, none "
     "of them takes a new job (owner and bot administrators; also the lead of the group or one above "
     "it, within the parent group's limit)", "UsagePoolLimit"),
    ("/healthz", "get", "Health", "getLiveness", "Is the server up (no sign-in)", None),
    ("/api/v2/health", "get", "Health", "getHealth", "Checks, computers and failures (bots see counts, computers and failed runs)", "Health"),
    ("/api/v2/health-monitor", "get", "Health", "getHealthMonitor",
     "Owners, admins and the Health Monitor: whether it is on, whether it files reports without asking, and what the "
     "last hourly scan found", None),
    ("/api/v2/health-monitor/turn-on", "post", "Health", "turnOnHealthMonitor",
     "Owner only: add the Health Monitor from the catalog (or wake a paused one) and activate it", None),
    ("/api/v2/health-monitor/turn-off", "post", "Health", "turnOffHealthMonitor", "Owner only: pause the Health Monitor", None),
    ("/api/v2/health-monitor/findings/{key}/report", "post", "Health", "reportHealthFinding",
     "The Health Monitor or the owner: report a finding (key or signature) as a Tico bug: one ticket per signature with "
     "the redacted diagnostics, a message on it when it comes back, or a support approval when the team rule is off", None),
    ("/api/v2/health-monitor/local-findings", "post", "Health", "recordLocalHealthFinding",
     "The Health Monitor: record a process or container its watcher saw busy, as a finding it can report", None),
    ("/api/v2/task-manager", "get", "Health", "getTaskManager",
     "People and bots: whether the Task Manager is on, its hourly report's switch, schedule and last run, and each "
     "department's last check and open check task", None),
    ("/api/v2/task-manager/turn-on", "post", "Health", "turnOnTaskManager",
     "Owner only: add the Task Manager from the catalog (or wake a paused one) and activate it", None),
    ("/api/v2/task-manager/turn-off", "post", "Health", "turnOffTaskManager", "Owner only: pause the Task Manager", None),
    ("/api/v2/system-routines", "get", "Health", "listSystemRoutines",
     "Every job Tico runs on its own: schedule, last and next run, on or off, and what the viewer may change", None),
    ("/api/v2/system-routines/{key}", "put", "Health", "saveSystemRoutine",
     "The owner or an admin: switch a system routine on or off, pick its schedule preset (15m, hourly, 4h, daily, "
     "business) or its daily time", "SystemRoutineUpdate"),
    ("/api/v2/system-routines/{key}/run", "post", "Health", "runSystemRoutine",
     "The owner or an admin: run one controlled system routine once, now; 409 while it is off or already running", None),
    ("/api/v2/threads/{thread_id}/catch-up", "post", "Conversations", "catchUpThread",
     "What is new in a thread (scope subtree: its sub-threads too) that you may read, summarized and posted to your "
     "Assistant DM; the newest 40 messages at most. Nothing new posts nothing", "CatchUpResult"),
]


def _t(kind):
    return {"s": {"type": "string"}, "i": {"type": "integer"}, "b": {"type": "boolean"}, "o": {"type": "object"},
            "n": {"type": ["string", "null"]}, "a": {"type": "array"}, "f": {"type": "number"}}[kind]


def obj(fields, required=None, **arrays):
    """An object schema from {"name": "s|i|b|o|n|a|f"} or {"name": {schema}}. Other fields are allowed."""
    props = {k: (_t(v) if isinstance(v, str) else v) for k, v in fields.items()}
    props.update(arrays)
    return {"type": "object", "properties": props, "required": sorted(required if required is not None else props),
            "additionalProperties": True}


NUM_N = {"type": ["number", "null"]}
PERIODS = ("per_30m", "hourly", "daily", "weekly", "monthly")   # the spend limit periods (backend/usage_limits.py); the 30-minute one
GRANOLA_SKIP_REASONS = {"type": "object", "additionalProperties": {"type": "integer"},
                        "description": "Skipped items in the last attempt, by fixed code and step"}
GRANOLA_ERROR_DETAIL = {"type": ["object", "null"], "additionalProperties": False,
                        "description": "Fixed facts about the last failure or skip; never provider text",
                        "properties": {"step": {"type": "string"}, "http_status": {"type": ["integer", "null"]},
                                       "rpc_code": {"type": ["integer", "null"]}, "tool_error": {"type": "boolean"},
                                       "retry_after": {"type": ["integer", "null"]},
                                       "signal": {"type": ["string", "null"],
                                                  "enum": ["rate limit", "slow down", "too many requests", None]},
                                       "batch": {"type": ["integer", "null"]}}}


def items(schema):
    return {"type": "array", "items": schema}


ref = lambda name: {"$ref": "#/components/schemas/" + name}   # noqa: E731
ACTORS = {"type": "object", "additionalProperties": {"type": "string"},
          "description": "On reads: display names for every actor id in the answer, {\"human:ana\": \"Ana Alvarez\"}"}

SCHEMAS = {
    "SubscriptionList": obj({"profiles_by_computer": items(obj({"runner_id": "s", "label": "s", "profiles": items(
        obj({"name": "s", "id": "s", "display_name": "s", "runtimes": "o"}))}))}),
    "SubscriptionIdentity": obj({"id": "s", "display_name": "s"}),
    "SubscriptionWeeklyResult": obj({"weekly": {"anyOf": [obj({"used_percent": {"type": ["number", "null"]}, "resets_at": "n", "reported_at": "s", "source": {"enum": ["manual"]}}), {"type": "null"}]}}),
    "SubscriptionRefreshResult": obj({"id": "s", "profile": "s", "runtime": "s",
        "state": {"enum": ["requested", "succeeded", "unavailable", "failed", "expired"]},
        "requested_at": "s", "updated_at": "s", "expires_at": "s"}),
    "BotSubscription": obj({"profile": "n", "id": "s", "display_name": "s", "source": {"enum": ["computer"]}, "computer": {"anyOf": [obj({"runner_id": "s", "label": "s"}), {"type": "null"}]},
                            "runtime": "s", "harness": "s", "signed_in": {"type": ["boolean", "null"]}, "problem": "s", "warning": "s"},
                           required=["profile", "id", "display_name", "source", "computer", "runtime", "harness", "signed_in", "problem"]),
    "ChatGoal": obj({"id": "s", "conversation_id": "s", "bot": "s", "objective": "s", "status": "s",
                     "note": "s", "set_by": "s", "set_at": "s", "updated_at": "s", "ended_at": "n"},
                    required=["id", "conversation_id", "bot", "objective", "status", "note", "set_by", "set_at", "updated_at", "ended_at"]),
    "CatchUpResult": obj({"thread_id": "s", "scope": "s", "messages": "i", "left_out": "i", "posted": "o",
                          "words": "s"}, required=["thread_id", "scope", "messages", "left_out"]),
    "ChatGoalResult": obj({"goal": {"anyOf": [ref("ChatGoal"), {"type": "null"}]}}, required=["goal"],
                          supported={"type": "boolean"}, commands=items({"type": "object"})),
    "Message": obj({"id": "s", "conversation_id": "s", "from_actor": "s", "to_actor": "s", "kind": "s", "body": "s",
                    "created": "s", "in_reply_to": "n", "refs": "o"}, required=["id", "conversation_id", "from_actor", "to_actor", "kind", "body", "created", "in_reply_to", "refs"],
                   from_name={"type": "string", "description": "Display name of from_actor, when it is a person or a bot"},
                   to_name={"type": "string", "description": "Display name of to_actor"},
                   body_raw={"type": "string", "description": "A notice Tico wrote, as stored (with actor ids); `body` shows names to people"},
                   edited_at={"type": ["string", "null"], "description": "When the author last changed a task comment's text; "
                              "null when never edited"},
                   deleted_at={"type": ["string", "null"], "description": "When the author deleted a task comment. A delete or "
                               "retry acknowledgment can include a tombstone with an empty body; deleted comments are never listed"},
                   run={"type": "object", "description": "The run that handled this message, once one has: on a person's message "
                        "`{job_id, attempt_id, state}` where `state` is `started_run` (it started the run) or `added_to_run` "
                        "(it was folded into a run already working); on a bot's reply `{job_id, attempt_id}` (the run that "
                        "wrote it, plus a summary of what it did). Absent until a run takes the message",
                        "properties": {"job_id": {"type": "string"}, "attempt_id": {"type": "string"},
                                       "state": {"enum": ["started_run", "added_to_run"]}}},
                   ask=ref("TaskReviewAskView"),
                   answer=ref("ReviewAnswer"),
                   answers=items({"oneOf": [{"type": "string"}, ref("ReviewAnswer")]}) | {
                       "description": "On a structured ask: all review answers, oldest first. On a bot reply: handled message ids"},
                   woke={"type": "boolean", "description": "On a send: false when the message was held (see `held`)"},
                   held={"type": "string", "description": "On a send: a bot's message to a bot held for that bot's next "
                         "run, and how to wake it now (backend/bot_to_bot.py)"},
                   sent_as=ref("SentAs") | {"description": "On a send to a bot: the way it went (steer, queue or defer) "
                                            "and a defer's deadline (backend/delivery.py)"}),
    "Execution": obj({"job_id": "s", "message_id": "s", "bot": "s", "attempt_id": "n", "state": "s", "label": "s", "text": "s"},
                     required=["job_id", "message_id", "bot", "attempt_id", "state", "label", "text", "parts"],
                     parts=items(obj({"kind": {"enum": ["progress", "reply", "tool"]}, "text": "s", "at": "s"})) | {
                         "description": "The run's pieces so far, in order, while it is leased or running (empty otherwise). "
                                        "`reply` and `progress` are what the bot wrote (`progress` when a tool call follows it); "
                                        "`tool` is a short label such as \"Ran hub task create\", never its arguments or output"}),
    "Conversation": obj({"id": "s", "kind": "s", "subject": "s", "participants": items({"type": "string"}),
                         "created": "s", "last_message_at": "s", "closed_at": "n"}),
    "TaskStep": obj({"id": "s", "type_id": "s", "name": "s", "public_name": "s", "position": "i", "status": "s",
                     "check_pool": "a", "check_limit": "i",
                     "flow": {"type": "object", "description": "What the step does with a ticket (docs/tasks.md, Step "
                              "flow). On a check step, other_company is prefer (the default) or require."}},
                    required=["id", "type_id", "name", "position", "status"]),
    "TaskType": obj({"id": "s", "name": "s", "numbered": "b", "steps_only": "b", "created": "s", "updated": "s", "steps": items(ref("TaskStep"))},
                    bots={"type": ["string", "null"], "enum": ["read", "work", None],
                          "description": "What every bot may do with the type's tasks beyond its own: read "
                                         "(read, comment, file subtasks) or work (also change them); null keeps "
                                         "each task to the bots on it"}),
    "TaskTypeList": obj({"types": items(ref("TaskType"))}),
    "TaskTypeResult": obj({"type": ref("TaskType")}),
    "Task": obj({"private": "b", "id": "s", "title": "s", "body": "s", "requester": "s", "owner": "s", "status": "s", "created": "s",
                 "updated": "s", "due": "n", "version": "i", "lane": "s", "labels": items({"type": "string"}),
                 "acceptance_criteria": items({"type": "string"}),
                 "occurrence": {"type": ["string", "null"], "description": "A due-date routine's occurrence, \"<automation_id>:<due_on>\", on the task it opened: the due is its deadline, not a wake date."}},
                required=["id", "title", "requester", "owner", "status", "created", "updated", "due", "version", "lane", "labels"],
                owner_name={"type": "string", "description": "Display name of owner (`owner` stays the actor id)"},
                requester_name={"type": "string", "description": "Display name of requester"},
                cover={"oneOf": [obj({"url": "s", "width": {"type": ["integer", "null"]},
                                      "height": {"type": ["integer", "null"]}}), {"type": "null"}]},
                open_asks={"type": "integer", "description": "Questions on this task with no answer or dismissal"},
                type_id={"type": ["string", "null"]}, step_id={"type": ["string", "null"]},
                step_since={"type": "string", "description": "A Dev ticket at a working step (In Progress, a Fixes "
                            "step): when it entered that step or came back to work; absent otherwise"},
                relations={"type": "object", "description": "Related tasks the reader may open, by kind (parent, "
                           "blocks, related, duplicate_of, follow_up): each {id, title, status, owner, direction}; "
                           "direction is out when this task is the subtask, blocker, duplicate or follow-up, in for "
                           "the other end, both for related. Subtasks are in the task's children.",
                           "additionalProperties": items(obj({"id": "s", "title": "s", "status": "s", "owner": "s",
                                                              "direction": "s"}))},
                type={"oneOf": [obj({"id": "s", "name": "s"}), {"type": "null"}]},
                step={"oneOf": [ref("TaskStep"), {"type": "null"}]},
                body={"type": "string", "description": "Left out of a list asked for with brief=true, "
                      "as is acceptance_criteria"},
                number={"type": ["integer", "null"], "description": "The task's number, unique across the team "
                        "(#18945); given once on a numbered type and never changed"},
                step_rank={"type": ["number", "null"], "description": "Its place within its step, lower first"},
                waiting_on={"type": ["string", "null"], "description": "The person a waiting task waits on; "
                            "the task is in their Needs you"},
                guest={"type": ["string", "null"], "description": "The one person a private task with no person on "
                       "it reached: its owning bot's first subtask, question or wait for them made them its guest. "
                       "They read the task as its requester and owner do, and nothing else of its tree"},
                snoozed_until={"type": ["string", "null"], "description": "Set aside until this moment (UTC): "
                               "off lists and Needs you, and quiet, until then"},
                snoozed_by={"type": ["string", "null"], "description": "Who snoozed it"},
                snoozed_note={"type": ["string", "null"], "description": "What to check when the snooze ends (sent in the owner's wake)"},
                roles={"type": "object", "description": "Who is on the task, by role: each role a short name the team "
                       "chooses (developer, reviewer, qa ...) with its actors in the order they were added; a role nobody "
                       "holds is absent, and one actor may hold several roles. Tico gives roles no meaning or rights. Task "
                       "update and create take the same shape to replace a role's people ([] clears one)",
                       "additionalProperties": items({"type": "string"})}),
    "Person": obj({"id": "s", "name": "s", "email": "s", "title": "s", "team": "s", "reports_to": "n", "org_parent": "s"},
                  required=["id", "name", "org_parent"]),
    "Access": obj({"see": "b", "read": "b", "write": "b"},
                  required=["see", "read", "write"]),
    "OrgBot": obj({"id": "s", "display_name": "s", "description": "s", "team": "s", "reports_to": "n", "org_parent": "s",
                   "owners": items({"type": "string"}), "status": "s", "access": ref("Access"),
                   "onboarding_state": "s", "stage": {"enum": ["onboarding", "active"], "description": "Onboarding (learns only: no tasks, routines or sends) until a person above it marks onboarding complete, then active"}},
                  required=["id", "display_name", "org_parent", "status", "access"]),
    "Seat": obj({"id": "s", "kind": {"enum": ["seat"]}, "stage": {"enum": ["recruiting"]}, "title": "s",
                 "description": "s", "reports_to": "s", "team": "s", "created": "s", "org_parent": "s"},
                required=["id", "kind", "stage", "title", "description", "reports_to"]),
    "SeatList": obj({"seats": items(ref("Seat"))}, required=["seats"]),
    "SeatClosed": obj({"id": "s", "closed": "b"}, required=["id", "closed"]),
    "SeatDetail": obj({"id": "s", "kind": {"enum": ["seat"]}, "stage": {"enum": ["recruiting"]}, "title": "s",
                       "description": "s", "reports_to": "s", "team": "s", "created": "s",
                       "description_by": {"enum": ["chart", "person"]},
                       "can_edit": "b"},
                      required=["id", "kind", "stage", "title", "description", "reports_to", "can_edit"]),
    "SeatDrafted": obj({"description": "s", "source": {"enum": ["catalog", "model", "none"]}},
                       required=["description", "source"]),
    "SeatNeed": obj({"tool": "s", "label": "s", "required": "b",
                     "state": {"enum": ["connected", "on_hire", "not_set"]}},
                    required=["tool", "label", "required", "state"]),
    "SeatCandidate": obj({"template": "s", "name": "s", "icon": "s", "department": "s", "department_id": "s",
                          "line": "s", "summary": "s", "tags": items({"type": "string"}),
                          "owns": items({"type": "string"}), "never": items({"type": "string"}),
                          "onboarding": items({"type": "string"}), "prerequisites": items(ref("SeatNeed")),
                          "match": {"enum": ["strong", "good", ""]}, "source": {"enum": ["template"]},
                          "starter": "b", "model": "s", "model_name": "s", "effort": "s"},
                         required=["template", "name", "icon", "department", "line", "tags", "owns", "prerequisites",
                                   "match", "source", "model", "model_name"]),
    "SeatCandidates": obj({"seat": ref("SeatDetail"), "candidates": items(ref("SeatCandidate")),
                           "models": items(obj({"id": "s", "name": "s", "provider": "s"}, required=["id", "name"]))},
                          required=["seat", "candidates", "models"]),
    "SeatHired": obj({"slug": "s", "name": "s", "seat": "s", "stage": {"enum": ["onboarding"]}, "reports_to": "s", "template": "s",
                      "model": "s", "setup_task_id": "n", "note": "n"},
                     required=["slug", "seat", "stage", "reports_to", "template"]),
    "TaskFlow": obj({"going": "i", "waiting": "i"}, required=["going", "waiting"]),
    "Bot": obj({"slug": "s", "display_name": "s", "state": "s", "online": "b", "queued": "i", "description": "s",
                "reports_to": "n", "team": "n", "operator": "n", "owners": "a", "status": {"type": ["object", "null"]},
                "keep_tasks_going": {"type": ["integer", "null"]}, "turns_at_once": {"type": ["integer", "null"]},
                "task_flow": ref("TaskFlow"),
                "access": ref("Access"), "onboarding_state": "s", "stage": {"enum": ["onboarding", "active"], "description": "Onboarding (learns only: no tasks, routines or sends) until a person above it marks onboarding complete, then active"}, "can_complete_onboarding": {"type": "boolean", "description": "While onboarding: whether the caller may mark its onboarding complete"}, "onboarding_ready": {"type": ["object", "null"], "description": "While onboarding: the bot's \"I'm ready\" ({at, summary}), or null"}},
               required=["slug", "display_name", "state", "owners", "access"]),
    "BotDetail": obj({"slug": "s", "display_name": "s", "description": "s", "state": "s", "online": "b", "queued": "i",
                      "status": {"type": ["object", "null"]}, "reports_to": "n", "reports_to_name": "s",
                      "operator": "n", "operator_name": "s", "team": "n", "owners": "a", "goals": "s",
                      "keep_tasks_going": {"type": ["integer", "null"]}, "turns_at_once": {"type": ["integer", "null"]},
                      "task_flow": ref("TaskFlow"),
                      "access": ref("Access"), "onboarding_state": "s", "template": "s", "template_version": "s",
                      "stage": {"enum": ["onboarding", "active"], "description": "Onboarding (learns only: no tasks, routines or sends) until a person above it marks onboarding complete, then active"}, "can_complete_onboarding": {"type": "boolean", "description": "While onboarding: whether the caller may mark its onboarding complete"}, "onboarding_ready": {"type": ["object", "null"], "description": "While onboarding: the bot's \"I'm ready\" ({at, summary}), or null"}},
                     required=["slug", "display_name", "state", "owners", "access"]),
    "Audience": obj({"everyone": "b", "people": items({"type": "string"}), "teams": items({"type": "string"}),
                     "bots": items({"type": "string"})}, required=["everyone", "people", "teams", "bots"]),
    "BotAccessView": obj({"bot": "s", "see": ref("Audience"), "read": ref("Audience"), "write": ref("Audience"),
                      "revision": "i", "you": ref("Access"), "teams": items({"type": "object"})},
                     required=["bot", "see", "read", "write", "revision"]),
    "TeammateAccessView": obj({"target": "s", "see": ref("Audience"), "read": ref("Audience"), "write": ref("Audience"),
                               "default": {"type": "object"}, "revision": "i",
                               "you": obj({"see": "b", "read": "b", "write": "b", "manage": "b"}),
                               "teams": items({"type": "object"})},
                              required=["target", "see", "read", "write", "revision"]),
    "Me": obj({"actor": "s", "role": "s", "email": "s"}),
    "Config": obj({"company_name": "s", "app_name": "s", "assistant_name": "s", "assistant_bot": "s",
                   "public_url": "s", "owner_email": "s", "version": "s",
                   "visual_origin": {"type": "string", "description": "Configured non-secret visual origin, or empty "
                                     "when absent or invalid; grants no native IPC permission."},
                   "visuals": {"type": "string", "enum": ["full", "hosted", "media"],
                               "description": "How visuals show, from the setup (no switch): full with a separate "
                                              "visual address; hosted without one while Tico file hosting takes new "
                                              "files (pages and SVG inline from the company's files address); media "
                                              "otherwise (pictures, video and audio inline, pages and SVG open in the "
                                              "file viewer)."},
                   "visual_files": {"type": "string", "description": "Signed-in callers: the Tico file hosting origin "
                                    "pages and SVG show from when there is no visual address, else empty; grants no "
                                    "native IPC permission."},
                   "s3_view_urls": {"type": "object", "additionalProperties": {"type": "string"},
                                    "description": "Signed-in callers: bucket -> https base an s3://bucket/key opens at "
                                                   "(base + '/' + key, each segment URL-encoded; TICO_S3_VIEW_URLS)"}},
                  required=["company_name", "app_name", "assistant_name", "assistant_bot", "public_url", "owner_email",
                            "version"]),
    "Group": obj({"id": "s", "name": "s", "parent": "s", "people": items({"type": "string"}),
                  "bots": items({"type": "string"}), "order": "i"}),
    "GroupList": items(ref("Group")),
    "ReportOrderView": obj({"manager": "s", "kind": "s", "name": "s", "can_order": "b",
                            "reports": items(obj({"id": "s", "kind": "s", "name": "s", "saved": "b"},
                                                 required=["id", "kind", "name", "saved"]))},
                           required=["manager", "kind", "name", "can_order", "reports"]),
    "Org": obj({"people": items(ref("Person")), "bots": items(ref("OrgBot")), "seats": items(ref("Seat")),
                "org_groups": "a", "teams": "o"}),
    "OrgModelSeat": obj({"team": "s", "template": "s", "title": "s"}, required=["team", "template"]),
    "OrgModel": obj({"model": "s", "name": "s", "about": "s", "seats": items(ref("OrgModelSeat"))},
                     required=["model", "name", "about", "seats"]),
    "OrgModels": obj({"models": items(ref("OrgModel"))}, required=["models"]),
    "SeatBatch": obj({"seats": items(ref("Seat"))}, required=["seats"]),
    "BotList": items(ref("Bot")),
    "BotInstructions": obj({"bot": "s", "content": "s", "published": "b", "updated": "n", "source": "s"}),
    "BotOnboarded": obj({"bot": "s", "onboarding_state": "s", "stage": "s", "changed": "b", "ready": {
                            "type": ["object", "null"], "description": "The bot's own call: its \"I'm ready\" "
                            "({at, summary})"}, "routine": {
                            "type": ["string", "null"], "description": "The template's first routine when this call "
                            "armed it (on, unless a person turned it off); null when setup had armed it already"}}, required=["bot", "onboarding_state", "changed"]),
    "BotTool": obj({"id": "s", "service": "s", "name": "s", "logo_key": "n", "identity": "s", "can": items({"type": "string"}),
                    "scope": {"type": "object", "description": "Service-specific fields, a string or a list of strings each: "
                              "database, channels, project, mailbox, sites, repo, effort ..."},
                    "note": "s", "status": {"enum": ["ready", "problem", "unknown", "pending"]}},
                   required=["id", "service", "name", "logo_key", "identity", "can", "scope", "note", "status"],
                   env={"type": "string", "description": "The environment variable's name, never its value"},
                   detail={"type": "string", "description": "One sentence on what the tool is or how it was checked"},
                   problem={"type": "string", "description": "Why status is `problem`, in words a person can act on"},
                   url={"type": "string", "description": "Where the tool opens, when it has an address (the repository)"},
                   pending={"enum": ["add", "remove", "update"], "description": "A request BotOps has not finished: `add` on a tool that is "
                            "not on the bot yet (status `pending`), `remove` on a declared one being taken out, `update` on "
                            "one whose can, scope or note is being changed"},
                   task_id={"type": "string", "description": "The BotOps task carrying the request"},
                   kind={"enum": ["mcp"], "description": "`mcp` when the tool is a remote MCP server (see `mcp`)"},
                   mcp={"type": "object", "description": "A remote MCP server: `host` (its URL's host), `transport` (http or sse) and "
                        "`status` from the runner's cheap check (reachable, auth_failed, unreachable, unchecked). Never the headers"}),
    "BotToolRegistered": obj({"tool": ref("BotTool"), "task_id": "s", "yaml": "s", "credentials": "s"}),
    "BotToolUpdated": obj({"task_id": "s", "yaml": "s", "credentials": "s"}, required=["task_id"], update={"type": "boolean"},
                          tool={"type": "string"}),
    "BotToolRemoval": obj({"task_id": "s"}, required=[], cancelled={"type": "boolean"}, removal={"type": "boolean"},
                          tool={"type": "string"}, detail={"type": "string"}),
    "BotTools": obj({"bot": "s", "tools": items(ref("BotTool")), "computer": "n", "online": "b", "reported_at": "n"},
                    required=["bot", "tools", "computer", "online", "reported_at"]),
    "Recent": obj({"actor": "s", "since": "s", "bots": "a"}),
    "ConversationList": obj({"actor": "s", "conversations": items(ref("Conversation"))},
                            required=["actor", "conversations"], actors=ACTORS),
    "MessagePage": obj({"conversation": ref("Conversation"), "messages": items(ref("Message")), "has_more": "b",
                        "next_before": "n"},
                       required=["conversation", "messages", "has_more", "next_before"], actors=ACTORS),
    "Snapshot": obj({"messages": items(ref("Message")), "has_more": "b", "next_before": "n",
                     "execution": {"oneOf": [ref("Execution"), {"type": "null"}], "description": "The latest run: state, label, "
                                   "text (the reply so far; separate messages are joined by a blank line), parts, bot"}}),
    "MessageResult": obj({"message": ref("Message")}),
    "ChatResult": obj({"conversation": ref("Conversation"), "message": ref("Message")}),
    "TaskList": obj({"tasks": items(ref("Task")), "next_offset": {"type": ["integer", "null"]},
                     "cursor": {"type": "string", "description": "Opaque: pass as changed_after to read only what changed since"},
                     "gone": {"type": "array", "items": {"type": "string"}, "description": "With changed_after: ids that "
                              "changed and no longer match (deleted, no longer readable, or out of the filters)"},
                     "reset": {"type": "boolean", "description": "With changed_after: the cursor is too old or no longer "
                               "applies; read in full"}},
                    required=["tasks", "next_offset", "cursor"], actors=ACTORS),
    "TaskResult": obj({"task": ref("Task")}),
    # A task's own model (backend/task_run_models.py `of`); null when its runs use the bot's task model.
    "TaskRunChoice": obj({"model": "s", "harness": "s", "runtime": "s", "reasoning_effort": "s", "set_by": "n",
                          "set_at": "n"}),
    "TaskRunModelResult": obj({"task": "s", "run_model": {"oneOf": [ref("TaskRunChoice"), {"type": "null"}]},
                               "readiness": {"type": ["object", "null"]}, "warning": "s"}),
    "TaskBulkResult": obj({"dry_run": "b", "matched": "i", "cap": "i", "changed": "i", "unchanged": "i", "skipped": "i",
                           "tasks": obj({"changed": items(obj({"id": "s", "number": {"type": ["integer", "null"]}, "title": "s"})),
                                         "skipped": items(obj({"id": "s", "number": {"type": ["integer", "null"]}, "title": "s",
                                                               "code": "s", "reason": "s"}))},
                                        )}),
    "RanOn": obj({"segments": items(obj({"model": "n", "provider": "n", "runtime": "n", "harness": "n", "effort": "n",
                                         "family": "n"})),
                  "task_model": {"type": ["object", "null"], "description": "For a turn in a task's own session on a bot "
                                 "with a task model: `source` is task (it ran on the task model), fallback (the task "
                                 "model could not run, so the bot's own model did) or check (a check subtask kept its "
                                 "check model), and `note` says it in plain words"},
                  "fallback": {"type": ["string", "null"], "description": "The fallback harness that ran the turn, if any"},
                  "family": {"type": ["string", "null"], "description": "The maker of the model that did the work, from "
                             "the model catalog, when every counted segment agrees; null is unknown (mixed, Cursor "
                             "auto, OpenRouter, an unlisted model, nothing counted). Never the bot's configured model."}}),
    "TaskRun": obj({"run": "s", "bot": "s", "started": "n", "finished": "n", "outcome": "n", "ran_on": ref("RanOn")}),
    # How a reader's comment reaches the task's bot (backend/delivery.py): the default way and a default defer's
    # deadline, whether they may steer or queue (wake it), whether its harness takes a steer, and whether they may set
    # the goal.
    "CommentDelivery": obj({"default": {"type": ["string", "null"], "enum": ["queue", "defer", None]},
                            "default_for": "n", "steer": "b", "queue": "b", "steers": "b", "goal": "b"},
                           required=["default", "default_for", "steer", "queue", "steers", "goal"]),
    # The way a send went (backend/delivery.py): steer, queue or defer, and a defer's deadline (null: none).
    "SentAs": obj({"delivery": {"type": "string", "enum": ["steer", "queue", "defer"]}, "until": "n"},
                  required=["delivery", "until"]),
    "TaskDetail": obj({"task": ref("Task"), "events": "a", "children": "a", "comments": items(ref("Message")),
                       "messages": items(ref("Message")), "has_more": "b", "can_comment": "b", "can_set_run_model": "b",
                       "mover": "b", "runs": items(ref("TaskRun")),
                       "run_model": {"oneOf": [ref("TaskRunChoice"), {"type": "null"}]},
                       "comment_delivery": {"oneOf": [ref("CommentDelivery"), {"type": "null"}]}},
                       required=["task", "events", "children", "comments", "messages", "has_more"], actors=ACTORS),
    "TaskReviewAskView": obj({"questions": "a", "who": "n", "by": "s"}),
    "ReviewAnswer": {"oneOf": [
        obj({"target": "o", "answers": "o", "other": "n", "dismiss": "b", "by": "s", "at": "s"}),
        obj({"by": "s", "text": "s", "at": "s"})]},
    "VersionReview": obj({"note": "n", "comment_id": "n", "ask": {"oneOf": [ref("TaskReviewAskView"), {"type": "null"}]},
                          "answers": items(ref("ReviewAnswer"))}),
    "TaskFileVersion": obj({"n": "i", "size": "i", "mime": "s", "sha256": "s", "created": "s", "by": "s",
                            "comment_id": "n", "note": "n", "ask": {"oneOf": [ref("TaskReviewAskView"), {"type": "null"}]},
                            "answers": items(ref("ReviewAnswer")), "url": "s", "poster_url": "n", "thumb_url": "n",
                            **{k: {"type": ["integer", "null"]} for k in ("width", "height", "duration_ms")},
                            "media_state": "n"}),
    "TaskFileView": obj({"id": "s", "name": "s", "mime": "s", "current_version": "i", "archived": "b",
                     "versions": items(ref("TaskFileVersion"))}),
    "TaskFileList": obj({"files": items(ref("TaskFileView"))}),
    "TaskFileResult": obj({"file_id": "s", "version": "i", "file": "o", "link": "s"}),
    "TaskComments": obj({"comments": items(ref("Message"))}),
    "TaskAnswers": obj({"answers": items(ref("ReviewAnswer"))}),
    "TaskAnswerResult": obj({"comment": ref("Message"), "answer": ref("ReviewAnswer"),
                             "comments": items(ref("Message")), "woke": "b", "delivered": "b"}),
    # `warning`: the bot it went to is paused or quarantined; the comment is kept and runs when it is back.
    # `held`: a bot's comment to a bot waits for that bot's next run; it says how to wake it now.
    "CommentResult": obj({"comment": ref("Message"), "comments": items(ref("Message")), "woke": "b", "delivered": "b",
                          "warning": "s", "held": "s", "goal": "o", "sent_as": ref("SentAs")},
                         required=["comment", "comments", "woke"]),
    "InboundTaskResult": obj({"task": {"oneOf": [obj({"id": "s"}), {"type": "null"}]}, "created": "b", "changed": "b"}),
    "UpdateList": obj({"updates": "a", "unread": "i", "next_before": "n"}, required=["updates", "unread"]),
    "UpdateArchiveResult": obj({"updated": "i", "archived": "b", "unread": "i"},
                                required=["updated", "archived", "unread"]),
    "ChangelogEntry": obj({"id": "s", "title": "s", "bullets": items({"type": "string"}), "area": "s",
                           "kind": {"type": "string", "enum": ["product", "activity"]}, "shipped_at": "s",
                           "unread": "b", "version": "s", "source": "s", "url": "s", "task_id": "s"},
                          required=["id", "title", "bullets", "area", "kind", "shipped_at"]),
    "ChangelogList": obj({"entries": items(ref("ChangelogEntry")), "unread_count": "i", "current_version": "s",
                          "next_offset": {"type": ["integer", "null"]}, "drafts": "a", "can_review": "b", "can_add": "b"}),
    "ChangelogReadResult": obj({"unread_count": "i"}),
    "Unread": obj({"unread": "i"}, meetings_pending={"type": "integer", "description": "Pending meetings filed for the caller; never part of Needs you"}),
    "NeedsYou": {"oneOf": [obj({"actor": "s", "items": items({
        "type": "object", "required": ["id", "kind", "title"], "additionalProperties": True,
        "properties": {"id": {"type": "string"}, "kind": {"enum": ["task", "question", "declined", "approval"]},
                       "title": {"type": "string"}}})}, required=["actor", "items"],
        older={"type": "array", "items": {"type": "object", "additionalProperties": True},
               "description": "Tasks there only for a question older than three days, out of the queue and its count"},
        went_away={"type": "object", "additionalProperties": True,
                   "description": "Items that left the caller's queue this week on their own: expired, withdrawn, "
                                  "answered by their Assistant, and the total"},
        count={"type": "integer", "description": "Items in the whole list, whatever page this is"},
        next={"type": ["string", "null"], "description": "With limit or cursor: pass as cursor for the next page; "
                                                         "null on the last page"},
        bots={"type": "object", "additionalProperties": {"type": "integer"},
              "description": "With limit or cursor: how many items in the whole list each bot is behind"},
        restart={"type": "boolean", "description": "The cursor was from an older version: this page is the first"}),
        obj({"actor": "s", "count": "i"})]},
    "GranolaStatus": obj({"mode": {"type": "string", "enum": ["account", "api_key", "off"]},
                          "connected": "b", "email": "n", "plan_hint": {"type": ["string", "null"], "enum": ["free", "paid", None]},
                          "last_sync": "n", "last_error": "n", "imported_count": "i", "needs_signin": "b", "syncing": "b", "skipped": "i"},
                         skip_reasons=GRANOLA_SKIP_REASONS, last_error_detail=GRANOLA_ERROR_DETAIL),
    "GranolaSignIn": obj({"state": "s", "mode": "s", "connected": "b", "email": "n", "plan_hint": "n",
                          "last_sync": "n", "last_error": "n", "imported_count": "i", "needs_signin": "b", "syncing": "b", "skipped": "i"},
                         skip_reasons=GRANOLA_SKIP_REASONS, last_error_detail=GRANOLA_ERROR_DETAIL),
    "GranolaDevice": obj({"user_code": "s", "verification_uri": "s", "verification_uri_complete": "s",
                          "expires_in": "i", "interval": "i"}, required=["user_code", "verification_uri", "expires_in", "interval"]),
    "GranolaSync": obj({"state": {"type": "string", "enum": ["syncing", "recent", "off", "needs_signin"]}, "last_sync": "n"}),
    "GranolaDisconnect": obj({"ok": "b"}),
    "MeetingSearch": obj({"results": "a", "next_offset": {"type": ["integer", "null"]}, "mode": "s"}),
    "ReviewedMeeting": obj({"id": "s", "title": "s", "review_state": {"type": "string", "enum": ["pending", "live", "dismissed"]}}),
    "MeetingReviewList": obj({"meetings": items(ref("ReviewedMeeting")), "count": "i", "pending_count": "i"}),
    "MeetingReviewPreferences": obj({"auto_share": {"type": ["boolean", "null"]},
                                     "review_default": {"type": "string", "enum": ["review", "auto"]}, "effective_auto_share": "b"}),
    "InternalDocRow": obj({"id": "s", "path": "s", "title": "s", "updated": "s", "updated_by": "s", "locked": "b", "version": "i"},
                          updated_by_name={"type": "string", "description": "Display name of updated_by"}),
    "InternalDoc": obj({"id": "s", "path": "s", "title": "s", "body": "s", "locked": "b", "version": "i", "created_by": "s",
                        "created": "s", "updated_by": "s", "updated": "s", "archived": "b"},
                       created_by_name={"type": "string"}, updated_by_name={"type": "string", "description": "Display name of updated_by"}),
    "InternalDocList": obj({"docs": items(ref("InternalDocRow")), "next_cursor": "n"}),
    "InternalDocResult": obj({"doc": ref("InternalDoc")}),
    "InternalDocVersion": obj({"version": "i", "title": "s", "path": "s", "actor": "s", "created": "s", "note": "s"},
                              actor_name={"type": "string"}, size={"type": "integer"}, current={"type": "boolean"}),
    "InternalDocVersions": obj({"doc": "s", "versions": items(ref("InternalDocVersion"))}),
    "InternalDocVersionResult": obj({"version": obj({"version": "i", "title": "s", "path": "s", "body": "s", "actor": "s",
                                                      "created": "s", "note": "s"}, actor_name={"type": "string"})}),
    "LinkedDoc": obj({"id": "s", "title": "s", "url": "s", "kind": {"enum": ["website", "google_drive", "google_doc", "notion", "github", "other"]},
                      "host": "s", "description": "s", "added_by": "s", "created": "s", "updated": "s"},
                     added_by_name={"type": "string"}),
    "LinkedDocList": obj({"linked": items(ref("LinkedDoc"))}),
    "LinkedDocResult": obj({"linked": ref("LinkedDoc")}),
    "DocSource": obj({"id": "s", "title": "s", "url": "s", "kind": "s", "provider": "s", "description": "s",
                      "added_by": "s", "repo": "s", "reference": "s", "reference_override": "s", "path": "s",
                      "status": {"enum": ["link_only", "queued", "preparing", "updating", "ready", "empty", "failed", "access_needed"]},
                      "last_checked": "n", "doc_count": "i", "skipped": "i", "error_code": "s"}),
    "DocSourceList": obj({"sources": items(ref("DocSource")), "next_cursor": "n"}),
    "DocSourceResult": obj({"source": ref("DocSource")}),
    "SourceDocumentList": obj({"source": ref("DocSource"), "documents": items(obj({"id": "s", "title": "s", "path": "s", "commit": "s"})), "next_cursor": "n"}),
    "ExternalHit": obj({"type": {"enum": ["external"]}, "id": "s", "title": "s", "path": "s", "section": "s",
                        "excerpt": "s", "score": "f", "trust": {"enum": ["untrusted_external"]}, "source_id": "s",
                        "source_title": "s", "source_url": "s", "commit": "s", "last_checked": "n", "citation_url": "s"}, collection={"type": "string"}),
    "ExternalDocResult": obj({"doc": obj({"id": "s", "type": {"enum": ["external"]}, "title": "s", "path": "s",
                        "content": "s", "trust": {"enum": ["untrusted_external"]}, "source_id": "s", "source_title": "s",
                        "source_url": "s", "commit": "s", "last_checked": "n", "citation_url": "s", "reference_notice": "s"})}),
    "DocsSearchResults": obj({"results": items({"oneOf": [ref("ExternalHit"),
        obj({"type": {"enum": ["internal"]}, "id": "s", "path": "s", "title": "s", "excerpt": "s", "score": "f"}, required=["type", "id", "path", "title", "excerpt", "score"],
            section={"type": "string"}, anchor={"type": "string"}, collection={"type": "string"}),
        obj({"type": {"enum": ["linked"]}, "id": "s", "title": "s", "url": "s", "kind": "s", "description": "s", "score": "f"}),
        obj({"type": {"enum": ["manual"]}, "id": "s", "title": "s", "path": "s", "url": "s", "excerpt": "s", "score": "f"}, required=["type", "id", "title", "path", "url", "excerpt", "score"],
            section={"type": "string"}, anchor={"type": "string"}, collection={"type": "string"})]})}),
    "DocSearch": obj({"query": "s", "results": "a", "has_more": "b", "mode": "s"}),
    "Storage": obj({"mode": {"type": "string", "enum": ["local", "s3"]}, "bucket": "s", "region": "s",
                    "files": "i", "bytes": "i", "copy": obj({"done": "i", "total": "i", "failed": "i"})}),
    "TurnCounts": obj({"running": "i", "starting": "i", "waiting": "i", "queued": "i", "start_seconds": "i",
                       "waits": items(obj({"reason": "s", "count": "i", "oldest": "s"}))}),
    "Health": obj({"audience": "s", "checks": "a", "attention": "i", "checked": "s"},
                  computers=items(obj({"id": "s", "turn_counts": ref("TurnCounts"), "turn_summary": "s"}, required=["id"])),
                  stalled_runs=items(obj({"run": "s", "bot": "s", "stalled": "b", "stalled_at": "s",
                                          "last_event": {"type": ["string", "null"]},
                                          "last_event_at": {"type": ["string", "null"]}, "stall_minutes": "i"})),
                  required=["audience", "checks", "attention", "checked"],
                  storage={**ref("Storage"), "description": "Read-only file storage usage and copy progress; Team owners only"},
                  stuck={"type": "array", "items": {"type": "object"}, "description":
                         "Bots with queued work that is not starting, or left in setup for over a day (the caller may "
                         "read them): bot, name, queued, oldest, computer, reason (not_ready, computer_offline, no_computer, "
                         "slow, limit, updating, restarting, setup, or a reason the claim holds the work: draining, maintaining, "
                         "lane_held, uncertain, scoped, bot_limit, cooling_off, run_hold, budget, backoff, "
                         "readiness_unknown, "
                         "sign_in_rejected, not_isolated, "
                         "isolation_unknown, profile, config, check_model, unreadable), why, drain_progress (counts of running "
                         "and handed-back turns on update holds), since (setup only: when the "
                         "bot was added), and fix (label plus href/tab, login, move or finish; null unless the caller "
                         "administers bots, or for setup may manage the bot: finish is POST /api/v2/bots/{bot}/onboarded)"},
                  on_fallback={"type": "array", "items": {"type": "object"}, "description":
                               "Bots whose latest run in the last day ran on a fallback harness (the caller may read "
                               "them): bot, harness, since (when the unbroken run of fallback turns began)"},
                  limited={"type": "array", "items": {"type": "object"}, "description":
                           "Bots a usage limit holds now (the caller may read them): bot, since"}),
    "BotFile": obj({"id": "s", "bot": "s", "title": "s", "kind": "s", "mime": "s", "locator": "s", "scope": "s",
                    "version": "i", "state": "s", "synced": "b", "size": {"type": ["integer", "null"]},
                    "name": "n", "open": {"type": ["object", "null"], "description": "{type: tico|external, url}: "
                                          "a Tico route, or the provider's address; null when nothing can be opened"},
                    "provider": "s", "provider_label": "s", "note": "s", "source": "s", "task_id": "n", "task_title": "n",
                    "working": "b", "github_url": "n", "actor": "n", "action": "n", "first_activity_at": "s",
                    "last_activity_at": "s", "archived": "b"},
                   required=["id", "bot", "title", "kind", "locator", "scope", "version", "state", "synced", "open",
                             "working", "last_activity_at"]),
    "BotFileList": obj({"bot": "s", "files": items(ref("BotFile")), "total": "i", "next_cursor": "n", "has_more": "b",
                        "can_manage": "b"}, required=["bot", "files", "total", "next_cursor", "has_more", "can_manage"],
                       actors=ACTORS),
    "BotFileResult": obj({"file": "o", "created": "b"}, required=["file"]),
    "FileActivity": obj({"file": "s", "activity": "a"}, actors=ACTORS),
    "FileVersions": obj({"file": "s", "versions": "a"}),
    "Assistant": obj({"available": "b", "state": "s", "bot": "s", "name": "s", "can_turn_on": "b", "room_id": "n",
                      "messages": items(ref("Message")), "has_more": "b", "next_before": "n",
                      "execution": {"oneOf": [ref("Execution"), {"type": "null"}], "description": "The assistant's current run: state, label, "
                                    "text so far; null when it is not working (show a thinking state while it is)"},
                      "actions": {"type": "object", "description": "{action id: proposal} for every card in `messages` "
                                  "(a message whose refs.action names one is a Confirm / Cancel card)"},
                      "pending": "a",
                      "earlier": {"oneOf": [obj({"room_id": "s", "bot": "s", "name": "s"},
                                                required=["room_id", "bot", "name"]), {"type": "null"}],
                                  "description": "The owner's earlier chat with the Strategy Bot, read-only (GET "
                                  "/api/v2/conversations/{room_id}/messages); null for everyone else, or none"}},
                     required=["available", "state", "bot", "name", "can_turn_on", "room_id", "messages",
                               "has_more", "next_before", "execution", "actions", "pending", "earlier"],
                     actors=ACTORS),
    "Librarian": obj({"available": "b", "state": "s", "bot": "s", "name": "s", "can_turn_on": "b"},
                     required=["available", "state", "bot", "name", "can_turn_on"]),
    "DocsAsked": obj({"conversation_id": "s", "message_id": "s",
                      "results": {"type": "array", "items": {"type": "object"},
                                  "description": "Matching internal, linked and manual docs, as GET /api/v2/docs/search?collection=all"}},
                     required=["conversation_id", "message_id", "results"]),
    "DocsConversations": obj({"conversations": items(obj({"id": "s", "title": "s", "created": "s",
                                                         "last_message_at": "n", "closed_at": "n"})),
                              "next_offset": {"type": ["integer", "null"]}}),
    "DocsReopened": obj({"conversation_id": "s"}),
    "LibrarianResearchFiled": obj({"task_id": "s", "bot": "s", "kind": "s", "target": "s"}),
    "LibrarianMarketSetup": obj({"can_answer": "b", "answers": obj({"sell": "s", "buyers": "s", "competitors": "s",
                                                                    "watch": "s"}),
                                 "step": "i", "skipped": "b", "task": {"type": ["object", "null"]}}),
    "LibrarianSetup": obj({"available": "b",
                           "docs": obj({"show": "b", "empty": "b", "skipped": "b", "task": {"type": ["object", "null"]}}),
                           "market": ref("LibrarianMarketSetup")}),
    "AssistantSent": obj({"message": ref("Message"), "reply": {"type": ["object", "null"]}, "fast": "b", "intent": "n"},
                         required=["message", "fast"]),
    "AssistantActionResult": obj({"action": {"type": "object", "description": "id, summary, method, path, body, status "
                                            "(pending, running, done, failed, cancelled, expired), result"}, "message_id": "s",
                                  "shown_once": {"type": "object", "description": "Confirm only, on a card that makes a "
                                                 "one-time value (a new computer's code): {code, expires, operator}, "
                                                 "in this answer alone and never stored on the card"}},
                                 required=["action"]),
    # Goals and KPIs
    "Goal": obj({"id": "s", "title": "s", "owner": "s", "parent_id": "n", "body": "s", "status": {"type": ["string", "null"], "description":
                 "red, yellow, green, gray (no data), done, dropped; null while it has no colour yet"},
                 "status_note": "s", "status_by": "n", "status_at": "n",
                 "status_source": {"type": ["string", "null"], "description": "auto: worked out by the Strategy Bot; "
                                                                              "person: "
                                                                              "set by hand and kept until handed back"},
                 "suggest_status": {"type": ["string", "null"], "description": "On a colour set by hand: "
                                                                               "the colour the Strategy "
                                                                               "Bot would choose, when it differs"},
                 "suggest_note": "n", "suggest_at": "n", "rank": {"type": ["integer", "null"]}, "created": "s", "created_by": "s", "updated": "s"},
                required=["id", "title", "owner", "parent_id", "body", "status", "status_note", "status_by", "status_at", "status_source",
                          "rank", "created", "created_by", "updated"],
                owner_name={"type": "string"}, status_by_name={"type": "string"},
                why={"type": "string", "description": "One line tying it to the company's vision; empty: not tied to the vision"},
                why_by={"type": ["string", "null"]}, why_at={"type": ["string", "null"]}),
    "KpiLink": obj({"id": "s", "goal_id": "s", "kind": {"enum": ["none", "improve", "maintain"]}, "baseline": NUM_N, "baseline_at": "n",
                    "target": NUM_N, "deadline": {"type": ["string", "null"], "description": "YYYY-MM-DD"}, "min": NUM_N, "max": NUM_N},
                   required=["goal_id", "kind"]),
    "KpiReadingView": obj({"id": "s", "value": "f", "period_start": "s", "period_end": "s", "collected_at": "s",
                       "quality": {"enum": ["measured", "estimate", "partial"]}, "evidence": "s", "note": "s", "source": "s",
                       "actor": "s", "definition_version": "i", "supersedes": "n"},
                      required=["id", "value", "period_end", "quality", "definition_version"],
                      kpi_id={"type": "string"}, ts={"type": "string"}, created={"type": "string"},
                      superseded_by={"type": ["string", "null"], "description": "The reading that corrected this one, if any"},
                      actor_name={"type": "string"}),
    "Kpi": obj({"id": {"type": "string", "description": "A UUID, or auto:<bot>:<metric> for a bot's automatic KPI"}, "slug": "n",
                "name": "s", "definition": "s", "unit": "s", "direction": {"enum": ["up", "down", "range"]},
                "cadence": {"enum": ["daily", "weekly", "monthly"]}, "owner": "s", "source_note": "s", "definition_version": "i",
                "created": "n", "created_by": "n", "updated": "n",
                "archived_at": {"type": ["string", "null"]}, "archived_by": {"type": ["string", "null"]}, "auto": "b",
                "latest": {"oneOf": [ref("KpiReadingView"), {"type": "null"}]}, "readings": "i",
                "freshness": {"enum": ["fresh", "stale", "missing", "archived"], "description": "stale: one period missed; missing: never read or two missed. archived: a retained goal link, not counting. Never zero"},
                "spark": {"type": "array", "items": {"type": "number"}, "description": "The last values, oldest first"},
                "status": {"enum": ["green", "yellow", "red", "gray", "none", None], "description": "Against the target on the link (or a range); gray is stale or missing data; none is fresh with no target; null is an archived goal link with no colour"},
                "reason": "s"},
               required=["id", "name", "unit", "direction", "cadence", "owner", "definition_version", "auto", "latest", "readings",
                         "freshness", "spark", "status", "reason"],
               link=ref("KpiLink"), target_label={"type": "string", "description": "'→ 65% by Dec 31' or 'range 40–60'"},
               expected={"type": ["number", "null"], "description": "Where the straight line from baseline to target is now"},
               owner_name={"type": "string"}),
    "GoalCheckinView": obj({"id": "s", "goal_id": "s", "kpi_id": "n", "ts": "s", "author": "s", "source_actor": "s", "body": "s",
                        "signal": {"type": ["string", "null"], "enum": ["on_track", "at_risk", "off_track", None]}},
                       required=["id", "goal_id", "ts", "author", "source_actor", "body", "signal"],
                       author_name={"type": "string"}, source_actor_name={"type": "string"}),
    "GoalProposalView": obj({"id": "s", "kind": {"enum": ["goal_wording", "goal_kpi", "kpi_definition", "kpi_target", "flag", "goal_why"]},
                         "goal_id": "n", "kpi_id": "n", "payload": "o", "reason": "s", "proposed_by": "s", "proposed_at": "s",
                         "status": {"enum": ["pending", "confirmed", "rejected"]}, "decided_by": "n", "decided_at": "n",
                         "decision_note": "s", "result": {"type": ["object", "null"]}},
                        required=["id", "kind", "goal_id", "kpi_id", "payload", "reason", "proposed_by", "proposed_at", "status"],
                        goal_title={"type": ["string", "null"]}, kpi_name={"type": ["string", "null"]},
                        card={"type": "object", "description": "What its card says beside the payload: goal_title, kpi_name; "
                              "an Add a KPI suggestion's measure {name, definition, source_note, unit, direction, existing} "
                              "and target_label; a new target's now_label and target_label; a new definition's or wording's "
                              "changes [{label, now, proposed}]"},
                        may_decide={"type": "boolean", "description": "Whether the caller may confirm or reject it"},
                        may_reason={"type": "boolean", "description": "Whether the caller may still add the reason it "
                                    "was turned down: they turned it down, and it has none"}),
    "GoalView": obj({}, required=["kpis", "children", "chain", "tasks", "events", "checkins", "proposals"],
                    kpis=items(ref("Kpi")), children=items(ref("Goal")), chain=items(ref("Goal")), tasks=items({"type": "object"}),
                    events=items({"type": "object"}), checkins=items(ref("GoalCheckinView")), proposals=items(ref("GoalProposalView"))),
    "GoalResult": obj({"goal": {}}),
    "CompanyResult": obj({"company": obj({"vision": "s", "description": "s", "updated": "n", "updated_by": "n",
                                          "updated_by_name": "s"}),
                          "may_edit": {"type": "boolean", "description": "The owner on their own click, never an admin or a bot"}}),
    "CompanyHistory": obj({"events": items(obj({"id": "s", "ts": "s", "actor": "s", "actor_name": "s",
                                                "field": {"enum": ["vision", "description"]}, "old": "s", "new": "s"}))}),
    "GoalList": obj({"goals": items(ref("Goal"))}, required=["goals"], owner={"type": "string"},
                    chain=items(ref("Goal")), reports=items(ref("Goal")), company=items(ref("Goal"))),
    "GoalTreeRow": obj({}, required=["kpis", "open_tasks"], kpis=items(ref("Kpi")), open_tasks={"type": "integer"},
                       checkin={"oneOf": [ref("GoalCheckinView"), {"type": "null"}], "description": "The latest check-in"}),
    "GoalTree": obj({"goals": {},
                     "owners": {"type": "object", "description": "{actor: {kind: company|person|bot, id, name}} for every owner"},
                     "unaligned": {"type": "object", "description": "{actor: open tasks that serve no goal}"},
                     "other_kpis": items(ref("Kpi")), "proposals": items(ref("GoalProposalView"))},
                    required=["goals", "owners", "unaligned", "other_kpis", "proposals"]),
    "StrategyBotView": obj({"name": "s", "regular_checks": {"type": "boolean",
                       "description": "Whether the KPI pass and the automatic goal colours run"},
                       "level": {"type": "integer", "enum": [1, 2, 3]},
                       "level_chosen": {"type": "boolean",
                                        "description": "Whether the owner chose the level; if not, it is the default"},
                       "levels": items(obj({"level": "i", "name": "s", "sentence": "s", "ready": "b"},
                                           required=["level", "name", "sentence", "ready"])),
                       "level_changed_by": "s", "level_changed_by_name": "s", "level_changed_at": "s",
                       "level_history": items(obj({"level": "i", "was": "i", "by": "s", "by_name": "s", "at": "s"},
                                                  required=["level", "was", "by", "at"])),
                       "asked_today": {"type": "object", "additionalProperties": {"type": "integer"},
                                                         "description": "{person actor: "
                                                                        "items the Strategy Bot put "
                                                                        "in their Needs you today (UTC)}"},
                       "level3_offer": ref("StrategyBotLevel3Offer")},
                      required=["name", "regular_checks", "level", "levels"]),
    "StrategyBotLogEntry": obj({"id": "s", "created": "s", "kind": {"type": "string", "enum": [
                               "decision", "cost", "publish", "send", "bots", "target", "vision", "instructions",
                               "person_task"]},
                           "reason": "s", "goal_id": "s", "state": {"type": "string", "enum": ["done", "proposed", "undone"]},
                           "proposal_task": {"type": "string", "description": "The owner's Needs you item, when filed"},
                           "note": "s", "done": items({"type": "object", "additionalProperties": True}),
                           "left": items({"type": "object", "additionalProperties": True}),
                           "undoable": "b", "undone_at": "s", "undone_by": "s",
                           "undo": {"type": "object", "additionalProperties": True,
                                    "description": "What Undo restored and what it kept: {restored, kept, text}"}},
                          required=["id", "created", "kind", "state", "done", "left", "undoable"]),
    "StrategyBotLog": obj({"items": items(ref("StrategyBotLogEntry")), "next_before": "s", "can_undo": "b"},
                     required=["items", "can_undo"]),
    "StrategyBotLevel3Offer": obj({"streak": "i", "needed": "i", "due": "b", "last_offered_at": "s"},
                             required=["streak", "needed", "due"]),
    "StrategyBotLevel3OfferResult": obj({"level3_offer": ref("StrategyBotLevel3Offer")}),
    "StrategyBotMergeResult": obj({"task": {"type": "object", "description": "The merged item: "
                                                                             "the Strategy "
                                                                             "Bot's task, waiting on the person"},
                              "originals": items(obj({"task_id": "s", "title": "s", "asker": "s", "asker_name": "s",
                                                      "state": "s", "note": "s"}, required=["task_id", "asker", "state"]))}),
    "GoalsNeedsYou": obj({"actor": "s", "items": items({
        "type": "object", "required": ["kind"], "additionalProperties": True,
        "properties": {"kind": {"enum": ["vision", "kpi_red", "recovery", "why", "proposal", "kpi_stale"]},
                       "goal_id": {"type": "string"},
                       "options": {"type": "array", "description": "kind recovery: each option in plain words, "
                                   "{label, pick, actions: [{text, note}]}", "items": {"type": "object"}},
                       "goals": {"type": "array", "description": "kind why: the goals whose proposed Why lines wait on you",
                                 "items": {"type": "object"}},
                       "goal_title": {"type": ["string", "null"]}, "kpi_id": {"type": "string"}, "kpi_name": {"type": ["string", "null"]},
                       "reason": {"type": "string"}, "freshness": {"type": "string"}, "proposal": {"type": "object"},
                       "task": {"type": "object", "required": ["id"], "properties": {
                           "id": {"type": "string"}, "title": {"type": "string"}, "status": {"type": "string"}}}}})},
                        required=["actor", "items"]),
    "GoalRefreshView": obj({"changed": "a", "suggested": "a", "checked": "i"}),
    "GoalCheckins": obj({"goal_id": "s", "checkins": items(ref("GoalCheckinView"))}),
    "GoalCheckinResult": obj({"checkin": ref("GoalCheckinView")}),
    "GoalKpiResult": obj({"kpi": ref("Kpi")}),
    "GoalProposalList": obj({"proposals": items(ref("GoalProposalView"))}),
    "GoalProposalResult": obj({"proposal": ref("GoalProposalView")}),
    "KpiList": obj({"kpis": items(ref("Kpi"))}, required=["kpis"]),
    "KpiDetail": obj({"kpi": ref("Kpi"), "links": {},
        "readings": items(ref("KpiReadingView")),
        "definitions": items({"type": "object", "description": "version, ts, actor, name, definition, unit, direction, cadence, source_note; newest first"}),
        "checkins": items(ref("GoalCheckinView")), "proposals": items(ref("GoalProposalView")), "may_edit": "b", "may_log": "b"},
                    required=["kpi", "links", "readings", "definitions", "checkins", "proposals", "may_edit", "may_log"]),
    "KpiReadings": obj({"kpi": {"type": "object", "description": "The KPI record"}, "readings": items(ref("KpiReadingView"))}),
    "KpiReadingResult": obj({"reading": ref("KpiReadingView")}),
    "BotKpis": obj({"bot": "s", "kpis": items(ref("Kpi"))}),
    "UsageFigures": obj({"runs": "i", "input_tokens": {"type": "integer", "description": "Uncached input tokens"},
                         "cached_tokens": {"type": "integer", "description": "Input tokens read from the provider's cache"},
                         "output_tokens": "i",
                         "est_cost_usd": {"type": ["number", "null"], "description": "List-price estimate for runs billed by API key; "
                                          "null when every such run used a model with no list price"},
                         "subscription_equiv_usd": {"type": "number", "description": "What runs on a ChatGPT or Claude sign-in would "
                                                    "cost through the API: not money spent, never added to est_cost_usd"},
                         "unpriced_runs": {"type": "integer", "description": "Runs with tokens on a model with no list price"},
                         "low_estimate_runs": {"type": "integer", "description": "Priced runs whose estimate may be low: the model "
                                               "charges more for long prompts and the runner did not report every request's tokens"}}),
    "Usage": obj({"from": "s", "to": "s", "prices_as_of": {"type": "string", "description": "The date the price table was read"},
                  "group": {"enum": ["bot", "day", "routine", "harness", "model", "effort", "subscription"]}, "department": "n", "totals": ref("UsageFigures"),
                  "rows": items({"type": "object", "description": "By bot: bot, name, department; by day: day; by routine: "
                                                 "routine (null for runs that came from none), title, bot, name. Every row has the "
                                                 "usage figures and `share`, its part of the total (estimate plus API-equivalent)",
                                                 "additionalProperties": True}),
                  "dimensions": {"type": "object", "description": "Available dimension filters, scoped to visible runs"},
                  "dimension_labels": {"type": "object", "description": "Display labels keyed by immutable filter value; subscription names include their Computer"},
                  "departments": items({"type": "string"}), "bot": "s", "name": "s", "daily": items({"type": "object", "additionalProperties": True}),
                  "routines": items({"type": "object", "additionalProperties": True})},
                 required=["from", "to", "prices_as_of", "totals"]),
    "UsageLimitView": obj({**{p + "_usd": NUM_N for p in PERIODS}, "source": {"type": "object", "description": "Where each cap comes "
                       "from: bot, company or null"}, **{p + "_spent": NUM_N for p in ("hour", "day", "week", "month")},
                       "percent": {"type": "integer", "description": "The highest share of one of the bot's own limits reached"},
                       "blocked": {"enum": [*PERIODS, "quarterly", "yearly", "budget", None], "description": "Set while a "
                                   "limit is met, the bot's own, a pool's or a Finance budget's (quarterly, yearly, or budget "
                                   "for a one-time budget): the bot takes no new job"},
                       "blocked_by": {"type": ["object", "null"], "description": "Whose limit holds the bot: scope (bot, company, "
                                      "group:<id> or budget), name (a pool's or budget's), period, until for a budget (\"\" "
                                      "when it never turns over), and spent and limit (a pool's to the owner and "
                                      "administrators only)", "additionalProperties": True},
                       **{"own_" + p + "_usd": NUM_N for p in PERIODS}, "may_edit": "b"}),
    "UsageDefaultView": obj({"default": obj({**{p + "_usd": NUM_N for p in PERIODS}, "count_subscription": "b"})}),
    "UsagePoolView": obj({**{p + "_usd": NUM_N for p in PERIODS}, **{p + "_spent": NUM_N for p in ("30m", "hour", "day", "week", "month")},
                          "percent": "i", "blocked": {"enum": [*PERIODS, None]}}),
    "UsagePoolLimit": obj({"scope": {"type": "string", "description": "company or group:<id>"}, "limit": ref("UsagePoolView")}),
    "UsageLimits": obj({"default": obj({**{p + "_usd": NUM_N for p in PERIODS}, "count_subscription": "b"}),
                        "may_edit_default": "b",
                        "pools": {"type": ["object", "null"], "description": "Owner and administrators: company (UsagePoolView) and "
                                  "groups, {id: UsagePoolView with name and parent}. A lead who is "
                                  "not an administrator: company null and only the groups it leads and those below, each "
                                  "with may_edit true", "additionalProperties": True},
                        "bots": {"type": "object", "additionalProperties": ref("UsageLimitView")}}),
    "BotUsageLimit": obj({"bot": "s", "limit": ref("UsageLimitView")}),
    "Token": obj({"access_token": "s", "token_type": "s", "expires_in": "i", "idle_timeout": "i", "person": "s"}),
    "Revoked": obj({"revoked": "b"}),
}


def _merge(*schemas, **more):
    """One object schema from several: the properties of each, and what each requires."""
    props, required = {}, set()
    for schema in schemas:
        props.update(schema["properties"])
        required.update(schema.get("required", []))
    props.update(more)
    return {"type": "object", "properties": props, "required": sorted(required), "additionalProperties": True}


# A goal with what the page adds to it, a link with what says how it is doing: one object each, so a client sees every field.
SCHEMAS.update({
    "BrowserExit": obj({"sharing": "b", "bots": items(_t("s")), "daily_cap_bytes": "i",
                        "computer": obj({"id": "s", "label": "s"}),
                        "used_today_bytes": {"type": ["integer", "null"]}, "may_change": "b", "may_turn_off": "b"}),
    "BrowserExitUsage": obj({"rows": items(obj({"day": "s", "bot": "s", "host": "s", "bytes": "i", "tunnels": "i"})),
                             "days": items(obj({"day": "s", "bytes": "i"})),
                             "next": {"type": ["integer", "null"]}}),
    "BotBrowserExitView": obj({"browser_exit": {"type": ["object", "null"], "properties": {
                               "runner_id": {"type": "string"}, "name": {"type": "string"}}}},
                          choices=items(obj({"runner_id": "s", "label": "s", "operator": "s"})),
                          may_change=_t("b"), required=["browser_exit"]),
})
SCHEMAS["GoalResult"]["properties"]["goal"] = _merge(SCHEMAS["Goal"], SCHEMAS["GoalView"])
SCHEMAS["GoalTree"]["properties"]["goals"] = items(_merge(SCHEMAS["Goal"], SCHEMAS["GoalTreeRow"]))
SCHEMAS["KpiDetail"]["properties"]["links"] = items(_merge(SCHEMAS["KpiLink"], obj({
    "goal_title": "s", "goal_owner": "s", "goal_status": "n", "target_label": "s", "status": "s", "reason": "s",
    "expected": NUM_N}, required=["goal_title", "status"])))
# Tags add rich display data beside the existing task label keys.
TAGS["Tags"] = "Tags, metadata, markdown checklists and reusable templates."
STABLE.extend([
    ("/api/v2/tags", "get", "Tags", "listTags", "Tags and templates", "TagList"),
    ("/api/v2/tags", "post", "Tags", "createTag", "Create a tag or template", "TagResult"),
    ("/api/v2/tags/{tag_id}", "get", "Tags", "getTag", "A tag and its visible tasks", "TagDetail"),
    ("/api/v2/tags/{tag_id}", "post", "Tags", "updateTag", "Edit a tag using its current version", "TagResult"),
    ("/api/v2/tags/{tag_id}/instances", "post", "Tags", "createTagInstance", "Copy a template into a task tag", "TagResult"),
])
SCHEMAS.update({
    "Tag": obj({"id": "s", "key": "s", "label": "s", "metadata": "o", "markdown": "s", "is_template": "b",
                "template_id": "n", "owner": "n", "version": "i", "created": "s", "updated": "s"}),
    "TagList": obj({"tags": items(ref("Tag"))}),
    "TagResult": obj({"tag": ref("Tag")}),
    "TagDetail": obj({"tag": ref("Tag"), "editable": "b", "tasks": items(ref("Task")),
                      "next_offset": {"type": ["integer", "null"]}}),
})
SCHEMAS["Task"]["properties"]["tags"] = items(ref("Tag"))

# The named events automations and outgoing webhooks listen for (backend/event_catalog.py).
TAGS["Events"] = "The catalog of named events (docs/routines.md)."
STABLE.append(("/api/v2/event-types", "get", "Events", "listEventTypes",
               "Every named event with its description, version and an example payload (`group` narrows it)", "EventTypeList"))
SCHEMAS.update({
    "EventType": obj({"name": "s", "group": "s", "description": "s", "version": "i", "example": "o",
                      "aliases": items({"type": "string"}), "sourceless": "b", "outbound": "b"},
                     required=["name", "group", "description", "version", "example"]),
    "EventTypeList": obj({"items": items(ref("EventType"))}, required=["items"]),
})

# Failed-run back-off and run budgets (backend/run_backoff.py, backend/turn_budgets.py).
STABLE.extend([
    ("/api/v2/bots/{bot}/budget", "get", "Bots", "getBotBudget",
     "A bot's run budget (runs per hour and per day), the team default, its runs so far and any hold; for the people who manage it", "BotBudgetState"),
    ("/api/v2/bots/{bot}/budget", "post", "Bots", "setBotBudget",
     "Set the bot's own runs per hour and per day: null follows the team, 0 is no limit; raising it above the runs that held the bot releases it", "BotBudgetState"),
    ("/api/v2/bots/{bot}/release", "post", "Bots", "releaseBot",
     "Release a bot Tico stopped for failed runs or too many runs, or for queued runs it could not start; its queued "
     "work starts again", "BotReleased"),
    ("/api/v2/settings/turn-budgets", "get", "Bots", "getTeamBudgets",
     "The team default runs per hour and per day for bots with no budget of their own (owner and admins)", "TeamBudgetState"),
    ("/api/v2/settings/turn-budgets", "post", "Bots", "setTeamBudgets",
     "Set the team default runs per hour and per day; 0 is no limit (owner and admins)", "TeamBudgetState"),
])
SCHEMAS.update({
    "BotBudgetState": obj({"bot": "s", "turns_per_hour": "i", "turns_per_day": "i", "own": {"type": "object"},
                      "team": {"type": "object"}, "runs": {"type": "object"},
                      "held": {"type": ["object", "null"]}, "run_hold": {"type": ["object", "null"]},
                      "revision": "i", "released": "b"}),
    "BotReleased": obj({"bot": "s", "released": {"type": "object"}}),
    "TeamBudgetState": obj({"turns_per_hour": "i", "turns_per_day": "i", "defaults": {"type": "object"}}),
    "TaskCapRow": obj({"sender": "s", "receiver": "s", "per_day": "i", "count": "i", "closed": "i", "updated": "s", "updated_by": "s"}),
    "TaskCapProposal": obj({"id": "s", "sender": "s", "receiver": "s", "per_day": "i", "status": "s", "proposed_by": "s", "created": "s"}),
    "TaskCaps": obj({"caps": items(ref("TaskCapRow")), "proposals": items(ref("TaskCapProposal"))}),
    "TaskCapWrite": obj({"cap": {"type": "object"}, "approval": "s", "status": "s"}),
})
STABLE.extend([
    ("/api/v2/task-caps", "get", "Tasks", "getTaskCaps",
     "Task caps with today's count and the tasks closed before the receiver acted, per pair; for the owner and admins, the "
     "changes bots asked for, waiting as approvals", "TaskCaps"),
    ("/api/v2/task-caps", "put", "Tasks", "setTaskCap",
     "Cap the tasks one teammate sends another a day (0 blocks, null removes). The receiver must be a manager above the "
     "sender. A bot's own change comes back as a pending approval a person decides", "TaskCapWrite"),
    ("/api/v2/task-caps", "delete", "Tasks", "removeTaskCap",
     "Remove the cap between two teammates (?sender=&receiver=); a bot's removal is an approval too", "TaskCapWrite"),
])

# The audit trail (backend/audit_log.py).
STABLE.extend([
    ("/api/v2/audit", "get", "Team", "listAudit",
     "Who changed caps, spend, groups and automations, newest first (owner: all; others: their own)", "AuditPage"),
])
SCHEMAS.update({"AuditPage": {"type": "object", "required": ["rows", "next"], "properties": {
    "rows": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "next": {"type": ["string", "null"]}}}})

# Finance (backend/finance_routes.py): every read is cut to the caller's own view.
DOC_CONTACT = {"type": ["object", "null"], "properties": {"id": {"type": ["string", "null"]},
                                                          "name": {"type": ["string", "null"]}}}
DOC_PAYMENT = {"anyOf": [obj({"id": "n", "status": {"type": "string", "enum": [
    "queued", "sending", "awaiting_bank_approval", "submitted", "settled", "returned", "failed", "cancelled",
    "unknown", "denied", "expired", "withdrawn"]}, "amount": "i", "on": "s", "error": "n"}), {"type": "null"}],
    "description": "A bill's latest payment: its state, the day it was asked for and why it ended, if it did. No id "
                   "when the scheduled ask ended before a payment existed (denied, expired, withdrawn or failed)"}
TAGS["Finance"] = ("Budgets, the P&L, expenses, bills, invoices, contacts, reports and the work inbox, as far as the "
                   "caller sees; Finance actions, committed or sent to one person for approval.")
STABLE.extend([
    ("/api/v2/finance/budgets", "get", "Finance", "listFinanceBudgets",
     "The budgets the caller sees, each with where it stands today, or with at=YYYY-MM-DD in the period holding that "
     "day (the version in effect then); amounts in cents. The owner may pass view_as", "FinanceBudgets"),
    ("/api/v2/finance/pnl", "get", "Finance", "getFinancePnl",
     "The P&L the caller sees, by day, week, month, quarter or year (past, current and projected columns), with budget "
     "and projection beside each actual; each row says its section. view=expenses filters by teams and teammates "
     "(several at once), who (bots, people or both) and unattributed. The owner may pass view_as",
     "FinancePnl"),
    ("/api/v2/finance/balance-sheet", "get", "Finance", "getFinanceBalanceSheet",
     "Assets, liabilities and equity on a day, each line with its sources. Owner-only unless granted",
     "FinanceBalanceSheet"),
    ("/api/v2/finance/transactions", "get", "Finance", "listFinanceTransactions",
     "Feed rows and entries the caller sees, newest first, with filters, search and a cursor", "FinanceTransactions"),
    ("/api/v2/finance/cards", "get", "Finance", "listFinanceCards",
     "The cards the caller follows, last 4 only, with allocation and spend this period", "FinanceCards"),
    ("/api/v2/finance/sources", "get", "Finance", "listFinanceSources",
     "Every source of the books grouped with counts, each with its account mapping, sync and access. Owner-only "
     "unless granted", "FinanceSources"),
    ("/api/v2/finance/webhook-deliveries", "get", "Finance", "listFinanceWebhookDeliveries",
     "Calls to Finance's triggers (Mercury) still kept, newest first, with their bodies, for a finance admin who sees "
     "sources; the ordinary trigger routes refuse them", "FinanceWebhookDeliveries"),
    ("/api/v2/finance/access", "get", "Finance", "getFinanceAccess",
     "Who can see what in Finance: teams, each teammate's default, grants and rights, whether the caller may grant "
     "them more (may_grant), private lines, the defaults",
     "FinanceAccess"),
    ("/api/v2/finance/payments", "get", "Finance", "listFinancePayments",
     "Payments the caller made or whose line they see, newest first, with a cursor; errors only for the payer or a "
     "finance admin", "FinancePayments"),
    ("/api/v2/finance/transfers", "get", "Finance", "listFinanceTransfers",
     "Internal transfers between two money accounts the caller sees, newest first, with their status and a cursor; "
     "errors only for the maker or a finance admin", "FinanceTransfers"),
    ("/api/v2/finance/tax/sales-tax", "get", "Finance", "getFinanceSalesTax",
     "The sales tax workpaper for a year (this year when omitted): each jurisdiction's quarters with taxable and exempt "
     "sales, tax collected and remitted, what is due and when. Full view only", "FinanceSalesTax"),
    ("/api/v2/finance/tax/sales-tax.csv", "get", "Finance", "getFinanceSalesTaxCsv",
     "The sales tax workpaper as CSV, the same lines as on screen. Full view only", None),
    ("/api/v2/finance/tax/1099", "get", "Finance", "getFinance1099",
     "The 1099 workpaper for a year (this year when omitted): each flagged vendor's cash paid with card payments left "
     "out, the threshold, whether a 1099-NEC is due and when. Full view only", "Finance1099"),
    ("/api/v2/finance/tax/1099.csv", "get", "Finance", "getFinance1099Csv",
     "The 1099 workpaper as CSV, the same lines as on screen. Full view only", None),
    ("/api/v2/finance/recipients", "get", "Finance", "listFinanceRecipients",
     "Current payment recipients, last 4 only, for teammates who may pay", "FinanceRecipients"),
    ("/api/v2/finance/mercury-users", "get", "Finance", "listFinanceMercuryUsers",
     "Mercury's users as last synced, to pick a card holder, for teammates who may issue cards; emails (masked) only "
     "for a finance admin", "FinanceMercuryUsers"),
    ("/api/v2/finance/reconciliations", "get", "Finance", "listFinanceReconciliations",
     "Reconciliations of the money accounts the caller sees, and how far each is locked", "FinanceReconciliations"),
    ("/api/v2/finance/statements", "get", "Finance", "listFinanceStatements",
     "Bank and card statements for the money accounts the caller sees", "FinanceStatements"),
    ("/api/v2/finance/rules", "get", "Finance", "listFinanceRules",
     "Finance gate and posting rules with their kind, priority, words, condition, outcome, examples and latest runs, "
     "for teammates who may write "
     "them; external_rule_id lists those linked to one external rule", "FinanceRules"),
    ("/api/v2/finance/bills", "get", "Finance", "listFinanceBills",
     "Bills the caller sees (payables: a view grant or finance.ap), newest first, filtered by status (comma-separated, "
     "or unpaid), vendor, dates, aging bucket or late, and a search, with the open total by aging bucket and a cursor; "
     "order=paid_on lists the last paid first",
     "FinanceBills"),
    ("/api/v2/finance/bills/{bill_id}", "get", "Finance", "getFinanceBill",
     "One bill with its lines, the payments settled against it, its attachments and the actions and approvals on it",
     "FinanceDocument"),
    ("/api/v2/finance/invoices", "get", "Finance", "listFinanceInvoices",
     "Invoices the caller sees (receivables: a view grant or finance.ar), newest first, with the same filters as bills",
     "FinanceInvoices"),
    ("/api/v2/finance/invoices/{invoice_id}", "get", "Finance", "getFinanceInvoice",
     "One invoice with its lines, the payments received, its attachments and the actions and approvals on it",
     "FinanceDocument"),
    ("/api/v2/finance/contacts", "get", "Finance", "listFinanceContacts",
     "Vendors (to whoever sees payables) and customers (receivables) by name, with what their open documents owe; a "
     "tax id's last 4 only for a finance admin", "FinanceContacts"),
    ("/api/v2/finance/chart", "get", "Finance", "getFinanceChart",
     "The lines the caller may post to (id, number, name, type), for the bill, invoice and journal forms; for= bill "
     "keeps expense and asset lines, invoice income lines; for teammates who write to the books", "FinanceChart"),
    ("/api/v2/finance/pay-from", "get", "Finance", "listFinancePayFrom",
     "The connected bank accounts the caller may pay bills from (finance.pay); balances only where the caller sees "
     "that account", "FinancePayFrom"),
    ("/api/v2/finance/feed/{feed_id}/expected", "get", "Finance", "listFinanceFeedExpected",
     "The payments marked paid a settled bank row not yet in the books may be the bank side of (same money account and "
     "direction, at most its amount, marked within 5 days), capped at 20, for the feed_match action",
     "FinanceFeedExpected"),
    ("/api/v2/finance/inbox", "get", "Finance", "getFinanceInbox",
     "The caller's Finance work queue: uncategorized rows, bills to enter, unmatched and missing receipts, bills due, "
     "overdue invoices, budgets near their limit, failed syncs and months ready to close, with counts and a cursor",
     "FinanceInbox"),
    ("/api/v2/finance/reports/{kind}", "get", "Finance", "getFinanceReport",
     "pnl (cash or accrual), balance_sheet, cash_flow, budget_vs_actual, ap_aging, ar_aging or spend_by, cut to the "
     "caller's view; compare=prior adds the window before; rows past top_n fold into one Other row (key \" other\"; the "
     "group with no vendor, team or teammate is keyed \" unattributed\": no real key starts with a space)",
     "FinanceReportOf"),
    ("/api/v2/finance/search", "get", "Finance", "searchFinance",
     "Search entries, bank and card rows, bills, invoices, contacts and rules the caller sees; with kind it pages by "
     "cursor, without it returns the first few of each kind", "FinanceSearch"),
    ("/api/v2/purchase-orders", "get", "Finance", "listPurchaseOrders",
     "Purchase orders the caller sees (payables), newest first, by status, vendor and a search over number, memo and "
     "vendor name, with a cursor", "PurchaseOrders"),
    ("/api/v2/purchase-orders/{po_id}", "get", "Finance", "getPurchaseOrder",
     "One purchase order with its lines and what each line has billed", "PurchaseOrder"),
    ("/api/v2/estimates", "get", "Finance", "listEstimates",
     "Estimates the caller sees (receivables), newest first, by status, customer and a search over number, memo and "
     "customer name, with a cursor", "Estimates"),
    ("/api/v2/estimates/{estimate_id}", "get", "Finance", "getEstimate",
     "One estimate with its lines; each line says whether it is taxable", "Estimate"),
    ("/api/v2/locations", "get", "Finance", "listLocations",
     "Locations by name; q searches names, archived hides them (default), shows only them or both; with a cursor",
     "Locations"),
    ("/api/v2/locations/{location_id}", "get", "Finance", "getLocation",
     "One location, archived or not", "LocationOne"),
    ("/api/v2/items", "get", "Finance", "listItems",
     "Products and services by name or sku; q searches both, archived hides them (default), shows only them or both; "
     "with a cursor. An item's accounts show to viewers who see them; its price shows with its income account",
     "Items"),
    ("/api/v2/items/{item_id}", "get", "Finance", "getItem",
     "One product or service, archived or not; its accounts show to viewers who see them", "ItemOne"),
    ("/api/v2/projects", "get", "Finance", "listProjects",
     "Projects by name; q searches names, archived hides them (default), shows only them or both; with a cursor",
     "Projects"),
    ("/api/v2/projects/{project_id}", "get", "Finance", "getProject",
     "One project, archived or not", "ProjectOne"),
    ("/api/v2/finance/metrics/{name}", "get", "Finance", "getFinanceMetric",
     "One number with when it was worked out (and for bank-backed numbers how fresh the data is): cash, burn, "
     "runway_months, revenue, spend, budget_remaining, budget_pct_used, ap_due, ar_overdue, uncategorized_count, "
     "unreconciled_days", "FinanceMetric"),
    ("/api/v2/finance/close/{month}", "get", "Finance", "getFinanceCloseChecklist",
     "A month's close checklist (YYYY-MM) for teammates who do the books, and whether it is ready to close",
     "FinanceCloseChecklist"),
    ("/api/v2/finance/action-kinds", "get", "Finance", "listFinanceActionKinds",
     "Every Finance action kind in a line, or with kind= one kind's payload fields, right and approval kind",
     "FinanceActionKinds"),
    ("/api/v2/finance/connections", "post", "Finance", "createFinanceConnection",
     "Connect Mercury with a stored token credential (finance admins): the token is tested, its accounts listed and "
     "mapped into the books. The Idempotency-Key is the action's key", "FinanceConnectionResult"),
    ("/api/v2/finance/expenses", "get", "Finance", "getFinanceExpenses",
     "Expenses the caller sees, grouped by department, team, teammate, bot, person, vendor or line. The owner may pass "
     "view_as", "FinanceReport"),
    ("/api/v2/finance/lines/{account_id}", "get", "Finance", "getFinanceLine",
     "One line's spend by vendor, team and teammate, its entries, how they were paid (paid_by) and next month's "
     "projection (next_month; by vendor, team and teammate in next_by, named in next_labels), as far as the caller sees; vendor narrows it to one vendor, and teams[], teammates[], who and "
     "unattributed filter it as the P&L's expenses view does (private totals only from whole cohorts). Rows past top_n "
     "fold into one Other row keyed \" other\", and the group with none is keyed \" unattributed\". The owner may pass view_as",
     "FinanceReport"),
    ("/api/v2/finance/actions", "post", "Finance", "requestFinanceAction",
     "Request a Finance action: committed, sent to one person for approval, denied or failed. The Idempotency-Key is "
     "the action's key; a retry re-reads the rest through the caller's current view. dry_run writes nothing and says "
     "what would happen",
     "FinanceAction"),
    ("/api/v2/finance/actions/{action_id}", "get", "Finance", "getFinanceAction",
     "An action's status, for whoever asked, its approver, or whose view covers it; its reason for the approver, and "
     "otherwise only while that view covers it",
     "FinanceAction"),
    ("/api/v2/finance/bills-address", "put", "Finance", "setFinanceBillsAddress",
     "Set the email address vendors send bills to, or clear it with null (owner or admin). inbox_id is the Email & "
     "Phone inbox that receives it, else null: mail to it never reaches Tico and uploads still work",
     "FinanceBillsAddress"),
])
SCHEMAS.update({
    "FinanceBudgets": obj({"budgets": items({"type": "object", "additionalProperties": True}), "more": "b",
                           "on": "s", "view_version": "i"}),
    "FinanceReport": obj({"view_version": "i"}),
    "FinancePnl": obj({
        "start": "s", "end": "s", "period": "s", "basis": "s", "view": "s", "team": "n", "teammate": "n",
        "budget_as_of": "n",
        "columns": items(obj({"start": "s", "end": "s", "label": "s",
                              "state": {"type": "string", "enum": ["past", "current", "future"]}})),
        "filter": obj({"teams": items({"type": "string"}), "teammates": items({"type": "string"}),
                       "who": {"type": "string", "enum": ["both", "bots", "people"]}, "unattributed": "b"}),
        "rows": items(obj({"account_id": "s", "code": "s", "name": "s", "type": "s", "parent_id": "s", "private": "b",
                           "section": {"type": "string", "enum": ["revenue", "cost_of_revenue", "operating", "other"]},
                           "actual": {"type": ["array", "null"], "items": {"type": "integer"}},
                           "total": {"type": ["integer", "null"]},
                           "budget": {"type": ["array", "null"], "items": {"type": ["integer", "null"]}},
                           "projection": {"type": ["array", "null"], "items": {"type": "integer"}}})),
        "income": items({"type": "integer"}), "expense": items({"type": "integer"}), "net": items({"type": "integer"}),
        "excluded": "b", "view_version": "i",
        "teammates": items(obj({"teammate": "s", "kind": "s", "spent": "i", "budget": {"type": ["integer", "null"]}}))},
        required=["start", "end", "period", "basis", "view", "columns", "rows", "income", "expense", "net",
                  "excluded", "view_version"]),
    "FinanceBalanceSheet": obj({
        "as_of": "s", "currency": "s", "scoped": "b", "view_version": "i", "closed_through": "n",
        "totals": obj({"asset": "i", "liability": "i", "equity": "i"}),
        **{k: items(obj({"account_id": "n", "code": "s", "name": "s", "subtype": "s", "parent_id": "n", "balance": "i",
                         "entry_sources": items({"type": "string"}),
                         "sources": items(obj({"money_account_id": "s", "name": "s", "provider": "s", "last4": "n",
                                               "provider_balance": {"type": ["integer", "null"]},
                                               "balance_as_of": "n", "due_out": obj({"count": "i", "amount": "i"})}))}))
           for k in ("assets", "liabilities", "equity")}}),
    "FinanceTransactions": obj({
        "rows": items(obj({"kind": {"type": "string", "enum": ["feed", "entry"]}, "id": "s", "date": "s", "amount": "i",
                           "currency": "s", "counterparty": "s", "description": "s", "status": "s", "pending": "b",
                           "card_id": "n", "entry_id": "n", "reconciled": "b", "money_account": {"type": ["object", "null"]},
                           "lines": items({"type": "object", "additionalProperties": True}),
                           "account_id": "s", "account": "s", "vendor": "s", "vendor_name": "s", "memo": "s",
                           "source": "s",
                           "reversed": "b", "tags": "o"}, required=["kind", "id", "date", "amount", "currency"])),
        "next": "n", "currency": "s", "view_version": "i"}),
    "FinanceCards": obj({
        "cards": items(obj({"id": "s", "provider": "s", "holder": "s", "nickname": "s", "last4": "n", "kind": "s",
                            "status": "s", "limit": {"type": ["integer", "null"]}, "limit_interval": "n",
                            "provider_limit": {"type": ["integer", "null"]}, "sync_status": "s", "currency": "s",
                            "money_account_id": "n", "budget": {"type": ["object", "null"]},
                            "allocation": {"type": ["integer", "null"]}, "spent": {"type": ["integer", "null"]},
                            "expires_at": "n", "created_at": "s"})),
        "next": "n", "view_version": "i"}),
    "FinanceSources": obj({
        "groups": items(obj({"group": {"type": "string", "enum": ["banks", "cards", "payments", "payroll",
                                                                  "bills_invoices", "tico", "accounting", "manual"]},
                             "name": "s", "count": "i",
                             "rows": items({"type": "object", "additionalProperties": True})})),
        "view_version": "i"}),
    "FinanceAccess": obj({
        "teams": items(obj({"id": "s", "name": "s", "parent": "n", "lead": "n"})),
        "teammates": items(obj({"teammate": "s", "kind": "s", "team": "n", "reports_to": "n",
                                "default": {"type": "string", "enum": ["everything", "team_expenses", "own_expenses",
                                                                       "none"]},
                                "default_team": "n",
                                "grants": items({"type": "object", "additionalProperties": True}),
                                "rights": {"type": ["array", "null"], "items": {"type": "string"}},
                                "may_grant": "b"})),
        "private_lines": items(obj({"account_id": "s", "name": "s", "others_see": "s",
                                    "full_detail": {"type": ["array", "null"], "items": {"type": "string"}}})),
        "defaults": obj({"manager": "s", "member": "s", "manager_payroll": "s"}),
        "can_view_as": "b", "can_edit_defaults": "b", "view_version": "i"}),
    "FinanceAction": obj({"status": "s", "kind": "s", "action_id": "n", "code": "s", "reason": "s"},
                         required=["status", "kind"]),
    "FinancePayments": obj({
        "payments": items(obj({
            "id": "s", "status": {"type": "string", "enum": ["queued", "sending", "awaiting_bank_approval", "submitted",
                                                             "settled", "returned", "failed", "cancelled",
                                                             "unknown"]},
            "amount": {"type": ["integer", "null"]}, "currency": "s", "method": "s", "memo": "s", "obligation": "n", "mode": "s",
            "recipient": {"type": ["object", "null"], "properties": {"id": {"type": "string"},
                                                                     "name": {"type": "string"},
                                                                     "last4": {"type": ["string", "null"]},
                                                                     "version": {"type": "integer"}}},
            "account": {"type": ["object", "null"], "properties": {"id": {"type": "string"},
                                                                   "code": {"type": "string"},
                                                                   "name": {"type": "string"}}},
            "money_account_id": "s", "created_by": "s", "created_at": "s", "settled_at": "n", "error": "n"},
            required=["id", "status", "amount", "currency"])),
        "next_cursor": "n"}),
    "FinanceTransfers": obj({
        "transfers": items(obj({
            "id": "s", "status": {"type": "string", "enum": ["queued", "submitted", "unknown", "failed", "posted",
                                                             "cancelled"]},
            "amount": "i", "currency": "s", "memo": "s",
            "from": obj({"id": "s", "name": "s", "last4": "n"}), "to": obj({"id": "s", "name": "s", "last4": "n"}),
            "created_by": "s", "created_at": "s", "settled_at": "n", "last_error": "n"},
            required=["id", "status", "amount", "currency", "from", "to"])),
        "next_cursor": "n"}),
    "FinanceSalesTax": obj({
        "year": "i", "currency": "s",
        "rows": items(obj({"period": "s", "code": "s", "jurisdiction": "s", "taxable_sales": "i", "exempt_sales": "i",
                           "tax_collected": "i", "tax_remitted": "i", "due": "i", "due_on": "n"},
                          required=["period", "code", "taxable_sales", "exempt_sales", "tax_collected", "due"]))}),
    "Finance1099": obj({
        "year": "i", "currency": "s", "threshold": "i", "due_on": "s",
        "rows": items(obj({"vendor_id": "s", "vendor": "s", "tin_on_file": "b", "cash_paid": "i",
                           "card_paid_excluded": "i", "threshold": "i", "reportable": "b"},
                          required=["vendor_id", "vendor", "cash_paid", "reportable"]))}),
    "FinanceRecipients": obj({"recipients": items(obj({
        "id": "s", "name": "s", "method": "s", "last4": "n", "version": "i", "replaces_id": "n", "known": "b",
        "synced": "b", "created_at": "s"}, required=["id", "name", "method", "version"]))}),
    "FinanceReconciliations": obj({
        "reconciliations": items(obj({
            "id": "s", "money_account_id": "s", "statement_end": "s", "statement_balance": "i", "difference": "i",
            "status": {"type": "string", "enum": ["open", "done", "undone"]}, "cleared_total": "i", "cleared": "i",
            "statement_id": "n", "created_by": "s", "created_at": "s", "undone_at": "n"},
            required=["id", "money_account_id", "statement_end", "statement_balance", "difference", "status"])),
        "locked_through": {"type": "object", "additionalProperties": {"type": ["string", "null"]}}}),
    "FinanceStatements": obj({"statements": items(obj({
        "id": "s", "money_account_id": "s", "start_date": "n", "end_date": "s",
        "starting_balance": {"type": ["integer", "null"]}, "ending_balance": "i", "currency": "s", "source": "s"},
        required=["id", "money_account_id", "end_date", "ending_balance"]))}),
    "FinanceRules": obj({"rules": items(obj({
        "id": "s", "kind": {"type": "string", "enum": ["gate", "posting"]}, "priority": "i", "name": "s", "words": "s",
        "when": "o", "outcome": "o",
        "examples": items({"type": "object", "additionalProperties": True}), "external_rule_id": "n", "owner": "s",
        "active": "b", "created_at": "s",
        "runs": items(obj({"action_id": "n", "outcome": "n", "at": "s"}))}, required=["id", "name", "when", "outcome"]))}),
    "FinanceMercuryUsers": obj({"users": items(obj({"id": "s", "name": "s", "connection_id": "s", "email": "n",
                                                    "updated_at": "s"}))}),
    "FinanceWebhookDeliveries": obj({"deliveries": items(obj({
        "trigger": "s", "delivery": "s", "received_at": "s", "type": "s", "via": "s", "outcome": "s", "body": "s"}))}),
    "FinanceConnectionResult": obj({"action_id": "n", "kind": "s", "status": "s", "code": "s", "reason": "s",
                                    "approver": "n", "result": "o"}, required=["status", "kind"]),
    "FinanceBillsAddress": obj({"bills_email": "n", "inbox_id": "n"}),
    "FinanceBills": obj({"bills": items(ref("FinanceDocumentRow")), "next_cursor": "n", "open": ref("FinanceOpenTotal"),
                         "currency": "s", "bills_email": "n", "view_version": "i"}),
    "FinanceChart": obj({"accounts": items(obj({"id": "s", "number": "s", "name": "s", "type": "s", "subtype": "s",
                                                "parent_id": "n", "company_wide": "b",
                                                "attributable": items(obj({"team": "s", "teammate": "s"}, required=[]))},
                                               required=["id", "number", "name", "type"])),
                         "more": "b", "view_version": "i"}),
    "FinancePayFrom": obj({"accounts": items(obj({"id": "s", "name": "s", "provider": "s", "last4": "n",
                                                  "balance": {"type": ["integer", "null"]},
                                                  "available_balance": {"type": ["integer", "null"]},
                                                  "balance_as_of": "n"}, required=["id", "name"])),
                           "currency": "s", "view_version": "i"}),
    "FinanceFeedExpected": obj({"feed_id": "s", "kind": {"type": "string", "enum": ["bill", "invoice"]},
                                "amount": "i", "date": "s", "expected": items(obj({
                                    "settlement_id": "s", "kind": "s", "document_id": "s", "number": "s",
                                    "contact_id": "s", "contact_name": "s", "amount": "i", "marked_on": "s"})),
                                "more": "b", "view_version": "i"}),
    "FinanceInvoices": obj({"invoices": items(ref("FinanceDocumentRow")), "next_cursor": "n",
                            "open": ref("FinanceOpenTotal"), "currency": "s", "view_version": "i"}),
    "FinanceOpenTotal": obj({"count": "i", "remaining": "i", "more": "b",
                             "aging": obj({k: "i" for k in ("current", "1_30", "31_60", "61_90", "over_90")})}),
    "FinanceDocumentRow": obj({
        "id": "s", "kind": {"type": "string", "enum": ["bill", "invoice"]}, "number": "s",
        "vendor": DOC_CONTACT, "customer": DOC_CONTACT, "date": "s", "due_date": "n", "status": "s", "currency": "s",
        "total": "i", "paid": "i", "remaining": "i", "aging": "n", "days_late": "i", "memo": "s", "entry_id": "n",
        "attachments": "i", "created_by": "s", "created_at": "s", "origin": "s", "approved_by": "n",
        "approved_at": "n", "pay_on": "n", "payment": DOC_PAYMENT, "sent_at": "n", "written_off": "i",
        "payment_link": "n", "account": {"type": ["object", "null"], "additionalProperties": True}, "split": "b",
        "team": "n", "paid_on": "n", "paid_by": "n"},
        required=["id", "kind", "number", "date", "status", "currency", "total", "paid", "remaining"]),
    "FinanceDocument": {"allOf": [ref("FinanceDocumentRow"), obj({
        "lines": items(obj({"id": "s", "account": {"type": ["object", "null"], "additionalProperties": True},
                            "amount": "i", "description": "s", "tags": "o", "taxable": "b"},
                           required=["id", "account", "amount", "description", "tags"])),
        "settlements": items(obj({"id": "s", "from_kind": {"type": "string", "enum": ["payment", "feed", "credit",
                                                                                       "account", "expected"]},
                                  "from_id": "n", "amount": "i", "settled_on": "s", "entry_id": "n", "voided_at": "n",
                                  "created_by": "s"})),
        "attachments": items(obj({"id": "s", "file_id": "s", "created_by": "s", "created_at": "s"})),
        "actions": items(obj({"action_id": "s", "kind": "s", "status": "s", "actor": "s", "approval_id": "n",
                              "approval_to": "n", "rule_id": "n", "created_at": "s"})),
        "settlement_count": "i", "settlements_more": "b", "attachment_count": "i", "attachments_more": "b",
        "terms_days": {"type": ["integer", "null"]}, "schedule": {"type": ["object", "null"]}, "view_version": "i"},
        required=["lines", "settlements", "attachments", "actions"])]},
    "FinanceContacts": obj({"contacts": items(obj({
        "id": "s", "name": "s", "kind": {"type": "string", "enum": ["vendor", "customer", "both"]},
        "emails": items({"type": "string"}), "default_account": {"type": ["object", "null"], "additionalProperties": True},
        "is_1099": "b", "has_tax_id": "b", "tax_id_last4": "n", "recipient_id": "n", "active": "b",
        "owed": obj({"bills": "i", "invoices": "i"}, required=[]), "created_at": "s"},
        required=["id", "name", "kind", "active"])), "next_cursor": "n", "view_version": "i"}),
    "FinanceInbox": obj({"items": items(obj({"kind": {"type": "string", "enum": [
        "uncategorized", "bill_to_enter", "unmatched_receipt", "missing_receipt", "bill_due", "invoice_overdue",
        "budget_near_limit", "sync_failed", "period_ready"]}, "id": "s", "label": "s"})),
        "counts": {"type": "object", "additionalProperties": {"type": "integer"}}, "more": items({"type": "string"}),
        "next_cursor": "n", "currency": "s", "view_version": "i"}),
    "FinanceReportOf": obj({"kind": {"type": "string", "enum": ["pnl", "pnl_by", "balance_sheet", "cash_flow",
                                                                 "budget_vs_actual", "ap_aging", "ar_aging",
                                                                 "spend_by"]},
                            "prior": {"type": "object", "additionalProperties": True}}, required=["kind"]),
    "FinanceOrderRow": obj({
        "id": "s", "number": "s", "contact": obj({"id": "s", "name": "s"}), "date": "s", "status": "s",
        "currency": "s", "total": "i", "memo": "s"}, required=["id", "number", "contact", "date", "status", "currency",
                                                             "total", "memo"]),
    "PurchaseOrders": obj({"orders": items(ref("FinanceOrderRow")), "next_cursor": "n"}, required=["orders",
                                                                                                "next_cursor"]),
    "PurchaseOrder": {"allOf": [ref("FinanceOrderRow"), obj({
        "lines": items(obj({"id": "s", "line_no": "i"}, required=["id", "line_no"])), "view_version": "i"},
        required=["lines"])]},
    "Estimates": obj({"estimates": items(ref("FinanceOrderRow")), "next_cursor": "n"}, required=["estimates",
                                                                                              "next_cursor"]),
    "Estimate": {"allOf": [ref("FinanceOrderRow"), obj({
        "lines": items(obj({"id": "s", "line_no": "i"}, required=["id", "line_no"])), "view_version": "i"},
        required=["lines"])]},
    "Locations": obj({"locations": items(ref("Location")), "next_cursor": "n"}, required=["locations", "next_cursor"]),
    "LocationOne": obj({"location": ref("Location")}, required=["location"]),
    "Location": obj({"id": "s", "name": "s", "kind": "s", "parent_id": "n", "address": "o", "archived_at": "n"},
                    required=["id", "name", "kind", "address"]),
    "Items": obj({"items": items(ref("Item")), "next_cursor": "n"}, required=["items", "next_cursor"]),
    "ItemOne": obj({"item": ref("Item")}, required=["item"]),
    "Item": obj({"id": "s", "name": "s", "sku": "s", "kind": {"type": "string", "enum": [
        "service", "inventory", "non_inventory", "bundle", "category"]}, "unit": "s",
        "price": {"type": ["integer", "null"]}, "currency": "s", "archived_at": "n", "income_account_id": "n",
        "expense_account_id": "n", "asset_account_id": "n"},
        required=["id", "name", "sku", "kind", "unit", "currency"]),
    "Projects": obj({"projects": items(ref("Project")), "next_cursor": "n"}, required=["projects", "next_cursor"]),
    "ProjectOne": obj({"project": ref("Project")}, required=["project"]),
    "Project": obj({"id": "s", "name": "s", "customer_id": "n", "status": {"type": "string",
                                                                          "enum": ["open", "closed"]},
                    "archived_at": "n"}, required=["id", "name", "status"]),
    "FinanceSearch": obj({"results": items(obj({"kind": {"type": "string", "enum": [
        "entry", "feed", "bill", "invoice", "contact", "rule", "card", "item", "location", "project"]}, "id": "s",
        "label": "s"})),
        "next_cursor": "n", "currency": "s", "view_version": "i"}),
    "FinanceMetric": obj({"name": "s", "value": {"type": ["integer", "string", "null"]}, "unit": {
        "type": "string", "enum": ["minor", "percent", "months", "count", "days"]}, "as_of": "s",
        "data_as_of": "n", "stale": "b", "currency": "s", "view_version": "i"}, required=["name", "value", "unit",
                                                                                         "as_of"]),
    "FinanceCloseChecklist": obj({"month": "s", "start": "s", "end": "s", "status": "s", "ready": "b",
                                  "checks": items(obj({"key": "s", "ok": "b", "label": "s"})), "view_version": "i"}),
    "FinanceActionKinds": obj({"kinds": items(obj({"kind": "s", "words": "s", "live": "b"})), "kind": "s",
                               "words": "s", "right": "s", "live": "b", "approval_kind": "s",
                               "fields": items(obj({"name": "s", "type": "s", "required": "b", "words": "s"}))},
                              required=[]),
})

# Proposals (backend/proposals.py): a change a person decides, applied by the hub on approve.
STABLE.extend([
    ("/api/v2/approvals", "post", "Needs you", "requestApproval",
     "Ask a person to approve an action. Kinds task_cap and spend_limit are proposals "
     "the top of the org decides; the hub applies one on approve, audited with via approval:<id>, and tells BotOps",
     "ApprovalRequested"),
])
SCHEMAS.update({"ApprovalRequested": obj({"id": "s", "kind": "s", "approval": "s", "status": "s"}, required=["id", "kind"])})
# Automations (backend/automations.py).
TAGS["Automations"] = ("A trigger and a tree of steps (filter, decide, llm, find, bot_turn, paths, action), saved as versions; "
                       "one permission rule for UI, CLI and MCP.")
STABLE.extend([
    ("/api/v2/automations", "get", "Automations", "listAutomations",
     "The automations you may see, newest first, each with can_edit; paged by cursor", "AutomationList"),
    ("/api/v2/automations", "post", "Automations", "createAutomation",
     "Save a new automation as version 1; a flow that fails its checks is refused with where and why. BotOps saves "
     "it off unless `enabled` is sent; a bot without rights to it saves a proposal (off, version 1 pending)",
     "Automation"),
    ("/api/v2/automations/guide", "get", "Automations", "getAutomationGuide",
     "How BotOps builds and edits automations, with the flow format generated from the hub's code", "AutomationGuide"),
    ("/api/v2/automations/runs", "get", "Automations", "listAllAutomationRuns",
     "Runs across every automation you may see in the last range (5m, 1h, 24h, 7d), newest first: what started each, "
     "its outcome or failing step, steps, time and cost; status, trigger, owner and ids narrow it (before → next)",
     "AutomationOverviewRuns"),
    ("/api/v2/automations/stats", "get", "Automations", "getAutomationStats",
     "Runs, failures and cost per automation you may see over the range, in buckets oldest first, with totals",
     "AutomationStats"),
    ("/api/v2/automations/question-sets", "get", "Automations", "listQuestionSets",
     "The saved question sets a Decide step may name (`set`), each with its questions", "QuestionSets"),
    ("/api/v2/automations/{aid}", "get", "Automations", "getAutomation",
     "One automation with set questions filled in, today's counts and, for a schedule or a due date, next_run (for a due date, next_due too)", "Automation"),
    ("/api/v2/automations/{aid}", "patch", "Automations", "updateAutomation",
     "Change it: a new trigger, steps or batch is a new version. A bot without edit rights gets "
     "`pending`, a proposal a person restores", "Automation"),
    ("/api/v2/automations/{aid}", "delete", "Automations", "deleteAutomation",
     "Delete it; its runs stay. A built-in one is refused", "AutomationDeleted"),
    ("/api/v2/automations/{aid}/delete", "post", "Automations", "deleteAutomationPost",
     "The same delete, for clients that only POST", "AutomationDeleted"),
    ("/api/v2/automations/{aid}/run", "post", "Automations", "runAutomation",
     "Run it now with `input` standing in for the trigger's output; answers the run", "AutomationRunNow"),
    ("/api/v2/automations/{aid}/held", "post", "Automations", "endAutomationHold",
     "End the run guard's hold: `release` queues its held runs, oldest first; `drop` skips them. `moved` is how many",
     "AutomationHeld"),
    ("/api/v2/automations/{aid}/runs", "get", "Automations", "listAutomationRuns",
     "Its runs, newest first (limit, before → next), step detail for 30 days, and daily run and drop counts",
     "AutomationRuns"),
    ("/api/v2/automations/{aid}/versions", "get", "Automations", "listAutomationVersions",
     "Its versions, newest first; status=pending for proposals", "AutomationVersions"),
    ("/api/v2/automations/{aid}/versions/{n}/restore", "post", "Automations", "restoreAutomationVersion",
     "Make a version current: a pending one keeps its number, an older one is saved as a new version; a draft's first "
     "pending version turns it on", "Automation"),
    ("/api/v2/automations/{aid}/versions/{n}", "delete", "Automations", "discardAutomationVersion",
     "Discard a pending version (409 for any other)", "Automation"),
    ("/api/v2/inboxes/{inbox}", "get", "Automations", "getInboxSend",
     "An inbox's send switch (on by default): email address, slack:dm or slack:#<channel>", "InboxSwitch"),
    ("/api/v2/inboxes/{inbox}", "patch", "Automations", "setInboxSend",
     "Turn the send switch on or off (the inbox's person, or the owner); off, send_reply saves a draft", "InboxSwitch"),
    ("/api/v2/people/{person}/assistant", "get", "Automations", "getPersonAssistant",
     "The bot behind a person's Assistant, or null until it is first used", "PersonAssistant"),
])
_RUN = obj({"id": "s", "automation_id": "s", "version": "i", "trigger_ref": "s", "started": "s",
            "finished": {"type": ["string", "null"]}, "status": {"enum": ["queued", "running", "waiting", "ok", "failed", "empty", "skipped", "held"]},
            "cost": "f", "summary": "n", "steps": {"type": ["array", "null"], "items": {"type": "object"},
                                                   "description": "Each step's status, outputs and cost; null after 30 days"}},
           required=["id", "automation_id", "version", "status", "steps"])
SCHEMAS.update({
    "Automation": obj({"id": "s", "name": "s", "owner": "s", "enabled": "b", "trigger": {"type": "object"},
                       "steps": items({"type": "object"}), "version": "i", "can_edit": "b", "can_pause": "b",
                       "spend_cap": {"type": ["object", "null"]}, "batch": {"type": ["object", "null"]},
                       "last_run": {"type": ["object", "null"]},
                       "counts": obj({"today": obj({"runs": "i", "dropped": "i"})}),
                       "next_run": {"type": ["string", "null"]},
                       "next_due": {"type": ["string", "null"], "description": "A due-date trigger's next due day "
                                    "(YYYY-MM-DD); next_run is when its run starts"},
                       "pending": "i", "warning": "s",
                       "kind": {"type": "string", "description": "The trigger as the overview groups it; system for "
                                "Tico's own jobs"},
                       "problems": {"type": "array", "description": "Wake rules that name a step their task type "
                                    "does not have (they never fire until changed): rule id, the step named and why",
                                    "items": obj({"rule": "s", "step": "s", "why": "s"})},
                       "paused": {"type": "boolean", "description": "Off because an earlier run guard paused it"},
                       "hold": {**obj({"rule": {"enum": ["flood", "sustained"]}, "since": "s", "held": "i",
                                       "dropped": "i", "task": {"type": ["string", "null"]}}),
                                "description": "Present while the run guard keeps its new runs held, not run, until "
                                               "someone releases or drops them; `task` is the Needs-you task"},
                       "schedule": {"type": "string", "description": "A system job's schedule in words"}},
                      required=["id", "can_edit"]),
    "AutomationList": obj({"automations": items(ref("Automation")), "next": {"type": ["integer", "null"]}}),
    "AutomationDeleted": obj({"id": "s", "deleted": "b"}),
    "AutomationRun": _RUN,
    "AutomationRunNow": obj({"run": ref("AutomationRun")}),
    "AutomationRuns": obj({"runs": items(ref("AutomationRun")), "next": {"type": ["string", "null"]},
                           "daily": items(obj({"day": "s", "runs": "i", "dropped": "i"}))}),
    "AutomationOverviewRuns": obj({
        "runs": items(obj({"id": "s", "automation_id": "s", "automation_name": "s",
                           "trigger": {"enum": ["message", "task_received", "schedule", "webhook", "listening", "event",
                                                "due", "system"]},
                           "owner": "s", "started": "s", "finished": {"type": ["string", "null"]}, "status": "s",
                           "cost": "f", "started_by": "s", "outcome": "s", "step_count": {"type": ["integer", "null"]},
                           "count": {"type": "integer", "description": "Quiet system-job runs folded into this row"}},
                          required=["id", "automation_id", "automation_name", "trigger", "started", "status"])),
        "next": {"type": ["string", "null"]}, "since": "s"}),
    "AutomationStats": obj({
        "range": "s", "since": "s", "until": "s", "bucket_seconds": "i",
        "automations": items(obj({"id": "s", "runs": "i", "failed": "i", "cost": "f",
                                  "buckets": items(obj({"ok": "i", "failed": "i"}))})),
        "totals": obj({"runs": "i", "failed": "i", "cost": "f", "buckets": items(obj({"ok": "i", "failed": "i"}))})}),
    "AutomationVersions": obj({"versions": items(obj({"n": "i", "actor": "s", "at": "s",
                                                      "status": {"enum": ["active", "pending"]}, "summary": "s",
                                                      "flow": {"type": "object"}},
                                                     required=["n", "actor", "at", "status", "flow"])),
                               "next": {"type": ["integer", "null"]}}),
    "AutomationGuide": obj({"text": "s"}),
    "QuestionSets": obj({"sets": items(obj({"id": "s", "summary": "s", "questions": {"type": "object", "description": "Keyed by question id, in judge.py's format"}}))}),
    "InboxSwitch": obj({"inbox": "s", "send": "b"}),
    "PersonAssistant": obj({"bot": {"type": ["string", "null"]}}),
})
SCHEMAS["AutomationHeld"] = _merge(SCHEMAS["Automation"], moved={"type": "integer", "description": "Runs released or "
                                                                   "dropped"})

# Bot profiles (backend/profiles.py).
STABLE.extend([
    ("/api/v2/profiles", "get", "Bots", "listProfiles",
     "Every bot profile with its one-line description, its values and the wake rows it adds, for a picker", "ProfileChoices"),
    ("/api/v2/bots/{bot}/profile", "get", "Bots", "getBotProfile",
     "A bot's profile and the values it runs on", "BotProfileState"),
    ("/api/v2/bots/{bot}/profile", "post", "Bots", "setBotProfile",
     "Set the bot's profile (doer, reviewer, lead, manager, responder, monitor, custom); leaving custom deletes the wake rules it "
     "copied and needs confirm_drop_rules", "BotProfileState"),
])
SCHEMAS.update({
    "ProfileChoice": obj({"name": "s", "label": "s", "description": "s", "batch_seconds": "i", "batch_scope": "s",
                          "sweep_seconds": "i", "digest_hour": {"type": ["integer", "null"]}, "pulls": "s", "sweeps": "b",
                          "takes": "s", "rules": {"type": "array", "items": {"type": "object"}}}),
    "ProfileChoices": obj({"profiles": items(ref("ProfileChoice"))}),
    "BotProfileState": obj({"bot": "s", "profile": {"type": "object"}, "wake": {"type": "object"}, "rules": "i",
                            "revision": "i"}),
})

SCHEMAS.update({
    "Repository": obj({"full_name": "s", "enabled": "b", "bot_repo": "b", "default_branch": "n",
                       "setup_command": "n", "setup_source": "n", "reachable": "b", "last_seen": "n"}),
    "ProductRepositoryPreview": obj({"org": "s", "name": "s", "repository": "s", "visibility": {"enum": ["private"]},
                                      "auto_init": "b", "capability": {"enum": ["available", "missing", "unknown", "not_installed"]},
                                      "capability_detail": "s"}),
    "ProductRepositoryResult": obj({"repository": "s", "html_url": "s", "visibility": {"enum": ["private"]},
                                     "auto_init": "b", "installation_access": {"enum": ["available", "owner_action_required", "unverified"]},
                                     "note": "s"}),
    "RepositoryList": obj({"repositories": items(ref("Repository")), "new_bot_default": {"enum": ["own", "all"]},
                           "github_connected": "b"}),
    # The workflow options are present only when on; a request's access defaults to write.
    "RepositoryGrant": obj({"full_name": "s", "access": {"enum": ["read", "write"]}, "run_workflows": "b",
                            "edit_workflows": "b"}, required=["full_name"]),
    "BotRepositories": obj({"mode": {"enum": ["own", "all", "chosen"]}, "all_access": {"enum": ["read", "write"]},
                            "chosen": items(ref("RepositoryGrant")), "all_repos": items(ref("TickedRepository")),
                            "effective": items(ref("RepositoryGrant"))}),
    # Only the options that are on are present.
    "TickedRepository": obj({"full_name": "s", "run_workflows": "b", "edit_workflows": "b"}, required=["full_name"]),
})
TAGS['Roadmaps'] = "Company roadmap settings, internal requesters, and anonymous publication (docs/roadmaps.md)."
STABLE.extend([
    ('/api/v2/roadmaps/settings', 'get', 'Roadmaps', 'getRoadmapSettings', 'Company roadmap settings', None),
    ('/api/v2/roadmaps/settings', 'post', 'Roadmaps', 'setRoadmapSettings', 'Partially update roadmap settings (owners and admins)', None),
    ('/api/v2/roadmap-tasks', 'get', 'Roadmaps', 'rankRoadmapTasks', 'Rank roadmap tasks with internal requester counts', None),
    ('/api/v2/tasks/{tid}/requesters', 'get', 'Roadmaps', 'listTaskRequesters', 'Internal requester contacts, paged and byte-capped', None),
    ('/api/v2/tasks/{tid}/requesters', 'post', 'Roadmaps', 'addTaskRequester', 'Record a requester; returns a removal token once', None),
    ('/api/v2/tasks/{tid}/requesters/{rid}', 'post', 'Roadmaps', 'updateTaskRequester', 'Replace internal requester details', None),
    ('/api/v2/tasks/{tid}/requesters/{rid}/delete', 'post', 'Roadmaps', 'deleteTaskRequester', 'Remove a requester', None),
    ('/api/v2/tasks/{tid}/publication-preview', 'post', 'Roadmaps', 'previewTaskPublication', 'Exact publish approval payload for public edits or replies', None),
    ('/api/v2/tasks/{tid}/comments/{mid}/moderate', 'post', 'Roadmaps', 'moderateTaskComment', 'Hide, restore or delete a public comment', None),
    ('/public/roadmaps/{label}', 'get', 'Roadmaps', 'listPublicRoadmap', 'Public top-level tasks carrying an enabled label', 'PublicRoadmap'),
    ('/public/roadmaps/{label}/tasks/{tid}', 'get', 'Roadmaps', 'getPublicRoadmapTask', 'Public task and paged public comments', 'PublicRoadmapTask'),
    ('/public/roadmaps/{label}/suggest', 'post', 'Roadmaps', 'suggestRoadmapTask', 'Private suggestion for Product Manager review', None),
    ('/public/roadmaps/{label}/tasks/{tid}/requesters', 'post', 'Roadmaps', 'requestPublicRoadmapTask', 'Add yourself; keep the returned removal token', None),
    ('/public/roadmaps/{label}/tasks/{tid}/requesters/{token}', 'delete', 'Roadmaps', 'removePublicRoadmapRequester', 'Remove yourself using your token', None),
    ('/public/roadmaps/{label}/tasks/{tid}/comments', 'get', 'Roadmaps', 'listPublicRoadmapComments', 'Visible public comments, paged', 'PublicRoadmapComments'),
    ('/public/roadmaps/{label}/tasks/{tid}/comments', 'post', 'Roadmaps', 'addPublicRoadmapComment', 'Add a public plain-text comment', None),
])
SCHEMAS.update({
    'PublicTask': obj({'id': 's', 'title': 's', 'summary': 's', 'step': 'n', 'requester_count': 'i',
                       'parts': obj({'total': 'i', 'done': 'i'})}),
    'PublicComment': obj({'id': 's', 'text': 's', 'author': {'enum': ['Team', 'Visitor']}, 'created': 's'}),
    'PublicRoadmap': obj({'tasks': items(ref('PublicTask')), 'next_offset': {'type': ['integer', 'null']}}),
    'PublicRoadmapComments': obj({'comments': items(ref('PublicComment')), 'next_offset': {'type': ['integer', 'null']}}),
    'PublicRoadmapTask': obj({'task': ref('PublicTask'), 'comments': items(ref('PublicComment')),
                              'next_offset': {'type': ['integer', 'null']}}),
})
SCHEMAS['Task']['properties'].update({
    'visibility': {'enum': ['public', 'private']}, 'public_title': {'type': 'string'},
    'public_summary': {'type': 'string', 'description': 'Separate public text; omitted with brief=true'}, 'size': {'type': ['number', 'null']},
    'requester_count': {'type': 'integer'}, 'external_requesters': {'type': 'integer'},
    'internal_requesters': {'type': 'integer'}, 'important_requesters': {'type': 'integer'},
})
SCHEMAS['TaskType']['properties'].update({'default_visibility': {'enum': ['public', 'private']},
                                         'manager': {'type': ['string', 'null']},
                                         'role': {'type': ['string', 'null'], 'enum': ['product-roadmap', 'engineering', None],
                                                  'description': 'The Product roadmap or Dev tickets type, whoever manages it'}})

TAGS['Rules'] = ("Org rules: what teammates follow (internal) and what someone outside requires (external), with their "
                 "due dates and sources.")
TAGS['Strategy'] = ("The team's strategy: a draft the owner edits, numbered published versions, and the bets goals "
                    "serve.")
STABLE.extend([
    ('/api/v2/strategy', 'get', 'Strategy', 'getStrategy', 'The published strategy, its bets with their goals and '
     'colour, the website; the owner also gets the draft', None),
    ('/api/v2/strategy/draft', 'put', 'Strategy', 'updateStrategyDraft', 'Owner only: change the draft, only the '
     'fields sent; a stale revision answers 409', None),
    ('/api/v2/strategy/publish', 'post', 'Strategy', 'publishStrategy', 'Owner only: the draft becomes the next '
     'numbered version', None),
    ('/api/v2/strategy/versions', 'get', 'Strategy', 'listStrategyVersions', 'Published versions, newest first', None),
    ('/api/v2/strategy/versions/{n}', 'get', 'Strategy', 'getStrategyVersion', 'One published version whole', None),
    ('/api/v2/strategy/diff', 'get', 'Strategy', 'diffStrategyVersions', 'What changed between two published '
     'versions', None),
    ('/api/v2/strategy/bets', 'post', 'Strategy', 'addStrategyBet', 'Owner only: a new bet', None),
    ('/api/v2/strategy/bets/{bet_id}', 'post', 'Strategy', 'updateStrategyBet', 'Owner only: change a bet, only the '
     'fields sent', None),
    ('/api/v2/strategy/summary', 'get', 'Strategy', 'getStrategySummary', 'The published strategy as the text every '
     'run carries; {text, n} with Accept: application/json', None),
    ('/api/v2/strategy/guides', 'get', 'Strategy', 'listStrategyGuides', 'The question guides the Strategy Bot drafts '
     'with', None),
    ('/api/v2/strategy/draft-from', 'post', 'Strategy', 'draftStrategyFrom', 'Owner only: research the team\'s '
     'website and/or the owner\'s words into the draft; answers the job (running, or no_model with no model)', None),
    ('/api/v2/strategy/draft-from/{job_id}', 'get', 'Strategy', 'getStrategyDraftFromJob', 'Owner only: one research '
     'job (latest for the newest): running, done, gave_up, failed or no_model, with what it found and wrote', None),
])

TAGS['Inbound'] = 'Shared company inbound email and SMS for people and agents; reads never consume messages.'
STABLE.extend([
    ('/api/v2/inbound/company-connection','post','Inbound','connectInboundCompany','A direct human owner or admin previews and connects Inbox to a portal company','InboundCompanyConnection'),
    ('/api/v2/inbound/inboxes','get','Inbound','listInboundInboxes','Company email and phone inboxes','InboundInboxes'),
    ('/api/v2/inbound/inboxes','post','Inbound','createInboundInbox','Create a free email inbox; SMS creation requires a phone approval request',None),
    ('/api/v2/inbound/inboxes/{ident}','patch','Inbound','editInboundInbox','Rename an inbox across connected interfaces and save local dependencies',None),
    ('/api/v2/inbound/inboxes/{ident}/audience','put','Inbound','setInboundInboxAudience','An owner or admin sets who reads an inbox: company, or team with group_id (that group and the groups under it)','InboundInbox'),
    ('/api/v2/inbound/inboxes/{ident}/release','post','Inbound','releaseInboundInbox','An admin retires email or cancels phone renewal',None),
    ('/api/v2/inbound/phone-quote','get','Inbound','quoteInboundPhone','A current monthly US mobile rental quote',None),
    ('/api/v2/inbound/phone-requests','get','Inbound','listInboundPhoneRequests','An owner or admin lists phone approval requests','InboundPhoneRequests'),
    ('/api/v2/inbound/phone-requests','post','Inbound','requestInboundPhone','An owner or admin requests a monthly mobile rental; no purchase',None),
    ('/api/v2/inbound/phone-requests/{ident}/accept','post','Inbound','acceptInboundPhoneQuote','An owner or admin accepts an exact quote for TICO review; no purchase',None),
    ('/api/v2/inbound/phone-requests/{ident}/cancel','post','Inbound','cancelInboundPhoneRequest','An owner or admin cancels a phone approval request',None),
    ('/api/v2/inbound/cursor','get','Inbound','getInboundCursor','Capture a cursor before requesting a code','InboundCursor'),
    ('/api/v2/inbound/messages','get','Inbound','listInboundMessages','Search one, many or all company inboxes','InboundMessages'),
    ('/api/v2/inbound/messages/{ident}','get','Inbound','readInboundMessage','Untrusted message text, up to 32 KB per page',None),
    ('/api/v2/inbound/messages/{ident}/code','get','Inbound','readInboundCode','Extracted code for a specific message',None),
    ('/api/v2/inbound/wait','post','Inbound','waitInboundMessages','Non-consuming wait after a cursor','InboundMessages'),
])
SCHEMAS.update({
    'InboundConnectionCompany': {**obj({'company_id':{'type':'string','format':'uuid'},'name':{'type':'string','minLength':1,'maxLength':100}}),'additionalProperties':False},
    'InboundCompanyConnection': {'oneOf':[
        {**obj({'connected':{'enum':[False]},'scope':{'enum':['inbox']},'company':{'$ref':'#/components/schemas/InboundConnectionCompany'},'expires_at':{'type':'number'}}),'additionalProperties':False},
        {**obj({'connected':{'enum':[True]},'scope':{'enum':['inbox']},'company':{'$ref':'#/components/schemas/InboundConnectionCompany'}}),'additionalProperties':False},
    ]},
    'InboundCursor': obj({'cursor':'i'}),
    'InboundInbox': obj({'inbox_id':'s','kind':'s','name':'s','address':'s','state':'s','audience':{'enum':['company','team']},
                         'group_id':{'type':['string','null']}}),
    'InboundInboxes': obj({'inboxes':items({'type':'object'}),'configured':'b','last_sync':NUM_N,'error':'s','gaps':items({'type':'object'})}),
    'InboundMessages': obj({'messages':items({'type':'object'}),'next':{'type':['integer','null']},'cursor':'i'}),
    'InboundPhoneRequests': obj({'phone_requests':items({'type':'object'}),'next':{'type':['string','null']}}),
})

TAGS['Triggers'] = 'Webhook triggers: an address any sender can post to, how its calls are verified, kept and run again.'
STABLE.extend([
    ('/api/v2/triggers','get','Triggers','listTriggers','Every trigger with its address (no secret), settings and routines',None),
    ('/api/v2/triggers','post','Triggers','createTrigger','Make a trigger (a person or a bot); the address with its secret is shown once',None),
    ('/api/v2/triggers/{trigger_id}','get','Triggers','getTrigger','One trigger',None),
    ('/api/v2/triggers/{trigger_id}','post','Triggers','updateTrigger','Change its name, preset, type source, hourly limit, keep days or unheard setting',None),
    ('/api/v2/triggers/{trigger_id}/rotate','post','Triggers','rotateTrigger','A new address secret; the old one stops at once',None),
    ('/api/v2/triggers/{trigger_id}/signing','post','Triggers','signTrigger','Verify calls with a preset (hmac, github, standard, stripe, slack, mercury) or stop; the signing secret is shown once',None),
    ('/api/v2/triggers/{trigger_id}/delete','post','Triggers','deleteTrigger','Delete a trigger and its kept calls',None),
    ('/api/v2/triggers/{trigger_id}/deliveries','get','Triggers','listTriggerDeliveries','The latest calls, newest first',None),
    ('/api/v2/triggers/{trigger_id}/deliveries/{delivery}','get','Triggers','getTriggerDelivery','One kept call: redacted headers and body (a bot reads fenced 20,000-character parts, only of a call it was given)',None),
    ('/api/v2/triggers/{trigger_id}/deliveries/{delivery}/replay','post','Triggers','replayTriggerDelivery','Run a kept call again as a new occurrence',None),
    ('/api/v2/triggers/{trigger_id}/recover','post','Triggers','recoverTrigger','Run again, under the current secret and preset, the calls refused in the last since_hours (1-720) whose body is kept; each keeps its delivery id',None),
])
TAGS['Connections'] = 'Every connection in one list: who reads what it brings in, its bots and where to configure it.'
STABLE.extend([
    ('/api/v2/connections','get','Connections','listConnections','Slack, GitHub, mail bots, Email & Phone inboxes, meeting '
     'importers, webhooks, physical mail and Listening: the owner and admins see every row, anyone else their own and the '
     'company\'s they may read. Paged with cursor','ConnectionList'),
])
SCHEMAS['Connection'] = obj({'id': 's', 'kind': {'enum': ['slack', 'github', 'mailbox', 'inbox', 'meetings', 'trigger',
                                                         'webhook', 'postal', 'listening']},
                             'name': 's', 'detail': 's', 'status': {'enum': ['on', 'off', 'waiting', 'error', 'paused']},
                             'scope': {'enum': ['company', 'team', 'person']}, 'readers': 's',
                             'bots': items(obj({'slug': 's', 'name': 's'})), 'more_bots': 'i', 'configure': 's',
                             'can_configure': 'b'},
                            required=['id', 'kind', 'name', 'status', 'scope', 'readers', 'bots', 'configure'])
SCHEMAS['ConnectionList'] = obj({'connections': items(ref('Connection')), 'next': 'n'})
TAGS['Webhook relay'] = 'Addresses for triggers through Tico Gateway: they keep deliveries while this server is off (backend/hook_relay.py).'
STABLE.extend([
    ('/api/v2/relay', 'get', 'Webhook relay', 'getWebhookRelay', 'The Gateway key, how many relay addresses exist, the last '
     'pull, the last problem and what waits at the Gateway', 'WebhookRelayState'),
    ('/api/v2/triggers/{trigger_id}/relay', 'post', 'Webhook relay', 'createWebhookRelay', 'Give a trigger an address through '
     'Tico Gateway; the answer carries the secret once (people who manage the trigger, and BotOps)', 'WebhookRelayAddress'),
    ('/api/v2/triggers/{trigger_id}/relay/rotate', 'post', 'Webhook relay', 'rotateWebhookRelay', 'Replace the Gateway '
     'address\'s secret; the old address stops at once and the new one is shown once', 'WebhookRelayAddress'),
    ('/api/v2/triggers/{trigger_id}/relay/wake', 'post', 'Webhook relay', 'setWebhookRelayWake', 'Say whether a delivery to '
     'the Gateway address starts a sleeping server (people who manage the trigger, and BotOps)', 'WebhookRelayWake'),
    ('/api/v2/triggers/{trigger_id}/relay/delete', 'post', 'Webhook relay', 'deleteWebhookRelay', 'Remove the Gateway address; '
     'the trigger and its own address stay', 'WebhookRelayDeleted'),
    ('/api/v2/hooks/wake-url', 'get', 'Webhook relay', 'getWebhookWakeUrl', 'The address Tico Gateway knocks on to start '
     'this server when a webhook waits for it, who set it and when (the owner)', 'WebhookWakeUrl'),
    ('/api/v2/hooks/wake-url', 'put', 'Webhook relay', 'setWebhookWakeUrl', 'Set the wake address (https) or turn it off '
     '(url null); the answer carries the signing secret once, for the receiver (the owner; needs the Gateway key)',
     'WebhookWakeUrlSet'),
])
TAGS['Sleep when idle'] = 'A server Tico hosts stops after quiet hours and starts again on a webhook, a routine or a sign-in (backend/sleep_idle.py).'
STABLE.extend([
    ('/api/v2/sleep', 'get', 'Sleep when idle', 'getSleepWhenIdle', 'Whether this server is hosted, the idle hours (0 = off), '
     'whether bot computers stop with it, since when it has been quiet and what Tico last answered', 'SleepWhenIdle'),
    ('/api/v2/sleep', 'post', 'Sleep when idle', 'setSleepWhenIdle', 'Turn Sleep when idle on (hours 1-24) or off (0) and choose '
     'whether bot computers stop with the server; an owner or an admin (and BotOps for one); on needs a server Tico hosts',
     'SleepWhenIdle'),
])
TAGS['Screening'] = 'Prompt-injection screening of outside text at the doors it comes in by (backend/screening.py).'
STABLE.extend([
    ('/api/v2/screening', 'get', 'Screening', 'getScreening', 'Each door\'s mode, the two detector switches, the defaults, '
     'whether a decision model answers and the newest holds and warnings, never their text (an owner or an admin)', None),
    ('/api/v2/screening', 'put', 'Screening', 'setScreening', 'Change door modes (off, warn, hold; mail off or warn) and the '
     'patterns and model switches; an owner or an admin', None),
    ('/api/v2/screening/test', 'post', 'Screening', 'testScreening', 'Run the built-in samples through the check; nothing is '
     'held or kept (an owner or an admin; each sample counts against their decision budget)', None),
])
TAGS['Notices'] = 'Notices from Tico Team: the ones that came through the Gateway relay and the current one from the release check.'
STABLE.append(('/api/v2/notices', 'get', 'Notices', 'listNotices', 'Notices from Tico Team, newest first, each once '
               '(any signed-in person)', 'NoticeList'))
SCHEMAS['NoticeList'] = obj({'notices': items(obj({'id': 's', 'kind': 's', 'body': 's', 'url': 's', 'sent_at': 's',
                                                    'received_at': 's', 'source': {'enum': ['relay', 'latest']}}))})
SCHEMAS.update({
    'SleepWhenIdle': obj({'sleep': obj({'hosted': 'b', 'layout': {'type': ['string', 'null']}, 'hours': 'i', 'minutes': 'i',
                                        'source': {'enum': ['cell', 'owner', 'default']}, 'managed': 'b', 'runners': 'b',
                                        'active': 'b', 'idle_since': {'type': ['string', 'null']}, 'blocker': 's',
                                        'wake_at': {'type': ['string', 'null']},
                                        'last_request': {'type': ['object', 'null']}, 'last_error': 's'})}),
    'WebhookRelayState': obj({'relay': obj({'key': 's', 'relays': 'i', 'cursor': 'i', 'last_pull': {'type': ['string', 'null']},
                                           'last_error': 's', 'pending': {'type': ['integer', 'null']},
                                           'oldest': {'type': ['string', 'null']}, 'deleting': 'i', 'active': 'b'})}),
    'WebhookRelayAddress': obj({'relay': obj({'trigger': 's', 'relay_id': 's', 'address_prefix': 's', 'address': 's',
                                             'replayed': 'b'})}),
    'WebhookRelayWake': obj({'relay': obj({'trigger': 's', 'relay_id': 's', 'wake': 'b'})}),
    'WebhookRelayDeleted': obj({'relay': obj({'trigger': 's', 'deleted': 'b', 'gateway_told': 'b'})}),
    'WebhookWakeUrl': obj({'url': {'type': ['string', 'null']}, 'set_at': {'type': ['string', 'null']},
                           'set_by': {'type': ['string', 'null']}}),
    'WebhookWakeUrlSet': obj({'url': {'type': ['string', 'null']}, 'secret': {'type': ['string', 'null']}}),
})
TAGS['Webhooks'] = "Outgoing webhooks: endpoints a person owns, the events Tico sends them, and each send's log (docs/webhooks.md)."
STABLE.extend([
    ('/api/v2/webhooks', 'get', 'Webhooks', 'listWebhooks', 'Your outgoing endpoints (an admin sees all) with 7-day '
     'delivered, failed and pending counts; paged', 'WebhookList'),
    ('/api/v2/webhooks', 'post', 'Webhooks', 'createWebhook', 'A person makes an endpoint; the secret is in the answer '
     'once. A bot files an approval and the answer says so', 'WebhookCreated'),
    ('/api/v2/webhooks/{hook_id}', 'get', 'Webhooks', 'getWebhook', 'One endpoint and its last ten attempts', 'WebhookShown'),
    ('/api/v2/webhooks/{hook_id}/update', 'post', 'Webhooks', 'updateWebhook', 'Change the name, address, events, '
     'filter, custom headers, hourly rate or (owner only) the plain-http switch', 'WebhookChanged'),
    ('/api/v2/webhooks/{hook_id}/rotate', 'post', 'Webhooks', 'rotateWebhookSecret', 'A new secret, shown once; the old '
     'one signs beside it for 24 hours', 'WebhookCreated'),
    ('/api/v2/webhooks/{hook_id}/pause', 'post', 'Webhooks', 'pauseWebhook', 'Stop sending; queued sends wait', 'WebhookChanged'),
    ('/api/v2/webhooks/{hook_id}/resume', 'post', 'Webhooks', 'resumeWebhook', 'Send again', 'WebhookChanged'),
    ('/api/v2/webhooks/{hook_id}/enable', 'post', 'Webhooks', 'enableWebhook', 'Turn an endpoint back on; after an automatic '
     'turn-off the answer offers `recover` with the date to send again from', 'WebhookEnabled'),
    ('/api/v2/webhooks/{hook_id}/recover', 'post', 'Webhooks', 'recoverWebhookSends', 'Send again every send that gave up '
     'since a date (same ids and bodies)', 'WebhookRecovered'),
    ('/api/v2/webhooks/{hook_id}/replay', 'post', 'Webhooks', 'replayWebhookEvents', 'Send the events since a date that '
     'the endpoint should have had and has no send for', 'WebhookReplayed'),
    ('/api/v2/webhooks/{hook_id}/delete', 'post', 'Webhooks', 'deleteWebhook', 'Delete the endpoint and cancel its '
     'pending sends', None),
    ('/api/v2/webhooks/{hook_id}/send', 'post', 'Webhooks', 'sendWebhook', 'A custom.<name> event to one endpoint: '
     'people, and bots with sending on', 'WebhookQueued'),
    ('/api/v2/webhooks/{hook_id}/example', 'post', 'Webhooks', 'sendWebhookExample', 'Send an event type\'s example '
     'to one endpoint', 'WebhookQueued'),
    ('/api/v2/webhooks/{hook_id}/sends', 'get', 'Webhooks', 'listWebhookSends', 'The send log, newest first: type, '
     'subject, state, attempts, last status, next attempt', 'WebhookSends'),
    ('/api/v2/webhooks/{hook_id}/sends/{send_id}', 'get', 'Webhooks', 'getWebhookSend', 'One send with its attempts', None),
    ('/api/v2/webhooks/{hook_id}/sends/{send_id}/retry', 'post', 'Webhooks', 'retryWebhookSend', 'Send a failed, '
     'cancelled or delivered send again now', None),
])
SCHEMAS.update({
    'Webhook': obj({'id': 's', 'name': 's', 'url': 's', 'host': 's', 'events': items({'type': 'string'}), 'enabled': 'b',
                    'paused_reason': 's', 'rate_per_hour': 'i', 'allow_http': 'b', 'created_by': 's',
                    'requested_by': 'n', 'created': 's', 'updated': 's', 'revision': 'i', 'old_secret_until': 'n',
                    'can_change': 'b', 'last_7_days': obj({'delivered': 'i', 'failed': 'i', 'pending': 'i'}),
                    'filter': {'type': 'object', 'description': 'departments, bots, task_types; an empty list means all'},
                    'headers': items({'type': 'object', 'description': 'name, secret, and value unless secret (then set: true)'}),
                    'disabled_at': 'n', 'disabled_reason': 'n'}),
    'WebhookSendRow': obj({'id': 's', 'webhook_id': 's', 'event_id': 's', 'type': 's', 'subject': 's', 'state': {
        'enum': ['ready', 'sending', 'delivered', 'failed', 'cancelled', 'withheld']}, 'attempts': 'i',
        'last_status': {'type': ['integer', 'null']}, 'last_error': 's', 'next_attempt': 'n', 'created': 's',
        'delivered_at': 'n'}),
})
SCHEMAS.update({
    'WebhookList': obj({'items': items(ref('Webhook')), 'more': 'b', 'next_offset': 'i'}, required=['items', 'more']),
    'WebhookCreated': obj({'webhook': {**ref('Webhook'), 'description': 'With `secret` once; `replayed: true` on a retry, without it'},
                           'approval': {'type': 'object', 'description': 'A bot\'s request: {id, status: pending}'}}, required=[]),
    'WebhookShown': obj({'webhook': ref('Webhook'), 'recent_attempts': items({'type': 'object'})}),
    'WebhookChanged': obj({'webhook': ref('Webhook')}),
    'WebhookEnabled': obj({'webhook': ref('Webhook'), 'recover': {'type': ['object', 'null'], 'description': '{since, failed} after an automatic turn-off'}}),
    'WebhookRecovered': obj({'webhook': 's', 'since': 's', 'recovered': 'i', 'more': 'b'}),
    'WebhookReplayed': obj({'webhook': 's', 'since': 's', 'checked': 'i', 'queued': 'i', 'more': 'b'}),
    'WebhookQueued': obj({'queued': 'i', 'type': 's'}),
    'WebhookSends': obj({'items': items(ref('WebhookSendRow')), 'more': 'b', 'next_offset': 'i'}, required=['items', 'more']),
})

# Pages and Library › Files (backend/pages.py, backend/file_library.py).
STABLE.extend([
    ("/api/v2/pages", "post", "Pages", "publishPage",
     "A bot publishes an .html page (raw with fields in the query, or JSON); scripts and outside loads are removed", "PagePublished"),
    ("/api/v2/pages", "get", "Pages", "listPages",
     "Pages the caller may read: `ids` (up to 50) in that order, leaving out the unreadable; else newest first by bot or task",
     "PageList"),
    ("/api/v2/pages/{fid}", "get", "Pages", "getPage", "One page and its status line; 404 when the caller may not read it",
     "PageShown"),
    ("/api/v2/pages/{fid}", "patch", "Pages", "editPage",
     "Change a page's cadence, data date or replacement, or archive or restore it (the bot, owner and bot managers)",
     "PageShown"),
    ("/api/v2/files", "get", "Files", "listFiles",
     "Library › Files: every file the caller may see, newest activity first, filtered by kind, bot, team, task, date, "
     "pinned or archived; paged and byte-capped, no totals", "FileLibrary"),
    ("/api/v2/files/search", "get", "Files", "searchFiles",
     "Search the files the caller may read by title and text, best first, each with an excerpt", "FileSearch"),
    ("/api/v2/files/{fid}", "delete", "Files", "deleteFile",
     "The owner deletes a file for good: every version's bytes leave storage; activity keeps who and when", "FileDeleted"),
])
SCHEMAS.update({
    "Page": obj({"file_id": "s", "title": "s", "bot": "s", "bot_name": "s", "version": "i", "updated": "s",
                 "updated_by": "n", "updated_by_name": "s",
                 "cadence": {"type": "string", "enum": ["none", "daily", "weekly", "monthly"]},
                 "as_of": {"type": ["string", "null"], "description": "The date the data is from (YYYY-MM-DD)"},
                 "stale_days": {"type": ["integer", "null"], "description": "Days since the last version once past "
                                "1.5x the cadence; null while current or without a cadence"},
                 "replaced_by": {"type": ["string", "null"], "description": "The newer page's file id, when the reader "
                                 "may see it"}, "replaced_by_title": "n", "archived": "b", "task_id": "n",
                 "src": {"type": ["string", "null"], "description": "Where to frame the page; null means open it "
                         "(GET /api/v2/files/{fid}/versions/{number})"}},
                required=["file_id", "title", "bot", "bot_name", "version", "updated", "updated_by", "cadence",
                          "as_of", "stale_days", "replaced_by", "archived", "task_id", "src"]),
    "PageShown": obj({"page": ref("Page"), "can_manage": "b", "can_delete": "b",
                      "pinned": {"type": "array", "description": "The places it is pinned that the caller may open",
                                 "items": obj({"place": "s", "label": "s"})}}, required=["page"]),
    "PagePublished": obj({"page": ref("Page"), "unembedded": items({"type": "string"}), "created": "b"},
                         required=["page", "unembedded"]),
    "PageList": obj({"pages": items(ref("Page")), "has_more": "b", "next_cursor": "n"}, required=["pages"]),
    "LibraryFile": obj({"id": "s", "bot": "s", "bot_name": "s", "title": "s", "kind": "s",
                        "library_kind": {"type": "string", "enum": ["page", "document", "image", "data", "media", "other"]},
                        "kind_label": "s", "version": "i", "last_activity_at": "s", "archived": "b",
                        "page": {"type": ["object", "null"], "description": "{cadence, archived} for a page"},
                        "pins": {"type": "array", "description": "Where the file is pinned, when pins exist"}},
                       required=["id", "bot", "title", "kind", "library_kind", "version", "last_activity_at"]),
    "FileLibrary": obj({"files": items(ref("LibraryFile")), "has_more": "b", "next_cursor": "n"}),
    "FileSearchHit": obj({"id": "s", "title": "s", "kind": "s", "library_kind": "s", "bot": "s", "bot_name": "s",
                          "version": "i", "updated": "s", "url": "n", "page": "b", "excerpt": "s", "score": "f"}),
    "FileSearch": obj({"results": items(ref("FileSearchHit"))}),
    "FileDeleted": obj({"deleted": "s", "versions": "i"}),
})

ANSWERS = SCHEMAS

# Documentation for the two sign-in routes whose bodies the handlers read by hand.
TOKEN_REQUEST = {"required": True, "content": {"application/json": {"schema": {
    "type": "object", "required": ["code", "code_verifier"], "additionalProperties": False,
    "properties": {"code": {"type": "string", "description": "From the URL fragment: #tico_code=..."},
                   "code_verifier": {"type": "string", "minLength": 43, "maxLength": 128,
                                     "description": "The PKCE verifier whose S256 hash was the code_challenge."}}}}}}

ERROR = {"description": "A problem: {\"error\": {\"code\", \"detail\", \"retryable\", ...}}",
         "content": {"application/json": {"schema": ref("Problem")}}}
PROBLEM = {"type": "object", "required": ["error"], "properties": {"error": {
    "type": "object", "required": ["code", "detail"], "additionalProperties": True,
    "properties": {"code": {"type": "string"}, "detail": {"type": "string"}, "retryable": {"type": "boolean"},
                   "sign_in": {"type": "string", "description": "On a 401 with built-in sign-in: the login path"}}}}}

TAGS['Cards and pins'] = ("Tico's built-in cards and where pages and cards are pinned: the Dashboard, team pages, bot "
                          "sidebars and tasks (docs/overview.md). A card shows each reader only what they may see.")
STABLE.extend([
    ('/api/v2/places/{place}', 'get', 'Cards and pins', 'getPlace', 'A place\'s pins in order (dashboard, team:<group>, '
     'bot:<slug>, task:<id>, person:me for My dashboard), each card with what it shows the caller; unreadable pages are '
     'left out', 'Place'),
    ('/api/v2/places/{place}', 'put', 'Cards and pins', 'savePlace', 'Save a Dashboard, team page or My dashboard (its '
     'default cards become pins); a team page\'s audience sets its group\'s Read', 'Place'),
    ('/api/v2/places/{place}/order', 'post', 'Cards and pins', 'orderPlace', 'Put every pin at a place in this order', 'Place'),
    ('/api/v2/pins', 'post', 'Cards and pins', 'addPin', 'Pin page:<file id> or card:<id> at a place: a bot its own, to '
     'its sidebar or task; a person who arranges the Dashboard or a team page there', 'Place'),
    ('/api/v2/pins/{pid}', 'patch', 'Cards and pins', 'updatePin', 'Resize or move a pin', 'Place'),
    ('/api/v2/pins/{pid}', 'delete', 'Cards and pins', 'removePin', 'Unpin; a card pinned nowhere else goes with it', None),
    ('/api/v2/cards', 'get', 'Cards and pins', 'listCards', 'Cards the caller made or sees pinned, with their data; '
     '`ids` reads up to 50', 'CardList'),
    ('/api/v2/cards', 'post', 'Cards and pins', 'addCard', 'A built-in card, pinned at `place` in the same write', None),
    ('/api/v2/cards/choices', 'get', 'Cards and pins', 'getCardChoices', 'Card templates and their settings, the teams, '
     'and the KPIs and pages the caller may pick', None),
    ('/api/v2/cards/{cid}', 'patch', 'Cards and pins', 'updateCard', 'Change a card\'s heading or settings', None),
    ('/api/v2/cards/{cid}', 'delete', 'Cards and pins', 'removeCard', 'Remove a card and every pin of it', None),
])
STABLE.extend([
    ('/api/v2/rules', 'get', 'Rules', 'listRules', 'The org rules, newest first, paged; mine=true adds the bot\'s settings as sentences', None),
    ('/api/v2/rules', 'post', 'Rules', 'addRule', 'Add an org rule: a bot\'s is a suggestion; only owners and admins add Everyone', None),
    ('/api/v2/rules/counts', 'get', 'Rules', 'getRulesCounts', 'Internal and external rules in force, due dates open, suggestions waiting', None),
    ('/api/v2/rules/read', 'post', 'Rules', 'readRuleDocument', 'Add a document for Counsel to read: a file you can read, or a public link (saved as a file version); Counsel suggests the rules in it', None),
    ('/api/v2/rules/ask', 'post', 'Rules', 'askRules', 'A can-I question: the active rules that share words with it, cited, and Counsel\'s answer on a task', None),
    ('/api/v2/rules/due', 'get', 'Rules', 'listRuleDue', 'Due dates of rules in force: overdue, then those falling due within next days', None),
    ('/api/v2/rules/{rule_id}', 'get', 'Rules', 'getRule', 'One rule, its due dates, and the quote it cites where its file is readable', None),
    ('/api/v2/rule-due/{due_id}', 'get', 'Rules', 'getRuleDue', 'One due date in full: its status, open occurrences, and its chain of edits (ended and removed rows included)', None),
    ('/api/v2/rules/{rule_id}', 'post', 'Rules', 'updateRule', 'Edit a rule against its version; a bot cannot edit', None),
    ('/api/v2/rules/{rule_id}/accept', 'post', 'Rules', 'acceptRule', 'Accept a suggestion, optionally changing its Who; a person only', None),
    ('/api/v2/rules/{rule_id}/skip', 'post', 'Rules', 'skipRule', 'Set a suggestion aside', None),
    ('/api/v2/rules/{rule_id}/retire', 'post', 'Rules', 'retireRule', 'Retire a rule in force; its due dates come back', None),
    ('/api/v2/rules/{rule_id}/due', 'post', 'Rules', 'addRuleDue', 'Add a due date to a rule: first day, repeat, owner, routine lead time', None),
    ('/api/v2/rule-due/{due_id}', 'post', 'Rules', 'updateRuleDue', 'Edit a due date; a change to first day or repeat starts a new row', None),
    ('/api/v2/rule-due/{due_id}/remove', 'post', 'Rules', 'removeRuleDue', 'Remove a due date; its history is kept', None),
    ('/api/v2/rule-due/{due_id}/satisfied', 'post', 'Rules', 'markRuleDueSatisfied', 'Mark the current occurrence satisfied; due_on when two are open', None),
    ('/api/v2/rule-sources', 'get', 'Rules', 'listRuleSources', 'Where external rules come from (From), by name', None),
    ('/api/v2/rule-sources', 'post', 'Rules', 'addRuleSource', 'Add a From; one that exists by name is returned', None),
    ('/api/v2/bots/{bot}/rules', 'get', 'Rules', 'getBotRules', 'A bot\'s Rules tab: its settings as sentences, then internal and external rules, each paged by its own cursor', None),
    ('/api/v2/rules/stats', 'get', 'Rules', 'getRuleCheckStats', 'Rule checks today: checks, unchecked, blocked, dollars left in the rules budget, and whether checks are on', None),
    ('/api/v2/rules/check', 'post', 'Rules', 'checkRuleDraft', 'A bot checks a draft as the write it will be; sending that exact draft then costs no second check', None),
    ('/api/v2/rule-holds', 'get', 'Rules', 'listRuleHolds', 'The held writes the caller may read, as summaries, a page at a time: a review task\'s holds (task), a conversation\'s held writes (thread), or every held write the caller may read', None),
    ('/api/v2/rule-holds/{hold_id}', 'get', 'Rules', 'getRuleHold', 'One held output as its reader sees it: the author reads the writes they made, a person or a bot; the reviewer and the person a bot reports to read the rest', None),
    ('/api/v2/rule-holds/{hold_id}/keep', 'post', 'Rules', 'keepRuleHold', 'The reviewer keeps it blocked', None),
    ('/api/v2/rule-holds/{hold_id}/withdraw', 'post', 'Rules', 'withdrawRuleHold', 'The author takes a held write back to edit and resend it, or the person a bot reports to withdraws its post; nothing is sent', None),
    ('/api/v2/rule-holds/{hold_id}/send', 'post', 'Rules', 'sendRuleHold', 'The reviewer sends it anyway, once, with the hold\'s nonce; for a person\'s write that is the rule\'s author, its editors or an admin', None),
    ('/api/v2/rule-holds/{hold_id}/appeal', 'post', 'Rules', 'appealRuleHold', 'The author bot asks the reviewer to look, with why', None),
])
SCHEMAS['Card'] = obj({'id': 'n', 'template': {'enum': ['tasks_by_status', 'done_per_week', 'needs_you', 'spend', 'kpi',
                                                        'automation_runs', 'latest_pages', 'needs_attention',
                                                        'unread_threads', 'my_tasks', 'updates']},
                       'title': 's', 'heading': 's', 'name': 's', 'team_name': 's', 'settings': 'o', 'hidden': 'b',
                       'data': {'type': ['object', 'null'], 'description': 'What the card shows the caller; null when hidden'}},
                      required=['id', 'template', 'heading', 'settings', 'hidden', 'data'])
SCHEMAS['CardList'] = obj({'cards': items(ref('Card')), 'next': 'n'})
SCHEMAS['Place'] = obj({'place': 's', 'kind': {'enum': ['dashboard', 'team', 'bot', 'task', 'person']}, 'saved': 'b', 'can_edit': 'b',
                        'pins': items(obj({'id': 'n', 'item': 'n', 'position': 'i', 'size': {'enum': ['small', 'medium', 'large']},
                                           'card': ref('Card'),
                                           'page': obj({'file_id': 's', 'title': 's', 'bot': 's', 'updated': 's', 'archived': 'b'})},
                                          required=['id', 'item', 'position', 'size'])),
                        'audience': {'type': 'object', 'description': 'A team page: who sees it (docs/permissions.md)'},
                        'name': 's', 'group': 's'}, required=['place', 'kind', 'saved', 'can_edit', 'pins'])

# Settings > Models: the company's subscriptions (backend/model_subscriptions.py). Values are never in an answer.
TAGS['Model subscriptions'] = ("The company's saved model logins and keys, given to computers per runtime. A value is "
                               "sent only to add or reconnect one; no answer carries it.")
STABLE.extend([
    ('/api/v2/model-subscriptions', 'get', 'Model subscriptions', 'listModelSubscriptions', 'Saved subscriptions and the kinds that can be added', 'ModelSubscriptionList'),
    ('/api/v2/model-subscriptions', 'post', 'Model subscriptions', 'addModelSubscription', 'Add a subscription: a kind, a name and the pasted value (owners and admins)', 'ModelSubscription'),
    ('/api/v2/model-subscriptions/{sid}', 'post', 'Model subscriptions', 'changeModelSubscription', 'Rename a subscription and/or reconnect it with a new value (owners and admins); a reconnect answers 409 `confirm_restart` while runs would stop unless `confirm` is true', 'ModelSubscription'),
    ('/api/v2/model-subscriptions/{sid}', 'delete', 'Model subscriptions', 'removeModelSubscription', 'Remove a subscription and take it off every computer; 409 `confirm_restart` while runs would stop unless `confirm=1`', 'ModelSubscriptionDeleted'),
    ('/api/v2/computer-subscriptions', 'get', 'Model subscriptions', 'listComputerSubscriptions', 'Which subscription each computer uses per runtime, and the computers that can take one', 'ComputerSubscriptionList'),
    ('/api/v2/computer-subscriptions', 'put', 'Model subscriptions', 'setComputerSubscription', 'Give a computer a subscription for one runtime, switch it, or clear it (null); 409 `confirm_restart` while live runs would stop unless `confirm` is true', 'ComputerSubscriptionResult'),
])
_SUB_KIND = {"enum": ["claude_token", "codex_login", "anthropic_key", "openai_key"]}
SCHEMAS['ModelSubscription'] = obj({
    'id': 's', 'name': 's', 'kind': _SUB_KIND, 'runtime': {'enum': ['claude', 'codex']}, 'label': 's',
    'status': {'enum': ['new', 'working', 'needs_reconnect']}, 'status_detail': 's', 'status_at': 'n', 'preview': 's',
    'revision': 'i', 'computers': items({'type': 'string'}),
    'usage': {'anyOf': [obj({'window': {'type': ['string', 'null'], 'enum': ['5h', 'week', None]}, 'used_pct': NUM_N,
                             'cost_usd_month': NUM_N, 'resets_at': 'n'}, required=['window', 'used_pct', 'cost_usd_month']),
                        {'type': 'null'}]},
    'added_by': 's', 'added_by_name': 's', 'created': 's', 'updated': 's'},
    required=['id', 'name', 'kind', 'runtime', 'label', 'status', 'status_detail', 'status_at', 'preview', 'revision',
              'computers', 'usage', 'added_by', 'added_by_name', 'created', 'updated'])
SCHEMAS['ModelSubscriptionList'] = obj({
    'subscriptions': items(ref('ModelSubscription')),
    'kinds': items(obj({'kind': _SUB_KIND, 'label': 's', 'runtime': {'enum': ['claude', 'codex']}, 'secret_label': 's', 'help': 's'}))})
SCHEMAS['ModelSubscriptionDeleted'] = obj({'deleted': 'b', 'id': 's', 'computers': 'i', 'stopped': 'i', 'sessions_reset': 'i'})
SCHEMAS['ComputerSubscriptionList'] = obj({
    'assignments': items(obj({'runner_id': 's', 'runtime': {'enum': ['claude', 'codex']}, 'subscription_id': 's'})),
    'capable': items({'type': 'string'})})
SCHEMAS['ComputerSubscriptionResult'] = obj({
    'runner_id': 's', 'runtime': {'enum': ['claude', 'codex']}, 'subscription_id': 'n', 'stopped': 'i', 'sessions_reset': 'i'})

TAGS['Slack links'] = ("A thread's link to a Slack channel, both ways (docs/slack.md): asked for by an admin, checked by the "
                       "gateway, then active, suspended or refused. Admins only.")
STABLE.extend([
    ('/api/v2/slack/links', 'get', 'Slack links', 'listSlackLinks', 'The Slack links (of one thread with `thread`), newest first, '
     'up to `limit` (at most 100) a page; `next_cursor` reads the next page; unlinked ones are left out', 'SlackLinkList'),
    ('/api/v2/slack/links', 'post', 'Slack links', 'requestSlackLink', 'Ask for a thread to be linked to a Slack channel '
     '(`#name` or its id): the gateway checks it and activates or refuses it later; `history` also brings in its earlier '
     'messages', 'SlackLink'),
    ('/api/v2/slack/links/{link_id}', 'delete', 'Slack links', 'unlinkSlackLink', 'Stop a Slack link: nothing more is sent '
     'to the channel after this; answers the link, now unlinked', 'SlackLink'),
    ('/api/v2/slack/links/{link_id}/resume', 'post', 'Slack links', 'resumeSlackLink', 'Check a suspended or refused link '
     'again; any other state answers 409', 'SlackLink'),
])
SCHEMAS['SlackLink'] = obj({
    'id': 's', 'thread_id': 's', 'channel': obj({'id': 's', 'name': 's', 'private': 'b'}),
    'audience': 's', 'state': {'enum': ['requested', 'active', 'suspended', 'refused', 'unlinked']}, 'reason': 's',
    'history': obj({'state': {'enum': ['off', 'pending', 'running', 'done', 'failed']}, 'count': 'i'}),
    'linked_by': 's', 'created_at': 's', 'updated_at': 's'})
SCHEMAS['SlackLinkList'] = obj({'links': items(ref('SlackLink')), 'next_cursor': 'n'})

# Independent reviewer cards: task closure never changes this audience or lifetime.
TAGS['Memory tidy'] = "Nightly memory tidy proposals, visible only to the named reviewer, independently of pass tasks."
STABLE.extend([
    ('/api/v2/memory-tidy/cards', 'get', 'Memory tidy', 'listMemoryTidyCards',
     'The named reviewer’s open cards; diffs are fetched on opening a card. Human and bot reviewers.', 'MemoryTidyCards'),
    ('/api/v2/memory-tidy/cards/{ident}', 'get', 'Memory tidy', 'getMemoryTidyCard',
     'A named reviewer’s card with its checked diff, including after both pass tasks close.', 'MemoryTidyCardResult'),
    ('/api/v2/memory-tidy/cards/{ident}/decide', 'post', 'Memory tidy', 'decideMemoryTidyCard',
     'Discard an open card without repository changes. Keep returns 409 until its separate implementation is available.', 'MemoryTidyCardResult'),
])
SCHEMAS['MemoryTidyCard'] = obj({
    'id': 's', 'kind': {'enum': ['memory_tidy']}, 'bot': 's', 'reviewer': 's', 'requester': 's', 'title': 's',
    'memory_commit': 's', 'model': 's', 'cost_usd': {'type': 'number', 'description': 'Filing-ledger list-price estimate, including subscription usage.'},
    'diary': 's', 'diff': {'type': 'string', 'description': 'Checked unified diff; present on detail reads and decisions, omitted from lists.'},
    'created': 's', 'expires': 's', 'status': {'enum': ['open', 'withdrawn', 'discarded', 'kept']}, 'reason': 's', 'keep_available': 'b',
}, required=['id', 'kind', 'bot', 'reviewer', 'requester', 'title', 'memory_commit', 'model', 'cost_usd', 'diary',
             'created', 'expires', 'status', 'reason', 'keep_available'])
SCHEMAS['MemoryTidyCards'] = obj({'cards': items(ref('MemoryTidyCard'))})
SCHEMAS['MemoryTidyCardResult'] = obj({'card': ref('MemoryTidyCard')})

DESCRIPTION = (
    "The stable API for building your own frontend on Tico. Everything here keeps its shape within v2: fields are added, "
    "never removed or renamed, and a breaking change is a new /api/v3. Routes not listed are internal. "
    "Public roadmap writes require an allowlisted Origin; reads permit server-side requests. No sign-in or idempotency key (docs/roadmaps.md). "
    "Granola connection and sync writes do not require an idempotency key, nor does POST /api/v2/inbound/tasks, whose "
    "own `key` is its idempotency. Inbox company connection actions reconcile the same short-lived code under current grant checks. "
    "Other writes need an `Idempotency-Key` header (1-200 characters; a retry with the same "
    "key and body returns the first answer). Errors are `{\"error\": {code, detail, retryable}}`. See docs/custom-frontend.md.")


def _refs(node, found):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/components/schemas/"):
                found.add(value.rsplit("/", 1)[1])
            else:
                _refs(value, found)
    elif isinstance(node, list):
        for value in node:
            _refs(value, found)


TAGS['Federation'] = ("Shared threads between Tico installs through Tico HQ: this install's key, peerings (both installs "
                      "approve, and a pause stops one) and the threads it shares (docs/federation.md).")
STABLE.extend([
    ('/api/v2/federation', 'get', 'Federation', 'getFederation', "This install's key id, Gateway, one page of peerings and shared threads with cursors (no key material)", None),
    ('/api/v2/federation/keys', 'post', 'Federation', 'createFederationKey', "Make this install's federation key and register it with Tico HQ", None),
    ('/api/v2/federation/keys/rotate', 'post', 'Federation', 'rotateFederationKey', 'A new key; the old one is retired and every peering needs approving again', None),
    ('/api/v2/federation/peerings', 'post', 'Federation', 'requestFederationPeering', 'Ask another install to peer; an Idempotency-Key is required', None),
    ('/api/v2/federation/peerings/{peering_id}/approve', 'post', 'Federation', 'approveFederationPeering', "Approve a peering after checking the other install's key; active once both approve", None),
    ('/api/v2/federation/peerings/{peering_id}/pause', 'post', 'Federation', 'setFederationPeeringPause', 'The kill switch: while paused nothing crosses the peering in either direction', None),
    ('/api/v2/federation/peerings/{peering_id}/revoke', 'post', 'Federation', 'revokeFederationPeering', 'End a peering; a new request starts over', None),
    ('/api/v2/federation/threads/{thread_id}/installs', 'post', 'Federation', 'shareFederationThread', 'Share a thread this install owns with an active peer; it gets the thread from now on', None),
    ('/api/v2/federation/threads/{thread_id}/installs/{install_id}', 'delete', 'Federation', 'unshareFederationThread', 'Remove an install from a thread this install owns; it gets the removal and nothing after', None),
    ('/api/v2/federation/threads/{thread_id}/readers', 'post', 'Federation', 'addFederationThreadReader', 'Let a local person read a shared thread copy on this install; only the owner adds readers', None),
])
# Threads (docs/dev/threads-contract.md §10): a thread is a conversation read through threads.view, and its messages
# carry thread_id, root_id-free fields as _message_out writes them.
TAGS["Threads"] = ("Threads: the sidebar, a thread and its timeline, read marks, search, members, follow, and the messages "
                   "in a thread. A thread the viewer may not read answers 404, never 403.")
SCHEMAS.update({
    "ThreadView": obj({"id": "s", "title": "s",
                       "audience": {"type": "string", "enum": ["company", "team", "members", "task", "home", "personal",
                                                               "participants", "meeting"]},
                       "audience_group_id": "n", "owner_actor": "n",
                       "parent_id": "n", "parent_message_id": "n", "root_id": "s", "path": "a", "kind": "n",
                       "task_id": "n", "created_at": "s", "last_message_at": "n", "archived_at": "n",
                       "last_seq": "i", "last_unread_seq": "i", "me": "o", "members": "o",
                       "retention_days": {"type": ["integer", "null"],
                                          "description": "Days a root keeps its messages; null keeps them for good"},
                       "home": {"type": ["object", "null"], "description": "Only for audience home: {actor}"}},
                      required=["id", "title", "audience", "root_id", "last_seq", "last_unread_seq"]),
    "ThreadReactions": items(obj({"emoji": "s", "count": "i", "actors": "a"}, required=["emoji", "count", "actors"])),
    "ThreadEarlier": obj({"home": "s", "messages": "i"}, required=["home", "messages"]),
    "ThreadUploaded": obj({"thread": ref("ThreadView"), "message": ref("ThreadMessage")},
                          required=["thread", "message"]),
    "ThreadSidebar": obj({"attention": items({"type": "object"}), "threads": items(ref("ThreadView")),
                          "dms": items({"type": "object"}), "next_cursor": "n"},
                         required=["attention", "threads", "dms"]),
    "ThreadSearch": obj({"hits": items(obj({"message_id": "s", "thread_id": "s", "title": "s", "from_actor": "s",
                                            "created": "s", "excerpt": "s"},
                                           required=["message_id", "thread_id", "excerpt"])),
                         "next_cursor": "n"}, required=["hits", "next_cursor"]),
    "ThreadMessage": obj({"id": "s", "conversation_id": "s", "thread_id": "s", "seq": "i", "from_actor": "s",
                          "to_actor": "n", "kind": "s", "body": "n", "created": "s", "edited_at": "n",
                          "revision": "i", "refs": "o", "deleted": "b", "root_id": "n", "origin_id": "n",
                          "parent_message_id": "n", "depth": "i", "hidden": "b",
                          "sub_thread": {"type": ["object", "null"], "description": "The sub-thread branching from this "
                                         "message: {id, count, last_seq, unread, more}"}},
                         required=["id", "seq", "thread_id"]),
    "ThreadCrumb": obj({"id": "s", "title": "s"}),
    "ThreadTimeline": obj({"thread": ref("ThreadView"), "breadcrumb": items(ref("ThreadCrumb")),
                           "messages": items({"type": "object"}), "next_cursor": "n"},
                          required=["thread", "messages"]),
    "ThreadMembers": obj({"members": items(obj({"actor": "s", "role": "s", "added_by": "n", "created_at": "s"},
                                               required=["actor", "role"])),
                          "next_cursor": "n"}, required=["members"]),
    "ThreadMemberChange": obj({"thread_id": "s", "actor": "s", "role": "s", "removed": "b"},
                              required=["thread_id", "actor"]),
    "ThreadRead": obj({"thread_id": "s", "last_read_seq": "i"}, required=["thread_id", "last_read_seq"]),
    "ThreadReadAll": obj({"threads": "i"}, required=["threads"]),
    "ThreadFollow": obj({"thread_id": "s", "following": "b", "notify": "s"}, required=["thread_id", "following"]),
    "ThreadMessageDeleted": obj({"id": "s", "deleted": "b", "revision": "i"}, required=["id", "deleted"]),
})
STABLE.extend([
    ("/api/v2/threads", "get", "Threads", "listThreads",
     "The viewer's sidebar, threads and DMs they may read: needs attention, then unread, then recent, one page "
     "(`cursor`); a root's row sums its sub-threads for the viewer; with "
     "`with=<actor>`, only the threads that teammate is a member of", "ThreadSidebar"),
    ("/api/v2/threads", "post", "Threads", "createThread",
     "Start a thread (audience members unless asked otherwise); with `parent_message_id` it is a sub-thread; with `body` "
     "its first message is posted too", "ThreadView"),
    ("/api/v2/threads/search", "get", "Threads", "searchThreads",
     "Messages whose text matches every word of `q`, newest first, only in threads and messages the viewer may read", "ThreadSearch"),
    ("/api/v2/threads/home/{actor}", "get", "Threads", "getThreadHome",
     "A teammate's Home (`actor` in the path: bot:<slug>, human:<id> or group:<id>), made on first use; needs Read on "
     "that teammate, else the same 404 whether or not they exist", "ThreadView"),
    ("/api/v2/threads/dm", "get", "Threads", "openThreadDm",
     "My DM with `with`: the open one with exactly those two, or a new one; needs Write on them (the reply-back rule "
     "applies)", "ThreadView"),
    ("/api/v2/threads/dm", "post", "Threads", "createThreadDm",
     "A DM with these teammates (`members`), the open one or a new one, with an optional first message (`body`); needs "
     "Write on each of them, and a bot's or person's first message passes the org rules", "ThreadView"),
    ("/api/v2/threads/{thread_id}", "get", "Threads", "getThread",
     "A thread as the viewer sees it: its unread count, read mark, what they may do and its first page of members",
     "ThreadView"),
    ("/api/v2/threads/{thread_id}", "patch", "Threads", "updateThread",
     "Rename, archive or unarchive a thread, change who it is for, or set how many days a root keeps its messages (`retention_days`, null for good); its managers only", "ThreadView"),
    ("/api/v2/threads/{thread_id}/timeline", "get", "Threads", "getThreadTimeline",
     "The thread in tree order, a page of its own messages at a time (`cursor` is the seq from next_cursor), each followed by its sub-thread's messages; a long branch has a `collapsed` row with the cursor that reads the rest", "ThreadTimeline"),
    ("/api/v2/threads/{thread_id}/members", "get", "Threads", "listThreadMembers",
     "Who a thread is for, a page at a time", "ThreadMembers"),
    ("/api/v2/threads/{thread_id}/members", "post", "Threads", "addThreadMember",
     "Add a member to a thread (its managers); the new member reads up to now, so the backfill is not unread", "ThreadMemberChange"),
    ("/api/v2/threads/{thread_id}/members/{actor}", "delete", "Threads", "removeThreadMember",
     "Remove a member from a thread (its managers)", "ThreadMemberChange"),
    ("/api/v2/threads/{thread_id}/messages", "post", "Threads", "postThreadMessage",
     "Post a message in a thread, or in a sub-thread with `parent_message_id`; it passes the org rules first", "ThreadMessage"),
    ("/api/v2/threads/{thread_id}/read", "post", "Threads", "markThreadRead",
     "Read up to `seq` in the thread; the highest mark wins, so a device that is ahead keeps its place", "ThreadRead"),
    ("/api/v2/threads/{thread_id}/read-all", "post", "Threads", "markThreadAndSubthreadsRead",
     "Read up to now in the thread and every sub-thread under it", "ThreadReadAll"),
    ("/api/v2/threads/{thread_id}/follow", "put", "Threads", "followThread",
     "Follow a thread so its messages reach the viewer (`notify`: live or digest)", "ThreadFollow"),
    ("/api/v2/threads/{thread_id}/follow", "delete", "Threads", "unfollowThread",
     "Stop following a thread", "ThreadFollow"),
    ("/api/v2/messages/{message_id}", "patch", "Threads", "editMessage",
     "Edit one's own message in a thread; the search index and the timeline follow", "ThreadMessage"),
    ("/api/v2/messages/{message_id}", "delete", "Threads", "deleteMessage",
     "Delete one's own message in a thread", "ThreadMessageDeleted"),
    ("/api/v2/messages/{message_id}/reactions/{emoji}", "put", "Threads", "reactToMessage",
     "Add my reaction to a message I read (the same emoji twice is one); answers the message's reactions", "ThreadReactions"),
    ("/api/v2/messages/{message_id}/reactions/{emoji}", "delete", "Threads", "unreactToMessage",
     "Remove my reaction from a message (a no-op without one); answers the message's reactions", "ThreadReactions"),
    ("/api/v2/threads/{thread_id}/open-earlier", "post", "Threads", "openEarlierMessages",
     "A manager of a bot opens its Home's earlier shared-room messages to the Home's readers; a bot's Home only (422 "
     "otherwise), managers only (403)", "ThreadEarlier"),
    ("/api/v2/uploads/threads/{thread_id}/messages", "post", "Threads", "uploadThreadMessage",
     "A person's message with files in a thread (multipart: text, titles and the files); each file is linked to the "
     "message, under the chat upload limit", "ThreadUploaded"),
    ("/api/v2/threads/messages/{message_id}/task", "post", "Threads", "makeTaskFromMessage",
     "A task from a message I read: the server copies its text and a link back, and the task is private unless the "
     "thread is company-wide", "TaskResult"),
])
# Tico Meet calls (docs/meet.md): a call belongs to a thread. Joining hands back a token for audio and screen share only.
STABLE.extend([
    ("/api/v2/threads/{thread_id}/calls", "get", "Calls", "listCalls",
     "The calls in a thread, newest first, a page at a time: before=<next_before> reads the next page", "CallList"),
    ("/api/v2/threads/{thread_id}/calls", "post", "Calls", "startCall", "Start a call in a thread you can write to", "Call"),
    ("/api/v2/calls/{call_id}", "get", "Calls", "getCall", "A call: its state, people, bots and how it ended", "Call"),
    ("/api/v2/calls/{call_id}/join", "post", "Calls", "joinCall", "Join a call: its media URL and a token good for 5 minutes",
     "CallJoin"),
    ("/api/v2/calls/{call_id}/token", "post", "Calls", "refreshCallToken", "A fresh token for a call you have joined", "CallToken"),
    ("/api/v2/calls/{call_id}/leave", "post", "Calls", "leaveCall", "Leave a call you joined", "Call"),
    ("/api/v2/calls/{call_id}/end", "post", "Calls", "endCall", "End a call you started; its transcript is posted to the thread",
     "Call"),
    ("/api/v2/calls/{call_id}/action-items/retry", "post", "Calls", "retryCallActionItems",
     "Ask again for a call's action items the Gateway could not make; the sweep makes them", "Call"),
    ("/api/v2/calls/{call_id}/bots", "post", "Calls", "attachCallBots", "Let bots listen to a call and reply in its thread", "Call"),
    ("/api/v2/calls/{call_id}/bots/{bot}", "delete", "Calls", "detachCallBot", "Take a bot out of a call", "Call"),
])
SCHEMAS.update({
    "CallParticipant": obj({"actor": "s", "joined_at": "s", "left_at": "n", "removed_at": "n"}),
    "CallHold": obj({"kind": {"enum": ["transcript", "action_items"]}, "reason": "s", "detail": "s",
                     "created_at": "s"}),
    "CallSummary": obj({"id": "s", "thread_id": "s", "title": "s", "state": {"enum": ["live", "ended"]}, "connector": "s",
                        "started_at": "s", "ended_at": "n", "end_reason": "n"}),
    "Call": obj({"id": "s", "thread_id": "s", "title": "s", "state": {"enum": ["live", "ended"]}, "connector": "s",
                 "meeting_id": "s", "started_at": "s", "ended_at": "n", "end_reason": "n",
                 "participants": items(ref("CallParticipant")), "bots": items({"type": "string"})},
                holds={"type": "array", "items": ref("CallHold"),
                       "description": "Posts the thread refused at the call's end. Only the connector sees them"},
                speak_replies={"type": "boolean", "description": "Whether bots were asked to speak their replies "
                               "on this call; off unless the call asked. Spoken replies are not sent yet"}),
    "CallList": obj({"calls": items(ref("CallSummary")), "next_before": "n"}),
    "CallJoin": obj({"call": ref("Call"), "url": "s", "expires_in": "i", "token": "s"}),
    "CallToken": obj({"url": "s", "expires_in": "i", "token": "s"}),
})


def spec(app):
    """The v2 document for this app: the stable operations of `app.openapi()`, described."""
    full = copy.deepcopy(app.openapi())
    paths = {}
    for path, method, tag, op_id, summary, answer in STABLE:
        op = (full["paths"].get(path) or {}).get(method)
        if op is None:
            raise RuntimeError("the stable API lists %s %s, which the server does not serve" % (method.upper(), path))
        op["tags"], op["operationId"], op["summary"] = [tag], op_id, summary
        op.pop("description", None)
        responses = {code: r for code, r in op.get("responses", {}).items() if code != "422"}
        if answer:
            responses["200"] = {"description": "OK", "content": {"application/json": {"schema": ref(answer)}}}
        if path == "/api/v2/events":
            responses["200"] = {"description": EVENTS_DESCRIPTION,
                                "content": {"text/event-stream": {"schema": {"type": "string"}}}}
        if method == "get" and path.startswith("/api/v2/files/") and path.endswith("/versions/{number}"):
            responses["200"] = {"description": "The file's bytes (application/octet-stream, sent as an attachment)",
                                "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}
        if method == "get" and path.startswith("/api/v2/finance/tax/") and path.endswith(".csv"):
            responses["200"] = {"description": "The workpaper as CSV (text/csv, sent as an attachment)",
                                "content": {"text/csv": {"schema": {"type": "string"}}}}
        if path == "/auth/login":
            responses = {"302": {"description": "Redirect to the identity provider"},
                         "400": {"description": "next is not an allowed origin, or code_challenge is missing"}}
        if not path.startswith("/public/roadmaps/") and path not in ("/healthz", "/auth/login", "/auth/token"):
            responses["401"] = {"description": "Not signed in", **ERROR}
            responses["422"] = {"description": "Invalid request or rule: error.code and field-specific error.detail", **ERROR}
        if path == "/auth/token":
            op["requestBody"] = TOKEN_REQUEST
            op["security"] = []
            responses["400"] = {"description": "invalid_grant, or the request is malformed", **ERROR}
        connection = path == '/api/v2/inbound/company-connection'
        if connection:
            op['requestBody'] = {'required':True,'content':{'application/json':{'schema':{
                'type':'object','additionalProperties':False,'required':['action','code'],'properties':{
                    'action':{'type':'string','enum':['preview','connect']},
                    'code':{'type':'string','minLength':43,'maxLength':43,'pattern':'^[A-Za-z0-9_-]{43}$','writeOnly':True},
                }}}}}
            op['description'] = ('Direct signed-in human owner/admin only; no personal tokens, support or delegated agents. '
                'Preview shows the target company before explicit consent to share Inbox in both directions. '
                'The server supplies its private key and existing reported installation identity; neither belongs in this body. '
                'Requests are limited to 2 KiB; code validation errors never echo supplied values. '
                'Disconnect in the portal stops future access, preserving merged history and existing local copies.')
            for code,detail in [('403','Direct human management or portal authorization required'),
                                ('409','Missing saved identity, expired code or conflicting connection'),
                                ('503','Inbox connection unavailable; reconcile the same code in its original lifetime')]:
                responses[code] = {**ERROR,'description':detail}
        if path in ("/healthz", "/auth/login"):
            op["security"] = []
        if path.startswith('/public/roadmaps/'):
            op['security'] = []
            op.setdefault('parameters', []).append({'name': 'Origin', 'in': 'header', 'required': method not in ('get', 'head'),
                'schema': {'type': 'string'}, 'description': 'An exact origin in the company roadmap allowlist.'})
            for code, detail in [('403', 'Origin not allowed'), ('404', 'Roadmap or task not public'),
                                 ('422', 'Invalid request'), ('429', 'Daily IP action limit reached')]:
                responses[code] = {**ERROR, 'description': detail}
        op["responses"] = dict(sorted(responses.items()))
        inbound = path == "/api/v2/inbound/tasks"
        granola = path.startswith("/api/v2/meetings/granola")
        if inbound:
            op["security"] = [{"serviceKey": []}]
        if method in ("post", "patch", "delete") and path.startswith("/api/v2/"):
            parameters = op.setdefault("parameters", [])
            parameters[:] = [p for p in parameters if (p.get("name"), p.get("in")) != ("Idempotency-Key", "header")]
            parameters.append({
                "name": "Idempotency-Key", "in": "header", "required": not (inbound or granola or connection), "schema": {"type": "string"},
                "description": ("Not needed: the pair (service key, `key`) makes a repeated call harmless, and the header "
                                "is ignored." if inbound else
                                'Optional. Retry the same action and code; HQ rechecks the current grant, role, session and original expiry without readoption.' if connection else
                                "Optional. Sync uses a two-minute debounce; connecting starts a new device sign-in."
                                if granola else
                                "1-200 characters. Reusing a key with the same body replays the first answer.")})
        if method == "post" and path.startswith("/api/v2/calls/") and path.endswith("/token"):
            # A refresh writes nothing, so the key is optional and ignored.
            for param in op["parameters"]:
                if param.get("name") == "Idempotency-Key":
                    param["required"] = False
                    param["description"] = "Not needed: a refresh writes nothing; the header is ignored."
        paths.setdefault(path, {})[method] = op
    used = set()
    _refs(paths, used)
    request_schemas = full.get("components", {}).get("schemas", {})
    collisions = SCHEMAS.keys() & request_schemas.keys()
    if collisions:
        raise RuntimeError("Request and response schema names collide: " + ", ".join(sorted(collisions)))
    components = {"Problem": PROBLEM, **SCHEMAS, **request_schemas}
    keep, queue = {}, sorted(used | {"Problem"})
    while queue:
        name = queue.pop()
        if name in keep or name not in components:
            continue
        keep[name] = components[name]
        more = set()
        _refs(components[name], more)
        queue.extend(sorted(more))
    return {
        "openapi": full["openapi"],
        "info": {"title": "Tico API (v2)", "version": VERSION, "description": DESCRIPTION},
        "tags": [{"name": name, "description": text} for name, text in TAGS.items()],
        "paths": dict(sorted(paths.items())),
        "components": {
            "schemas": dict(sorted(keep.items())),
            "securitySchemes": {
                "bearer": {"type": "http", "scheme": "bearer",
                           "description": "A bearer session from POST /auth/token (browser apps) or a personal API token (servers)."},
                "serviceKey": {"type": "http", "scheme": "bearer",
                               "description": "A service key, tico_sk_...: a tasks key reaches POST /api/v2/inbound/tasks and nothing else "
                                              "(an update key reaches only /api/v2/system/update, docs/service-keys.md)."},
                "cookie": {"type": "apiKey", "in": "cookie", "name": "tico_session",
                           "description": "The browser session of Tico's own page (`__Host-tico_session` over https)."}}},
        "security": [{"bearer": []}, {"cookie": []}],
    }


# Cloud agents (backend/cloud_agents.py): a seat asks Tico's team to connect an agent it does not connect yet.
STABLE.extend([
    ("/api/v2/cloud-agents", "get", "Team chart", "listCloudAgents",
     "The cloud coding agents: the supported ones are added to the team, the others are requested from Tico", "CloudAgentList"),
    ("/api/v2/cloud-agents/{agent_id}/request", "post", "Team chart", "requestCloudAgent",
     "Request an agent Tico does not connect yet: a seat for it goes on the chart now and Tico's team builds the "
     "connection. `other` takes the agent's name or website in `name`", "Seat"),
    ("/api/v2/seats/{seat_id}/request-agent", "post", "Team chart", "requestSeatAgent",
     "Request a cloud agent for a seat that has none: the seat keeps its role and holds its place until the agent connects",
     "Seat"),
    ("/api/v2/seats/{seat_id}/request", "get", "Team chart", "getSeatAgent",
     "A seat's cloud agent: where its request stands (requested, building, ready) and the first task waiting for it",
     "SeatAgent"),
    ("/api/v2/cloud-agents/{agent_id}/add", "post", "Team chart", "addCloudAgent",
     "Add an agent Tico connects as a Tico bot on the harness it runs on, placed under `reports_to`, or in the seat's "
     "place when `seat_id` names one, which the bot then fills", "CloudAgentAdded"),
    ("/api/v2/seats/{seat_id}/cancel-request", "post", "Team chart", "cancelSeatRequest",
     "Withdraw a seat's cloud agent request: Tico hears of it, the seat closes and its first task is declined",
     "SeatCancelled"),
])
SCHEMAS.update({
    "CloudAgent": obj({"id": "s", "name": "s", "maker": "s", "site": "s", "line": "s", "supported": "b",
                       "connect_id": "n"},
                      required=["id", "name", "maker", "site", "line", "supported", "connect_id"]),
    "CloudAgentList": obj({"agents": items(ref("CloudAgent"))}, required=["agents"]),
    "CloudAgentAdded": obj({"bot": "s", "harness": "s", "model": "s", "seat": "s"},
                           required=["bot", "harness", "model", "seat"]),
    "SeatAgent": obj({"seat": ref("Seat"), "agent": ref("CloudAgent"),
                      "steps": items({"enum": ["requested", "building", "ready"]}),
                      "tasks": items(obj({"id": "s", "title": "s", "status": "s"}, required=["id", "title", "status"]))},
                     required=["seat", "agent", "steps", "tasks"]),
    "SeatCancelled": obj({"id": "s", "closed": "b", "request_state": "s"}, required=["id", "closed", "request_state"]),
})
SCHEMAS["Seat"]["properties"].update({name: _t("s") for name in ("agent", "agent_name", "request_state")})
SCHEMAS["Seat"]["required"] = sorted(set(SCHEMAS["Seat"]["required"]) | {"agent", "agent_name", "request_state"})


def generate():
    """The document for a default installation: what docs/openapi/v2.json holds."""
    import tempfile
    from .app import create_app
    from .config import Settings
    with tempfile.TemporaryDirectory() as tmp:
        return spec(create_app(Settings(db_path=Path(tmp) / "hub.db")))


def render(document):
    return json.dumps(document, indent=2, sort_keys=False) + "\n"


if __name__ == "__main__":
    text = render(generate())
    if "--check" in sys.argv:
        sys.exit(0 if COPY.exists() and COPY.read_text() == text else "docs/openapi/v2.json is stale: python -m backend.openapi_v2")
    COPY.parent.mkdir(parents=True, exist_ok=True)
    COPY.write_text(text)
    print("wrote", COPY)
