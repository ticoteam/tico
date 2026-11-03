# The Assistant

Every human has one private DM with the team's assistant (the `coo` bot; you may have renamed it): a personal
bot that knows how Tico is laid out and does things in it on your behalf. **Ask Tico** opens it from any page: on a
computer it is the sidebar's bottom row, above your account, with Search's icon (⌘K) and the Tico team's **?** at its end; it (or ⌘J / Ctrl+J)
opens a small panel straight above that row, over the sidebar, and nothing floats over the page. On a phone the Liquid Dot in the
middle of the tab bar opens it as a sheet. The panel names the page it sees, and a message sent from it carries that
page, so "this" and "here" mean what you are looking at. The panel is called **Tico Assistant** (or the name your team
gave the assistant); its header has the dot, the name and the page it sees. The DM is also a
page of its own (`#/assistant`): the assistant's name, then the same thread and composer as a bot's Home, with a
few things to ask while it is empty. The **Assistant** tab, first on your own page (`#/person/<you>`), opens the same
page. Attach files or
pictures there, or in the Ask panel, with the clip, a drop or a paste, like any bot chat. There is no BotOps, Strategy Bot, Librarian or Assistant row in
the rail: you ask the Assistant, and it hands its helpers what needs them (below). BotOps' own Home is still at
`#/bot/botops`. These built-ins stay out of the team chart and Goals tree; Settings > Bots still manages all four.
It works on a phone too.

In the thread the Assistant's turns are plain text; yours are dark bubbles on the right. The live dot stays in the
header: opening flies it up from the row (or tab) to its place beside the name, quickly, closing flies it back down; while the
panel is open a close mark holds the dot's place. Bots' questions
and Confirm cards sit in the thread by when they were asked, among the messages, so nothing waits out of sight.

The Ask panel is only the Assistant. Talking to the people at Tico is a separate chat: the **?** beside Search (on a
phone, beside Search in the More drawer), or any link to `#/support`, such as the Assistant's "Message the Tico team"
reply, opens it ([Support](support.md)). Nothing from the DM goes to the Tico team.

A **peek** is a few words beside the closed dot, just past the sidebar level with the Ask row (above the tab bar on a phone), Tico saying something waits on you:
"Got a quick question for you", "3 questions are waiting on you", "Need your OK on something". Six words at most,
never who asked. Tap it and the DM opens at that card; × dismisses it for good, for every item it spoke for. One at a time, none in the first
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

