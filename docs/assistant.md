# The Assistant

Every human has one private chat with the team's assistant (the `coo` bot; you may have renamed it): a personal
bot that knows how Tico is laid out and does things in it on your behalf. **Assistant** in the main left rail opens it
as a page of its own (`#/assistant`): the assistant's name, then the same thread and composer as a bot's chat, with a
few things to ask while it is empty. The **Assistant** tab, first on your own page (`#/person/<you>`), opens the same
page, and so does **Ask the Assistant…** at the bottom of search (⌘K), with your words in the box. The room takes text
only, so the composer has no attach button. **BotOps** beside it in the rail opens BotOps' bot page. These built-ins stay out of the team chart and Goals tree; Settings > Bots still manages all four.
It works on a phone too.

Ask it to find a meeting, a doc, a file or a task; to tell you what needs you; to make a task; to hand work to the
right bot; to ask BotOps for a new bot; or how something works. It answers briefly and links what it names (tasks,
meetings, docs, files, bots, humans), and the links open inside the app.

## Private to you

The chat is a personal room (`scope: personal`, `room_key: assistant`) owned by you. Only you can read it or post in it.
The team owner and administrators cannot read anyone else's, and no other route reaches the assistant: the ordinary
chat routes still refuse it (`403`). Every new team gets the assistant, BotOps, the [Librarian](librarian.md) and the [Goal Manager](goals-and-kpis.md#the-goal-manager) built in (all required in setup, active once a computer is enrolled),
and none can be archived or deleted by anyone: the owner included, through Settings, the API, `hub` or BotOps
(`409 system_bot`); pausing and renaming stay allowed, and Settings > Bots shows them as **Built-in**. A team
that set the assistant aside before it was built in keeps it archived on update, and the Assistant page says the Assistant is off
instead of failing. The owner gets **Turn on Assistant** there and at the top of **Settings > Bots**: one click restores the
archived assistant (same bot, same history, renamed to the team's assistant name) or, if there is none, adds it from
the template, puts it on the computer BotOps runs on and activates it, and every human's Assistant page starts working
(`POST /api/v2/assistant/turn-on`). With no computer enrolled yet it is left planned and the button says so; enroll one
and press it again. Once on, it cannot be archived again. Anyone else is told to ask the owner.

## Two speeds

Bot runs take 30 seconds or more, so lookups never wait for one. The server answers these itself, from Tico's own data
and as you (what you may see, nothing more), with no run and no model:

| You ask | It answers from |
|---|---|
| A simple greeting (`hi`, `hello`, `hey`, or a time-of-day greeting) | A short greeting; no provider or runner turn |
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

**Direct writes stay inside the team:** creating a task owned by you or by a bot (how it routes work and asks BotOps for a
bot), updating a task that is yours alone (not finishing, declining or closing it, and not handing it to someone else),
commenting on a task no other human is on, a message or chat to a bot, marking updates read. "Yours alone" means you own it
and no bot is on it (owner, requester, origin or delegate). What it writes to a bot is marked "via assistant", so BotOps
never takes it for your request. Owners are compared as full actor ids (`human:<id>`), never bare names, and a recipient
that resolves to a human is not a bot. Everything else the server refuses (`403 confirm_required`) and the assistant must
propose, including a task or message for another human, a note to a bot, a comment on a task another human is on, and
running a task now. The owner may turn **Assistant acts without asking** off (Settings > Humans, or `PUT
/api/v2/access/rules {"assistant_direct": false}`): then only your own tasks, and comments on tasks with no bot on them, are
direct, and a task, message or comment involving a bot is a card again. An assistant run's credential works only while its
lease is live.

**What you ask for in your chat, it does.** When you tell it to close, finish, decline, reopen, reassign, comment on or
link a task ("close those three", "mark the launch done"), it does it as you, with no card. The write cites your
message (`X-Tico-On-Behalf-Of`: the message's id, or `turn` for the one that started the run; `hub task
close|update|comment|link|label --on-behalf-of turn`), and the server checks that message: yours, typed in your own
Assistant chat (not written by a bot or the Assistant, not another chat), and no more than 30 minutes old. Otherwise it
answers `403 on_behalf_of` and the assistant proposes instead. Your own rights are the ceiling: what you could not do
yourself it cannot do for you. Only these task routes count (`POST /api/v2/tasks/{id}`, `.../comments`, `.../links`);
an approval, running a task now, a message to another human and everything below stay a card whatever you said, and
with **Assistant acts without asking** off they are cards again. The record shows you as the actor, `via` assistant,
and the audit event's `delegation` names the message (`{"for": "human:<you>", "cited": "<message id>", "run": "asked"}`).

The Computer fetches the Assistant's own granted credentials to start the run, including on older versions of Tico.
It never fetches your personal credentials for that run; its Tico tools still act with your rights. If a job's lease
expires before start ten times since the server started, Tico marks it failed and leaves a message in your private
Assistant chat naming the Computer and asking you to check it. Only a Computer below the server's minimum runner release
is asked to update its Tico. Later messages can start new jobs.

**Every proposal is shown for what it is.** The card carries the server's own one-line description (route kind and target
name, never the bot's words), the request body as a key: value list (a long value is folded behind "Show more", never cut), for an approval its kind, requester and subject, and the exact field changes for a task update. Only routes
on an allowlist can be proposed (never tokens, sign-in, runner enrollment, this assistant or anything that returns a credential);
the path must be plain (`/api/v2/...` without `//`, `..`, `%` or `\`), and exactly that path runs. Only the status and an
error's detail are kept of the result, never the answer's body.

**Anything with a side effect that matters is proposed, and only your click runs it:** approving or declining a Needs-you
item (a send, spend, publish or merge approval, even if you ask for it in chat), task actions you did not clearly ask for,
sending anything outside the team or to another human, spending, changing humans, access or settings, archiving or
deleting, activating a bot. The assistant calls `hub assistant propose` (`hub_assistant_propose`, `POST /api/v2/assistant/actions`)
with the exact API operation. That leaves a **pending action** and a Confirm / Cancel card in your chat. The server
refuses these operations when the assistant tries them itself (`403 confirm_required`). **Confirm** runs the recorded
operation through the same routes with *your* credential, once (a claim makes a second click a `409`), and
records `assistant.action.confirmed` and the operation's own events via assistant. A task change proposed without the
task's `version` is given the one the task has (a change in between is still a `409`), and `{"status": "closed"}`
is taken as the close it means (`close: true`), so such a card runs, including one stored before this rule.

**Many at once is one card.** Several task actions (closing twenty stale tasks) are one bulk card, never twenty:
`POST /api/v2/assistant/actions` with path `/api/v2/assistant/batch` and body `{"actions": [{"method": "POST", "path":
"/api/v2/tasks/<id>", "body": {...}}, ...]}`, 1 to 100 task changes, comments or links and nothing else (`422`). The
card lists one line per task. One **Confirm** runs each step in order through its own route, as you; a step that fails
does not stop the rest, and the result lists every step's status (`result.items`) with the failures summed up in
`result.error` ("2 of 20 did not go through: ..."). It is `failed` only when none went through. The bot cannot confirm or cancel
(`403`; a confirm run carries a credential made at click time, bound to the stored method and path, valid for two minutes and then failed), a personal API token cannot either, another human sees `404`, and an unconfirmed proposal expires after
24 hours.

Use `hub_assistant_read` and `hub_assistant_send` (CLI: `hub assistant read|send`) with your personal token.
`hub_message_send` also accepts `assistant`; it uses your own private room. Other humans' rooms remain private,
and Confirm and Cancel still require your own click in Tico.

## API

Stable v2 (`docs/openapi/v2.json`, tag **Assistant**). All signed-in humans; the confirm and cancel routes need your
own session, not a personal token.

| Request | Answer |
|---|---|
| `GET /api/v2/assistant` | `{"available", "state", "bot", "name", "can_turn_on", "room_id", "messages": [...], "has_more", "next_before", "execution", "actions": {id: action}, "pending": [...]}`. Creates your room the first time. `execution` is the assistant's current run (null when idle): show a thinking state while it is set and poll again. A message whose `refs.action` names an id in `actions` is a Confirm / Cancel card; `refs.fast` marks a server answer; `refs.links` lists what it linked. |
| `POST /api/v2/assistant/messages` `{"text"}` | `{"message", "reply", "fast", "intent"}`. `fast: true` carries the answer as `reply`; otherwise the bot is working. `409 assistant_off` when it is off. |
| `POST /api/v2/assistant/turn-on` | Owner only. `{"bot", "state", "restored", "placed"}`. |
| `POST /api/v2/assistant/actions` `{"summary", "method", "path", "body"}` | A proposal `{"action": {...}}` (what the assistant's tool calls). |
| `GET /api/v2/assistant/actions/{id}` | `{"action": {"status": "pending"\|"running"\|"done"\|"failed"\|"cancelled"\|"expired", "result": {...}}}` |
| `POST /api/v2/assistant/actions/{id}/confirm` | Runs it as you. `{"action": {...}}` with the result of the operation. |
| `POST /api/v2/assistant/actions/{id}/cancel` | Drops it. |

Names come with ids (`from_name`, `owner_name`, and an `actors` map), as elsewhere in v2. A custom frontend (see
[custom-frontend.md](custom-frontend.md)) can embed the Assistant with these routes alone.

## For the assistant bot

Its instructions are `templates/catalog/assistant/AGENT.md` and `playbooks/assistant-chat.md`: how Tico is organised,
how to route work to the right bot, to answer briefly with links, never to act beyond you, to do the task actions you
ask for, and to propose (never do) anything else with a side effect, several at once on one card. They live in the bot's repository; edit them there or through BotOps.
