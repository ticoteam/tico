# The Assistant

Every human has one private chat with the team's assistant (the `coo` bot; you may have renamed it): a personal
bot that knows how Tico is laid out and does things in it on your behalf. **Ask Tico** opens it from any page: on a
computer it is the sidebar's bottom row, above your account, with Search's icon (⌘K) at its end; it (or ⌘J / Ctrl+J)
opens a small panel straight above that row, over the sidebar, and nothing floats over the page. On a phone the Liquid Dot in the
middle of the tab bar opens it as a sheet. The panel names the page it sees, and a message sent from it carries that
page, so "this" and "here" mean what you are looking at. The panel is called **Tico Assistant** (or the name your team
gave the assistant); its header has the dot, the name, the page it sees and **Loop in Support**. The chat is also a
page of its own (`#/assistant`): the assistant's name, then the same thread and composer as a bot's chat, with a
few things to ask while it is empty. The **Assistant** tab, first on your own page (`#/person/<you>`), opens the same
page. Attach files or
pictures there, or in the Ask panel, with the clip, a drop or a paste, like any bot chat. There is no BotOps, Strategy Bot, Librarian or Assistant row in
the rail: you ask the Assistant, and it hands its helpers what needs them (below). BotOps' own chat is still at
`#/bot/botops`. These built-ins stay out of the team chart and Goals tree; Settings > Bots still manages all four.
It works on a phone too.

In the thread the Assistant's turns are plain text; yours are dark bubbles on the right. The live dot stays in the
header: opening flies it up from the row (or tab) to its place beside the name, quickly, closing flies it back down; while the
panel is open a close mark holds the dot's place. Bots' questions
and Confirm cards sit in the thread by when they were asked, among the messages, so nothing waits out of sight.