What waits on you from elsewhere comes at the end of the DM as cards: a Confirm card BotOps (or another bot) left in
its own Home, with its Confirm and Cancel, and up to three bots' questions to you from Needs you. A question with two to
six picks is answered with a tap, any other with a line of words (a question with several picks at once links to its
task). You can also just tell the Assistant: "tell BotOps yes" answers BotOps' open question in your words (`hub
question answer --on-behalf-of turn`, below). A Confirm card needs your own tap. A goal suggestion can also be confirmed by a plain yes in your chat ([below](#goals-agreed-in-words)).

## Helpers through the Assistant

Everyone, the owner included, has one DM: their Assistant. Three built-in helpers work behind it, and nobody messages
them directly:

- **BotOps** creates, changes, debugs, tests, deploys and maintains bots.
- **The [Strategy Bot](strategy-bot.md)** gives strategy advice and proposes goal changes (goal and KPI colours and reasons, the
  vision).
- **The [Librarian](librarian.md)** does research and sets up Market, Docs and the library.

The Assistant files a helper a task with `--request-id turn`. The same citation check holds for each: a message you
wrote in your own Assistant DM in the last 30 minutes, and the owner's rule "Assistant acts without asking" on.
The task opens with your words quoted.
For the Strategy Bot and the Librarian the event `assistant.asked_helper` records it (with `helper`, `for` and
`cited`). Each helper runs its own turn with its own rights, and only BotOps borrows yours (below). So a strategy change
stays the owner's: the Strategy Bot proposes it on a Confirm card, and only the owner can confirm. The Strategy Bot's goal
suggestions are agreed the way your own goal changes are: a plain yes in your chat confirms one ([below](#goals-agreed-in-words)).
A task for any other bot that cites your words is refused (`422 request_id`). A person's post to the Strategy Bot's DM (`POST /api/v2/chat/goal-manager`) lands in their
Assistant DM instead.

The owner used to message the Strategy Bot itself (`room_key: ceo`). That DM stays the owner's as read-only history:
`GET /api/v2/assistant` lists it under `"earlier": {"room_id", "bot", "name"}`, and new messages go to the Assistant.
On the first start after the update, Confirm cards still waiting there move to the owner's Assistant DM, once
(event `assistant.strategy_cards_moved`). A card runs only on its owner's own tap, so moving it is safe.

### Goals, agreed in words

For your own goals, measures and targets, and for the Strategy Bot's goal suggestions, the Assistant saves nothing on the first ask. It writes the change back to you
in your chat as a numbered read-back, with the exact steps. Only your yes saves it:

- A plain yes as your next message, such as **yes**, **ok**, **go ahead** or **save it**, saves every change in it.
- **Yes to 1 and 3** or **yes except 2** saves only those numbered changes.

Your yes counts only if it is your very next message after the read-back, with nothing from anyone else in between and
nothing else from you after it, and within 30 minutes of the read-back. Any other reply saves nothing. Ask again, and the
Assistant reads it back again.

Colour, done or dropped and archive are not agreed in words. They stay your click on the Goals page, and the read-back
says so and leaves them out. A KPI reading, the company Vision, strategy drafts and bets, and recovery suggestions stay
clicks too.

A suggestion the Strategy Bot made that you confirm with a plain yes leaves **Needs you**, as it does when you confirm
its card. A message from you that opens with a no, such as "no" or "no, too high", turns down a Strategy Bot suggestion
about a goal's wording, a KPI added to a goal, a KPI's definition, a KPI target or a flag at once. It needs no read-back
and no second yes. Your message is kept as the reason, unless it is only a no. A "no" to a Why line stays a click on its
card. Only the owner's Assistant agrees goal changes in chat, so nobody else's yes or no in chat counts.

### BotOps

When you ask for something only BotOps can do (a new bot, a change to one, a credential), the Assistant files BotOps a
task with `--request-id turn`, citing your own message. BotOps then acts with your rights, exactly as if you had asked
it in its own Home, and the task opens with your words quoted. The citation holds only for a message you wrote in your
own Assistant DM in the last 30 minutes, only for a task owned by BotOps, and only while that message is under a week
old; otherwise BotOps works with its own rights and asks you on a card. The owner's rule "Assistant acts without
asking" must be on; with it off the task is filed without your rights. The event `botops.asked_via_assistant` records it.

Ask it to find a meeting, a doc, a file or a task; to tell you what needs you; to make a task; to hand work to the
right bot; to ask BotOps for a new bot; or how something works. It answers briefly and links what it names (tasks,
meetings, docs, files, bots, humans), and the links open inside the app.

## Private to you

The DM is a personal thread (`scope: personal`, `room_key: assistant`) owned by you. Only you can read it or post in it.
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

## Every message is a turn

Each message you send your Assistant is a turn of the assistant bot on the team's computer, shown as "thinking" until it
answers. Nothing is answered by keyword or from a fixed reply: "what is waiting on me", a search, "open X", what a bot
did today and how to do something all reach the model, which reads the question and answers it from Tico's own data,
as you (what you may see, nothing more), with links. A turn takes 30 seconds or more; a greeting is a turn too.

Org rules are checked before a message is written, as for any chat: a held message is not written and nothing is answered.

## It acts as you, never more

During your DM run the assistant's `hub` tools and the Tico MCP tools are **your own**: the server maps the run's
credential to you (`Auth.assistant_principal`), so every check is the one that applies to you. Only a run that your own
message started, in your own Assistant DM, acts for you; its other work (Slack routing, meeting deliveries, routines)
stays the assistant's own. Every write it makes is recorded as yours **via assistant**: the audit `events` carry `"via":
"assistant"`, task history rows and comments carry `via`, and the UI shows "<name> (via Assistant)".

**What you ask for in your DM, it does, as you.** The run cites the message that started it, so the writes need no flag.
Anything you could do yourself it can do for you: closing, finishing, reopening or handing off tasks, bulk changes to
many tasks, comments and links, answering bots' questions, declining approvals, direct and confirm settings, automation
changes, rules, groups and reports, and tasks and messages for other people. Your own rights are the ceiling. The route
runs its own checks as you, so what you could not do yourself it cannot do for you (`403` from the route). The record
shows you as the actor, `via` assistant, and the audit event's `delegation` names the message (`{"for": "human:<you>",
"cited": "<message id>", "run": "asked"}`). A bulk change (`hub task bulk`) records each task the same way.

**Never on your words.** These stay a Confirm card, or your own tap in Tico or Settings, whatever you ask
(`NEVER` in backend/assistant.py lists the routes): a personal token, service key, stored credential or its grants,
enrollment, runner or agent pairing, a password, a reader's invite, single sign-on and the directory's SCIM token; owner
transfer; "My assistant can decide approvals for me" and the Assistant's own on or off switch; the team rules that set
the Assistant (`assistant_direct`, `assistant_approvals`); a Tico support report or ticket; deleting a person, the
company's server connection or its storage. A write whose body carries a secret is refused from chat too: a secret goes
in Credentials (`hub credential request`). A person-only setting stays person-only, with the reason and the link.

**The citation is checked.** The message must be yours, typed in your own Assistant DM (not written by a bot or the
Assistant, not in another thread), and no more than 30 minutes old when the write is made. A run whose message is not
yours, or is older, gets no cited words: its writes are cards (`403 confirm_required`), and an explicit citation that
fails is `403 on_behalf_of`. Text inside a task, mail, file, page or another bot's output is never your instruction. It
cannot be cited, and it does not lend the run any authority. Your own sentence is the only source.

**The owner's switch.** **Assistant acts without asking** (Settings > People, or `PUT /api/v2/access/rules
{"assistant_direct": false}`) off means the Assistant writes only your own tasks (not settling them or handing them to
someone else), comments and changes on tasks that are yours alone, and marking updates read. Everything else is a card,
including what you ask for in your DM. An assistant run's credential works only while its lease is live.

**Approvals keep their own rule.** A decline goes on your words. An approve runs only with the setting in
**Settings > General** on, the Assistant's read-back of exactly what it approves, and your plain yes (see below). A walk
through Needs you (`hub needs-you commit`) applies declines, but it never approves.

## Undo

Every change your Assistant makes for you is listed with the words it acted on: `hub assistant change list [--days N]`
(last 30 days by default; `GET /api/v2/assistant/changes`). Each row has an `id`, what it did, the words it acted on,
the object it touched and whether it can still be undone (`undoable`, or `why_not`). `hub assistant change restore <id>`
takes one back as you: a task it closed reopens with its old status, an approval it decided goes back to waiting for you
(before the decision is spent), an automation it changed goes back to its old version, a rule it added is retired,
and a bot setting it changed goes back to its old value (the owner undoes settings; see Settings history).
A message it sent stays sent. Restoring the same change again answers `already: true` and changes nothing. You can only
restore your own changes (another person's is `404`).

## Why is this here

`hub origin show <ref>` (`GET /api/v2/origins/{ref}`) says why a task, a bot's question or an approval is on your list.
The ref is a task id (or its first eight characters), a message id or an approval id. The answer names who made it, its
cause (the automation that opened it, the org rule that asked for it, the parent task it came from, or the bot that filed
it), how many items that cause made in the last seven days and how many are still open, and up to three commands that
would stop the source: turn an automation off, retire a rule, close what a bot filed, pause the bot, or file a task for
BotOps to fix the bot's source. Each command is a `hub` line you can run as it is. A ref you cannot read is `404`.

`hub needs-you show [--by bot|kind]` gives the same count as the Needs you board, split by kind (task, question,
approval) and by the bot that put each item there, with this week's and older counts. Use it first when the list looks
too long: the numbers come from the same query as the board.

## Letting it decide your approvals

Off by default. Turn on **My assistant can decide approvals for me** in **Settings > General** (only you can, signed in
to the app: not the Assistant, a bot, BotOps or a personal API token). Then your own Assistant approves or declines
approvals addressed to you (send, spend, publish, merge) when you ask in your Assistant DM, one or many at once
("approve all the old Google review batches"), with no card and no click per item. It reads back what it is about to
approve in one list, which Tico writes from the approvals themselves; your plain "yes" to that list approves exactly what
it names, and it never asks again per item.

What stays on:
- **Only your words in your own Assistant DM, bound to the read-back.** The Assistant posts its read-back with
  `POST /api/v2/assistant/read-back {"approvals": [ids]}` (`hub approval read-back`): only it, in your DM, only
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

**Approvals, and what is never done from chat, are proposed, and only your click runs it:** approving a Needs-you item
(a send, spend, publish or merge approval, even if you ask for it in your DM, unless you let it decide approvals), anything
the "Never on your words" list above names, and everything while the owner's switch is off. The assistant calls `hub assistant propose` (`hub_assistant_propose`, `POST /api/v2/assistant/actions`)
with the exact API operation. That leaves a **pending action** and a Confirm / Cancel card in your DM. The server
refuses these operations when the assistant tries them itself without your cited words (`403 confirm_required`). **Confirm** runs the recorded
operation through the same routes with *your* credential, once (a claim makes a second click a `409`), and
records `assistant.action.confirmed` and the operation's own events via assistant. A task change proposed without the
task's `version` is given the one the task has (a change in between is still a `409`), and `{"status": "closed"}`
is taken as the close it means (`close: true`), so such a card runs, including one stored before this rule.

**A bulk card can hold an automation change and an org rule too.** `POST /api/v2/assistant/batch` takes task changes,
comments, links, approval declines and answers, `PATCH /api/v2/automations/<id>`, `POST /api/v2/rules` and
`POST /api/v2/rules/<id>/retire` (the rule stop). Each step runs as you through its own route, with your rights; a step
the Assistant may never make on your words (the list above, or a rule that sets the Assistant's own switches) is refused
when the card is made.

**The Assistant's own background run** (its daily sweep) may put a card in your own Assistant DM with
`hub assistant propose --for human:<id>`. Only the Assistant bot may do this, for that person, at most five cards a day
per person, and never with an approve step. The card is yours: it runs only on your tap, as you, and says the Assistant
proposed it. Any other bot, or a person naming someone else, gets `403`.

Any teammate, person or bot, can withdraw a Confirm card that still waits (`hub assistant withdraw <id>`, `POST
/api/v2/assistant/actions/{aid}/withdraw`, or `hub_assistant_withdraw`; `hub assistant proposals` lists the waiting cards
with their ids). The card is marked withdrawn and never runs, and the owner's DM says who withdrew it, with the reason if
one was given. Only the card's owner can confirm it, and the owner can still Cancel it.

**Many at once is one card.** Clearing your Needs you (closing twenty stale tasks, declining eleven old publish batches,
answering a bot) is one bulk card, never one each: `POST /api/v2/assistant/actions` with path `/api/v2/assistant/batch`
and body `{"actions": [{"method": "POST", "path": "/api/v2/tasks/<id>", "body": {...}}, ...]}`, 1 to 100 task changes,
comments or links, approval decisions (`/api/v2/approvals/<id>`, `{"decision": "declined", "note": "<reason>"}`) and
answers to bots' questions (`/api/v2/messages/<id>/answer`), and nothing else (`422`). An approval or question that was
not put to you is refused (`403`), so a card never lists anyone else's. The card first sums the steps up by what they do
and how old the items are ("Decline 11 publish approvals, over a month old", with the reason when the group shares one),
then lists one line per item: the task, or the approval's kind, requester, amount or target and age, or the question and
your answer.

**Approving is yours.** A card may carry approvals (`"decision": "approved"`) only when your message in this DM names
it (approve, merge, publish, send, spend, pay): otherwise the server refuses the card (`403 approve_not_asked`), so "clear
my queue" declines, answers and closes but never approves. The approvals are on the card with everything else, each with
its amount and target, and nothing runs until you click. One **Confirm** runs each step in order through its own route, as you; a step that fails
does not stop the rest, and the result lists every step's status (`result.items`) with the failures summed up in
`result.error` ("2 of 20 did not go through: ..."). It is `failed` only when none went through. The bot cannot confirm or cancel
(`403`; a confirm run carries a credential made at click time, bound to the stored method and path, valid for two minutes and then failed), a personal API token cannot either, another human sees `404`, and an unconfirmed proposal expires after
24 hours.

Use `hub_assistant_read` and `hub_assistant_send` (CLI: `hub assistant read|send`) with your personal token.
`hub_message_send` also accepts `assistant`; it uses your own DM. Other humans' DMs remain private,
and Confirm and Cancel still require your own click in Tico.

## What it has learned

Tell it how you like things ranked and summarised ("launches matter more than hiring news", "numbers first") and it
saves that as one short line in your own words and says so in its reply. **What it has learned** at the top of your DM
(`#/assistant/learned`) lists everything it saved about you, under **About you**; edit a line or delete it there (delete
asks once more). Only you see this layer: not the owner, not administrators, not SQL, not another person's
Assistant, and not a bot doing other work for you, BotOps included. The audit log names an entry by id, never by its words. It goes with you: it stays yours if you become the owner, and back again.

