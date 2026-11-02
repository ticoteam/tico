# Hub commands for the Assistant

Use these commands with these flags. For any other, run `hub <group> --help` first. Never guess a command or a flag.
Copy ids from earlier output, whole. A task id may be given as its first 8 characters.
Not commands, do not try them: hub --version, hub message show (read messages with `hub message list`), hub conversation <id> (it needs show), hub replay --since (it takes --from).

## Read the record

| Command | What it gives you |
|---|---|
| `hub message list` | What waits for the person: messages, notices, bots' questions (`kind` ask) and open tasks. An approval is a notice whose `refs.approval` is the approval id. Pending approval ids come from here. |
| `hub task list` | Their open tasks. `--all` for the whole board. `--status waiting`, `--owner <slug>` narrow it. |
| `hub task show <id> [<id> ...]` | One or more tasks: body, history, conversation and comments. |
| `hub brief` | A catch-up: bots that are down, who waits on the person, what bots said. |
| `hub health check` | What is wrong with bots and their computers, each with its fix. It does not list approvals or questions. |
| `hub bot status list` | Every bot's live status. `hub bot status history <slug>` for its past. |
| `hub team show` | People, bots (what each does, who it reports to) and groups. `--person <id>` or `--team <group>` narrows it. |
| `hub doc search "<words>"` | Team docs, linked docs and the Tico manual. `--market` adds the market. Then `hub doc read <id or path>`. |
| `hub meeting search "<words>"` | Meetings. Then `hub meeting read <id>` for the transcript. |
| `hub goal list` | Their goals in order. `hub goal show <id>` for one, with its KPIs. `hub kpi list` for the KPIs. |
| `hub approval show <id>` | One approval, by an id from `hub message list`. |
| `hub conversation show <id>` | What was said in a conversation (a notice's `conversation_id`). `--before <message id>` for the page before. |
| `hub replay show --from <ISO> --to <ISO>` | What happened in a window of at most 31 days. `--person`, `--bot` or `--team` narrow it. |

## Tasks

| Command | Use it for |
|---|---|
| `hub task create --owner <bot slug> --title "<ask>" --body "<body>"` | Work a bot owns. The title is the ask in one line. |
| `hub task create --owner botops --request-id turn --title "<ask>" --body "<body>"` | A new bot, a broken bot, or a change to a bot's instructions or schedule. `--request-id turn` only when the person asked in this chat, and never on another owner's task. |
| `hub task create --owner human:<person id> --title "<ask>" --body "<body>"` | A decision, price, promise or exception for one person. The body's first line is the question. |
| `hub task question "<question>" --on <person id> --option "A -> what happens" --option "B -> what happens"` | One decision put to a person. With a choice (a price, a discount, a scope), give 2 or 3 `--option`s, each with what happens. Yes or No: leave out `--option`. |
| `hub task ask <id> "<question>"` | The one question that unblocks a task you own. |
| `hub task update <id> --status waiting --on <person id> --note "<the ask>"` | A task waits on a person. |
| `hub task close <id> --note "<why>" --on-behalf-of turn` | Close a task the person asked you to close in this chat. |
| `hub task update <id> --status done --note "<why>" --on-behalf-of turn` | Set a task's status for them the same way (`doing` or `declined` too). |
| `hub task comment <id> "<text>" --on-behalf-of turn` | Comment on a task as them. On a task with no other human on it, `--on-behalf-of` is not needed. |

## Approvals and questions

| Command | Use it for |
|---|---|
| `hub approval read-back <id> [<id> ...]` | List their approvals in their chat, one line each. It is your reply: end the turn there. Only what it lists can be approved. |
| `hub approval approve <id> --on-behalf-of turn` | Once per approval, after their plain yes to the read-back. |
| `hub approval decline <id> --note "<reason>" --on-behalf-of turn` | A decline they named. No read-back needed. |
| `hub question answer <message id> "<their words>" --on-behalf-of turn` | Answer a bot's question in their words. A question with picks takes the pick's label. |

## Messages and bots

| Command | Use it for |
|---|---|
| `hub message send <bot slug or person id> "<text>"` | A message to a bot or a person. `--fyi` for a note that needs no reply. Never to an outsider. |
| `hub question ask <bot slug> "<question>" --wait 60` | A question to a bot, waiting for its answer. |
| `hub approval request --kind <kind> --payload-file <file>` | Ask a person for a yes when the scope or the standing rules are unclear. `--kind` names what needs the yes: send, spend, publish, merge and so on. |
| `hub bot status set "<one factual line>"` | What you are doing now, in one line while you work. |
| `hub assistant learn "<line>"` | Save one thing they taught you, in their words. |

Replies between bots: use `hub question ask` when you need another bot's answer. A plain `hub message send` does not deliver the recipient's final answer to you. If an incoming ordinary bot message asks for a reply, send it with `hub message send <sender> "<reply>"`, so that bot is not left waiting. An ask message receives your final answer automatically.

## Proposals and settings

| Command | Use it for |
|---|---|
| `hub assistant propose --summary "<what happens on confirm>" --path <api path> --body '<json>'` | One Confirm card for one change only they can click: approval, spend, setting, access, archive or delete, or activating a bot. |
| `hub assistant propose --summary "<groups, with counts>" --path /api/v2/assistant/batch --body '{"actions": [{"path": "/api/v2/tasks/<id>", "body": {"close": true}}]}'` | Many changes on ONE card, up to 100 steps: task changes, comments, links, `/api/v2/approvals/<id>` decisions, `/api/v2/messages/<id>/answer` answers. |
| `hub settings find "<words>"` | Find a setting by its words. It prints the key. |
| `hub settings show <key>` | One setting: its value and who may change it. |
| `hub settings set <key> <value> --on-behalf-of turn` | Change a setting they asked you to change. `--bot <slug>` for a per-bot setting. |
