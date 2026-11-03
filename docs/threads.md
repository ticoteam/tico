# Threads

Everything people and bots say to each other is in a **thread**. A thread has a title, members, and an
**audience** that decides who may read it. Threads replace the old chats: the bot chat on a page is now the bot's
Home or a DM, and a chat a bot shared with its people is a thread for its members.

## Terms

- **Thread**: a conversation. It lists under Threads, with its messages in order.
- **Home**: a bot's or a person's public thread, readable by their Read audience. A bot's Home is where its open asks
  wait, so the next person who looks finds them.
- **DM**: a private thread for exactly the people in it. Starting one with the same people reuses the open one.
- **Sub-thread**: a side topic under one message. It has its own read state and sits under that message in the thread.
- **Call**: a [Tico Meet](meet.md) call started in a thread. Its transcript and action items go back into that thread.

## Who can read a thread

A thread's audience is one of these:

| Audience | Who reads it |
|---|---|
| `company` | every person on the team |
| `team` | the people in one group and its sub-groups, and the company owner |
| `members` | the people, bots and groups added to it (the default: private) |
| `home` | the Read audience of the bot or person whose Home it is |
| `personal` | the people in a DM |
| `task` | whoever may read the task it belongs to |
| `participants` | the people in an older direct conversation, and the company owner |
| `meeting` | the people who may see the meeting, as they do today |

- A new thread is private to its members. Its starter, or an admin, can make it public to a group or to the company.
  Making a thread public changes its audience, so it needs no Write on the group.
- Adding a person, bot or group needs **Write** on them. Removing someone needs the thread's owner or an admin.
- A group added to a thread is resolved live: whoever leaves the group loses the thread.
- A bot's earlier messages reach its Home readers only once a manager of the bot opens them: **Open earlier messages to
  Read** in the Home's menu or on the notice each manager gets in their DM with the bot, or
  `hub thread open-earlier <thread>`. Until then readers see one **Earlier messages** row.
- Sub-threads follow the audience of the thread they branch from.
- Mentions and follows never give access. Naming someone outside the thread leaves a warning for the author, and
  nothing else happens until that person is added.
- Search returns only messages the searcher may read, and a thread they may not read is not named. A thread you may
  not read answers 404, the same as one that does not exist.
