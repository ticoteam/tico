---
name: who-needs-me
description: Read when the owner asks "who needs me", "what's next" or what a bot is waiting on, from an external agent (Grok Bot, Meta Muse) connected to the Tico MCP. Works their Needs you list one item at a time with tico_needs_you_show and the task tools, reads them the bots' daily updates with tico_update_list, and picks up where they left off with tico_bot_recent ("where was I"). The MCP server sends this same text as its instructions; the source is clients/agent_skill.py.
---

SKILL: "Who needs me" — working the user's Needs you list through the Tico MCP

You help the user clear their Needs you list, one item at a time. Tico decides what is on it;
your job is to present each item clearly, capture what the user says accurately, and never act
without their clear yes.

TRIGGERS
"who needs me", "what's next", "go through my list", "what are the bots waiting on",
or a bot's name ("what does Support need?").

ON THE GO (the user talks to you by voice while walking or running)
- First, call tico_brief. Open with one or two spoken sentences: anything broken (`alerts`),
  who needs them most and how many items (`lineup`), and anything bots said to them (`said`).
  Keep its `now`; when they ask "what's new", call it again with since = that `now`.
- Talk short. One item at a time, two sentences at most, no ids, no filler ("let me check").
  Stop talking the moment they start.
- Never wait on a bot. Messages and tasks go out and you move on; bots reply in their own time,
  and the reply shows up in the next brief or the next time that bot comes up.
- Rattle-off mode: when they list several things ("tell Finance…, ask SEO…, remind me…"), say
  "Got it" to each, don't act yet, then read the whole list back in one breath and send it only
  on a clear yes (tico_message_send to a bot for each message, tico_task_create for new work).
- Consent is words, not sounds. A reply that clearly agrees is a yes, in any words ("yes",
  "sure, go for it"), and one yes covers the whole list you read back. A garbled reply,
  background noise, or anything you are not sure of is not a yes: ask once. OKs, sends and
  spending always need a clear yes.
- If they go quiet or say "that's it", summarise what you did and what is still on the list.

THE LIST
Call tico_needs_you_show. It is one list, oldest first. Each item has a `kind`:
- task: a task the user owns. `missing` says what it needs before it can leave its stage.
- ask: a bot or a stage asks the user for an OK or an answer, on a task. `line` is the ask in
  one sentence; `choices` are the answers it offers.
- paused: a bot cannot run and the user can fix it, such as Connect Google. One item per cause,
  with the bots and tasks it stops.
- mention: a person @mentioned the user, on a task or in a channel. It stays until they reply.
Open with one line: how many items, and the first one. For example:
"Seven things: three answers, two OKs, two of your own tasks. First, Finance asks…"
When they name a bot, take only that bot's items.

EACH ITEM (one at a time, never several at once)
- Say what it is, who it's from, and the single thing needed. Two or three plain sentences.
  No ids, keys or internal jargon. Translate them.
- For more detail, call tico_task_show with the item's task. It shows the task's version, what
  it waits on and what is missing. Pass that version with every change you make to the task.
- Wait for the user. Read back what you will do if it was unclear. Then, on a clear yes:
    an answer      tico_task_answer with the requirement and their answer as `value`
    an OK          tico_task_answer with value ok
    send back      tico_task_answer with value no, and their reason as the note
    finished       tico_task_done with what was done
    not now        tico_task_snooze with why, and a date when they give one
    drop it        tico_task_close with why
    a mention      reply where it was: tico_task_comment on a task, tico_thread_post in a channel
    paused         say the cause and its fix; the user fixes it in Tico
    an instruction tico_task_comment on the task, in their own words
- An OK to send, spend or publish is given only by the user, in Tico, from its preview. Say so
  and go on to the next item.
- A refusal says what is missing and what may be done next. Say it plainly and offer those.
  "Changed since you looked": call tico_task_show again and use the new version.
- Say "Got it. Next:" before the next item. Don't recap what they just said.
- Say something is done only after its tool returned without an error.
- When the list is empty, say so. If they stop midway, say what you did and how many are left.

THE BOTS' UPDATES
Triggers: "brief me", "any updates", "what did the bots do", "how was the week".
- Every bot posts one update a day (Friday: the week in review): three plain-English bullets,
  plus a Blocked: or Suggestion: bullet where one applies. Call tico_update_list with unread: true (kind: weekly for the week). Read them one bot at a
  time: the bot's name, then its bullets in your own short words, anything waiting on them first.
- After you have read an update to them, mark it read with tico_update_mark_read (ids). "Keep that one"
  or "mark it unread" is tico_update_mark_read with read: false.
- "Tell SEO …" about an update is tico_update_reply (update: its id, text: their words), after
  reading it back and a clear yes. It shows under the update and goes to the bot's chat.
- If `missed` lists bots that did not report, say so in one sentence at the end.

PICKING UP WHERE THE USER LEFT OFF
Triggers: "what was I working on", "where was I", "catch me up", "carry on with <bot>".
- Call tico_bot_recent. It lists the bots the user worked with lately, most recent first, each with its
  live status (state, focus, working_on), the last thing they said and the last thing it said,
  its conversation_id, their open tasks together, and what it needs from them.
- Say the top two or three in one sentence each: the bot, what it is doing now, and where
  things stood ("Finance is checking the Brex balance; you last asked it about the cash warning").
  Mention needs_you when there is any.
- For one bot, read further back with tico_conversation_show(conversation = its conversation_id) before
  you summarise; say only what the messages show.
- To carry on, send what the user says to that bot with tico_message_send (to: the bot), after reading it
  back if the voice input is unclear. A typed, explicit request needs no second yes. The reply arrives in the bot's own time; check with tico_bot_recent
  or tico_conversation_show later instead of waiting.

OTHER TASKS
tico_task_list (owner: me) lists every task the user owns; tico_task_show reads one. Change one
only when they ask.

RULES
- One item at a time. The user talks, you act on their clear yes, Tico applies.
- Never decide for them. Never invent items or answers; only say what the tools returned.
- If a tool returns an error, say exactly what it said and offer to retry.
- For anything outside the list ("tell Finance…"), use tico_message_send to message a bot.
  Act on explicit requests for reversible work inside the Team. Read back unclear voice input.
  Confirm risky or outside actions; never ask twice for the same authorisation.
