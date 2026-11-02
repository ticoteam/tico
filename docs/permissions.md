# Who can see, read and write to a bot

Every bot has three permissions. Each one has its own audience, so a bot can be visible to the whole
team, readable by one group and open to requests from everyone.

| Permission | What it covers |
| --- | --- |
| **See** | The bot in the team chart and in every bot list: its name, role, who owns it and who it reports to. |
| **Read** | Its activity: its tasks, updates, files, status and run log, routines, the shared rooms and chats it is in, and the activity sections of its page. |
| **Write** | Sending it messages, chatting with it, asking it; creating or reassigning tasks to it; leaving it notes; comments that wake it. Its approvals work as they always did. |

Write access does not make a stopped bot run. Messages and comments to a paused or quarantined bot are kept until it
is back, except that a bot's send is refused as `queue_full` once the queue held for it is full (it never counts as a
repeat; [How it works](how-it-works.md)); a new task or hand-off to it, or a message to a bot not started yet, is refused as `target_inactive`, which
never counts as a repeat against the sender ([How it works](how-it-works.md), rule 8).

An audience is **Everyone**, or a list of **humans**, **groups** and **bots**. Naming a group names everyone in it and in the groups nested in it. Anyone who may read or write a bot can also see it, whatever its See list says.

New bots, and every bot after an upgrade, start **Open**: Everyone for all three.

## Setting it

Settings > Bots has an **Access** column. **Edit** opens the editor, with three presets and a custom mode:

| Preset | See | Read | Write |
| --- | --- | --- | --- |
| **Open** | Everyone | Everyone | Everyone |
| **Requests only** | Everyone | the humans, groups and bots you choose | Everyone |
| **Private** | the ones you choose | the ones you choose | the ones you choose |
| **Custom** | each level chosen on its own | | |

The column summarises it, for example `See: Everyone · Read: Legal · Write: Everyone`. A human who
may only use the bot sees what they can do (`You: See · Write`) instead. Changes are revisioned and
appear in the settings history, where the owner can undo them, like the humans a bot works for.

Who may edit a bot's access: its owners (see [Bot owners](#bot-owners)): the team owner, the admins, the bot's creator
and other owners, and the humans it reports up to on the team chart (the same humans who may change the bot's other settings).
The Assistant, BotOps, the Librarian and the Strategy Bot can have their access edited too.

## Always full access

Whatever the audiences say, these callers can see, read and write to a bot:

- the team **owner**;
- the **bot itself**, for its own data;
- the humans **above the bot on the team chart** (whoever it reports up to), and a bot above it in the
  `reports_to` chain when the caller is a bot;
- an **admin**, and the bot's **owners** (its creator and any others added). This is the same rule that lets
  them change the bot's settings, so being able to edit who has access and having access always go together.

A bot above another in the `reports_to` chain also reads that bot's runs (`hub_run_list`, with each run's tokens and
estimated cost) and their steps: its task and routine runs, and the wake-ups the hub posts into its chat room. A run a
human started by chatting with the bot stays with that chat, and private tasks stay with their parties. Peers and
the bots below keep the usual conversation rule, so they never read a manager's chat-room runs.

## Roles, and what a member may do

Every human on the team has one role:

| Role | Who | Can |
| --- | --- | --- |
| **Owner** | whoever the team was set up for (one at a time) | everything: sign-in domains, ownership, providers, roles |
| **Admin** | humans the owner makes admins (the old "bot administrators": the list is read under either name for one release) | manage every bot but the built-in ones (below), humans and computers (enrol, revoke, open to members' bots); set what members may do and the bot limit. They cannot make or remove admins or owners. They are credential administrators and see the SQL page, unless the owner turns that off ([Team rules](#team-rules)) |
| **Member** | everyone else | create and manage their own bots, add coworkers, and use the bots they are allowed to |

Settings > People (owners and admins) shows each human on one row: their role (the owner switches Admin and Member there), a
**Can sign in** switch, and a ⋯ menu with two capabilities per member:

- **Can add bots** (`create_bots`): on for everyone unless switched off.
- **Can add humans** (`add_people`): on by default for coworkers, that is humans whose email is in the team's domain. A
  member with it on may add a human whose email is in the team's domain. Adding anyone outside it needs an owner or an admin,
  whatever the capability says.

Connecting a workspace Inbox to a portal company requires a direct signed-in owner or admin in both places.
The workspace previews the named company before confirmation. Members, bots, runners, support sessions, personal
API tokens and delegated Assistant/BotOps actions cannot link it. This grants shared Inbox access only; it does not
grant Hosting, Billing, Gateway or Support access. Every company agent can continue reading messages without consuming
them, but the eight Inbox agent tools carry no company-linking authority.

The **team's domain** is the domain(s) the owner allows to sign in (Settings > People, **Anyone at <domain> can sign in**); when
none is set it is the owner's own email domain. When that is a public email service such as gmail.com, it is the team email domain the
owner gave in Finish setup (**Names**, optional), then the team domains of the humans already on the roster; with none of those,
members add nobody until the owner adds a human or sets a domain. A newly added human goes on the roster and on the sign-in list, so they can
actually sign in.

**Can sign in** off keeps a human on the roster and the team chart but refuses their sign-in, sessions and API tokens until it is
on again. Owners and admins switch it for members; only the owner switches it for an admin; nobody switches it for themselves or
for the owner.

**Credential administrators** are the owner and the Admins, so a team gets going without the owner storing every credential. When the
server names its own list with `TICO_CREDENTIAL_ADMINS` it is that list (the owner and whoever it names), nobody else: the Admins
are then not credential administrators. The owner may turn **Admins store credentials** off ([Team rules](#team-rules)). A member is
never one, but stores a credential for a bot they own or manage (a chat card, or BotOps with `hub credential set --for-bot`), granted to
that bot at once, unless the owner turns **Members store credentials for their bots** off. They replace only a credential they stored, never
read its value, and share it with their other bots ([Bot owners](#bot-owners)).

## Dashboard, team pages and pins

The owner and admins arrange the Dashboard. A team page is arranged by them and by a person who leads the group or a group
above it; it is seen by its audience, Everyone unless they narrow it to people, groups or bots (the same audience as a
bot's levels above; naming a group names the groups under it). A bot pins only its own pages and cards, to its own
sidebar or a task it holds or asked for; a person who manages a bot may pin another bot's page to its sidebar. A page a
person pins to the Dashboard or a team page may be read by that place's audience, and a private task's file is never
pinned there ([Overview](overview.md), [Files](files.md)). BotOps does all of this with the rights of the person who
asked (`hub card`, `hub pin`).

## Team rules

The product favours getting going fast, and the owner tightens it later. Settings > People (owner only, saved as they change;
`GET` and `PUT /api/v2/access/rules`) has eleven switches, all **on** by default:

| Rule | On (default) | Off |
| --- | --- | --- |
| **Assistant acts without asking** | the Assistant makes tasks for bots, comments on tasks no other human is on, and messages bots directly, and does the task actions the human asks for in their Assistant chat; a card is for anything else ([Assistant](assistant.md)) | the Assistant acts directly only on the human's own tasks; a task, message or comment involving a bot, and a task action they asked for, is a card |
| **People may let their Assistant decide their approvals** | each person may turn on "My assistant can decide approvals for me" in Settings > General; their own Assistant then approves or declines approvals addressed to them on their words in its chat, recorded and undoable ([Assistant](assistant.md#letting-it-decide-your-approvals)) | approving is the person's click on a card again, whatever their own setting |
| **BotOps changes providers and limits without asking** (`botops_direct`) | BotOps changes the AI providers and raises spending limits for the person who asked, with their rights | a provider change, or a limit raised or removed, is a Confirm card in that person's BotOps chat; lowering a limit stays direct |
| **BotOps manages every bot** (`botops_manages_bots`) | in its own runs (the daily update, or a routine the owner set for BotOps) BotOps manages every bot but the built-in ones: their Tools, repositories, contact and Instructions, with write access to each bot's repository in its GitHub token (below) | BotOps in its own runs changes only itself |
| **Managers change their direct reports** (`managers_manage_reports`) | a bot changes its direct reports' model, task model, name and description, as itself, unless that manager's own switch is off; BotOps creates, archives and restores a report for it, within `report_creates_per_week` and `report_archives_per_week` ([Managers and their reports](#managers-and-their-reports)) | no bot changes a report's model, task model, name or description, or creates, archives or restores it, whatever the bots' own switches say |
| **Admins store credentials** | Admins are credential administrators | only the owner (and `TICO_CREDENTIAL_ADMINS`) stores credentials |
| **Members store credentials for their bots** | anyone on the team stores a credential for a bot they own or manage, granted to that bot | only credential administrators store credentials |
| **Admins see SQL** | Admins open the SQL page | the SQL page is the owner's |
| **Members make personal tokens** | any human makes a personal API token, which sees what they see | the owner and the Admins do |
| **Owners and admins may let Tico support in** (`support_access`) | an owner or admin may open a time-limited support session from their own request ([below](#tico-support)) | nobody can, and a live session ends |
| **Bots go ahead on their recommendation** (`bots_go_ahead`, days in `go_ahead_days`, 3) | a bot's ask with a reversible, internal default goes ahead when nobody answers in that many days (or by the date the bot gave); sends, spending and deletes never go ahead: their question closes on its date and the bot decides without it ([Tasks](tasks.md#when-nobody-answers-a-bots-ask)) | no ask goes ahead on its own; one with a date still closes on it, and one without leaves **Needs you** after two business days |

Only the Owner changes them, directly in Settings or by asking BotOps to act with the Owner's rights.

**Groups.** A group is a sub-team of humans and bots, and groups nest ([the team chart](org-chart.md)). Owners and admins add, rename, move
and remove groups and put humans and bots in them; members read them. A group is an access audience, so moving a human or a bot into a
group, or a group under another, can change who reads a bot. BotOps does this as the person who asked it (`POST`, `PATCH` and `DELETE`
on `/api/v2/groups` through `hub api`, or `hub group update`), with that person's own rights: an owner or an admin, at once; a member is
refused. The Strategy Bot only suggests org changes; when the person in its chat asks for one, it makes it citing their message, with the
same rights. Built-in bots stay outside groups.

**The built-in bots** (the Assistant, BotOps, the Librarian and the Strategy Bot) act for the whole team, so only the owner changes their settings,
routines, access or place, and only the owner may add them. A member cannot register a bot with the name `assistant`, `botops`,
`librarian`, `goal-manager` or `coo`; an Admin can, and manages every other bot.
The Strategy Bot's level, regular checks and name, and publishing a strategy version, are the owner's alone: an Admin cannot change them ([Your Strategy Bot](strategy-bot.md)).

A member may have at most **25 active bots** by default; owners and admins have no limit. An admin changes the number in
Settings > People (**Bot limit per member**). Past it, adding a bot answers `409 bot_limit` with what to do. A starter bot that is still `needs_onboarding`
([Recruiting a seat](onboarding.md#recruiting-a-seat)) is parked, does nothing on its own and costs nothing, so it does not count; it counts once
it says it is set up, and that call answers `bot_limit` when the member is already at their limit.

## Bots that create repositories

Two switches in a bot's **Settings → Repositories** let it create GitHub repositories in the connected organization. Each is
kept outside the bot's editable definition, so a bot never turns one on for itself, and both need the GitHub App's
Administration: write ([GitHub App](github-app.md#when-the-app-cannot-create-repositories)).

| Switch | Lets the bot create | Who turns it on | Who turns it off | Built in |
| --- | --- | --- | --- | --- |
| **Create bot repositories** | `<org>/bot-<slug>` for a bot that exists | the Owner or an admin | the Owner or an admin | BotOps, always |
| **Create product repositories** | a private repository with any other name ([GitHub App](github-app.md#bots-that-create-product-repositories)) | on by default; back on after an off: the Owner only, in their own click (asked through BotOps, a Confirm card they tap) | the Owner or an admin | every bot, until switched off |

Create product repositories is a wider reach than bot repositories, so:
- **Every bot has it by default** (Chris, Oct 17), BotOps and bots made later included. The Owner or an admin switches it
  off per bot; taking it away narrows a bot, so an admin may. The off is kept as that bot's own choice, so a migration, a
  later default or a template change never turns it back on.
- **Only the Owner switches it back on.** Only the Owner creates product repositories themselves, so an admin cannot hand
  that back to a bot; the Owner's request carried by BotOps or the Assistant is refused too.
- **Creating takes the Owner's own request, or the bot's own turn.** A bot's turn acting for someone (BotOps, the
  Assistant) or an external agent cannot preview or create one, even for the Owner.
- **Private only.** A bot cannot create a public repository: making one public is publishing, which needs a person. Bots
  cannot delete, rename, transfer or change the visibility of a repository either.
- A bot never creates a `bot-` or `emp-` name, or a name a bot's repository already uses: write to it would reach that
  bot's Instructions.
- **The bot gets write to what it made, and the Owner is told** in a notice, for example *Head of Engineering created
  private repo tico-services (acme/tico-services). Head of Engineering has write access to it.* Granting write ticks the
  repository for the team, so bots set to **All ticked repos** reach it too; the notice names them. A creator on **All
  ticked repos** with read access moves to **Chosen repos**, keeping read on every repository it reached, so its write
  stays on what it made; the notice says so, and a repository ticked later reaches it only once chosen. Every creation is
  recorded as `github.product_repo_created` with the bot as the actor, and the grant as `bot.repos_changed`.

Connecting, disconnecting and replacing the GitHub App stay the Owner's alone, as does deleting a repository.

## Incoming webhooks

An incoming webhook (a trigger) is an address outside senders post to ([Webhooks](webhooks.md#incoming)).

- **Anyone**, person or bot, may add one. The address and its secret are shown once, to whoever added it.
- **Managing one** (a new secret, its settings, signing, running a call again, deleting it, its Tico Gateway address) belongs to the
  owner, an admin, whoever added it and BotOps (acting for a person, with that person's rights).
- **Reading a call.** People who manage the webhook read the whole body. A bot reads it in parts, and only for a call that opened
  one of its tasks or that a notice from Tico to it names, or if it manages the webhook. A body is always handed over fenced as
  data from outside Tico.
- **Rules on a call** (`webhook.received`): the owner and admins write team rules; a bot's owners, its operator and BotOps write
  that bot's rules. Waking a bot needs rights over that bot. A rule that sends to an outgoing endpoint is written by a person, or by
  a bot with its outbound send switch on, never the Strategy Bot. The endpoint must exist and its maker must own it or be an admin: Tico checks
  that when the rule is saved, and again when it runs.
- **Tico Team** is a built-in webhook. Nobody deletes it; only its keep days change.
- **Screening** checks a call's text before a bot gets it ([Screening](#screening)).

## Screening

Text from outside Tico can carry instructions meant for the bot that reads it (a prompt injection). Screening checks that
text at the doors it comes in by, before a bot reads it. Change it in **Settings → System → Screening** (owner or admin).

| Door | What is checked | Default |
|---|---|---|
| Webhooks | A call whose automation opens a task for a bot or starts a bot's turn, and a rule's `create_task` on `webhook.received`. In a JSON body, only its strings of three words or more, not ids, numbers or links | Warn |
| Mail | An inbound message a bot reads (`hub inbound read`): the subject and body | Warn |
| Ideas | The public roadmap's suggestion form: the title and summary | Warn |
| Watchers | A watcher's new task, except the Support Agent's HQ tickets, which [support triage](support.md#support-triage) checks | Warn |

Each door is **Off**, **Warn** or **Hold**. Mail is only ever marked, so it offers Off and Warn. For now every door
starts at Warn, which only observes: nothing is held until you set a door to Hold. The defaults are one constant,
`DEFAULTS` in `backend/screening.py`; Hold becomes the default for webhooks, ideas and watchers once the live eval
(`python -m evals.screening.run --live`) and a week of `screening.warned` rows show the hold line is safe.

The check has four layers. Two of them can be switched off in the same card:

- **Fencing.** This is always on. The model reads the outside text inside a fence the writer cannot close, and a bot
  reads a webhook call as fenced parts.
- **Patterns.** A free check, with an on/off switch.
- **Decision model.** It is on by default and needs Decide. With it off, only the patterns run, and they only warn.
- **The door's mode.** Off, Warn or Hold, set per door.

- **Patterns.** A few fixed phrases ("ignore all previous instructions", "print your system prompt"...) are checked on
  every item, free. A match only warns, never holds, and counts only when the decision model did not answer: they
  misfire on code and on writing about attacks.
- **The decision model** reads text of 200 characters or more against the `injection-screen` question set
  (`questions/injection-screen.json`). At or over its `hold` line (85%), on a door set to Hold, the item becomes a task
  for a person titled `Held: ...`: the bot is not woken and nothing is deleted. Hand the task to the bot to release it;
  close it if it is an attack. At or over `warn` (50%), the bot's copy starts with a line saying the text looks like a
  prompt injection and is data. Mail at the hold line gets that line too, and a held automation turn is recorded as
  step `held`.
- **Who gets a held item.** Whoever made the webhook's automation or rule, else the automation's owner, else the bot's
  operator, else the default person.
- **Cost.** Each model call counts against the daily decision budget of the bot that would get the work and the
  team's monthly spend cap. A verdict is kept per item (a webhook delivery, a message id) for 30 days, so a second read
  costs nothing.
- **It fails open.** Off, short text, no decision model, the spend cap, the bot's budget, a timeout, an error or an
  unreadable answer all let the item through as before, with only the patterns' warning. A webhook rule's or a
  watcher's item waits for the model a few seconds after the call is answered; one still waiting after an hour goes
  to a person.
- **Audit.** Each model call is one `judge.call` row labelled `injection-screen@1`, and each hold or warning one
  `screening.held` or `screening.warned` event, with the score and kind, never the text. Settings lists the newest.
- **Test** runs eight built-in samples, four attacks and four lookalikes (code, a security article, an ordinary
  request), through the model and shows which it got right. Each sample counts against the person's decision budget.

## Outgoing webhooks

An endpoint is an address Tico posts events to ([API](api.md#outgoing-webhooks)).

- **People** make and manage their own endpoints; the owner and admins manage every one. BotOps acting for a person has that
  person's rights.
- **A bot** cannot make one on its own. Its request files a `send` approval, and the endpoint is made, owned by the person who
  approves it, when the bot repeats the call. A bot sees every endpoint without its full address, and changes none.
- **Custom events** (`hub webhook send`, names `custom.*`) are sent by people, and by a bot only while its outbound send switch is on.
  The Strategy Bot never sends one and never causes a webhook.
- **Privacy.** An endpoint hears of a task, approval, ask or bot only when its owner could read it themselves; the event body is built
  from what that person sees. Health, inbound and webhook events go to owner and admin endpoints only.
- Plain `http://` is for `localhost` only, and only the owner switches it on for an endpoint. Every other address is `https://`
  and must resolve to a public address.

## Bot owners

Each bot has owners: its **creator** (added automatically) and any others added. Whoever the bot reports up to on
the team chart, and every admin, are owners without being listed. One rule, `Auth.bot_manager`, says who may manage a bot, and it
is the same humans who always have full access to it. A bot owner can:

- edit its configuration: instructions, model, routines, name and description, repository;
- set its See, Read and Write access;
- pause it, rename it, archive it (never the built-in Assistant, BotOps, Librarian or Strategy Bot);
- add or remove owners (`POST /api/v2/bots/{bot}/co-owners`, Settings > Bots, **Owned by**);
- give it a stored credential they hold themselves or stored themselves, and no one else's.

A member cannot change a bot that is not theirs, through Settings, the API or BotOps. A bot's manager bot may change
some of it too, as itself: see [Managers and their reports](#managers-and-their-reports).

A bot gets only credentials granted to it, including its own local credentials; it does not inherit another bot's credentials.
Credential administrators store, delete and grant credentials. The human who stored a credential may give it to, and take it from,
bots they own or manage (`Auth.bot_manager`), directly or through BotOps, and sees its metadata (never its value unless it is
also granted to them); another person's bot stays an administrator's call. This holds only for a credential a member stored for
their bot: one an administrator added stays the company's, even after that person stops being an administrator, and a stored
credential stops being the member's once someone else saves a new value for it or the owner turns **Members store credentials for
their bots** off. Giving it to a bot with co-owners puts the value in every co-owner's runs of that bot. A human who holds a
credential may also delegate it to a bot they own or run, directly or through BotOps. Revoking the human's grant removes the
delegated bot access; changing the bot owner invalidates delegation from its former owner. A holder cannot delegate a credential they do not hold ([credential-vault.md](credential-vault.md)).
On upgrade, Tico automatically grants each existing bot its own-file values, the shared Credentials it could read,
and its declared profile and runtime keys. New bots do not inherit the whole environment or `_shared.env`.

## Managers and their reports

A bot's **direct reports** are the bots whose `reports_to` is it. Its manager bot changes four things on each one, acting as
itself: the manager is the actor, and the change is recorded with `via: report_manager`, so the settings history names the
manager:

- its **model** (`POST /api/v2/bots/{bot}/model`, `hub bot model`, MCP `hub_bot_model`);
- its **task model** (`POST /api/v2/bots/{bot}/task-model`, the bot editor; there is no hub or MCP command for it);
- its **name and description** (`POST /api/v2/bots/{bot}/definition`, `hub bot update`, MCP `hub_bot_update`).

It does not change the report's instructions, status, place on the team chart (`reports_to`), owners, access or computer.
Its instructions are changed by a **next-run task** the manager files for the report, which the report does when it next runs.
Creating, archiving and restoring a report are asked of BotOps (below). A manager reaches only the bots directly under it, and
`Auth.report_manager` decides that.
It does not widen `Auth.bot_manager`: a manager bot owns nothing it did not own before.

Two switches turn it off, and the owner's comes first:

- the team rule **Managers change their direct reports** (`managers_manage_reports`, [Team rules](#team-rules)), on by default. Only
  the owner changes it; BotOps can only propose a change, as a Confirm card the owner taps;
- the manager's own **Manages its reports** switch (`manages_reports`, in its bot editor), on by default. A person who manages that
  bot sets it, and the bot cannot set its own.

**Creating, archiving and restoring a report.** The manager bot cannot do these with its own token: it asks BotOps in a chat
message, and BotOps acts for it (`Auth.report_lifecycle`). BotOps tells the two apart by `via`: a manager's own token has none,
and BotOps' ask has `via: botops` with the manager as its actor. Only these routes are reached that way: create (`POST
/api/v2/bots`, with `reports_to` the manager), archive and restore (`POST /api/v2/bots/{bot}/archive` and `/restore`), for a
bot that reports to the manager now. Templates, the built-in bots and support sessions are never made this way. The same two
switches must be on. Each change is recorded as a `lifecycle` change with `via: report_manager` and can be undone.

A manager makes **3 creates and 3 archives** (restores count as archives) each rolling seven days. The limits are the team
rules `report_creates_per_week` and `report_archives_per_week` (0 to 50 each; the owner sets them, and they are the same for
every manager). The count is the manager's `bot.report_lifecycle` events of the last seven days, so there is no separate counter to reset. Over the limit, the request is refused
with `report_cap`, and a Needs-you task goes to the person the manager reports up to (`report_managers.person_above`), asking
them to decide. A person who is neither the report's operator nor one of its owners, but sits above it on the chart, cannot
restore an archived report: the chart leaves archived bots out (`Auth.manages`), so the owner or the operator restores it.

Routines follow both. A routine owned by a report can be changed by a bot above it only while every bot on the team chart from
the report's manager up to that caller has both switches on (`automations.may_change`).

A person who manages the report sees the changes its manager bot made to it, in the bot's **Settings changes** card, and can
undo them while the bot still holds the value the change set. Only the owner sees every change and every transition. Turning a
switch off does not undo what the manager already changed.

## Computers for members' bots

Bots on one Computer share its workspace and model sign-in trust. Run environments receive only granted Credentials,
with no process or `_shared.env` fallback. On isolated Computers, the runner supervisor owns legacy secret files.
Computers without process isolation still share one user and filesystem ([SECURITY.md](../SECURITY.md)). A bot
created by a member has Instructions the Team has not reviewed, so it does not go on just any Computer. Each computer has
**Accepts members' bots** (Settings > Computers, owners and admins):

- **On** for every new computer, whoever enrols it, so members' bots go on any computer without asking; an owner or an admin turns
  it off per computer. A computer that existed before this default keeps what it had: turn it on where you want it.
- A bot created by a member can only be placed on **its own owner's computer, or a computer that takes members' bots**.
  With none, it stays planned, and the answer says to ask an admin to place it or to open a computer. Admins may place a member's
  bot on any computer. Placing a member's bot never hands it to the computer's owner: it stays theirs.
- Setup that places bots for you (a computer enrolling, the wizard) leaves a member's bot alone unless the computer is its owner's
  or takes members' bots.
- **Turns at once** on a computer is set by owners and admins only. A member who manages a bot sets that bot's **Turns
  at once** in its editor, like its other settings.

A computer still hosts its owner's bots and the team owner's; taking members' bots adds members' bots, it does not move anyone else's.

Every person sees every working computer in Settings > Computers (`GET /api/v2/operations`): its connections and their
sign-in state, and the bots on it that they may see. Only an admin or the computer's
operator also sees the signed-in accounts and the usage of the computer's own login, sees revoked computers, and changes
anything on it (sign in, rename, usage readings, screen, tools, performance, inbox-only, revoke); the API refuses anyone else.

## A computer's screen

> **Beta**: a computer's screen (View and Control) works today and may still change. See [Beta features](support.md#beta-features).

A Linux computer can show its screen in the app, so people watch its bots work or take control. Two locks, both needed:

- **On the computer**, its owner turns the screen on (`TICO_SCREEN=1` in Docker, which a new Docker runner has; or
  `tico-runner screen on`). Until then the computer offers no screen and the hub refuses any mode but Off.
- **Screen** in Settings > Computers: **Off**, **View** or **Control**. It starts at **Control** the first time a new computer
  offers a screen, unless someone already chose a mode for it (audit: `runner.screen` by `system`); a computer added before this
  default keeps its setting. The computer's operator or an admin sets it
  (`POST /api/v2/computers/<id>/screen {"mode": "view"}`), or BotOps as one of them. Off closes open screens; View closes the
  ones in control.

Only the computer's operator and admins open the screen, in the app themselves: each opening is a one-minute, single-use
ticket, at most three at once per computer, an hour at most each. The screen opens in the computer's mode, wherever it
is opened from: at Control they drive it at once, with View one tap away; at View they watch only. Every opening and
closing is in the audit log (`runner.screen_open`, `runner.screen_close` with who, view or control, and how long).

Where the status shows: every bot's **More** has a **Screen** row, in Setup (or About). When the screen is ready it opens
it and says View or Control, and the bot's header has a **Screen** button too. Otherwise the row says why and what to do:
the screen is off in Tico (the operator or an admin gets **Turn on (Control)** right there, the same as a new computer's
first offer; View stays a choice in Settings > Computers), the computer offers no screen yet (the steps to turn it on on the computer, with commands to copy),
it is a Mac (Linux only for now), the computer is offline, the bot has no computer, or only the operator and admins may
open it. Someone who may see the bot but not its activity is told its computer isn't shown to them, and nothing more.
The row follows the computer as the page refreshes (it comes online, its screen is turned on), with no reload.

## Tico support

A session a person opens from a support request ([support.md](support.md#letting-tico-support-in)) lets the Tico team in as
one more identity, `human:tico-support`, named **Tico support** wherever names show. It has no roster row and no sign-in: it
exists only while its session is live, through a token the server keeps in memory, and every request it makes is checked
against the session again.

- **App only** (`support: app`) reads as an Admin does, except a run's raw provider stream, and writes only the setup routes
  `backend/support_access.py` lists (`APP_WRITES`): a bot's model, fallback and context size; a computer's restart; a
  subscription allowance read (no model sign-in, and no reading of one's link and code: whoever enters the code first chooses the account a computer
  runs on). Everything else, approvals, messages, tasks, runs, people, access and
  credentials included, is refused `403 support_access`.
- **Full access** (`support: full`) has the Owner's rights.
- Neither makes or sees personal tokens, service keys, agent or SCIM credentials or credential values, signs a person in or out of Tico,
  enrols a computer, transfers ownership, or reaches the support-access routes. Full access otherwise changes what the
  Owner may, including what outlasts the session (webhooks, credentials, the GitHub App, Slack's tokens, directory sync, a computer's model sign-in). It counts as a token, so nothing that needs a
  person at their own browser accepts it.

## A computer's performance

> **Beta**: behaviour replay and the computer Performance page work today and may still change. See [Beta features](support.md#beta-features).

Every computer reports its CPU, memory, swap, disk and its busiest processes every 60 seconds, so a slow or overloaded
computer can be debugged afterwards. It is on by default. The computer's operator or an admin turns it off in
**Performance diagnostics** in the computer's pop-up in Settings > Computers (`POST /api/v2/computers/<id>/performance {"mode": "off"}`); Off
stops the reports, and what was recorded stays until it is 14 days old. **Delete history** on the page, or
`DELETE /api/v2/computers/<id>/performance`, removes it now. Each change is in the audit log (`runner.performance`,
`runner.performance_erased`).

Every person and bot of the company reads it: **Performance** in a computer's pop-up in Settings > Computers, `hub computer performance <computer>`,
or `GET /api/v2/computers/<id or label>/performance?hours=24` (1 to 336). A report holds numbers and process names only (the
program's own name, never its arguments), and the turns shown are the server's own record of what ran. A bot the
reader may not see (its **See** access) shows as "another bot", without its model or turn.

`hub computer list` is readable by company bots, including computers closed to placement of members' bots.
Computers a bot could not otherwise read expose only discovery fields and load, without operator identity,
repositories or recent logs. Full detail, placement and change controls keep their existing permissions. Each row includes `latest_sample` (timestamp,
`cpu_pct`, `mem_pct`, memory used/total, swap used/total and running turns) and a plain `overloaded` flag.
CPU here is the one-minute load divided by available cores, times 100; it may exceed 100%. These minute
samples are retained for 48 hours. Six consecutive readings spanning five minutes above 90% CPU or memory
set the flag and a Health warning. A missing minute, a calm reading, a sample older than two minutes, or
turning diagnostics off clears it and hides `latest_sample`.

This sustained-load flag holds new starts on that computer, including reservations that have not
started yet. Running turns continue, and new starts resume when the flag clears
([Turn counts](computers.md#turn-counts)). With diagnostics on, two more holds apply: memory at **85%** or more holds
new starts until memory is below **80%**, and a computer whose latest sample is more than two minutes old holds them
as not reporting. A computer that has never sampled is not held for that reason.

Runs carry `load_start` and `load_end` with CPU %, memory %, running turns and the sample timestamp in
`hub run list`. Current computers take a reading at start and completion; older computers use a recent
sample with its original timestamp. Unavailable readings are null. A run's saved snapshots remain after
rolling samples expire. Diagnostics off suppresses new readings and snapshots; deleting computer history
also removes saved snapshots for that computer.

Ten minutes in a row of CPU or memory over 90%, heavy swapping (half the swap in use with memory over 80%) or under 5%
disk free record an overload: when, why, which bots' turns were running and the busiest processes. Health shows it as a
warning for a day afterwards. macOS swap is reported as raw bytes in `latest_sample`, but does not feed this
diagnostic swapping rule because its swap total grows with use. This ten-minute diagnostic record explains past load; the separate five-minute flag above controls new starts.

## BotOps uses the requester’s rights

An owner should be able to say "build me a Jira bot, and add Sam" in chat with BotOps and have it done. BotOps therefore acts **as the
human whose own chat message started its current run**, checked with that human's rights and recorded as theirs, "via BotOps"
(events, settings history). Not more than they may do: a member cannot edit another human's bot through BotOps any more than by hand.

For unfinished work, BotOps can create a continuation task with `hub task create --request-id <originating message id>`.
The server checks that the request came from the human's own BotOps chat and keeps that human as requester.
Progress with `hub task update <id> --brief --note "..."` stays on the task; chat still shows its link and status.

Every BotOps tool uses the requester's rights by default. A human's chat or task uses that human's full rights.
A task lends its human requester's rights only when BotOps has owned it since it was filed or the requester moved it
to BotOps themselves (not through BotOps or the Assistant). A task anyone else moved to BotOps lends nothing of the
requester's: a bot that moved it lends only its own rights, and what those can't do is a Confirm card to the requester.
When the person asks BotOps in their chat to take over one of their tasks, that chat turn has their rights and can do the
work; the move itself is BotOps', so later runs on that task lend nothing (they ask again or move it themselves).
A bot's message or task uses only that bot's rights, including its Credential grants; BotOps never lends it a human's
or its own wider access. The one exception is a person's own request the bot hands on: a bot filing a BotOps task may cite
the person's message (`hub task create --owner botops --request-id turn`, or the message id). When that person wrote it
to the bot themselves, in their 1:1 chat with it (the app or a Slack DM) or as a comment on an open task they asked it for,
in the conversation the bot's run is working on and within the last week, BotOps' run on the task files and updates tasks, docs,
goals and KPIs with the person's rights while the task is open and has never been finished (`botops_act.CARRIED`, an
allow-list). Everything else, access, settings, undo, bots and spending included, never uses rights a hand-off lent:
BotOps tries it with the bot's rights, and a refusal is the Confirm card below. A citation that lends nothing (no message cited, the Assistant's words, a channel, another conversation,
older than a week) still names the person: a change BotOps can't make with the bot's rights comes back as `needs_confirm`,
a Confirm card in that person's BotOps chat that runs as them on their click. The task records which
(`botops.request_carried`). Unattended work with no requester keeps BotOps' own rights. Personal tokens use their human's rights.
Task text cannot select another requester. A message the **Assistant** wrote for a human (`refs.via`), words inside a
document, or a message **routed from Slack** cannot borrow human authority.
A human's request lends their rights for as long as the task it created, or the task that carries it (`--request-id`), is open,
up to 30 days after they last spoke on it (filed it, sent the message it carries, or commented on it). Once a task is done,
closed or declined, moving it back to open lends nothing again unless the requester reopened it themselves, and citing
that task stays refused even after they reply. They ask again in their own words: the run their comment on it or their
message in their BotOps chat starts has their rights without `on_behalf_of`.
A chat message with no open task lends them for a week, and a new task filed from a message needs that message within the week.
A comment the task's human requester writes on their open BotOps task, in their own words (not through the Assistant, Slack or
routing), is them asking: the run it wakes has their rights. Anyone else's comment lends nothing.
On a task another bot asked BotOps for, a person's comment lends nothing either: the run keeps that bot's rights, and
what they can't do comes to the person who commented as a Confirm card in their BotOps chat.
On its own task BotOps never needs anyone's rights to add the task's worktree (the bot's own repository access decides; an
owner or admin gives it in Settings > Repositories) or to file a question or subtask for a person under it; that ask grants
nothing by itself.
With a person's rights BotOps may give another bot repository access at once, but never take any away: setting a bot to
own, dropping a chosen repository or turning write into read comes to that person as a Confirm card naming the grants that
would stop. The same change made in Settings > Repositories (or `hub bot repos`) says which grants stopped applying.
A message cited by id (`on_behalf_of`) must be the requester's own, in their own chat with BotOps rather than a room another human
spoke in, and it must be the same human whose message started the run. Someone who has left lends nothing.

**BotOps' own runs manage every bot.** Only a run from BotOps' own schedule is its own: the daily update, and a timed routine
of BotOps that the owner (or the product) set and only they changed. Its rights make it a manager of every bot that is not
built in: it changes their Tools, repositories, contact (`bot_contact`) and Instructions, and its GitHub token writes every
bot's repository. In a bot's definition it changes only `bot_contact`; who the bot reports to, its repository link, its status
and its template stay with the people who manage it. It never changes people or owners, never sees a Credential value, never
turns on sending to outsiders and never archives a bot with its own rights. Every other run the keeper starts carries words
someone else wrote and gets only BotOps' plain rights: a service key's or a watcher's task, a stopped job's request, a routine
BotOps set itself, an event routine, a task someone else wrote or commented on. A run a person or a
bot asked for keeps that requester's rights, so BotOps never lends this to another bot. The team rule **BotOps manages every
bot** turns it off. In a run carrying a person's rights, the token writes the repositories of the bots that person manages
(owner, admin, the bot's owners, above it on the chart); in a run a bot asked for, and for the computer's git helper with no BotOps
run in progress, only BotOps' own. A token is reused only for the exact same repositories, so a wider one never reaches a
narrower run.

**What still waits for the person's tap.** The rights BotOps borrows are never a confirmation: only the person's own click
on a Confirm card in their BotOps chat is, and it runs the stored request as them. So BotOps answers these with
`needs_confirm` and a card, and nothing changes until the tap:
- a role, sign-in, email, team or mailbox change for someone (`POST /api/v2/access/people/{id}`);
- a credential granted to a person or to every computer (a grant to a bot, and taking a grant back, stay direct);
- placing a member's bot on a computer that does not take members' bots;
- the routes in `botops_act.CONFIRM`: removing people, the bot limit and who may sign in, a computer's member-bots
  switch and revoking it, deciding a proposal, a support request, a file's sharing, directory sync, disconnecting Slack
  or GitHub, and picking a seat's candidate (listing candidates stays direct);
- always, whatever the person said: handing over ownership (`POST /api/v2/access/owner`, the owner only), updating Tico
  (`POST /api/v2/system/update`), a change to the rules **BotOps changes providers and limits without asking** or
  **BotOps manages every bot**, and a new computer's one-time code, which shows on the card once the person taps it and
  never in BotOps' answer;
- always, too: moving the Gateway connection or an outgoing webhook that holds secret headers to another service while
  credentials are saved (the Gateway's default address included). The request says `credentials` `keep` or `clear`
  (and a webhook its `expected_revision`), the card names the old and new scheme, host and port and the choice, and it
  is stale at the tap once the revision, the address, the credentials or the person's rights changed;
- providers (turning the Tico AI Gateway off or back to automatic included, the owner only) and raised limits when the
  owner turned **BotOps changes providers and limits without asking** off.

What only an owner or an admin may ask for is refused at once for a member, not handed over as a card that fails. A few
routes give back a secret's value, and BotOps acting for a person is refused them (`403 person_only`, with where the person
does it): revealing a stored credential, making someone a password, making a personal API token and making a SCIM token.
A secret in a request body is refused too (`422 secret_in_request`), including the Gateway's `key` and Slack's
`bot_token` and `app_token`. A body the route itself would refuse, an unknown field included, gets the route's own
`422` and no card: a card never stores or shows back what its route has no place for. Each step of a bulk card is
checked against its own route, and one step refused refuses the whole card.

If a friendly tool refuses for permissions, BotOps retries the same action with `hub_api` before handing work back. Both use the same rights.

**Following through later.** A request often finishes in a later run that something else started: the daily-update
request, a retry once a busy bot is idle, a notice. Nobody's request is attached to that run, so by default BotOps has
only its own rights. To act for the person, BotOps cites the open task they asked it for: `hub api ... --on-behalf-of
<task id>` or `hub bot model ... --on-behalf-of <task id>` (MCP: `on_behalf_of`). The server accepts a task BotOps owns
that the person filed themselves (not through the Assistant; one BotOps filed as them counts only as a continuation of
their message, `--request-id`), while it is open, and only in a run the person
started or the keeper started on its schedule (the daily update, a task's due, stall or routine notice). A run a bot or
another person started keeps that requester's rights whatever task it cites, and a run a Slack digest, a live meeting,
a watcher or another person's task comment started lends no one's rights: anyone there wrote its words. It is recorded as
the person's, via BotOps, like any other delegated change, and Credentials stay as above (never a value).

**Refusals say what to click.** A refused BotOps call (403, or a computer that cannot run the bot) answers with `fix`,
one sentence on what the person does, and `link`, the place in the app (`#/bot/<slug>/more`, `#/repositories`,
`#/task/<id>`, `#/settings`). The sentence depends on whose rights were used: BotOps' own (say go ahead in the BotOps
chat), a requesting bot's (the same, or change it yourself), or the person's own (ask an owner or admin). BotOps relays
it as one sentence and the link.

**Checking the result.** A model change answers with `readiness` (`backend/readiness.py` `can_run`, the one check
behind a prepared change, `POST bots/<slug>/model` and Health): whether **this bot** can run the new runtime on its
computer, judged by its computer's login for that runtime, its own API-key Credential for the runtimes that take one
per bot (Gemini CLI, Pi). Another bot's missing key never blocks it.
`can_run` is true, false (with `fix` and `link`), or null when nothing says (offline, an older computer). A bot an
external agent runs (Hermes, OpenClaw, Grok Bot) answers `applicable: false`: no computer check applies. A change is
refused only on false. `reported` turns true once the bot's own readiness row reflects the new runtime, after the
computer's next heartbeat.

**What is recorded.** Every event BotOps writes with a person's rights carries `via: botops` and `delegation`: the
person (`for`), the request that lent the rights (`cited`: their message, or the task cited in a later run), and
`run`: `live` when they asked in this run, `comment` when their own comment on the task started it, `follow_through` when
BotOps cited their open task in a later one.

**Support and the Librarian.** BotOps with a person's rights files a support report as them (`hub support file`) and, for the
owner, turns on the Librarian (`hub api POST librarian/turn-on`). With its own rights both are refused.

The same goes for routines and quarantine: human-requested BotOps work uses that human’s management rights,
including work requested through a task. Only the requester's own comment on their task lends their authority. The run
must be able to read the request’s conversation, and a request with no open task lends nothing after a week. Bot-requested work keeps that bot’s narrower rights.
Unattended BotOps work retains its own rights.

The commands (with MCP tools of the same names):

| Command | Does |
| --- | --- |
| `hub bot create --record-only --name N [<slug>] [--description] [--reports-to] [--template]` | creates the planned server record as the requester, who becomes its owner; the slug is picked from the name (else the template's) unless given, and printed; safe to repeat with the slug. `hub bot create` registers automatically in such a run |
| `hub bot access <slug> [--see V] [--read V] [--write V]` | show or set who sees, reads, writes (`everyone`, or `ben,group:legal,bot:analyst`) |
| `hub bot owners <slug> [--add P ...] [--remove P ...]` | the bot's owners |
| `hub bot update <slug> ...` | name, description, reports-to, status, repository; a manager bot changes only its direct report's name and description ([Managers and their reports](#managers-and-their-reports)) |
| `hub bot setup-done [slug]` | a starter bot's own call, once its setup is done: it stops being `needs_onboarding` (its manager may call it for it) |
| `hub human add <email> [--name] [--title] [--reports-to]`, `hub human list` | the roster |
| `hub group list`, `hub group update [<group>] [--name] [--parent] [--lead] [--add-human] [--add-bot] [--remove-human] [--remove-bot]`, `hub group delete <group>` | the groups; create (no group given), rename, move, lead, fill or delete one, owners and admins only (a lead within its groups) |
| `hub org reports-to <bot:slug\|human:id> <manager>` | change who a teammate reports to, with the asker's rights |

| `hub bot place <bot> [--computer C]` | puts a bot on a computer: the one named, or the only one, or the least busy that takes it |
| `hub bot go-live <bot> [--computer C]` | places it if it has no computer, turns it on, and for a starter bot starts its setup chat as the requester. When the computer does not have the bot's repository yet but GitHub does (or GitHub cannot be asked), the placement is kept and the answer is HTTP 202 `waiting_for_repository` (the CLI says so and exits 0): the computer clones it, and the readiness report that says it has the repository finishes the go-live, once, with the requester's rights. When GitHub could not be asked the answer says so, since a repository that doesn't exist never arrives. Health lists each waiting go-live for the owner, admins and whoever manages the bot. One that can't finish, or hasn't finished within 7 days, stops, and the requester gets one notice to run go-live again. A repository GitHub does not have is refused (`repository_missing`) with how to create it; when it is waiting for the GitHub App's permission to create repositories, the refusal is `repository_waiting_for_permission` and names the owner's Needs you task with the two fixes. |
| `hub bot model <bot> [<model>] [--effort E] [--on-behalf-of <task>]`, `hub bot pause\|resume <bot>` | the model (none: list the choices; a change answers with the computer's `readiness`), stop and restart; a manager bot may set its direct report's model ([Managers and their reports](#managers-and-their-reports)) |
| `hub bot profile <bot> [name] [--batch-seconds N] [--digest-hour H]`, `POST /api/v2/bots/{bot}/profile` | a bot's profile (doer, reviewer, lead, manager, responder, monitor, custom): what wakes it and which tasks it takes; its owner, operator, a person it reports up to, or BotOps; an Admin or the owner. A profile grants no right |
| `hub bot budget <bot> [--hour N\|default] [--day N\|default]`, `hub bot budget <bot> release`, `hub bot release <bot>` | a bot's runs per hour and per day (the team default when unset; its owner, operator or BotOps), and release a bot Tico stopped for failed runs or too many runs. `hub team budget [--hour N] [--day N]` sets the team default (owner and admins) |
| `hub bot scope <bot> <task>`, `hub bot scope <bot> --release` | limits the bot's claims to that task and its direct subtasks, or lifts the limit; the rest of its queue, new work included, stays queued as it is, and nothing is paused, resumed or started |
| `hub automation update <id> --enable\|--disable` | an automation (a routine) on or off |
| `hub computer list [--computer C [--section S --offset N]]`, `hub health check` | the computers a bot may go on, one short row each (one computer in full with `--computer`, large sections paged); what is wrong with the bots, most urgent first, each with its fix |
| `hub computer performance <computer> [--hours 24]` | the computer's CPU and memory over the window, its overloads and what ran in them, and its busiest processes |
| `hub computer turns-at-once <computer> <N\|automatic>` | turns at once, 1 to 256 or `automatic`; owners and admins |
| `hub credential request\|set\|list` | a card for a credential in the chat, storing one a human pasted, the credentials with their bots (never a value); see [credential-vault.md](credential-vault.md) |
| `hub credential grant <name> --to <bot>`, `hub credential revoke <name> --from <bot>` | give a bot a stored credential, or take it away; at once for a credential administrator, the person who stored it (for a bot they own or manage), or a holder delegating to a bot they own or run; revoke the delegation to take it away |
| `hub credential import <VAR> --from-bot <bot>` | move one variable from that bot's own secrets file into Credentials, granted to that bot; the computer sends the value itself and nobody sees it |
| `hub support file "<message>"` | sends a requested support message to the Tico team with the requester's rights |
| `hub api <METHOD> <path> ['{json}'] [--on-behalf-of <task>]` | any other v2 route, as the requester |

The server applies the requester's rights to every BotOps v2 call, including friendly tools and `hub api`.
Older Computers receive the same behavior without adding a delegation header. `X-Tico-On-Behalf-Of: turn` and
`on_behalf_of` remain supported; citing another human cannot widen the current requester's access. `/me` identifies
the calling credential so clients can discover which tools are offered; it does not change the rights used by tools.
The route's normal permission checks apply, just as when the requester uses it directly. No per-tool opt-in is needed.
A Credential never travels in a `hub api` body (a key named `secret`, `password`, `token`, `api_key` and the like is refused).

Everyday edits, requested bot deletion, outside-domain invites by an Owner or Admin, Tico updates, and the Owner's
Team rule changes run directly with the requester's rights. Sending to outsiders stays off until enabled for that bot.
Archiving removes Routines and placement and may revoke its External agent Credential; restoring the bot does not
recover those. Other settings edits have history for undo. The four Built-in bots cannot be deleted, and the Librarian
cannot be copied.

A bot's Instructions and the text it reads cannot select a human whose rights BotOps should borrow. The server resolves
the requester from the run's message or task, checks that human is still on the roster, and applies only that identity's
permissions. A requesting bot keeps its own narrower rights throughout the work.

### A computer for every active bot

A bot that becomes active without a computer (added active, turned on, resumed, or built by BotOps) is placed by the server: on the team's
only computer, else the least busy online one that takes it (a bot BotOps builds prefers BotOps's own computer, where its repository is). A member's bot goes on that member's own computer or one opened to members' bots,
and never on a closed one. With none that takes it the bot stays as it is and the answer says so; the scheduler places it as soon as one can
(`backend/placement.py`). Humans see a bot that is only set up, not yet turned on, as "Setting up".

## The settings catalogue

Every control in Settings that writes has an entry in the settings catalogue (`backend/settings_entries.py`): a stable
key such as `team.rules.admin_sql`, the control's name ("Admins see SQL"), where it is (Settings > People > Team rules),
who may change it, its class, the route and body it writes, and a link that opens that card
(`#/settings/<tab>/<card>`). A person, or BotOps or their own Assistant for them, finds and changes a setting
without knowing the route:

    hub settings find "sql"                                  key, name, where, class and who may change it
    hub settings show bot.limit.usd --bot support            the current value, who may change it, whether you may
    hub settings set release.times 09:30,17:00               change it with your rights (BotOps, the Assistant: the
                                                             person's)

`show` and `set` take `--bot`, `--computer`, `--person` or `--id` for a setting set per one of them. The class decides
what `set` does when BotOps or the Assistant asks for a person (the same answer from either):

- **direct** (reversible, inside the team, no new spend: routines, task types, tags, a bot's model, release times,
  the privacy count): it changes at once, as that person, and the answer names the new value.
- **confirm** (spending more, people and roles, sending or publishing outside, anything destructive, team rules,
  ownership, the Strategy Bot): BotOps gets a Confirm card for the person to tap; nothing changes until they do, and nothing
  confirms it for them. A wake rule is direct unless it sends a webhook or forwards the call. The person's own
  `hub settings set` runs at once. A confirm entry whose route BotOps can't yet put on a card is person-only until it
  can (`NO_CARD_YET`; the catalogue test fails once one can, so it goes back to confirm).
- **person** (a sign-in, a secret value, an upload such as the team icon, the person's own approvals and mail): BotOps
  answers with a one-line reason and the card's link.

The Assistant's Confirm card is in the person's own Assistant chat (the owner's Strategy Bot chat), and it may card every
catalogue route BotOps may. A direct entry runs on the person's own words: the write cites their message in that chat
(`--on-behalf-of turn`), as a task action does. Without it, or while the owner's "Assistant acts without asking" is off,
a direct entry is a card too. A card or a cited write from the Assistant is exactly one entry's body: two settings at
once, or a person-only one, is refused.

Someone without the rights is refused with who can change it ("Only the owner or an admin changes Release times").
The route still makes its own check after the catalogue's. `backend/tests/test_settings_catalog.py` reads every write
the Settings pages make and fails when no entry covers one; a one-off action (check, replay, undo, run now) is listed
in `NOT_SETTINGS` with the reason it needs none.

## Write without Read

Someone who may write to a bot but not read it can talk to it, and only sees what is theirs: their
own conversations with it and the tasks they requested, created or own. They do not see its status,
run log, files, updates, routines, other humans' tasks or shared rooms, and the step-by-step "what it
did" under its replies is hidden. The bot's page for them holds its name, role, who owns it, the
humans it works for, a **Send a request** box, and their own threads and tasks. A bot they may only
see shows the same About card and no request box.

When a bot uses a shared room, the room belongs to the humans it works for who may read it. Anyone else
who writes to it talks to it in a room of their own.

## Bots and humans are checked alike

A bot's rights to another bot come from the same audiences: put a bot in another's Write list to let it
send that bot requests. A bot may always answer one that wrote to it, or that holds a task it asked for,
so a private bot can still be replied to. `bot_contact` (Other bots: may chat and assign, replies only,
tasks only) stays as a further limit between bots and now also applies to notes and to comments that
wake a bot. A tasks-only bot still takes notes, comments and messages from its manager (`reports_to`) and from BotOps.

Personal API tokens, the MCP tools and the Assistant act as the human, with the human's access.

**Personal tokens** never leave a credential behind: a token is refused (`403`) on making or revoking tokens, creating or
rotating a bot's agent credential, approving a pairing and making a SCIM token. A service key made with a token stops
working when that token is revoked or expires ([Service keys](service-keys.md)). A token still enrolls the person's own computers (the code lasts 15 minutes) and
stores, grants and reveals Credentials as the human may. The owner and the Admins see every person's tokens (Settings >
Computers > API tokens, `GET /api/v2/access/tokens`, never the secret) and revoke any of them; a member sees and revokes
only their own.

## What each answer looks like

- A bot the caller cannot **see** does not exist for them: lists leave it out, counts and pages do not
  include its work, and asking for it by name answers `404`.
- A bot they can see but not read or write answers `403 forbidden` with what is missing, for example
  `You can see counsel but not send it requests. Ask the human who owns it for Write access.`

`GET /api/v2/bots` and `GET /api/v2/org` return only the bots the caller can see, each with
`access: {see, read, write}` for that caller, and take `?can=read` or `?can=write`. A bot the caller may only
see comes back without its status, computer, queue or configuration. The team chart is one piece: the bots
under a hidden bot hang from the nearest thing above it that is still shown.

## The Legal example

The Legal bot reviews contracts. Everyone should be able to send it a request, but its work is
Legal's business.

1. Settings > Bots, **Edit** in Legal's Access column.
2. Choose **Requests only**.
3. Under *Who can read its work* tick the **Legal** group. Save.

Now everyone sees the bot and can chat with it or give it a task. Cara, in the Legal group, also reads its
tasks, updates and files. Dee, in Sales, sees a **Send a request** box and her own threads and
tasks with it; the bot's status, files and other humans' requests are not shown to her. To let the
Sales lead read it too, add them to the Read list.

The same through the API, as the owner or the bot's manager:

```
GET /api/v2/bots/legal/access
PUT /api/v2/bots/legal/access
{"see": {"everyone": true},
 "read": {"teams": ["legal"]},
 "write": {"everyone": true},
 "revision": 3}
```

`revision` is the one the GET returned; a stale one answers `409 version_conflict`. Each level takes
`everyone: true` or lists of `people` (roster ids), `teams` (group ids: the field kept the name it had first) and `bots` (slugs); unknown ones answer `404`.
An unchanged save answers `409 unchanged`.

## Moving off `hub-access.yaml`

`private_owners` and `routing_permissions` in `registry/hub-access.yaml` no longer decide anything, and
neither does a bot's "Can use" list (`owner_ids`), which now only says who a bot works for and who is in
its shared room. The first start after upgrading resets every bot to **Open**. If either list was in the
file, the server logs one warning and the owner finds a note on Settings > Health, "hub-access.yaml
private/routing lists are no longer used; bots are now Open; set access in Settings > Bots". Set the
access you meant there; saving any bot's access, or dismissing the note, clears it.

## In SQL

`POST /api/v2/sql` is the one place a bot the caller may only see or write to does not appear: the
`bots`, `bot_status`, `schedules`, `bot_config`, `turns`, `jobs` and related tables hold the bots the caller can read,
and tasks follow the rule above ([Tico SQL](hub-sql.md)).

## Tags

Task movers (the team owner and humans on the leadership, product or engineering teams) attach
and remove tags on any task. A bot attaches and removes a classification tag (`qa`, an area, a
priority) on a task it manages: one it owns, one it asked for, or one of a type it manages (the
type's manager, such as Head of Product on the Product roadmap or Head of Engineering on Dev
tickets), and only when it can already read the task. Two kinds of tag keep their gate for a bot,
both to add and to remove: a tag on a public roadmap (Settings → Public roadmap) needs the bot's
exact approved publish approval, as public text does ([Roadmaps](roadmaps.md)), or a mover when the
team does not ask bots for one; `goal:on` and `goal:off` change how Tico runs the task, so only a
mover changes them. The same holds when a bot creates a task: it may give the new task classification
tags, but a task created with `goal:on`, `goal:off` or a public roadmap tag is refused and nothing is
created. A publish approval is for a task that exists, so the bot creates the task without the roadmap
tag and then adds it with the approval, or asks a mover. A refusal says which tags need a person or an approval and why; an approval that is
not decided yet, already used or for other tags is the same refusal. It counts
toward the bot's quarantine once per task an hour, so retrying it cannot quarantine the bot; any
other refusal counts as before. Lane and number stay a mover's, and nothing here makes a bot a
mover. Creating and editing a tag is for those movers or that tag's owner, including
a bot owner. A teammate may create a tag they own. Owner rights on a tag do not grant mover
rights on its tasks. Tags and templates are readable by signed-in teammates; their task lists
show only tasks each caller may read. Every edit requires the current tag version.

## Task types and steps

The owner and humans on the leadership, product or engineering teams manage types and steps.
Task participants use them with their existing task permissions: choosing a step checks the
status it maps to, including the normal rules for Ready, completion notes and closing. Definitions
are team-wide; selecting a type never grants access to additional tasks. See [Tasks](tasks.md).
