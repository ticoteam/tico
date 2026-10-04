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
    "Repositories": "Team repositories and bot repository access.",
    "Subscriptions": "Named provider logins on Computers and defaults for groups and bots.",
    "Team chart": "Humans and bots, and who reports to whom.",
    "Bots": "The bots a person can see, and their live status.",
    "Conversations": "Chats with bots: send, list, and stream replies.",
    "Tasks": "Work assigned to people and bots.",
    "Updates": "Daily and weekly updates bots post to people.",
    "Needs you": "What is waiting on the signed-in person: questions, tasks, approvals.",
    "Meetings": "Recorded meetings.",
    "Files": "What a bot creates, revises or delivers, listed on its page (docs/files.md).",
    "Docs": "The team's docs (docs/docs.md): internal docs written or imported in Tico, with history and locks; linked docs, which are links; and search across both.",
    "Assistant": "The signed-in person's own private Assistant chat (docs/assistant.md): ask, and confirm what it proposes.",
    "Goals": "What every person and bot is for (docs/goals-and-kpis.md): goals with a colour the Goal Manager works out from their KPIs, "
             "unless a person set one; check-ins in the owner's words; proposals the owner confirms.",
    "KPIs": "Measures that stand on their own: a goal links to them and carries the target. Readings are facts with a period, "
            "evidence and a quality, never edited; a correction supersedes the old one.",
    "Usage": "Estimated model spend per bot (docs/usage.md): tokens counted by each run's computer, priced at list price.",
    "Health": "Whether the installation is working.",
}