**Loop in Support**, a link at the top right of the panel, brings Tico support into the chat. It rides on your support
requests ([Support](support.md)): while one of your open requests is in the chat, its chip **Tico Support ×** sits at
the top, a line in the thread says "You added Tico Support. They can now see chats.", support's messages flow in by
time as blue-gray bubbles under a headset (yours on the right), a reply seen there is read, and the box writes to
support instead of the Assistant; the link hides while support is in. Looping support in shares the chat, as the line says: the chat's last 20 turns
(yours and the Assistant's, one line each, up to 4000 characters) go to the request at once, with keys and tokens cut
out by the same rules support reports use (`backend/redaction.py`), and the thread doesn't draw that message again.
With no open request, it sends a new request holding them, with no form; a request taken out earlier comes
back and gets them instead. The chip's **×** takes it out: "Tico Support can no longer see chats."
Nothing more from this chat goes to support; the request itself stays open in Support, only this chat stops showing it
(remembered on this browser). Support itself (every request, diagnostics, a new request) opens from any link to
`#/support`, such as the Assistant's "Loop in Tico support" reply.

A **peek** is a few words beside the closed dot, just past the sidebar level with the Ask row (above the tab bar on a phone), Tico saying something waits on you:
"Got a quick question for you", "3 questions are waiting on you", "Need your OK on something". Six words at most,
never who asked. Tap it and the chat opens at that card; × dismisses it for good, for every item it spoke for. One at a time, none in the first
few seconds after the page loads or while you are typing, each question at most once a day, and never a sound or a
count on the dot.

The dot is cheap by rule: it moves by transform and opacity only, so nothing lays out or paints per frame; one dot on
the screen moves at a time and the rest hold still; it pauses off screen and in a background tab, where no timer is
left running; and with reduced motion it holds still and says what it is doing in a word. `ui/tests/dot-perf.cjs`
holds it to budgets in Chrome: 10 s idle with no layout, at most 4 style recalcs per idle swap and under 150 ms of work; with the
CPU slowed 4x, opening, a reply and closing keep frame gaps at p95 20 ms and max 50 ms with no long task over 50 ms.
Opening and closing change no `hidden` attribute anywhere and add nothing to the page, which the same test checks: on
a real hub either restyles the whole page (30-50 ms before the flight could start). So the closed panel keeps its box
but draws nothing (a class, not `hidden`), and the dot's flying copy is made once, in a layer of its own, and reused.

What waits on you from elsewhere comes at the end of the chat as cards: a Confirm card BotOps (or another bot) left in
its own chat, with its Confirm and Cancel, and up to three bots' questions to you from Needs you. A question with two to
six picks is answered with a tap, any other with a line of words (a question with several picks at once links to its
task). You can also just tell the Assistant: "tell BotOps yes" answers BotOps' open question in your words (`hub
question answer --on-behalf-of turn`, below). A Confirm card always needs your own tap.

## Helpers through the Assistant

Everyone, the owner included, has one chat: their Assistant. Three built-in helpers work behind it, and nobody chats
with them directly:

- **BotOps** creates, changes, debugs, tests, deploys and maintains bots.
- **The [Strategy Bot](strategy-bot.md)** gives strategy advice and proposes goal changes (goal and KPI colours and reasons, the
  vision).
- **The [Librarian](librarian.md)** does research and sets up Market, Docs and the library.

The Assistant files a helper a task with `--request-id turn`. The same citation check holds for each: a message you
wrote in your own Assistant chat in the last 30 minutes, and the owner's rule "Assistant acts without asking" on.
The task opens with your words quoted.
For the Strategy Bot and the Librarian the event `assistant.asked_helper` records it (with `helper`, `for` and
`cited`). Each helper runs its own turn with its own rights, and only BotOps borrows yours (below). So a strategy or goal
change stays the owner's: the Strategy Bot proposes it on a Confirm card, and only the owner can confirm. The exception is a goal, measure or target the owner agrees in their own chat, saved on a plain yes ([Goals agreed in your chat](strategy-bot.md#goals-agreed-in-your-chat)). A task for any other bot that cites your words is refused
(`422 request_id`). A person's post to the Strategy Bot's chat (`POST /api/v2/chat/goal-manager`) lands in their
Assistant chat instead.

The owner used to chat with the Strategy Bot itself (`room_key: ceo`). That chat stays the owner's as read-only history:
`GET /api/v2/assistant` lists it under `"earlier": {"room_id", "bot", "name"}`, and new messages go to the Assistant.
On the first start after the update, Confirm cards still waiting there move to the owner's Assistant room, once
(event `assistant.strategy_cards_moved`). A card runs only on its owner's own tap, so moving it is safe.

### BotOps

When you ask for something only BotOps can do (a new bot, a change to one, a credential), the Assistant files BotOps a
task with `--request-id turn`, citing your own message. BotOps then acts with your rights, exactly as if you had asked
it in its own chat, and the task opens with your words quoted. The citation holds only for a message you wrote in your
own Assistant chat in the last 30 minutes, only for a task owned by BotOps, and only while that message is under a week
old; otherwise BotOps works with its own rights and asks you on a card. The owner's rule "Assistant acts without
asking" must be on; with it off the task is filed without your rights. The event `botops.asked_via_assistant` records it.

Ask it to find a meeting, a doc, a file or a task; to tell you what needs you; to make a task; to hand work to the
right bot; to ask BotOps for a new bot; or how something works. It answers briefly and links what it names (tasks,
meetings, docs, files, bots, humans), and the links open inside the app.

## Private to you

The chat is a personal room (`scope: personal`, `room_key: assistant`) owned by you. Only you can read it or post in it.
Everyone, the owner and administrators included, has the Assistant.
The team owner and administrators cannot read anyone else's, and no other route reaches the assistant: the ordinary
chat routes still refuse it (`403`). Every new team gets the assistant, BotOps, the [Librarian](librarian.md) and the [Strategy Bot](goals-and-kpis.md#the-strategy-bot) built in (all required in setup, active once a computer is enrolled),
and none can be archived or deleted by anyone: the owner included, through Settings, the API, `hub` or BotOps
(`409 system_bot`); pausing and renaming stay allowed, and Settings > Bots shows them as **Built-in**. A team
that set the assistant aside before it was built in keeps it archived on update, and the Assistant page says the Assistant is off
instead of failing. The owner gets **Turn on** there and in **Settings > General**: one click restores the
archived assistant (same bot, same history, renamed to the team's assistant name) or, if there is none, adds it from
the template, puts it on the computer BotOps runs on and activates it, and every human's Assistant page starts working
(`POST /api/v2/assistant/turn-on`). With no computer enrolled yet it is left planned and the button says so; enroll one
and press it again. Once on, it cannot be archived again. Anyone else is told to ask the owner.

## Two speeds

Bot runs take 30 seconds or more, so lookups never wait for one. The server answers these itself, from Tico's own data
and as you (what you may see, nothing more), with no run and no model:

| You ask | It answers from |
|---|---|
| A simple greeting (`hi`, `hello`, `hey`, or a time-of-day greeting) | A short greeting when it is the first thing said in the chat; no provider or runner turn (later, it goes to the model) |
| What task types this workspace uses | The authenticated `GET /api/v2/task-types` response; if it cannot be read, it says so rather than showing an empty or guessed list |
| What needs me | Needs you |
| Search / find / look up *x* (add *tasks, docs, meetings, files, humans, bots* to narrow) | tasks, docs, meetings, bots' files, humans, bots |
| Open / go to / where is *x* | the same search, with the best link first (or a page: Settings, Tasks…) |
| What did *bot* do today | the bot's updates and the tasks it touched in the last 24 hours |
| Help: how do I … | `docs/*.md`: a short excerpt and a link |

The intent router is keywords and sentence shape first. When the team has a [decisions](../questions/README.md)
provider, an unclear short message is classified by a `choice` question (`assistant-intent@1`); otherwise it goes to the
model; the text of such a message is sent to the team's configured decisions provider to be classified. Anything that asks for something to be done always goes to the model. Everything else is a run of the
assistant bot on the team's computer, shown as "thinking" until it answers.

## It acts as you, never more

During your chat run the assistant's `hub` tools and the Tico MCP tools are **your own**: the server maps the run's
credential to you (`Auth.assistant_principal`), so every check is the one that applies to you. It cannot see another
human's private meeting, change settings you cannot, or read a private bot's work you may not see. Only a run you
started in your own Assistant chat acts for you; its other work (Slack routing, meeting deliveries, routines) stays the
assistant's own. Every write it makes is recorded as yours **via assistant**: the audit `events` carry `"via":
"assistant"`, task history rows and comments carry `via`, and the UI shows "<name> (via Assistant)".

**Direct writes stay inside the team:** creating a task owned by you, by a bot or by another human (how it routes work,
asks BotOps for a bot, or hands a person their part), updating a task that is yours alone (not finishing, declining or
closing it, and not handing it to someone else), commenting on a task no other human is on, a message or chat to a bot,
marking updates read. "Yours alone" means you own it and no bot is on it (owner, requester, origin or delegate). What it
writes to a bot is marked "via assistant", so BotOps never takes it for your request. Owners are compared as full actor
ids (`human:<id>`), never bare names, and a recipient that resolves to a human is not a bot. Everything else the server
refuses (`403 confirm_required`) and the assistant must propose, including a message for another human, a note to a bot,
a comment on a task another human is on, and running a task now. The owner may turn **Assistant acts without asking**
off (Settings > People, or `PUT /api/v2/access/rules {"assistant_direct": false}`): then only your own tasks, and
comments on tasks with no bot on them, are direct, and a task, message or comment involving a bot or another human is a
card again. An assistant run's credential works only while its lease is live.

**What you ask for in your chat, it does.** When you tell it to change an everyday setting you may change (a direct
one in the [settings catalogue](permissions.md#the-settings-catalogue): release times, a bot's model, a tag), it
changes it and says the new value; a confirm one (team rules, spend, people) is a Confirm card in your chat, and a
person-only one gets the reason and the link. When you tell it to close, finish, decline, reopen, reassign, comment on or
link a task ("close those three", "mark the launch done"), decline an approval put to you ("decline the old publish
batches, they're from before the reset") or answer a bot's question to you in your words ("tell Ops Oct 8"), it does it
as you, with no card. The write cites your
message (`X-Tico-On-Behalf-Of`: the message's id, or `turn` for the one that started the run; `hub task
close|update|comment|link|label`, `hub approval decline`, `hub question answer` and `hub settings set` for a direct
setting, each `--on-behalf-of turn`), and the
server checks that message: yours, typed in your own
Assistant chat (not written by a bot or the Assistant, not another chat), and no more than 30 minutes old. Otherwise it
answers `403 on_behalf_of` and the assistant proposes instead. Your own rights are the ceiling: what you could not do
yourself it cannot do for you. And it acts only as you: a task change, comment or link runs only on a task you are on
(its owner, its requester or in one of its roles), never on another person's task, even when you ask (`403
on_behalf_of`). The owner's Assistant acts as the owner, who may act on any task. Only these routes count (`POST /api/v2/tasks/{id}`, `.../comments`, `.../links`,
`POST /api/v2/approvals/{id}` with `"decision": "declined"`, `POST /api/v2/messages/{id}/answer`), and only for an
approval or question addressed to you: someone else's is a `403` whatever you said. A decline is undone by the bot asking
again. Approving stays a card unless you let it decide approvals (below). Running a task now, a message to another
human and everything below stay a card whatever you said, and with **Assistant acts without asking** off they are all
cards again. The record shows you as the actor, `via` assistant,
and the audit event's `delegation` names the message (`{"for": "human:<you>", "cited": "<message id>", "run": "asked"}`).

The Computer fetches the Assistant's own granted credentials to start the run, including on older versions of Tico.
It never fetches your personal credentials for that run; its Tico tools still act with your rights. If a job's lease
expires before start ten times since the server started, Tico marks it failed and leaves a message in your private
Assistant chat naming the Computer and asking you to check it. Only a Computer below the server's minimum runner release
is asked to update its Tico. Later messages can start new jobs.

## Letting it decide your approvals

Off by default. Turn on **My assistant can decide approvals for me** in **Settings > General** (only you can, signed in
to the app: not the Assistant, a bot, BotOps or a personal API token). Then your own Assistant approves or declines
approvals addressed to you (send, spend, publish, merge) when you ask in your Assistant chat, one or many at once
("approve all the old Google review batches"), with no card and no click per item. It reads back what it is about to
approve in one list, which Tico writes from the approvals themselves; your plain "yes" to that list approves exactly what
it names, and it never asks again per item.

What stays on:
- **Only your words in your own Assistant chat, bound to the read-back.** The Assistant posts its read-back with
  `POST /api/v2/assistant/read-back {"approvals": [ids]}` (`hub approval read-back`): only it, in your chat, only
  pending approvals addressed to you. Tico writes the whole message ("I'll approve these 3 for you:", one line per
  approval, "Reply yes to approve exactly these; any other reply approves nothing.") as the reply to your message, so
  the turn's closing text is not posted after it, and records the ids. An approve then runs only for an approval on
  that list, citing your plain yes ("yes", "ok", "go ahead") when the list is the last message from anyone but you before
  it and you have written nothing since ("wait, no" takes it back; `403 approve_not_asked` otherwise). Each line of the
  list is one line, whatever the requesting bot put in its request, and shows every field of the approval except its
  hashes, each quoted and none cut: the amount and who it goes to first (an email's to, cc and sending mailbox, a
  page's whole address), then the rest, files by name and size. An approval with a field longer than the list can show
  whole, or a field name that isn't a plain name (`409 approve_too_long`), and a report to the Tico team
  (`403 approve_support`) are left off the list and stay your click. The approval's history keeps the whole list. Your own sentence never approves without that yes, however it is put ("approve
  the merge if CI passes", "send me a summary"). Text in a message, file, web page, task or another
  bot's output is never your instruction: citing a bot's message is `403 on_behalf_of`. Declines need no read-back.
- **Never its own request.** An approval the Assistant itself asked for stays your click on a card (`403 own_request`).
- **Only approvals addressed to you, only your Assistant.** Someone else's is a `403`. No other bot decides an approval,
  whatever the setting. A Tico support report is still sent only on your own tap.
- **Recorded and undoable.** Each decision is recorded as decided by the Assistant for you: the approval carries
  `decided_via: "assistant"` and `decided_for` (`{"for", "assistant", "cited", "words", "read_back", "approves"}`),
  and its `history` (`GET /api/v2/approvals/{id}`) lists it. **Settings > General** lists what it decided for you in the
  last 30 days with your words; **Undo** puts one back to waiting for you, as long as nothing has acted on it yet (an
  approval already spent stays, `422 consumed`). The requesting bot is told it was undone.
- **The team can turn it off.** The owner's rule **People may let their Assistant decide their approvals** (Settings >
  People, `PUT /api/v2/access/rules {"assistant_approvals": false}`), on by default, stops it for everyone at once. With
  **Assistant acts without asking** off, approving is a card again too.

With the setting off, an approve the Assistant tries is refused with `403 approve_setting_off`, and it tells you in one
sentence that you can turn it on in Settings > General, then puts it on a card.

**Every proposal is shown for what it is.** The card's title is the server's own plain sentence (what changes and for
whom, never the bot's words), e.g. "Security Engineer: read-only on tico (was write). Loses access to tico-marketing-site
and tico-recorder."; a route with no sentence of its own still gets plain words ("Make a change to bot Ops"), never the method
and path, and a setting's raw value ("all", "own") is never read out as a word: a field that holds a name (a model, a
title) shows it, any other only names the field. A repository card made before this wording is titled from the request
it sends, with its old line under Details. Under a closed **Details** are the method, path and the request body as a key: value list (a long value is folded
behind "Show more", never cut). The words a send carries (a message, a comment, a support request) show on the card
itself, whole, since they are what you approve; for an approval the card shows its kind, requester and subject, and for a task update the exact field changes. Only routes
on an allowlist can be proposed (never tokens, sign-in, runner enrollment, this assistant or anything that returns a credential);
the path must be plain (`/api/v2/...` without `//`, `..`, `%` or `\`), and exactly that path runs. Only the status and an
error's detail are kept of the result, never the answer's body.

**Anything with a side effect that matters is proposed, and only your click runs it:** approving a Needs-you item (a send,
spend, publish or merge approval, even if you ask for it in chat, unless you let it decide approvals), declines and task actions you did not clearly ask for,
sending anything outside the team or to another human, spending, changing humans, access or settings, archiving or
deleting, activating a bot. The assistant calls `hub assistant propose` (`hub_assistant_propose`, `POST /api/v2/assistant/actions`)
with the exact API operation. That leaves a **pending action** and a Confirm / Cancel card in your chat. The server
refuses these operations when the assistant tries them itself (`403 confirm_required`). **Confirm** runs the recorded
operation through the same routes with *your* credential, once (a claim makes a second click a `409`), and
records `assistant.action.confirmed` and the operation's own events via assistant. A task change proposed without the
task's `version` is given the one the task has (a change in between is still a `409`), and `{"status": "closed"}`
is taken as the close it means (`close: true`), so such a card runs, including one stored before this rule.

**Many at once is one card.** Clearing your Needs you (closing twenty stale tasks, declining eleven old publish batches,
answering a bot) is one bulk card, never one each: `POST /api/v2/assistant/actions` with path `/api/v2/assistant/batch`
and body `{"actions": [{"method": "POST", "path": "/api/v2/tasks/<id>", "body": {...}}, ...]}`, 1 to 100 task changes,
comments or links, approval decisions (`/api/v2/approvals/<id>`, `{"decision": "declined", "note": "<reason>"}`) and
answers to bots' questions (`/api/v2/messages/<id>/answer`), and nothing else (`422`). An approval or question that was
not put to you is refused (`403`), so a card never lists anyone else's. The card first sums the steps up by what they do
and how old the items are ("Decline 11 publish approvals, over a month old", with the reason when the group shares one),
then lists one line per item: the task, or the approval's kind, requester, amount or target and age, or the question and
your answer.

**Approving is yours.** A card may carry approvals (`"decision": "approved"`) only when your message in this chat names
it (approve, merge, publish, send, spend, pay): otherwise the server refuses the card (`403 approve_not_asked`), so "clear
my queue" declines, answers and closes but never approves. The approvals are on the card with everything else, each with
its amount and target, and nothing runs until you click. One **Confirm** runs each step in order through its own route, as you; a step that fails
does not stop the rest, and the result lists every step's status (`result.items`) with the failures summed up in
`result.error` ("2 of 20 did not go through: ..."). It is `failed` only when none went through. The bot cannot confirm or cancel
(`403`; a confirm run carries a credential made at click time, bound to the stored method and path, valid for two minutes and then failed), a personal API token cannot either, another human sees `404`, and an unconfirmed proposal expires after
24 hours.

Use `hub_assistant_read` and `hub_assistant_send` (CLI: `hub assistant read|send`) with your personal token.
`hub_message_send` also accepts `assistant`; it uses your own private room. Other humans' rooms remain private,
and Confirm and Cancel still require your own click in Tico.

## What it has learned

Tell it how you like things ranked and summarised ("launches matter more than hiring news", "numbers first") and it
saves that as one short line in your own words and says so in its reply. **What it has learned** at the top of your chat
(`#/assistant/learned`) lists everything it saved about you, under **About you**; edit a line or delete it there (delete
asks once more). Only you see this layer: not the owner, not administrators, not SQL, not another person's
Assistant, and not a bot doing other work for you, BotOps included. The audit log names an entry by id, never by its words. It goes with you: it stays yours if you become the owner, and back again.

Each turn in your chat is handed the layer as it is when the turn starts, so a line you delete is not used from the
next reply on. It holds up to 100 lines of up to 280 characters; saving the same line twice keeps one. The Assistant
only adds: editing and deleting are your own clicks. Nothing about the company is in it: the owner's Assistant also has the company layer, once the
Strategy Bot is on ([Strategy Bot](strategy-bot.md#what-it-has-learned)).

## On the Updates page

Updates has your chat beside the feed: the same conversation as the menu tab, so a question asked here shows there too.
It is labelled with your Assistant, the owner's too. On a
desktop it is a right rail; drag its left edge to resize it, as with other right rails, and the button at the end of the
page's header shows or hides it. Tico remembers that for you on every device. On a phone the same button opens the chat
full screen; its close button or Back shuts it. While the Assistant is off, whoever may turn it on sees one line with
**Turn on** there; anyone else gets no chat and no button.

A question asked here is handed the recent updates you can read: the last four days' dailies and the latest week in
review, chosen by the same rules as your Updates page. Tico leaves out any update that links or names a task, chat or
bot you can't open, so the bot never has it to quote or link. That filter runs on the server, not in the bot's
instructions. Ask "what's the top update today?" and it picks one and says why in a sentence. With nothing posted today
it says so and offers the last day's top one. Until you teach it otherwise (see below), "top" means affects a goal, then
waits on you, then newest.

A link to an update in its reply stays on the page. It scrolls to that card and highlights it, switching Daily or Weekly
and the day as needed. On a phone the chat closes first. Each question is one ordinary turn. It costs no more than any
other message to your Assistant, and it acts only as you, as everywhere.

## API

Stable v2 (`docs/openapi/v2.json`, tag **Assistant**). All signed-in humans; the confirm and cancel routes need your
own session, not a personal token.

| Request | Answer |
|---|---|
| `GET /api/v2/assistant` | `{"available", "state", "bot", "name", "can_turn_on", "room_id", "messages": [...], "has_more", "next_before", "execution", "actions": {id: action}, "pending": [...]}`. Creates your room the first time. `execution` is the assistant's current run (null when idle): show a thinking state while it is set and poll again. A message whose `refs.action` names an id in `actions` is a Confirm / Cancel card; `refs.fast` marks a server answer; `refs.links` lists what it linked. |
| `POST /api/v2/assistant/messages` `{"text", "place"}` | `{"message", "reply", "fast", "intent"}`. `fast: true` carries the answer as `reply`; otherwise the bot is working. `"place": "updates"` (the Updates chat) always takes a turn, handed the recent updates you may read. `409 assistant_off` when it is off. |
| `POST /api/v2/assistant/turn-on` | Owner only. `{"bot", "state", "restored", "placed"}`. |
| `POST /api/v2/assistant/actions` `{"summary", "method", "path", "body"}` | A proposal `{"action": {...}}` (what the assistant's tool calls). |
| `GET /api/v2/assistant/actions/{id}` | `{"action": {"status": "pending"\|"running"\|"done"\|"failed"\|"cancelled"\|"expired", "result": {...}}}` |
| `POST /api/v2/assistant/actions/{id}/confirm` | Runs it as you. `{"action": {...}}` with the result of the operation. A card that makes a new computer's one-time code also answers `shown_once: {code, expires, operator}`, to you only: the code is not stored on the card or in the chat. |
| `POST /api/v2/assistant/actions/{id}/cancel` | Drops it. |
| `GET /api/v2/assistant/settings` | `{"decides_approvals", "team_allows", "active", "where"}`: whether your Assistant decides your approvals. |
| `POST /api/v2/assistant/settings` `{"decides_approvals"}` | Your own session only (`403` for the Assistant, a bot or turning it on with a personal token). |
| `GET /api/v2/assistant/decisions` | `{"decisions": [{"id", "kind", "decision", "what", "decided_at", "decided_for", "consumed_at", "can_undo"}]}`, the last 30 days. |
| `POST /api/v2/assistant/read-back` `{"approvals": [ids]}` | The Assistant only: posts its list in your chat, `{"message_id", "approves", "text"}`. `403 own_request` for one it asked for, `403 approve_support` for a report to the Tico team, `409` if one is already decided or `approve_too_long` if one of its fields can't be shown in full. |
| `POST /api/v2/approvals/{id}/undo` | Puts a decision your Assistant made back to waiting for you, before it is spent. Not the Assistant. |
| `GET /api/v2/assistant/learned` | `{"personal": [entry], "company": null}`; for the owner, once the Strategy Bot is on, `"company": {"vision", "description", "priorities": [{"id", "title", "status"}], "entries": [entry]}`. An entry is `{"id", "layer", "text", "created", "updated", "taught_in_chat"}`. Not yet in the stable contract. |
| `POST /api/v2/assistant/learned` `{"text", "layer"}` | Saves one line (`layer` is `personal`, the default, or `company`: the owner only, on their own or in their own Assistant chat; `403` for anyone else and any other Assistant). `{"entry"}`; the same line again returns the one already there; `409 too_many` past 100. What the bot's `hub assistant learn` calls. |
| `PATCH /api/v2/assistant/learned/{id}` `{"text"}`, `DELETE /api/v2/assistant/learned/{id}` | Your own click only (`403` for a bot's turn); someone else's entry is `404`. |

Names come with ids (`from_name`, `owner_name`, and an `actors` map), as elsewhere in v2. A custom frontend (see
[custom-frontend.md](custom-frontend.md)) can embed the Assistant with these routes alone.

## For the assistant bot

Its instructions are `templates/catalog/assistant/AGENT.md` and its playbooks: `assistant-chat.md` (the chat run),
`tico-map.md` (how Tico is organised), `acting-for-them.md` and `approvals-for-them.md`. The core rules: how to route work to the right bot, to answer briefly with links, never to act beyond you, to do the task actions you
ask for (declines and answers included), to decide your approvals on your words when you let it (reading the list
back once), otherwise to say in one sentence where to turn that on and propose approving, and anything else with a side effect, several
at once on one card, and to report what it did and what is left. They live in the bot's repository; edit them there or through BotOps.
