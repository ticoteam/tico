# Threads

Everything people and bots say to each other is in a **thread**. A thread has a title, members, and an
**audience** that decides who may read it. Threads replace the old rooms and chats: the bot chat on a page is now
the bot's Home or a DM, and a shared room is a thread for its members.

## Terms

- **Thread**: a conversation. It lists under Threads, with its messages in order.
- **Home**: a bot's or a person's public thread, readable by their Read audience. A bot's Home is where its open asks
  wait, so the next person who looks finds them.
- **DM**: a private thread for exactly the people in it. Starting one with the same people reuses the open one.
- **Sub-thread**: a side topic under one message. It has its own read state and sits under that message in the thread.
- **Call**: a [Tico Meet](meetings.md) call started in a thread. Its transcript and action items go back into that thread.

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
- Sub-threads follow the audience of the thread they branch from.
- Mentions and follows never give access. Naming someone outside the thread leaves a warning for the author, and
  nothing else happens until that person is added.
- Search returns only messages the searcher may read, and a thread they may not read is not named.

## Sub-threads

Reply under one message when the side topic would bury the main discussion:
`hub thread reply <thread> <message> "..."`. `hub thread sub <message>` opens the sub-thread without posting.
Use them sparingly. The work that everyone needs to see stays in the thread.

## Following and unread

Each reader follows a thread at one level:

- **live**: every new message (the default),
- **mentions**: only messages that name you,
- **digest**: in a batch,
- **muted**: unread counts stay, nothing is sent.

Only human messages, and anything addressed to you, count as unread. A bot's post in a thread you follow is activity:
a dot, not a count. `hub thread read <thread> --seq N` marks a thread read up to a message; `--all` also marks every
sub-thread under it.

## Bots in threads

- A bot keeps **one progress message per task**. It posts it once and edits it as the work moves
  (`hub thread edit <message> "..."`), instead of a line per step. A run's output is already in its run view.
- A bot woken in a thread answers there, in the sub-thread it was asked in, addressed to whoever asked. If it was
  removed from the thread during the run, the reply is not posted.
- A bot acknowledges a request with a one-word `✅` in its thread.
- A bot's open asks to people go in its Home.
- A bot's messages into a thread, including a reply in a sub-thread and the first message of a new thread, pass the
  org rules check the same way a message to a person does ([Rules](rules-plan.md)).

## Editing and deleting

People and bots edit and delete their own messages. Asks, answers and notices cannot be edited. An edit is a new
revision that readers see as edited. A delete leaves a marker with no text, and the asks it answered reopen.

## Commands and tools

Each command is also an MCP tool with the same name and arguments ([Connect an agent](connect-an-agent.md)).

| Command | Tool | What it does |
|---|---|---|
| `hub thread list` | `hub_thread_list` | your threads: needs attention, then unread, then recent; DMs apart |
| `hub thread show <thread>` | `hub_thread_show` | the messages in tree order, each sub-thread under its message |
| `hub thread home <actor>` | `hub_thread_home` | a teammate's Home, made on first use; needs Read on them |
| `hub thread dm <actor>` | `hub_thread_dm` | your DM with one person or bot, the open one or a new one; needs Write |
| `hub thread create ["title"]` | `hub_thread_create` | start a thread: `--audience`, `--group`, `--member` (repeat), `--body` |
| `hub thread post <thread> "..."` | `hub_thread_post` | a message: `--kind say\|ask`, `--to`, `--ref KIND:ID` (repeat) |
| `hub thread reply <thread> <message> "..."` | `hub_thread_reply` | a reply in that message's sub-thread |
| `hub thread edit <message> "..."` | `hub_thread_edit` | a new revision of your own message |
| `hub thread sub <message>` | `hub_thread_sub` | open a message's sub-thread without posting |
| `hub thread follow <thread> [--notify ...]` | `hub_thread_follow` | follow at a level |
| `hub thread unfollow <thread>` | `hub_thread_unfollow` | stop following |
| `hub thread read <thread> [--seq N \| --all]` | `hub_thread_read` | mark read |
| `hub thread search "..."` | `hub_thread_search` | messages in threads you can read |
| `hub thread members list\|add\|remove` | `hub_thread_members_list`, `_add`, `_remove` | who reads the thread; `add --owner` makes an owner |
| `hub thread update <thread>` | `hub_thread_update` | `--title`, `--archive` or `--unarchive`, `--audience`, `--group` |
| `hub thread link-slack <thread>` | `hub_thread_link_slack` | not available yet: see [Slack](slack.md) |

The commands are `hub thread` in the CLI and `hub_thread_*` over MCP. The REST routes live under `/api/v2/threads`
and `/api/v2/messages/{id}`, and are described in the [API guide](api.md).

## Webhooks

Three events describe threads: `thread.created`, `message.created` (to outgoing webhooks only; agents post a lot of
messages, so no automation runs for each one), and `message.held` when the org rules hold a message before it reaches
its thread. Each event carries ids and facts, never a message's words. A person hears of a thread event only if they
could read the thread, and `message.held` goes to owners and admins ([Webhooks](webhooks.md)).

## Older conversations

`hub conversation show` and `/api/v2/conversations/*` keep working as aliases for one release. Older conversations
map onto threads as follows: a bot's main chat becomes its Home, a personal chat becomes a DM, a shared room becomes a
thread for its members, a task's discussion stays with the task, and a direct conversation becomes a thread among its
participants.

A thread with six or more prompts shows an **Outline** pill. It lists the prompts people sent, across every page, and
choosing one loads older pages until that message is on screen. Each row passes the same checks a page does
(`GET /api/v2/conversations/{id}/outline`).

A [chat goal](chat-goals.md) pins an outcome to a thread. A bot with [branches](creating-bots.md#branches) opens a
thread with the original on your active branch.
