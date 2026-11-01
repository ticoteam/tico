# Thread helper: Needs attention, follow-ups, catch up, update cards

The thread helper is the person's Assistant, acting with that person's rights. It does three things with threads,
and each one shows up where the person already looks.

## Needs attention

Threads that need the person sit in the same **Needs you** list as their tasks, questions and approvals:

| Item | Listed when | Cleared when |
|---|---|---|
| **Question** | A bot or a person asked them something in a thread, and they have not answered it | They post in that thread after the ask, or the ask is answered or deleted |
| **Mention** | Someone @mentioned them (a bot's mention counts too), or addressed a message to them | They post in that thread after it, or it is deleted |
| **Direct message** | A DM (a one-to-one or a migrated group DM) has a message past their read point that they may read, from a person or addressed to them | They read the DM |
| **Held** | A write held for review (org rules, `org_rule_holds`) that they review, or that their bot posted for them (the person its bot reports up to) | The reviewer sends or keeps it; the person withdraws it (keeps it blocked) |
| **Paused** | A thread's bot replies were held back by the loop guard (`TICO_THREAD_LOOP_GUARD`, 6 an hour by default) | Its held replies resume, when a person posts in the thread or the hour passes |

Bot posts that name nobody are activity, not attention. A paused thread is listed once, to the people who can manage
it. Items the Needs you list already shows (a bot's question outside any task) are not listed twice.

The thread items are not cut from the list: they page with the rest of Needs you, in the same order. A held item is
listed to its reviewer and to the person its author bot reports up to. The reviewer sees the card with Send anyway and Keep
blocked. The person sees the text and Withdraw, which keeps the write blocked, and cannot release it. A peer does not
see it. Only the first person up the bot's chain reads its holds, not everyone above them (`org_rule_check.represented`).

The list moves live. A delivery to the person, a change in its state, their read point in a thread, and a hold created
or decided each write a change, so the list is read again.

**Email digest.** A person who follows a thread on **digest** (the thread's notify setting) gets one email an hour
that lists the threads with new messages for them: each title, its unread count and a link to the thread. The email
carries no message text and no author names. A title goes only for a thread the person can still read, checked
message by message when the email is sent, and nothing is sent when nothing is new. The person's **Email digests**
switch, under **Notifications** on their page, turns it off; what is unread waits until it is on again. The mail is
the Tico account issuer's, which mails only an address on the install's people list and holds each person to one
digest an hour. An install that does not sign in with a Tico account sends none.

**Browser notifications.** Off by default. Turn them on in the person's page under **Notifications**, then allow the
browser's prompt. While Tico is in the background, a new thread item sends one browser notification, once. The memory is
kept per person (their id). A live change and a refresh both check it, one after another, so an item notifies once.

## Follow-ups

A bot's question to a person that stays unanswered for a day gets one nudge in its thread: a short line from Tico,
addressed to the person, quoting the question. The nudge is written once and recorded, so it is never sent twice.

A nudge takes the question's restriction and migrated audience, so no one who cannot read the question reads the nudge.
A restricted or migrated question is nudged without its quote. A question whose recipient can no longer read it gets no
nudge and is retired. Each follow-up runs in its own savepoint: one that fails is retired without a nudge, and the
others still run.

The follow-up is plain code on a 15-minute schedule (the **Follow-ups** row in
[System routines](system-routines.md)). It never calls or wakes a model. A run with nothing to nudge writes nothing.

## Catch up

Catch up summarizes what is new for the person in a thread, or in the thread and its sub-threads:

- It reads only what the person may read, message by message. A message they cannot read is left out, not summarized.
- It reads the newest 40 messages at most, within 12,000 bytes. The budget counts the lines the model reads: each
  message cut to 400 characters. Older messages are named as left out.
- Before the reply is written, every message it read is checked again. If one has become unreadable, nothing is
  posted and the call answers 409 (catch up again), so no text from it reaches the Assistant DM.
- It starts at the person's read point. Nothing from before they joined the thread is new, in any sub-thread, nor is
  what the threads migration carried over or history written back (imports, replays).
- A person they may not see is named only to someone in the same thread. The model reads "Someone" for anyone else.
- It skips the person's own posts and system notices (update cards, follow-up nudges).
- The summary is written by the company's model when one is available. Without one, the reply is the last few lines.
- The reply is posted in the person's **Assistant** DM, and only that person reads it. It names its source messages in
  `refs.catch_up.sources`. Nothing new means nothing is posted. Messages too long for the budget are reported as
  `left_out`, which is how that case differs from nothing new.

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
- Held writes: the reviewer and the person their bot reports to see the held item and its card. Only the reviewer
  sends one; the person may withdraw it.
- Update cards: the author's Home audience decides who reads them.