# Path, method, tag, operationId, summary, name of the 200 answer in ANSWERS.
STABLE = [
    ("/api/v2/subscriptions", "get", "Subscriptions", "listSubscriptions", "Profiles and assignments", "SubscriptionList"),
    ("/api/v2/subscriptions", "put", "Subscriptions", "assignSubscription", "Assign or clear a subscription", "SubscriptionAssignmentResult"),
    ("/api/v2/subscriptions/name", "put", "Subscriptions", "renameSubscription", "Rename a subscription without changing its local login", "SubscriptionIdentity"),
    ("/api/v2/subscriptions/weekly", "put", "Subscriptions", "recordSubscriptionWeekly", "Record or clear a manual weekly allowance observation", "SubscriptionWeeklyResult"),
    ("/api/v2/subscriptions/refresh", "post", "Subscriptions", "refreshSubscriptionWeekly", "Request a bounded weekly allowance read on its computer", "SubscriptionRefreshResult"),
    ("/api/v2/bots/{bot}/subscription", "get", "Subscriptions", "getBotSubscription", "Effective bot subscription", "BotSubscription"),
    ("/api/v2/repositories", "get", "Repositories", "listRepositories", "Team repositories", "RepositoryList"),
    ("/api/v2/repositories/refresh", "post", "Repositories", "refreshRepositories", "Refresh from GitHub", "RepositoryList"),
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
    ("/api/v2/me/tokens/{token_id}/revoke", "post", "Session", "revokeMyToken", "Revoke a personal API token", None),
    ("/api/v2/service-keys", "get", "Session", "listServiceKeys", "Service keys, never the secret (owner and admins)", None),
    ("/api/v2/service-keys", "post", "Session", "createServiceKey",
     "Make a key another system uses to file, update and close tasks, and nothing else; shown once (owner and admins)", None),
    ("/api/v2/service-keys/{key_id}/revoke", "post", "Session", "revokeServiceKey", "Revoke a service key", None),
    ("/api/v2/openapi.json", "get", "Session", "getOpenApi", "This document", None),
    ("/api/v2/config", "get", "Team", "getConfig", "Team and app names, version, setup state", "Config"),
    ("/api/v2/org", "get", "Team chart", "getOrg",
     "Humans, the bots the caller can see and reports_to; ?can=read|write keeps the bots they can read or write to", "Org"),
    ("/api/v2/humans/{pid}", "post", "Team chart", "updatePerson", "Edit a person's profile or reports_to", None),
    ("/api/v2/groups", "get", "Team chart", "listGroups",
     "The groups, nested by `parent`, each with its humans and the bots the caller can see", "GroupList"),
    ("/api/v2/groups", "post", "Team chart", "createGroup",
     "Add a group: a name, an optional parent group and teammates to start with (owners and admins)", "Group"),
    ("/api/v2/groups/{gid}", "patch", "Team chart", "updateGroup",
     "Rename a group, move it under another (`parent`, empty for the top) and add or remove teammates (owners and admins)", "Group"),
    ("/api/v2/groups/{gid}", "delete", "Team chart", "deleteGroup",
     "Remove a group; the groups and teammates in it move up to its parent (owners and admins)", None),
    ("/api/v2/bots", "get", "Bots", "listBots",
     "Bots the caller can see, each with the caller's own access; ?can=read|write keeps the ones they can read or write to",
     "BotList"),
    ("/api/v2/bots/{bot}", "get", "Bots", "getBot",
     "One bot: its profile for anyone who can see it; status, computer, queue and goals too for anyone who can read it",
     "BotDetail"),
    ("/api/v2/bots/{bot}/routines", "get", "Bots", "listBotRoutines",
     "A bot's routines, its recurring work (Read on the bot; add include_deleted=true for removed ones)", "RoutineList"),
    ("/api/v2/bots/{bot}/access", "get", "Bots", "getBotAccess",
     "Who may see, read and write to a bot (its managers only; docs/permissions.md)", "BotAccessView"),
    ("/api/v2/bots/{bot}/access", "put", "Bots", "setBotAccess",
     "Set who may see, read and write to a bot; send the revision you read (409 version_conflict otherwise)", "BotAccessView"),
    ("/api/v2/bots/{bot}/instructions", "get", "Bots", "getBotInstructions",
     "Latest Instructions snapshot published by the Computer; requires Read, published is false until content arrives",
     "BotInstructions"),
    ("/api/v2/bots/{bot}/onboarded", "post", "Bots", "markBotOnboarded",
     "A starter bot's own call, or its manager's, once its setup is done: `onboarding_state` goes "
     "from `needs_setup` to `onboarded`. Repeating it changes nothing; 409 bot_limit for a member's bot over their limit",
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
     "The newest messages (up to 200); before=<message id> pages back, since=<timestamp> only newer", "MessagePage"),
    ("/api/v2/conversations/{cid}/messages", "post", "Conversations", "replyInConversation",
     "Send a message in a conversation", "MessageResult"),
    ("/api/v2/conversations/{cid}/snapshot", "get", "Conversations", "getConversationSnapshot",
     "The newest messages and the bot's current run, in one read", "Snapshot"),
    ("/api/v2/conversations/{cid}/watch", "get", "Conversations", "watchConversation",
     "Server-sent events: whole-conversation snapshots while a bot works (reconnect for a fresh one)", None),
    ("/api/v2/conversations/{cid}/stream", "get", "Conversations", "streamConversation",
     "Server-sent events: bot output deltas with a resumable cursor (after=<id>) and message lists", None),
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
     "Tasks the caller can see; type, step, number and updated_since filter, sort=step orders a board's columns, "
     "brief=true leaves out bodies", "TaskList"),
    ("/api/v2/tasks", "post", "Tasks", "createTask", "Create a task", "TaskResult"),
    ("/api/v2/tasks/dry-run", "post", "Tasks", "checkTask", "The checks a create would fail; writes nothing", None),
    ("/api/v2/tasks/labels", "get", "Tasks", "listTaskLabels", "Labels in use", None),
    ("/api/v2/tasks/{tid}", "get", "Tasks", "getTask", "A task with its history, comments and messages", "TaskDetail"),
    ("/api/v2/tasks/{tid}", "post", "Tasks", "updateTask",
     "Change a task; send the version you read (409 version_conflict otherwise)", "TaskResult"),
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
    ("/api/v2/updates", "get", "Updates", "listUpdates", "Daily and weekly updates", "UpdateList"),
    ("/api/v2/updates/unread", "get", "Updates", "countUnreadUpdates", "How many updates are unread", "Unread"),
    ("/api/v2/updates/read", "post", "Updates", "markUpdatesRead", "Mark updates read or unread", None),
    ("/api/v2/updates/{uid}", "get", "Updates", "getUpdate", "One update and the replies to it", None),
    ("/api/v2/updates/{uid}/reply", "post", "Updates", "replyToUpdate", "Reply to an update (goes to the bot)", None),
    ("/api/v2/needs-you", "get", "Needs you", "getNeedsYou",
     "What waits on the caller; count=true for the number alone", "NeedsYou"),
    ("/api/v2/messages/{mid}/answer", "post", "Needs you", "answerMessage", "Answer a question a bot asked", None),
    ("/api/v2/approvals/{aid}", "get", "Needs you", "getApproval", "One approval request", None),
    ("/api/v2/approvals/{aid}", "post", "Needs you", "decideApproval", "Approve or reject", None),
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
    ("/api/v2/linked-docs", "get", "Docs", "listLinkedDocs", "Linked docs: links to where other docs live; Tico keeps no copy", "LinkedDocList"),
    ("/api/v2/linked-docs", "post", "Docs", "addLinkedDoc",
     "Link a doc by its address; the kind is detected and the title defaults to the host and path. Anyone may add one", "LinkedDocResult"),
    ("/api/v2/linked-docs/{link_id}", "patch", "Docs", "updateLinkedDoc",
     "Change or remove (archived) a linked doc: whoever added it, owners and bot administrators", "LinkedDocResult"),
    ("/api/v2/context/search", "get", "Docs", "searchDocs", "Search team documents", "DocSearch"),
    ("/api/v2/context/document", "get", "Docs", "getDocument", "One document", None),
    ("/api/v2/docs/ask", "post", "Docs", "askDocs",
     "Ask the Librarian a question about the team's docs. It goes to the caller's own private docs conversation; "
     "`results` is the instant search (same shape as docs/search) and the answer streams on "
     "GET /api/v2/conversations/{cid}/watch", "DocsAsked"),
    ("/api/v2/librarian", "get", "Docs", "getLibrarian", "Whether the Librarian is on, and whether the caller can turn it on",
     "Librarian"),
    ("/api/v2/librarian/conversations", "get", "Docs", "listDocsConversations",
     "The caller's private Docs conversations, including archived ones; limit and offset page back", "DocsConversations"),
    ("/api/v2/librarian/conversations/{cid}/reopen", "post", "Docs", "reopenDocsConversation",
     "Make the caller's own Docs conversation current, archiving their current one; 409 busy while it is answering",
     "DocsReopened"),
    ("/api/v2/librarian/turn-on", "post", "Docs", "turnOnLibrarian",
     "Owner only: add the Librarian from the catalog (or wake a planned one) and activate it", None),
    ("/api/v2/assistant", "get", "Assistant", "getAssistant",
     "The caller's Assistant: their private room id, whether it is on, the recent messages and what waits for their OK",
     "Assistant"),
    ("/api/v2/assistant/messages", "post", "Assistant", "sendAssistantMessage",
     "Say something to the Assistant. A lookup is answered at once (fast: true, with the reply); anything else "
     "is a turn of the assistant bot, shown as `execution` on GET /api/v2/assistant", "AssistantSent"),
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
    # Goals and KPIs (docs/goals-and-kpis.md)
    ("/api/v2/goal-manager/turn-on", "post", "Goals", "turnOnGoalManager",
     "Owner only: add the Goal Manager from the catalog or restore it, and activate it", None),
    ("/api/v2/goals", "get", "Goals", "listGoals",
     "The caller's own goals, the chain above them and their reports' goals; ?all=true is every goal the caller may read "
     "(?status=red,gray filters); ?owner= names someone else", "GoalList"),
    ("/api/v2/goals", "post", "Goals", "createGoal",
     "Set a goal for yourself, under a goal you own, or for someone below you; the team owner sets team goals", "GoalResult"),
    ("/api/v2/goals/tree", "get", "Goals", "getGoalTree",
     "Every goal with its owner, its KPIs (each with its target and colour), the KPIs no goal uses and the pending proposals: "
     "what the Goals page draws", "GoalTree"),
    ("/api/v2/goals/needs-you", "get", "Goals", "getGoalsNeedsYou",
     "Red KPIs on goals the caller owns, stale KPIs they own, and definitions or targets they are asked to confirm", "GoalsNeedsYou"),
    ("/api/v2/goals/refresh", "post", "Goals", "refreshGoalStatuses",
     "The Goal Manager's status pass (or the owner's): work automatic colours out again. A colour a person set only gets a suggestion",
     "GoalRefreshView"),
    ("/api/v2/goals/{gid}", "get", "Goals", "getGoal",
     "One goal with its KPIs, the goals under and above it, tasks, history, recent check-ins and pending proposals", "GoalResult"),
    ("/api/v2/goals/{gid}", "post", "Goals", "updateGoal",
     "Change a goal's title, body, parent, owner or place in its owner's order", "GoalResult"),
    ("/api/v2/goals/{gid}/status", "post", "Goals", "setGoalStatus",
     "Set the colour by hand with one sentence (red, yellow, green) or end the goal (done, dropped). It sticks, with the setter's "
     "name, until a person hands it back", "GoalResult"),
    ("/api/v2/goals/{gid}/status/auto", "post", "Goals", "handBackGoalStatus",
     "\"Let Goal Manager set it\": end a colour set by hand; the automatic colour is worked out at once", "GoalResult"),
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
     "Propose a change the caller may not make: goal wording, a KPI for a goal, a definition, a target, or a flag", "GoalProposalResult"),
    ("/api/v2/proposals/{pid}/decide", "post", "Goals", "decideGoalProposal",
     "A person's own decision, made by whoever owns the goal or KPI: confirm makes the change as them, reject drops it", "GoalProposalResult"),
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
     "The team default limit and each bot's daily and monthly limits with this period's spend, for the bots the caller may "
     "see usage for", "UsageLimits"),
    ("/api/v2/usage/limits", "put", "Usage", "setUsageDefault",
     "The default limit for bots with none of their own, in estimated USD (empty is no limit), and whether subscription runs "
     "count toward it (owner and bot administrators)", "UsageDefaultView"),
    ("/api/v2/usage/limits/{bot}", "put", "Usage", "setBotUsageLimit",
     "A bot's own daily and monthly limit in estimated USD; empty follows the team default. Owner and administrators, or "
     "the person who runs the bot within the team default. A bot over a limit takes no new job until the period turns "
     "over or the limit is raised", "BotUsageLimit"),
    ("/healthz", "get", "Health", "getLiveness", "Is the server up (no sign-in)", None),
    ("/api/v2/health", "get", "Health", "getHealth", "Checks, computers and failures (people only)", "Health"),
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