Each turn in your DM is handed the layer as it is when the turn starts, so a line you delete is not used from the
next reply on. It holds up to 100 lines of up to 280 characters; saving the same line twice keeps one. The Assistant
only adds: editing and deleting are your own clicks. Nothing about the company is in it: the owner's Assistant also has the company layer, once the
Strategy Bot is on ([Strategy Bot](strategy-bot.md#what-it-has-learned)).

## On the Updates page

Updates has your DM beside the feed: the same thread as the menu tab, so a question asked here shows there too.
It is labelled with your Assistant, the owner's too. On a
desktop it is a right rail; drag its left edge to resize it, as with other right rails, and the button at the end of the
page's header shows or hides it. Tico remembers that for you on every device. On a phone the same button opens the DM
full screen; its close button or Back shuts it. While the Assistant is off, whoever may turn it on sees one line with
**Turn on** there; anyone else gets no DM and no button.

A question asked here is handed the recent updates you can read: the last four days' dailies and the latest week in
review, chosen by the same rules as your Updates page. Tico leaves out any update that links or names a task, thread or
bot you can't open, so the bot never has it to quote or link. That filter runs on the server, not in the bot's
instructions. Ask "what's the top update today?" and it picks one and says why in a sentence. With nothing posted today
it says so and offers the last day's top one. Until you teach it otherwise (see below), "top" means affects a goal, then
waits on you, then newest.

A link to an update in its reply stays on the page. It scrolls to that card and highlights it, switching Daily or Weekly
and the day as needed. On a phone the DM closes first. Each question is one ordinary turn. It costs no more than any
other message to your Assistant, and it acts only as you, as everywhere.

## API

Stable v2 (`docs/openapi/v2.json`, tag **Assistant**). All signed-in humans; the confirm and cancel routes need your
own session, not a personal token.

| Request | Answer |
|---|---|
| `GET /api/v2/assistant` | `{"available", "state", "bot", "name", "can_turn_on", "room_id", "messages": [...], "has_more", "next_before", "execution", "actions": {id: action}, "pending": [...]}`. Creates your DM the first time. `execution` is the assistant's current run (null when idle): show a thinking state while it is set and poll again. A message whose `refs.action` names an id in `actions` is a Confirm / Cancel card; `refs.links` lists what it linked. |
| `POST /api/v2/assistant/messages` `{"text", "place"}` | `{"message"}` (with `held` or `rule_warning` when the org rule check has something to say). The bot is working on it: poll `GET /api/v2/assistant`. `"place": "updates"` (the Updates DM) always takes a turn, handed the recent updates you may read. `409 assistant_off` when it is off. |
| `POST /api/v2/assistant/turn-on` | Owner only. `{"bot", "state", "restored", "placed"}`. |
| `POST /api/v2/assistant/actions` `{"summary", "method", "path", "body"}` | A proposal `{"action": {...}}` (what the assistant's tool calls). |
| `GET /api/v2/assistant/actions/{id}` | `{"action": {"status": "pending"\|"running"\|"done"\|"failed"\|"canceled"\|"expired", "result": {...}}}` |
| `POST /api/v2/assistant/actions/{id}/confirm` | Runs it as you. `{"action": {...}}` with the result of the operation. A card that makes a new computer's one-time code also answers `shown_once: {code, expires, operator}`, to you only: the code is not stored on the card or in the DM. |
| `POST /api/v2/assistant/actions/{id}/cancel` | Drops it. |
| `GET /api/v2/assistant/settings` | `{"decides_approvals", "team_allows", "active", "where"}`: whether your Assistant decides your approvals. |
| `POST /api/v2/assistant/settings` `{"decides_approvals"}` | Your own session only (`403` for the Assistant, a bot or turning it on with a personal token). |
| `GET /api/v2/assistant/changes?days=&limit=&before=` | `{"actor", "days", "changes": [{"id", "at", "what", "words", "object", "undoable", "why_not"?}], "next"}`: what the Assistant did for you, newest first. |
| `POST /api/v2/assistant/changes/{id}/restore` | Undoes one of those changes as you: `{"id", "restored", "already", "object"}`. `409 not_undoable` with the reason when the record does not allow it; `404` for another person's change. |
| `GET /api/v2/needs-you/summary?limit=` | `{"actor", "total", "this_week", "by_kind", "by_bot": [{"bot", "count", ...}], "bots_left"}`: the board's own count, split. |
| `GET /api/v2/origins/{ref}` | `{"ref", "kind", "id", "title", "created_at", "made_by", "task", "cause", "siblings", "stop": [{"how", "command", "note"}]}`: why an item is on your list. |
| `GET /api/v2/assistant/decisions` | `{"decisions": [{"id", "kind", "decision", "what", "decided_at", "decided_for", "consumed_at", "can_undo"}]}`, the last 30 days. |
| `POST /api/v2/assistant/read-back` `{"approvals": [ids]}` | The Assistant only: posts its list in your DM, `{"message_id", "approves", "text"}`. `403 own_request` for one it asked for, `403 approve_support` for a report to the Tico team, `409` if one is already decided or `approve_too_long` if one of its fields can't be shown in full. |
| `POST /api/v2/approvals/{id}/undo` | Puts a decision your Assistant made back to waiting for you, before it is spent. Not the Assistant. |
| `GET /api/v2/assistant/learned` | `{"personal": [entry], "company": null}`; for the owner, once the Strategy Bot is on, `"company": {"vision", "description", "priorities": [{"id", "title", "status"}], "entries": [entry]}`. An entry is `{"id", "layer", "text", "created", "updated", "taught_in_chat"}`. Not yet in the stable contract. |
| `POST /api/v2/assistant/learned` `{"text", "layer"}` | Saves one line (`layer` is `personal`, the default, or `company`: the owner only, on their own or in their own Assistant DM; `403` for anyone else and any other Assistant). `{"entry"}`; the same line again returns the one already there; `409 too_many` past 100. What the bot's `hub assistant learn` calls. |
| `PATCH /api/v2/assistant/learned/{id}` `{"text"}`, `DELETE /api/v2/assistant/learned/{id}` | Your own click only (`403` for a bot's turn); someone else's entry is `404`. |

Names come with ids (`sender_name`, `owner_name`, and an `actors` map), as elsewhere in v2. A custom frontend (see
[custom-frontend.md](custom-frontend.md)) can embed the Assistant with these routes alone.

## For the assistant bot

Its instructions are `templates/catalog/assistant/AGENT.md` and its playbooks: `assistant-chat.md` (the DM run),
`tico-map.md` (how Tico is organised), `acting-for-them.md` and `approvals-for-them.md`. The core rules: how to route work to the right bot, to answer briefly with links, never to act beyond you, to do the task actions you
ask for (declines and answers included), to decide your approvals on your words when you let it (reading the list
back once), otherwise to say in one sentence where to turn that on and propose approving, and anything else with a side effect, several
at once on one card, and to report what it did and what is left. They live in the bot's repository; edit them there or through BotOps.
