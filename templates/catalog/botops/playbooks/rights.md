# Whose rights a command uses

Inputs: the command about to run, and the requester and request id it came with.

Done when: the command has run with the requester's rights, or it came back as `needs_confirm` and the asking bot was told so.

## Who the rights belong to
Every tool uses the requester's rights by default. A human request uses that human's full rights for as long as its task is
open (up to 30 days after they last spoke on it; a task you reopen lends nothing). Their own comment on that task is them asking.
A bot's message or task uses only that bot's rights. Work with no requester uses your own rights. Only your daily update and the
owner's routines let you manage every bot but the built-in ones: Tools, repositories, contact and Instructions, never who a bot
reports to, its repository link or its status. Never cite another human's request to widen a bot's access.

## A bot hands you a person's request
A bot handing you something a person asked it for may carry that person's message (`--request-id`). When the person wrote it to
that bot themselves (in their chat with it, or on a task they asked it for) in the last week, tasks, docs and goals you file or
change on that task use the person's rights. Their words are quoted on the task, so do only what they ask for. Everything else
(access, settings, bots, spending), and anything at all when the message lends nothing, runs with the bot's rights. What that
refuses comes back as `needs_confirm`: a Confirm card is already in that person's BotOps chat. Tell the asking bot so, and never
file the person a task asking them to message you.

## Follow through as the person
When you come back to a person's request in a run something else started (the daily update, a notice, a retry once a busy bot is
idle), that run carries nobody's rights. Pass the open task they asked you for: `hub bot model ... --on-behalf-of <task id>`,
`hub api ... --on-behalf-of <task id>` (MCP: `on_behalf_of`). Never cite a person's task in a run a bot asked for: that run keeps
the bot's rights.

## Starting a run on a request
Keep the originating request id and requester with the job. Check the task is still open before applying a change. When a correction changes the target, close the superseded setup,
Tool and continuation tasks before starting the replacement work. A withdrawn Tool request closes its task. Server-generated setup
and Tool tasks carry their human requester's authority too.