def items(schema):
    return {"type": "array", "items": schema}


ref = lambda name: {"$ref": "#/components/schemas/" + name}   # noqa: E731
ACTORS = {"type": "object", "additionalProperties": {"type": "string"},
          "description": "On reads: display names for every actor id in the answer, {\"human:ana\": \"Ana Alvarez\"}"}

SCHEMAS = {
    "SubscriptionList": obj({"profiles_by_computer": items(obj({"runner_id": "s", "label": "s", "profiles": items(
        obj({"name": "s", "id": "s", "display_name": "s", "runtimes": "o"}))})), "assignments": items(obj({"scope": "s", "target": "s", "profile": "s", "updated": "s", "updated_by": "s"}))}),
    "SubscriptionIdentity": obj({"id": "s", "display_name": "s"}),
    "SubscriptionWeeklyResult": obj({"weekly": {"anyOf": [obj({"used_percent": {"type": ["number", "null"]}, "resets_at": "n", "reported_at": "s", "source": {"enum": ["manual"]}}), {"type": "null"}]}}),
    "SubscriptionRefreshResult": obj({"id": "s", "profile": "s", "runtime": "s",
        "state": {"enum": ["requested", "succeeded", "unavailable", "failed", "expired"]},
        "requested_at": "s", "updated_at": "s", "expires_at": "s"}),
    "SubscriptionAssignmentResult": obj({"scope": {"enum": ["group", "bot"]}, "target": "s", "profile": "n"}),
    "BotSubscription": obj({"profile": "n", "id": "s", "display_name": "s", "source": "s", "computer": {"anyOf": [obj({"runner_id": "s", "label": "s"}), {"type": "null"}]},
                            "signed_in": {"type": ["boolean", "null"]}, "problem": "s"}),
    "ChatGoal": obj({"id": "s", "conversation_id": "s", "bot": "s", "objective": "s", "status": "s",
                     "note": "s", "set_by": "s", "set_at": "s", "updated_at": "s", "ended_at": "n"},
                    required=["id", "conversation_id", "bot", "objective", "status", "note", "set_by", "set_at", "updated_at", "ended_at"]),
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
                       "description": "On a structured ask: all review answers, oldest first. On a bot reply: handled message ids"}),
    "Execution": obj({"job_id": "s", "message_id": "s", "bot": "s", "attempt_id": "n", "state": "s", "label": "s", "text": "s"},
                     required=["job_id", "message_id", "bot", "attempt_id", "state", "label", "text", "parts"],
                     parts=items(obj({"kind": {"enum": ["progress", "reply", "tool"]}, "text": "s", "at": "s"})) | {
                         "description": "The run's pieces so far, in order, while it is leased or running (empty otherwise). "
                                        "`reply` and `progress` are what the bot wrote (`progress` when a tool call follows it); "
                                        "`tool` is a short label such as \"Ran hub task create\", never its arguments or output"}),
    "Conversation": obj({"id": "s", "kind": "s", "subject": "s", "participants": items({"type": "string"}),
                         "created": "s", "last_message_at": "s", "closed_at": "n"}),
    "TaskStep": obj({"id": "s", "type_id": "s", "name": "s", "position": "i", "status": "s"}),
    "TaskType": obj({"id": "s", "name": "s", "numbered": "b", "created": "s", "updated": "s", "steps": items(ref("TaskStep"))},
                    bots={"type": ["string", "null"], "enum": ["read", "work", None],
                          "description": "What every bot may do with the type's tasks beyond its own: read "
                                         "(read, comment, file subtasks) or work (also change them); null keeps "
                                         "each task to the bots on it"}),
    "TaskTypeList": obj({"types": items(ref("TaskType"))}),
    "TaskTypeResult": obj({"type": ref("TaskType")}),
    "Task": obj({"private": "b", "id": "s", "title": "s", "body": "s", "requester": "s", "owner": "s", "status": "s", "created": "s",
                 "updated": "s", "due": "n", "version": "i", "lane": "s", "labels": items({"type": "string"}),
                 "acceptance_criteria": items({"type": "string"})},
                required=["id", "title", "requester", "owner", "status", "created", "updated", "due", "version", "lane", "labels"],
                owner_name={"type": "string", "description": "Display name of owner (`owner` stays the actor id)"},
                requester_name={"type": "string", "description": "Display name of requester"},
                cover={"oneOf": [obj({"url": "s", "width": {"type": ["integer", "null"]},
                                      "height": {"type": ["integer", "null"]}}), {"type": "null"}]},
                open_asks={"type": "integer", "description": "Questions on this task with no answer or dismissal"},
                type_id={"type": ["string", "null"]}, step_id={"type": ["string", "null"]},
                type={"oneOf": [obj({"id": "s", "name": "s"}), {"type": "null"}]},
                step={"oneOf": [ref("TaskStep"), {"type": "null"}]},
                body={"type": "string", "description": "Left out of a list asked for with brief=true, "
                      "as is acceptance_criteria"},
                number={"type": ["integer", "null"], "description": "The task's number, unique across the team "
                        "(#18945); given once on a numbered type and never changed"},
                step_rank={"type": ["number", "null"], "description": "Its place within its step, lower first"}),
    "Person": obj({"id": "s", "name": "s", "email": "s", "title": "s", "team": "s", "reports_to": "n", "org_parent": "s"},
                  required=["id", "name", "org_parent"]),
    "Access": obj({"see": "b", "read": "b", "write": "b"},
                  required=["see", "read", "write"]),
    "OrgBot": obj({"id": "s", "display_name": "s", "description": "s", "team": "s", "reports_to": "n", "org_parent": "s",
                   "owners": items({"type": "string"}), "status": "s", "access": ref("Access"),
                   "onboarding_state": "s"},
                  required=["id", "display_name", "org_parent", "status", "access"]),
    "Bot": obj({"slug": "s", "display_name": "s", "state": "s", "online": "b", "queued": "i", "description": "s",
                "reports_to": "n", "team": "n", "operator": "n", "owners": "a", "status": {"type": ["object", "null"]},
                "access": ref("Access"), "onboarding_state": "s"},
               required=["slug", "display_name", "state", "owners", "access"]),
    "BotDetail": obj({"slug": "s", "display_name": "s", "description": "s", "state": "s", "online": "b", "queued": "i",
                      "status": {"type": ["object", "null"]}, "reports_to": "n", "reports_to_name": "s",
                      "operator": "n", "operator_name": "s", "team": "n", "owners": "a", "goals": "s",
                      "access": ref("Access"), "onboarding_state": "s", "template": "s", "template_version": "s"},
                     required=["slug", "display_name", "state", "owners", "access"]),
    "Routine": obj({"id": "s", "bot": "s", "key": "n", "title": "s", "cron": "s", "on": "s", "kind": "s",
                    "timezone": "s", "enabled": "b", "text": "s", "last_fired": "n", "next_due": "n",
                    "deleted_at": "n", "updated_at": "n", "active": "b"},
                   required=["id", "bot", "title", "cron", "on", "kind", "timezone", "enabled", "active"]),
    "RoutineList": obj({"routines": items(ref("Routine"))}, required=["routines"]),
    "Audience": obj({"everyone": "b", "people": items({"type": "string"}), "teams": items({"type": "string"}),
                     "bots": items({"type": "string"})}, required=["everyone", "people", "teams", "bots"]),
    "BotAccessView": obj({"bot": "s", "see": ref("Audience"), "read": ref("Audience"), "write": ref("Audience"),
                      "revision": "i", "you": ref("Access"), "teams": items({"type": "object"})},
                     required=["bot", "see", "read", "write", "revision"]),
    "Me": obj({"actor": "s", "role": "s", "email": "s"}),
    "Config": obj({"company_name": "s", "app_name": "s", "assistant_name": "s", "assistant_bot": "s",
                   "public_url": "s", "owner_email": "s", "version": "s"}),
    "Group": obj({"id": "s", "name": "s", "parent": "s", "people": items({"type": "string"}),
                  "bots": items({"type": "string"}), "order": "i"}),
    "GroupList": items(ref("Group")),
    "Org": obj({"people": items(ref("Person")), "bots": items(ref("OrgBot")), "org_groups": "a", "teams": "o"}),
    "BotList": items(ref("Bot")),
    "BotInstructions": obj({"bot": "s", "content": "s", "published": "b", "updated": "n", "source": "s"}),
    "BotOnboarded": obj({"bot": "s", "onboarding_state": "s", "changed": "b"}, required=["bot", "onboarding_state", "changed"]),
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
    "TaskList": obj({"tasks": items(ref("Task")), "next_offset": {"type": ["integer", "null"]}},
                     required=["tasks", "next_offset"], actors=ACTORS),
    "TaskResult": obj({"task": ref("Task")}),
    "TaskDetail": obj({"task": ref("Task"), "events": "a", "children": "a", "comments": items(ref("Message")),
                       "messages": items(ref("Message")), "has_more": "b", "can_comment": "b", "mover": "b"},
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
                             "comments": items(ref("Message")), "woke": "b"}),
    "CommentResult": obj({"comment": ref("Message"), "comments": items(ref("Message")), "woke": "b"}),
    "InboundTaskResult": obj({"task": {"oneOf": [obj({"id": "s"}), {"type": "null"}]}, "created": "b", "changed": "b"}),
    "UpdateList": obj({"updates": "a", "unread": "i", "next_before": "n"}, required=["updates", "unread"]),
    "Unread": obj({"unread": "i"}, meetings_pending={"type": "integer", "description": "Pending meetings filed for the caller; never part of Needs you"}),
    "NeedsYou": {"oneOf": [obj({"actor": "s", "items": items({
        "type": "object", "required": ["id", "kind", "title"], "additionalProperties": True,
        "properties": {"id": {"type": "string"}, "kind": {"enum": ["task", "question", "declined", "approval"]},
                       "title": {"type": "string"}}})}), obj({"actor": "s", "count": "i"})]},
    "GranolaStatus": obj({"mode": {"type": "string", "enum": ["account", "api_key", "off"]},
                          "connected": "b", "email": "n", "plan_hint": {"type": ["string", "null"], "enum": ["free", "paid", None]},
                          "last_sync": "n", "last_error": "n", "imported_count": "i", "needs_signin": "b", "syncing": "b", "skipped": "i"}),
    "GranolaSignIn": obj({"state": "s", "mode": "s", "connected": "b", "email": "n", "plan_hint": "n",
                          "last_sync": "n", "last_error": "n", "imported_count": "i", "needs_signin": "b", "syncing": "b", "skipped": "i"}),
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
    "DocsSearchResults": obj({"results": items({"oneOf": [
        obj({"type": {"enum": ["internal"]}, "id": "s", "path": "s", "title": "s", "excerpt": "s", "score": "f"}, required=["type", "id", "path", "title", "excerpt", "score"],
            section={"type": "string"}, anchor={"type": "string"}, collection={"type": "string"}),
        obj({"type": {"enum": ["linked"]}, "id": "s", "title": "s", "url": "s", "kind": "s", "description": "s", "score": "f"}),
        obj({"type": {"enum": ["manual"]}, "id": "s", "title": "s", "path": "s", "url": "s", "excerpt": "s", "score": "f"}, required=["type", "id", "title", "path", "url", "excerpt", "score"],
            section={"type": "string"}, anchor={"type": "string"}, collection={"type": "string"})]})}),
    "DocSearch": obj({"query": "s", "results": "a", "has_more": "b", "mode": "s"}),
    "Storage": obj({"mode": {"type": "string", "enum": ["local", "s3"]}, "bucket": "s", "region": "s",
                    "files": "i", "bytes": "i", "copy": obj({"done": "i", "total": "i", "failed": "i"})}),
    "Health": obj({"audience": "s", "checks": "a", "attention": "i", "checked": "s"},
                  required=["audience", "checks", "attention", "checked"],
                  storage={**ref("Storage"), "description": "Read-only file storage usage and copy progress; Team owners only"}),
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
                      "pending": "a"},
                     required=["available", "state", "bot", "name", "can_turn_on", "room_id", "messages", "has_more",
                               "next_before", "execution", "actions", "pending"], actors=ACTORS),
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
    "AssistantSent": obj({"message": ref("Message"), "reply": {"type": ["object", "null"]}, "fast": "b", "intent": "n"},
                         required=["message", "fast"]),
    "AssistantActionResult": obj({"action": {"type": "object", "description": "id, summary, method, path, body, status "
                                            "(pending, running, done, failed, cancelled, expired), result"}, "message_id": "s"},
                                 required=["action"]),
    # Goals and KPIs
    "Goal": obj({"id": "s", "title": "s", "owner": "s", "parent_id": "n", "body": "s", "status": {"type": ["string", "null"], "description":
                 "red, yellow, green, gray (no data), done, dropped; null while unscored or proposed"},
                 "status_note": "s", "status_by": "n", "status_at": "n",
                 "status_source": {"type": ["string", "null"], "description": "auto: worked out by the Goal Manager; person: set by hand and kept until handed back"},
                 "suggest_status": {"type": ["string", "null"], "description": "On a colour set by hand: the colour the Goal Manager would choose, when it differs"},
                 "suggest_note": "n", "suggest_at": "n", "rank": {"type": ["integer", "null"]}, "created": "s", "created_by": "s", "updated": "s"},
                required=["id", "title", "owner", "parent_id", "body", "status", "status_note", "status_by", "status_at", "status_source",
                          "rank", "created", "created_by", "updated"],
                owner_name={"type": "string"}, status_by_name={"type": "string"}),
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
                "freshness": {"enum": ["fresh", "stale", "missing"], "description": "stale: one period missed; missing: never read or two missed. Never zero"},
                "spark": {"type": "array", "items": {"type": "number"}, "description": "The last values, oldest first"},
                "status": {"enum": ["green", "yellow", "red", "gray", "none"], "description": "Against the target on the link (or a range); gray is stale or missing data; none is fresh with no target"},
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
    "GoalProposalView": obj({"id": "s", "kind": {"enum": ["goal_wording", "goal_kpi", "kpi_definition", "kpi_target", "flag"]},
                         "goal_id": "n", "kpi_id": "n", "payload": "o", "reason": "s", "proposed_by": "s", "proposed_at": "s",
                         "status": {"enum": ["pending", "confirmed", "rejected"]}, "decided_by": "n", "decided_at": "n",
                         "decision_note": "s", "result": {"type": ["object", "null"]}},
                        required=["id", "kind", "goal_id", "kpi_id", "payload", "reason", "proposed_by", "proposed_at", "status"],
                        goal_title={"type": ["string", "null"]}, kpi_name={"type": ["string", "null"]},
                        may_decide={"type": "boolean", "description": "Whether the caller may confirm or reject it"}),
    "GoalView": obj({}, required=["kpis", "children", "chain", "tasks", "events", "checkins", "proposals"],
                    kpis=items(ref("Kpi")), children=items(ref("Goal")), chain=items(ref("Goal")), tasks=items({"type": "object"}),
                    events=items({"type": "object"}), checkins=items(ref("GoalCheckinView")), proposals=items(ref("GoalProposalView"))),
    "GoalResult": obj({"goal": {}}),
    "GoalList": obj({"goals": items(ref("Goal"))}, required=["goals"], owner={"type": "string"},
                    chain=items(ref("Goal")), reports=items(ref("Goal")), company=items(ref("Goal"))),
    "GoalTreeRow": obj({}, required=["kpis", "open_tasks"], kpis=items(ref("Kpi")), open_tasks={"type": "integer"},
                       checkin={"oneOf": [ref("GoalCheckinView"), {"type": "null"}], "description": "The latest check-in"}),
    "GoalTree": obj({"goals": {},
                     "owners": {"type": "object", "description": "{actor: {kind: company|person|bot, id, name}} for every owner"},
                     "unaligned": {"type": "object", "description": "{actor: open tasks that serve no goal}"},
                     "other_kpis": items(ref("Kpi")), "proposals": items(ref("GoalProposalView"))},
                    required=["goals", "owners", "unaligned", "other_kpis", "proposals"]),
    "GoalsNeedsYou": obj({"actor": "s", "items": items({
        "type": "object", "required": ["kind"], "additionalProperties": True,
        "properties": {"kind": {"enum": ["kpi_red", "kpi_stale", "proposal"]}, "goal_id": {"type": "string"},
                       "goal_title": {"type": ["string", "null"]}, "kpi_id": {"type": "string"}, "kpi_name": {"type": ["string", "null"]},
                       "reason": {"type": "string"}, "freshness": {"type": "string"}, "proposal": {"type": "object"}}})},
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
                         "unpriced_runs": {"type": "integer", "description": "Runs with tokens on a model with no list price"}}),
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
    "UsageLimitView": obj({"daily_usd": NUM_N, "monthly_usd": NUM_N, "source": {"type": "object", "description": "Where each cap comes "
                       "from: bot, company or null"}, "day_spent": NUM_N, "month_spent": NUM_N,
                       "percent": {"type": "integer", "description": "The highest share of a limit reached"},
                       "blocked": {"enum": ["daily", "monthly", None], "description": "Set while a limit is met: the bot takes no new job"},
                       "own_daily_usd": NUM_N, "own_monthly_usd": NUM_N, "may_edit": "b"}),
    "UsageDefaultView": obj({"default": obj({"daily_usd": NUM_N, "monthly_usd": NUM_N, "count_subscription": "b"})}),
    "UsageLimits": obj({"default": obj({"daily_usd": NUM_N, "monthly_usd": NUM_N, "count_subscription": "b"}),
                        "may_edit_default": "b", "bots": {"type": "object", "additionalProperties": ref("UsageLimitView")}}),
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

