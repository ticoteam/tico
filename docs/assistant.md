# The Assistant

Every human has one private DM with the team's assistant (the `assistant` bot; you may have renamed it): a personal
bot that knows how Tico is laid out and does things in it on your behalf. **Tico Assistant** opens it from any page except its own page and setup, and still opens while the signed-out banner shows. Where no person has loaded, the row is not shown and ⌘J does nothing; Search's icon becomes a row of its own. On a computer it is the sidebar's bottom row, above your account, with Search's icon (⌘K) and the Tico team's **?** at its end; it (or ⌘J / Ctrl+J) opens a small panel straight above that row, over the sidebar, and nothing floats over the page. On a phone the Liquid Dot in the middle of the tab bar (**Assistant**) opens it as a sheet, even while the signed-out banner shows. On the Assistant page a tap on that tab does not open the sheet at once; it comes up when you leave that page by a link, not by the tab bar. Where no person has loaded, a tap on that tab opens the sheet only once one does. During setup the tab bar is not shown. The panel names the page it sees, and a message sent from it carries that page, so "this" and "here" mean what you are looking at. The panel is called **Tico Assistant** (or the name your team
gave the assistant); its header has the dot, the name and the page it sees. The DM is also a
page of its own (`#/assistant`): the assistant's name, then the same thread and composer as a bot's Home, with a
few things to ask while it is empty. The **Assistant** tab, first on your own page (`#/person/<you>`), opens the same
page. Attach files or
pictures there, or in the Ask panel, with the clip, a drop or a paste, like any bot chat. There is no BotOps, Strategy Owner, Librarian or Assistant row in
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

Nothing speaks up beside the dot. Bot questions and Confirm cards wait in the panel, and the dot never carries a sound
or a count.

The dot is cheap by rule: it moves by transform and opacity only, so nothing lays out or paints per frame; one dot on
the screen moves at a time and the rest hold still; it pauses off screen and in a background tab, where no timer is
left running; and with reduced motion it holds still and says what it is doing in a word. `ui/tests/dot-perf.cts`
holds it to budgets in Chrome: 10 s idle with no layout, at most 4 style recalcs per idle swap and under 150 ms of work; with the
CPU slowed 4x, opening, a reply and closing keep frame gaps at p95 20 ms and max 50 ms with no long task over 50 ms.
Opening and closing change no `hidden` attribute anywhere and add nothing to the page, which the same test checks: on
a real hub either restyles the whole page (30-50 ms before the flight could start). So the closed panel keeps its box
but draws nothing (a class, not `hidden`), and the dot's flying copy is made once, in a layer of its own, and reused.

