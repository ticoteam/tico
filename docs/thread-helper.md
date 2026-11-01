# Thread helper: Needs attention, follow-ups, catch up, update cards

The thread helper is the person's Assistant, acting with that person's rights. It does three things with threads,
and each one shows up where the person already looks.

## Needs attention

Threads that need the person sit in the same **Needs you** list as their tasks, questions and approvals:

| Item | Listed when | Cleared when |
|---|---|---|
| **Question** | A bot or a person asked them something in a thread, and they have not answered it | They post in that thread after the ask, or the ask is answered or deleted |
| **Mention** | Someone @mentioned them, or addressed a message to them | They post in that thread after it, or it is deleted |
| **Direct message** | A DM has a message from a person (or one addressed to them) past their read point | They read the DM |
| **Held** | A message to them is held for review | A reviewer releases it |
| **Paused** | A thread's bot replies were held back by the loop guard (`TICO_THREAD_LOOP_GUARD`, 6 an hour by default) | Its held replies resume, when a person posts in the thread or the hour passes |

Bot posts that name nobody are activity, not attention. A paused thread is listed once, to the people who can manage
it. Items the Needs you list already shows (a bot's question outside any task) are not listed twice.

**Email digest.** Not built in this release: Tico has no outbound email for a person's digest yet. The existing
notification settings do not send one.

**Browser notifications.** Off by default. Turn them on in the person's page under **Notifications**, then allow the
browser's prompt. While Tico is in the background, a new thread item sends one browser notification, once.

## Follow-ups

A bot's question to a person that stays unanswered for a day gets one nudge in its thread: a short line from Tico,
addressed to the person, quoting the question. The nudge is written once and recorded, so it is never sent twice.

The follow-up is plain code on a 15-minute schedule (the **Follow-ups** row in
[System routines](system-routines.md)). It never calls or wakes a model. A run with nothing to nudge writes nothing.

## Catch up

Catch up summarizes what is new for the person in a thread, or in the thread and its sub-threads:

- It reads only what the person may read, message by message. A message they cannot read is left out, not summarized.
- It reads the newest 40 messages at most, within 12,000 bytes; older ones are named as left out.
- It skips the person's own posts and system notices (update cards, follow-up nudges).
- The summary is written by the company's model when one is available. Without one, the reply is the last few lines.
- The reply is posted in the person's **Assistant** DM. Nothing new means nothing is posted.

```
hub thread catchup <thread-id>                 the thread and its sub-threads (the default)
hub thread catchup <thread-id> --thread-only   this thread only
```

REST: `POST /api/v2/threads/{thread_id}/catch-up` with `{"scope": "subtree"}` or `{"scope": "thread"}`. The response is
`{"thread_id", "scope", "messages", "left_out", "posted", "words"}`. MCP: `hub_thread_catchup`, with the same arguments
as `thread_id` and `scope`.

## Update cards

Each daily update and each week in review keeps its own structure and its own page, as before. It also posts one card
in its author's Home: a single line (for example "Daily update, 2026-11-01") that links back to the update. The
update's body is never copied into the thread. The card follows the Home's audience: whoever can read the Home sees
it, and nobody else.

The card is posted as the author, so a bot's card in its own Home does not wake that bot.

## Who can do what

- Needs attention: the person sees their own items only.
- Follow-ups: the watcher posts as Tico; the nudge goes to the person who was asked.
- Catch up: a person, for their own threads. Bots and other callers get a 403.
- Update cards: the author's Home audience decides who reads them.