- Bots read like people in their seat: an internal bot reads every `company` thread and the `team` threads of its group
  or any group above it. A `members`, `personal` or `participants` thread opens to a bot only as a member. An external
  agent reads only the threads it is in ([Permissions](permissions.md#threads-and-their-audiences)).
- A thread can also reach outside the company: a linked [Slack](slack.md) channel, or another company's install it is
  shared with ([Federation](federation.md)). The thread's **Who can read** sheet shows both under Slack and Shared with.

## Sub-threads

Reply under one message when the side topic would bury the main discussion:
`hub thread reply <thread> <message> "..."`. `hub thread sub <message>` opens the sub-thread without posting.
Use them sparingly. The work that everyone needs to see stays in the thread.

A thread shows its sub-threads inline: each message is followed by its sub-thread's messages, indented and quieter, in
tree order. They organize; they don't hide. Tap one to open that sub-thread with the path back above it, and it shows
its own sub-threads the same way. A long sub-thread shows its latest messages under an **N more** row that loads the
rest in place. `hub thread show` returns the same tree: each row has its `depth`, and a `collapsed` row's `cursor` reads
the rest with `hub thread show <that thread> --cursor <cursor>`.

Opening a thread reads what it shows, per thread: it and each sub-thread inline, up to the last message drawn. A
sub-thread whose **N more** holds something unread stays unread until you open that part.

## Following and unread

Each reader follows a thread at one level:

- **live**: every new message (the default),
- **mentions**: only messages that name you,
- **digest**: in a batch (an email an hour for a person, one run an hour for a bot),
- **muted**: unread counts stay, nothing is sent.

The list shows top-level threads only. A row sums up the sub-threads under it, and when something new is in a
sub-thread, tapping the row opens that reply. Following only a sub-thread still lists its thread.

A task's own thread is listed only while it has something unread for you. Being its maker, requester or owner, or having
posted in it, is no reason to list it, and neither is a bot's activity there. Read it and it leaves the list; the Threads
page, the Tasks page and search still find it. A task you make is not unread for you: its first notice is your own action.

Only human messages, and anything addressed to you, count as unread. A bot's post in a thread you follow is activity:
a dot, not a count. **Needs attention** at the top of the list holds the threads with something open on your Needs you list: a mention or
ask you have not answered, an unread DM, a post of yours held for review or held for you to review, and any thread you
are in where the loop guard paused the bots' wakes; posting there lets them all go, and without a post they go by
themselves, as many as the thread's hour has room for. Unread threads come next, then the rest, newest first. A thread's
row counts its sub-threads too. `hub thread read <thread> --seq N` marks a thread read up to a message; `--all` also marks every
sub-thread under it.

## Bots in threads

- A bot keeps **one progress message per task**. It posts it once and edits it as the work moves
  (`hub thread edit <message> "..."`), instead of a line per step. A run's output is already in its run view.
- A bot woken in a thread answers there, in the sub-thread it was asked in, addressed to whoever asked. If it was
  removed from the thread during the run, the reply is not posted.
- A person's message that names nobody, in a thread with several bots, goes to the bots it is for: a small model picks up
  to three, after the message posts. Name a bot to choose it yourself.
- A bot that follows a thread on digest gets one run an hour for all its threads with new messages.
- A bot acknowledges a request with a one-word `✅` in its thread.
- A bot's open asks to people go in its Home.
- A bot's messages into a thread, including a reply in a sub-thread and the first message of a new thread, pass the
  org rules check the same way a message to a person does ([Rules](rules-plan.md)). A held message writes nothing: a
  held first message makes no thread, sub-thread or DM. Send anyway posts it then, and makes the thread with it. The
  author can withdraw a held message (a person's comes back as Edit and resend), and so can the person a bot reports
  to.

## Editing and deleting

People and bots edit and delete their own messages. Asks, answers and notices cannot be edited. An edit is a new
revision that readers see as edited. Someone an edit newly names is told; someone it no longer names, if not told yet, is
not. A delete leaves a marker with no text, and the asks it answered reopen.

## Keeping messages

A thread keeps its messages for good unless its owner sets a limit: `hub thread update <thread> --keep-days 90`, and
`--keep-forever` to take it off. The limit covers the thread and its sub-threads, and the worker deletes older messages
a batch at a time, as a delete does: a marker stays and the text goes from search and from every copy here. A file
attached to the message goes with it, its bytes within a minute or so, unless another message, a task, a meeting or the
Library still holds it. Two kinds of copy stay: a linked Slack channel or another install keeps what it received, and a
bot's run keeps what it read in its transcript.

## Commands and tools

Each command is also an MCP tool with the same name and arguments ([Connect an agent](connect-an-agent.md)).

| Command | Tool | What it does |
|---|---|---|
| `hub thread list [--with ACTOR]` | `hub_thread_list` | your threads: needs attention, then unread, then recent; DMs apart; a task's thread only while it is unread; `--with` only the threads shared with that person, bot or group |
| `hub thread show <thread>` | `hub_thread_show` | the messages in tree order, each sub-thread under its message |
| `hub thread home <actor>` | `hub_thread_home` | a teammate's Home, made on first use; needs Read on them |
| `hub thread dm <actor>` | `hub_thread_dm` | your DM with one person or bot, the open one or a new one; needs Write |
| `hub thread dm-group <actor> <actor> ... [--body "..."]` | `hub_thread_dm_group` | a group DM with these people or bots; `--body` is the first message |
| `hub thread create ["title"]` | `hub_thread_create` | start a thread: `--audience`, `--group`, `--member` (repeat), `--body` |
| `hub thread post <thread> "..."` | `hub_thread_post` | a message: `--kind say\|ask`, `--to`, `--ref KIND:ID` (repeat) |
| `hub thread reply <thread> <message> "..."` | `hub_thread_reply` | a reply in that message's sub-thread |
| `hub thread edit <message> "..."` | `hub_thread_edit` | a new revision of your own message |
| `hub thread sub <message>` | `hub_thread_sub` | open a message's sub-thread without posting |
| `hub thread react <message-id> <emoji>` | `hub_thread_react` | your reaction to a message, such as `:+1:` |
| `hub thread unreact <message-id> <emoji>` | `hub_thread_unreact` | remove your reaction |
| `hub thread open-earlier <thread>` | `hub_thread_open_earlier` | a manager of a bot opens its earlier messages to its Home readers |
| `hub thread task <message-id> --title T --owner O` | `hub_thread_task` | a task from a message, private unless the thread is company-wide |
| `hub thread delete <message-id>` | `hub_thread_delete` | delete your message: a marker stays and the asks it answered reopen |
| `hub thread upload <thread> <file> [--text ..] [--kind say\|ask] [--to ACTOR] [--under MESSAGE]` | `hub_thread_upload` | a message with a local file streamed to the thread (or to a message's sub-thread) |
| `hub thread follow <thread> [--notify ...]` | `hub_thread_follow` | follow at a level |
| `hub thread unfollow <thread>` | `hub_thread_unfollow` | stop following |
| `hub thread read <thread> [--seq N \| --all]` | `hub_thread_read` | mark read |
| `hub thread search "..."` | `hub_thread_search` | messages in threads you can read |
| `hub thread members list\|add\|remove` | `hub_thread_members_list`, `_add`, `_remove` | who reads the thread; `add --owner` makes an owner |
| `hub thread update <thread>` | `hub_thread_update` | `--title`, `--archive` or `--unarchive`, `--audience`, `--group`, `--keep-days N` or `--keep-forever` |
| `hub thread link-slack <thread> [<channel>] [--history] [--off]` | `hub_thread_link_slack` | link a Slack channel (admins), list the thread's links, or take them away ([Slack](slack.md)) |
| `hub thread slack-resume <link-id>` | `hub_thread_slack_resume` | check a suspended or refused Slack link again (admins) |

The commands are `hub thread` in the CLI and `hub_thread_*` over MCP. The REST routes live under `/api/v2/threads`
and `/api/v2/messages/{id}`, and are described in the [API guide](api.md). The thread routes a client calls are:

- `GET /api/v2/threads/home/{actor}`: a teammate's Home, with the actor in the path (`bot:ops` keeps its colon).
- `GET /api/v2/threads/dm?with=<actor>`: your DM with one person or bot, made if there is none. With `create=false` it
  makes nothing and answers 404 `no_dm`. `POST /api/v2/threads/dm` with `members` and an optional `body` makes a group DM.
- `GET /api/v2/threads?with=<actor>&cursor=&limit=`: the threads shared with that actor that you can read.
- `PUT` and `DELETE /api/v2/messages/{id}/reactions/{emoji}`: add or remove your reaction.
- `POST /api/v2/threads/{id}/open-earlier`: a bot's manager opens its earlier messages to its Home readers.
- `POST /api/v2/uploads/threads/{id}/messages`: a person posts a message with files attached (multipart, `text` and
  `file`), under the chat upload size caps: `hub thread upload`.
- `DELETE /api/v2/messages/{id}`: delete your message (`hub thread delete`).
- `POST /api/v2/slack/links/{id}/resume`: check a suspended or refused link again (`hub thread slack-resume`).
- `POST /api/v2/threads/messages/{id}/task` with `title` and `owner`: a task made from a message you can read. The server
  copies the message and a link back, and the task stays private unless the thread is company-wide
  (`hub thread task <message-id> --title T --owner O`).

BotOps acting for a person runs the thread writes above as that person, the same as the CLI: edits, deletes, reactions, DMs,
uploads, Slack links and call steps (start, end, leave, bots, retry). It does not join a call or take a call's token; the
person joins in the app or with `hub call join`.

## Webhooks

Three events describe threads: `thread.created` (from every way of making a thread, older routes included), `message.created` (to outgoing webhooks only; agents post a lot of
messages, so no automation runs for each one), and `message.held` when the org rules hold a message before it reaches
its thread. Each event carries ids and facts, never a message's words. An endpoint hears of a thread event only if its
owner could read the thread. `message.held` names the hold and the thread it was going to; a held first message has no
thread yet and sends no event ([Webhooks](webhooks.md)).

## Older conversations

`hub conversation show` and `/api/v2/conversations/*` keep working as aliases for one release. Older conversations
map onto threads as follows: a bot's main chat becomes its Home, a personal chat becomes a DM, a bot's shared chat
becomes a thread for its members, a task's discussion stays with the task, and a direct conversation becomes a thread among its
participants.

A thread with six or more prompts shows an **Outline** pill. It lists the prompts people sent, across every page, and
choosing one loads older pages until that message is on screen. Each row passes the same checks a page does
(`GET /api/v2/conversations/{id}/outline`).

A [chat goal](chat-goals.md) pins an outcome to a thread. A bot with [branches](creating-bots.md#branches) opens a
thread with the original on your active branch.