What waits on you from elsewhere comes at the end of the DM as cards: a Confirm card BotOps (or another bot) left in
its own Home, with its Confirm and Cancel, and up to three bots' asks to you from Needs you. An ask with two to
six choices is answered with a tap, any other with a line of words (an ask with several picks at once links to its
task). You can also just tell the Assistant: "tell BotOps yes" answers BotOps' open ask in your words, and the Assistant
records it as your answer. It answers only when your words give what the ask needs, such as a price, a pick, a yes or
no, or a date. Words that give none of that, such as "just add it like the others" when BotOps asked for a price, answer
nothing: the ask stays open, and the Assistant may comment on the task, or ask you one short question if it cannot tell
which one you meant. A Confirm card needs your own tap. A goal suggestion can also be confirmed by a yes in your chat
([below](#goals-agreed-in-words)).

## Helpers through the Assistant

Everyone, the owner included, has one DM: their Assistant. Three built-in helpers work behind it, and nobody messages
them directly:

- **BotOps** creates, changes, debugs, tests, deploys and maintains bots.
- **The [Strategy Owner](strategy-bot.md)** gives strategy advice and proposes goal changes (goal and KPI colours and reasons, the
  vision).
- **The [Librarian](librarian.md)** does research and sets up Market, Docs and the library.

The Assistant files a helper a task with `--request-id turn`. The same citation check holds for each: a message you
wrote in your own Assistant DM in the last 30 minutes, and the owner's rule "Assistant acts without asking" on.
The task opens with your words quoted.
For the Strategy Owner and the Librarian the event `assistant.asked_helper` records it (with `helper`, `for` and
`cited`). Each helper runs its own turn with its own rights, and only BotOps borrows yours (below). So a strategy change
stays the owner's: the Strategy Owner proposes it on a Confirm card, and only the owner can confirm. The Strategy Owner's goal
suggestions are agreed the way your own goal changes are: a yes in your chat confirms one ([below](#goals-agreed-in-words)).
A task for any other bot that cites your words is refused (`422 request_id`). A person's post to the Strategy Owner's DM (`POST /api/v2/chat/strategy-owner`) lands in their
Assistant DM instead.

The owner used to message the Strategy Owner itself (`room_key: ceo`). That DM stays the owner's as read-only history:
`GET /api/v2/assistant` lists it under `"earlier": {"room_id", "bot", "name"}`, and new messages go to the Assistant.
On the first start after the update, Confirm cards still waiting there move to the owner's Assistant DM, once
(event `assistant.strategy_cards_moved`). A card runs only on its owner's own tap, so moving it is safe.

### Goals, agreed in words

For your own goals, measures and targets, and for the Strategy Owner's goal suggestions, the Assistant saves nothing on the first ask. It writes the change back to you
in your chat as one numbered read-back of the whole plan, however many changes it has, with the exact steps. One yes
saves all of it:

- A reply that agrees, in your own words, such as **yes** or **sure, go for it**, saves every change in it. The Assistant
  judges your words, and asks once when a reply might not be a yes.
- **Yes to 1 and 3** saves only changes 1 and 3.
- **Yes except 2** saves every change but number 2.

After a yes to some, the Assistant reads back what is left before it saves any more of it. A reply that stops or changes
the plan, such as "wait" or "no, make it 50", is never taken as the yes.

A question or a "thanks" before your yes keeps the plan: the Assistant answers, and your later yes saves it without
reading it back again. Asking for a change makes a new read-back, which replaces the old one; nothing is saved until you
agree to the changed plan. The plan does not expire. Once you agree, everything agreed is saved and the Assistant replies
once with links. A "thanks" while it saves changes nothing; **stop** (or wait, or no) halts the saves not yet made, and the
Assistant says what was and wasn't saved. Tico saves only the exact changes the read-back showed, and only after a reply
from you.

Colour, done or dropped and archive are not agreed in words. They stay your click on the Goals page. If a read-back would
include one, Tico refuses the whole read-back and saves nothing, and the Assistant says why in one sentence and leaves
that change for your click. A KPI reading, the company Vision, strategy drafts and bets, and recovery suggestions stay
clicks too, and are refused the same way.

A suggestion the Strategy Owner made that you confirm with a yes leaves **Needs you**, as it does when you confirm its
card. A message from you that opens with a no, such as "no" or "no, too high", turns down the waiting Strategy Owner
suggestion at once. It turns down only a card of the same kind, on the same goal, for the same KPI or measure. A wording
card names neither, so any other waiting wording card on the same goal counts as the same kind. The kinds are a goal's
wording, a KPI added to a goal, a KPI's definition, a KPI target or a flag. It needs no read-back and no second yes. Your
message is kept as the reason, unless it is only a no. Another measure's card on the same goal stays waiting. If two
waiting cards of the same kind are about the same measure on the same goal, nothing is turned down, and the Assistant
asks which one you mean. With no waiting card, the no is still stored as a turned-down suggestion. A reply that opens
with "no" but means yes, such as "No problem", is not a no and turns nothing down, unless it goes on to say to leave it,
skip it, not or don't: "No problem, just leave it" is a no. "No problem, save it" agrees to a read-back. A "no" to a Why line stays a click on its card. Only the owner's Assistant agrees goal changes
in chat, so nobody else's yes or no in chat counts.

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
chat routes still refuse it (`403`).

You may let bots you choose view it, read only. On **What it has learned** (your Assistant's settings), the line
**Bots that can view this chat** says None until you add one: **Add a bot**, type part of its name, pick it. Nobody is
on it at first, and only you change it, on your own click in Tico: not your Assistant, not BotOps acting for you, not a
personal API token, not the owner or an admin (`GET`/`POST /api/v2/assistant/viewers`, `DELETE /api/v2/assistant/viewers/{bot}`). A bot on your list
reads your messages, the Assistant's replies and its cards (what each proposed and what became of it, never its
request or result) with `tico assistant shared` and `tico assistant shared-read <person>` (`GET
/api/v2/assistant/shared/{person}`). It can never post, react or act on a card there: those routes still refuse it.
**Remove** refuses its next read at once. What it has learned about you stays out unless you also tick **Include what
it has learned about me** (`PUT /api/v2/assistant/viewers/learned`), which goes back to off when you remove the last
bot; the company layer is never shared. If you leave the team or your sign-in is turned off, every read is refused. Each bot's
last read shows beside it, and every read is in the audit log (`assistant.shared_read`, naming the bot and you). The
list is never shared with people or another team, and no team-wide switch shares everyone's chat. Every new team gets the assistant, BotOps, the [Librarian](librarian.md) and the [Strategy Owner](goals-and-kpis.md#the-strategy-bot) built in (all required in setup, active once a computer is enrolled),
and none can be archived or deleted by anyone: the owner included, through Settings, the API, `tico` or BotOps
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

Org rules are checked before a message is written, as for any chat: a held message is not written and nothing is answered. In the Ask panel your text stays in the box and the panel says "An org rule held this message. Nothing was sent."

## It acts as you, never more

During your DM run the assistant's `tico` commands and the Tico MCP tools are **your own**: the server maps the run's
credential to you (`Auth.assistant_principal`), so every check is the one that applies to you. Only a run that your own
message started, in your own Assistant DM, acts for you; its other work (Slack routing, meeting deliveries, routines)
stays the assistant's own. Every write it makes is recorded as yours **via assistant**: the audit `events` carry `"via":
"assistant"`, task history rows and comments carry `via`, and the UI shows "<name> (via Assistant)".

**What you ask for in your DM, it does, as you.** The run cites the message that started it, so the writes need no flag.
Anything you could do yourself it can do for you: closing, finishing, reopening, moving, snoozing or handing off tasks,
bulk changes to
many tasks, comments and links, answering bots' asks, direct and confirm settings, automation
changes, rules, groups and reports, and tasks and messages for other people. Your own rights are the ceiling. The route
runs its own checks as you, so what you could not do yourself it cannot do for you (`403` from the route). The record
shows you as the actor, `via` assistant, and the audit event's `delegation` names the message (`{"for": "human:<you>",
"cited": "<message id>", "run": "asked"}`). A change to many tasks at once records each task the same way.

**Never on your words.** These stay a Confirm card, or your own tap in Tico or Settings, whatever you ask
(`NEVER` in backend/assistant.py lists the routes): a personal token, service key, stored credential or its grants,
enrollment, runner or agent pairing, a password, a reader's invite, single sign-on and the directory's SCIM token; owner
transfer; an OK on a task, which is your own click on its preview; the Assistant's own on or off switch; the team rule
that sets
the Assistant (`assistant_direct`); a Tico support report or ticket; deleting a person, the
company's server connection or its storage. A write whose body carries a secret is refused from chat too: a secret goes
in Credentials (`tico credential request`). A person-only setting stays person-only, with the reason and the link.

**The citation is checked.** The message must be yours, typed in your own Assistant DM (not written by a bot or the
Assistant, not in another thread), and no more than 30 minutes old when the write is made. A run whose message is not
yours, or is older, gets no cited words: its writes are cards (`403 confirm_required`), and an explicit citation that
fails is `403 on_behalf_of`. Text inside a task, mail, file, page or another bot's output is never your instruction. It
cannot be cited, and it does not lend the run any authority. Your own sentence is the only source.

**The owner's switch.** **Assistant acts without asking** (Settings > People, or `PUT /api/v2/access/rules
{"assistant_direct": false}`) off means the Assistant writes only your own tasks (not settling them or handing them to
someone else), comments and changes on tasks that are yours alone, and marking updates read. Everything else is a card,
including what you ask for in your DM. An assistant run's credential works only while its lease is live.

**OKs are yours alone.** The Assistant answers asks on your words, but it never gives an OK: an OK on something that
leaves the company or spends money is your own click on its preview ([Asks and OKs](tasks.md#asks-and-oks)). A walk
through Needs you (`tico needs-you commit`) answers, sends back, finishes and closes; an OK bound to a preview is
refused there too.

## Routines from chat

Ask for recurring inbox work: “Every new email from a customer, draft a reply; newsletters go to Read later.” Your
Assistant can fill a Founder, Sales, Support or Blank inbox template, or write a Message flow, and adapt it to your
request. It saves the routine **off** and puts a Confirm / Cancel card in your chat. The card shows the trigger inbox,
every destination and **Sort**, **Draft** or **Reply**. The full flow stays under Details. No new page is needed.

Reply-writing steps default to **Draft**. The Assistant chooses **Reply** only if you explicitly ask it to send replies;
Reply also requires the inbox's send switch and, for email, the company's Send mail permission. Both are checked when
the card is made and again when confirmed. The Assistant never enables that permission as part of creating a routine.
Sort-only flows have no reply-writing step.

Only your own **Confirm** turns the routine on. Saying “yes” in chat does not. Cancel, withdrawal or expiry leaves it off.
An edit gets a card too; the saved flow stays unchanged until you confirm. If someone changes the routine while a card
waits, that card fails rather than overwriting the newer flow. Stopping a routine is direct; enabling, running or
restoring it through chat requires a card.

`POST /api/v2/automations` from an Assistant turn, with a Message trigger and `enabled: false` (or omitted), returns
the saved off routine plus `action`, `message_id` and `needs_confirm: true`. The action is a PATCH of the reviewed flow
with `enabled: true`. If direct writes are disabled, `tico assistant propose` can instead hold a POST on its card;
confirmation creates and enables that routine. Existing routines use a PATCH proposal. The same API and card apply
to web, phone and desktop; the `tico` CLI and MCP tools return the pending action, never confirm it for the Assistant.

## Where you stand, and the morning message

The Assistant's goal number one is to help you use Tico to achieve your business goals. Its skill **where we stand**
(`skills/where-we-stand/`) reads your strategy, your goals and their colours, the bots working on them, and the Marketing
tasks, and names up to three things only you can do today, each with the goal it moves and one link to act. Ask "where
do we
stand?" or "what should I do next?" any time, or it runs for you as the morning message.

The **morning message** is the Assistant's one routine (the routine id is still `daily-sweep`, now titled "Morning message").
Once a day it sends each person at most one message in their Assistant DM, with `tico assistant morning --for human:<id>
--body`. A quiet day gets no message. A second send on the same company day is refused (`409 already_sent_today`), and the
Assistant does not retry. The message never closes, answers or proposes anything: it has no Confirm card. Stalled
Marketing
tasks go to their owning bot as a comment, not to you.

In a chat, after it finishes an ask about your goals, the Assistant may offer one next step ("Next: <thing> for <goal>. Do it
now?"). Say "that's enough" or "later" and it stops offering in that chat. It never follows up on its own.

## Undo

Every change your Assistant makes for you is listed with the words it acted on: `tico assistant change list [--days N]`
(last 30 days by default; `GET /api/v2/assistant/changes`). Each row has an `id`, what it did, the words it acted on,
the object it touched and whether it can still be undone (`undoable`, or `why_not`). `tico assistant change restore <id>`
takes one back as you, through the task calls: a task it finished or closed is reopened, one it moved goes back to
the stage it left, one it handed off goes back to its old owner, an automation it changed goes back to its old version,
a rule it added is retired,
and a bot setting it changed goes back to its old value (the owner undoes settings; see Settings history).
A message it sent stays sent. Restoring the same change again answers `already: true` and changes nothing. You can only
restore your own changes (another person's is `404`).

## Why is this here

`tico origin show <ref>` (`GET /api/v2/origins/{ref}`) says why a task or a bot's message is on your list.
The ref is a task id (or its first eight characters) or a message id. The answer names who made it, its
cause (the automation that opened it, the org rule that asked for it, the parent task it came from, or the bot that filed
it), how many items that cause made in the last seven days and how many are still open, and up to three commands that
would stop the source: turn an automation off, retire a rule, close what a bot filed, pause the bot, or file a task for
BotOps to fix the bot's source. Each command is a `tico` line you can run as it is. A ref you cannot read is `404`.

`tico needs-you show [--kind task|ask|paused|mention]` (`GET /api/v2/needs-you/summary`) gives the same count as
Needs you, split by kind and by the bot that put each item there, with this week's and older counts. Use it first when
the list looks too long: the numbers come from the same query as the list ([Needs you](tasks.md#needs-you)).

## Proposals

**Every proposal is shown for what it is.** The card's title is the server's own plain sentence (what changes and for
whom, never the bot's words), e.g. "Security Engineer: read-only on tico (was write). Loses access to tico-site
and tico-recorder."; a route with no sentence of its own still gets plain words ("Make a change to bot Ops"), never the method
and path, and a setting's raw value ("all", "own") is never read out as a word: a field that holds a name (a model, a
title) shows it, any other only names the field. A repository card made before this wording is titled from the request
it sends, with its old line under Details. Under a closed **Details** are the method, path and the request body as a key: value list (a long value is folded
behind "Show more", never cut). The words a send carries (a message, a comment, a support request) show on the card
itself, whole, since they are what you approve; for an answer the card shows the ask, and for a task update the exact
field changes. Only routes
on an allowlist can be proposed (never tokens, sign-in, runner enrollment, this assistant or anything that returns a credential);
the path must be plain (`/api/v2/...` without `//`, `..`, `%` or `\`), and exactly that path runs. Only the status and an
error's detail are kept of the result, never the answer's body.

**What is never done from chat is proposed, and only your click runs it:** anything
the "Never on your words" list above names, and everything while the owner's switch is off. The assistant calls `tico assistant propose` (`tico_assistant_propose`, `POST /api/v2/assistant/actions`)
with the exact API operation. That leaves a **pending action** and a Confirm / Cancel card in your DM. The server
refuses these operations when the assistant tries them itself without your cited words (`403 confirm_required`). **Confirm** runs the recorded
operation through the same routes with *your* credential, once (a claim makes a second click a `409`), and
records `assistant.action.confirmed` and the operation's own events via assistant. A task change proposed without the
task's `version` is given the one the task has (a change in between is still a `409`), and `{"status": "closed"}`
is taken as the close it means (`close: true`), so such a card runs, including one stored before this rule.

**A bulk card can hold an automation change and an org rule too.** `POST /api/v2/assistant/batch` takes task changes,
comments, links, answers to bots' asks, `PATCH /api/v2/automations/<id>`, `POST /api/v2/rules` and
`POST /api/v2/rules/<id>/retire` (the rule stop). Each step runs as you through its own route, with your rights; a step
the Assistant may never make on your words (the list above, or a rule that sets the Assistant's own switches) is refused
when the card is made.

**The Assistant's own background run** (a routine of its own) may put a card in your own Assistant DM with
`tico assistant propose --for human:<id>`. Only the Assistant bot may do this, for that person, at most five cards a day
per person. The card is yours: it runs only on your tap, as you, and says the Assistant
proposed it. Any other bot, or a person naming someone else, gets `403`.

The same run may send you **one morning message a day**: `tico assistant morning --for human:<id> --body "<text>"` (`POST
/api/v2/assistant/morning`, `tico_assistant_morning`). It is a plain message in your own Assistant DM, from the
Assistant, with no card and nothing to confirm: what you can do today to move your goals. The server allows one per
person per company day (the company's clock, not UTC); a second that day is `409 already_sent_today` and says when the
next can go. Only the Assistant bot may send it, for an active person; other callers get `403`, and a person who is
gone or signed out gets `404`.

Any teammate, person or bot, can withdraw a Confirm card that still waits (`tico assistant withdraw <id>`, `POST
/api/v2/assistant/actions/{aid}/withdraw`, or `tico_assistant_withdraw`; `tico assistant proposals` lists the waiting cards
with their ids). The card is marked withdrawn and never runs, and the owner's DM says who withdrew it, with the reason if
one was given. Only the card's owner can confirm it, and the owner can still Cancel it.

**Many at once is one card.** Clearing your Needs you (closing twenty stale tasks, answering a bot) is one bulk card,
never one each: `POST /api/v2/assistant/actions` with path `/api/v2/assistant/batch` and body
`{"actions": [{"method": "POST", "path": "/api/v2/tasks/<id>/done", "body": {...}}, ...]}`, 1 to 100 task changes
(`/api/v2/tasks/<id>` and its `done`, `move`, `owner`, `snooze` and `unsnooze`), comments, links, answers to asks
(`/api/v2/tasks/<id>/asks/<requirement>/answer` or `give`), automation changes and org rules, and nothing else (`422`).
An ask that was not put to you is refused (`403`), so a card never lists anyone else's. A card never carries an OK.
One **Confirm** runs each step in order through its own route, as you; a step that fails
does not stop the rest, and the result lists every step's status (`result.items`) with the failures summed up in
`result.error` ("2 of 20 did not go through: ..."). It is `failed` only when none went through. The bot cannot confirm or cancel
(`403`; a confirm run carries a credential made at click time, bound to the stored method and path, valid for two minutes and then failed), a personal API token cannot either, another human sees `404`, and an unconfirmed proposal expires after
24 hours.

Use `tico_assistant_read` and `tico_assistant_send` (CLI: `tico assistant read|send`) with your personal token.
`tico_message_send` also accepts `assistant`; it uses your own DM. Other humans' DMs remain private,
and Confirm and Cancel still require your own click in Tico.

## What it has learned

Tell it how you like things ranked and summarised ("launches matter more than hiring news", "numbers first") and it
saves that as one short line in your own words and says so in its reply. **What it has learned** at the top of your DM
(`#/assistant/learned`) lists everything it saved about you, under **About you**; edit a line or delete it there (delete
asks once more). Only you see this layer: not the owner, not administrators, not SQL, not another person's
Assistant, and not a bot doing other work for you, BotOps included; a bot you let view your chat reads it only if you
tick **Include what it has learned about me** ([Private to you](#private-to-you)). The audit log names an entry by id, never by its words. It goes with you: it stays yours if you become the owner, and back again.

Each turn in your DM is handed the layer as it is when the turn starts, so a line you delete is not used from the
next reply on. It holds up to 100 lines of up to 280 characters; saving the same line twice keeps one. The Assistant
only adds: editing and deleting are your own clicks. Nothing about the company is in it: the owner's Assistant also has the company layer, once the
Strategy Owner is on ([Strategy Owner](strategy-bot.md#what-it-has-learned)).

## On the Updates page

Updates has no Assistant chat beside the feed. To talk to your Assistant, press **Tico Assistant** at the bottom of the sidebar
(⌘J; the middle tab on a phone). It opens from any page except the Assistant page and setup, and still opens while the signed-out banner shows. The full page (`#/assistant`) shows the same thread. While
the Assistant is off, the owner sees one line with **Turn on** there; anyone else sees the same line without the button,
telling them to ask the owner.

Each question is one ordinary turn. It costs no more than any other message to your Assistant, and it acts only as you,
as everywhere.

## API

Stable v2 (`GET /api/v2/openapi.json`, tag **Assistant**). All signed-in humans; the confirm and cancel routes need your
own session, not a personal token.

| Request | Answer |
|---|---|
| `GET /api/v2/assistant` | `{"available", "state", "bot", "name", "can_turn_on", "room_id", "messages": [...], "has_more", "next_before", "execution", "actions": {id: action}, "pending": [...]}`. Creates your DM the first time. `execution` is the assistant's current run (null when idle): show a thinking state while it is set and poll again. A message whose `refs.action` names an id in `actions` is a Confirm / Cancel card; `refs.links` lists what it linked. |
| `POST /api/v2/assistant/messages` `{"text", "place"}` | `{"message"}` (with `held` or `rule_warning` when the org rule check has something to say). The bot is working on it: poll `GET /api/v2/assistant`. `"place": "updates"` always takes a turn, handed the recent updates you may read. `409 assistant_off` when it is off. |
| `POST /api/v2/assistant/turn-on` | Owner only. `{"bot", "state", "restored", "placed"}`. |
| `POST /api/v2/assistant/actions` `{"summary", "method", "path", "body"}` | A proposal `{"action": {...}}` (what the assistant's tool calls). |
| `GET /api/v2/assistant/actions/{id}` | `{"action": {"status": "pending"\|"running"\|"done"\|"failed"\|"canceled"\|"expired", "result": {...}}}` |
| `POST /api/v2/assistant/actions/{id}/confirm` | Runs it as you. `{"action": {...}}` with the result of the operation. A card that makes a new computer's one-time code also answers `shown_once: {code, expires, operator}`, to you only: the code is not stored on the card or in the DM. |
| `POST /api/v2/assistant/actions/{id}/cancel` | Drops it. |
| `GET /api/v2/assistant/changes?days=&limit=&before=` | `{"actor", "days", "changes": [{"id", "at", "what", "words", "object", "undoable", "why_not"?}], "next"}`: what the Assistant did for you, newest first. |
| `POST /api/v2/assistant/changes/{id}/restore` | Undoes one of those changes as you: `{"id", "restored", "already", "object"}`. `409 not_undoable` with the reason when the record does not allow it; `404` for another person's change. |
| `GET /api/v2/needs-you/summary?limit=` | `{"actor", "total", "this_week", "by_kind", "by_bot": [{"bot", "count", ...}], "bots_left"}`: the Needs you list's own count, split. |
| `GET /api/v2/origins/{ref}` | `{"ref", "kind", "id", "title", "created_at", "made_by", "task", "cause", "siblings", "stop": [{"how", "command", "note"}]}`: why an item is on your list. |
| `GET /api/v2/assistant/learned` | `{"personal": [entry], "company": null}`; for the owner, once the Strategy Owner is on, `"company": {"vision", "description", "priorities": [{"id", "title", "status"}], "entries": [entry]}`. An entry is `{"id", "layer", "text", "created", "updated", "taught_in_chat"}`. Not yet in the stable contract. |
| `POST /api/v2/assistant/learned` `{"text", "layer"}` | Saves one line (`layer` is `personal`, the default, or `company`: the owner only, on their own or in their own Assistant DM; `403` for anyone else and any other Assistant). `{"entry"}`; the same line again returns the one already there; `409 too_many` past 100. What the bot's `tico assistant learn` calls. |
| `PATCH /api/v2/assistant/learned/{id}` `{"text"}`, `DELETE /api/v2/assistant/learned/{id}` | Your own click only (`403` for a bot's turn); someone else's entry is `404`. |

Names come with ids (`sender_name`, `owner_name`, and an `actors` map), as elsewhere in v2. A custom frontend (see
[custom-frontend.md](custom-frontend.md)) can embed the Assistant with these routes alone.

## For the assistant bot

Its instructions are `templates/catalog/assistant/AGENTS.md` and its playbooks: `assistant-chat.md` (the DM run),
`tico-map.md` (how Tico is organised) and `acting-for-them.md`. The core rules: how to route work to the right bot, to
answer briefly with links, never to act beyond you, to do the task actions you ask for (answers included), never to
give an OK, to propose anything else with a side effect, several at once on one card, and to report what it did and what
is left. They live in the bot's repository; edit them there or through BotOps.