SCHEMAS.update({
    "Repository": obj({"full_name": "s", "enabled": "b", "bot_repo": "b", "default_branch": "n",
                       "setup_command": "n", "setup_source": "n", "reachable": "b", "last_seen": "n"}),
    "RepositoryList": obj({"repositories": items(ref("Repository")), "new_bot_default": {"enum": ["own", "all"]},
                           "github_connected": "b"}),
    "RepositoryGrant": obj({"full_name": "s", "access": {"enum": ["read", "write"]}}),
    "BotRepositories": obj({"mode": {"enum": ["own", "all", "chosen"]}, "all_access": {"enum": ["read", "write"]},
                            "chosen": items(ref("RepositoryGrant")), "effective": items(ref("RepositoryGrant"))}),
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

DESCRIPTION = (
    "The stable API for building your own frontend on Tico. Everything here keeps its shape within v2: fields are added, "
    "never removed or renamed, and a breaking change is a new /api/v3. Routes not listed are internal. "
    "Granola connection and sync writes do not require an idempotency key, nor does POST /api/v2/inbound/tasks, whose "
    "own `key` is its idempotency. Other writes need an `Idempotency-Key` header (1-200 characters; a retry with the same "
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
        if path.endswith(("/stream", "/watch")):
            events = ("`event: output` (id = cursor) and `event: messages`" if path.endswith("/stream")
                      else "`event: snapshot`, `event: expired`, and `: keepalive` comments")
            responses["200"] = {"description": "text/event-stream: " + events + ". Ends after about a minute; reconnect.",
                                "content": {"text/event-stream": {"schema": {"type": "string"}}}}
        if method == "get" and path.startswith("/api/v2/files/") and path.endswith("/versions/{number}"):
            responses["200"] = {"description": "The file's bytes (application/octet-stream, sent as an attachment)",
                                "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}
        if path == "/auth/login":
            responses = {"302": {"description": "Redirect to the identity provider"},
                         "400": {"description": "next is not an allowed origin, or code_challenge is missing"}}
        if path not in ("/healthz", "/auth/login", "/auth/token"):
            responses["401"] = {"description": "Not signed in", **ERROR}
            responses["422"] = {"description": "Invalid request or rule: error.code and field-specific error.detail", **ERROR}
        if path == "/auth/token":
            op["requestBody"] = TOKEN_REQUEST
            op["security"] = []
            responses["400"] = {"description": "invalid_grant, or the request is malformed", **ERROR}
        if path in ("/healthz", "/auth/login"):
            op["security"] = []
        op["responses"] = dict(sorted(responses.items()))
        inbound = path == "/api/v2/inbound/tasks"
        granola = path.startswith("/api/v2/meetings/granola")
        if inbound:
            op["security"] = [{"serviceKey": []}]
        if method in ("post", "patch", "delete") and path.startswith("/api/v2/"):
            parameters = op.setdefault("parameters", [])
            parameters[:] = [p for p in parameters if (p.get("name"), p.get("in")) != ("Idempotency-Key", "header")]
            parameters.append({
                "name": "Idempotency-Key", "in": "header", "required": not (inbound or granola), "schema": {"type": "string"},
                "description": ("Not needed: the pair (service key, `key`) makes a repeated call harmless, and the header "
                                "is ignored." if inbound else
                                "Optional. Sync uses a two-minute debounce; connecting starts a new device sign-in."
                                if granola else
                                "1-200 characters. Reusing a key with the same body replays the first answer.")})
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
                               "description": "A service key, tico_sk_..., which reaches POST /api/v2/inbound/tasks and nothing else."},
                "cookie": {"type": "apiKey", "in": "cookie", "name": "tico_session",
                           "description": "The browser session of Tico's own page (`__Host-tico_session` over https)."}}},
        "security": [{"bearer": []}, {"cookie": []}],
    }


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
