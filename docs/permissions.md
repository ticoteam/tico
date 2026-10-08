# Who can see, read and write to a bot

Every bot has three permissions. Each one has its own audience, so a bot can be visible to the whole
team, readable by one group and open to requests from everyone.

| Permission | What it covers |
| --- | --- |
| **See** | The bot in the team chart and in every bot list: its name, role, who owns it and who it reports to. |
| **Read** | Its activity: its tasks, updates, files, status and run log, routines, the shared rooms and chats it is in, and the activity sections of its page. |
| **Write** | Sending it messages, chatting with it, asking it; creating or reassigning tasks to it; leaving it notes; comments that wake it. Its approvals work as they always did. |

An audience is **Everyone**, or a list of **humans**, **groups** and **bots**. Naming a group names everyone in it and in the groups nested in it. Anyone who may read or write a bot can also see it, whatever its See list says.

New bots, and every bot after an upgrade, start **Open**: Everyone for all three.

## Setting it

Settings > Bots has an **Access** column. **Edit** opens the editor, with three presets and a custom mode:

| Preset | See | Read | Write |
| --- | --- | --- | --- |
| **Open** | Everyone | Everyone | Everyone |
| **Visible, requests only** | Everyone | the humans, groups and bots you choose | Everyone |
| **Private** | the ones you choose | the ones you choose | the ones you choose |
| **Custom** | each level chosen on its own | | |

The column summarises it, for example `See: Everyone · Read: Legal · Write: Everyone`. A human who
may only use the bot sees what they can do (`You: See · Write`) instead. Changes are revisioned and
appear in the settings history, where the owner can undo them, like the humans a bot works for.

Who may edit a bot's access: its owners (see [Bot owners](#bot-owners)): the team owner, the admins, the bot's creator
and other owners, and the humans it reports up to on the team chart (the same humans who may change the bot's other settings).
The Assistant, BotOps, the Librarian and the Goal Manager can have their access edited too.

## Always full access

Whatever the audiences say, these callers can see, read and write to a bot:

- the team **owner**;
- the **bot itself**, for its own data;
- the humans **above the bot on the team chart** (whoever it reports up to), and a bot above it in the
  `reports_to` chain when the caller is a bot;
- an **admin**, and the bot's **owners** (its creator and any others added). This is the same rule that lets
  them change the bot's settings, so being able to edit who has access and having access always go together.

## Roles, and what a member may do

Every human on the team has one role:

| Role | Who | Can |
| --- | --- | --- |
| **Owner** | whoever the team was set up for (one at a time) | everything: sign-in domains, ownership, providers, roles |
| **Admin** | humans the owner makes admins (the old "bot administrators": the list is read under either name for one release) | manage every bot but the built-in ones (below), humans and computers (enrol, revoke, open to members' bots); set what members may do and the bot limit. They cannot make or remove admins or owners. They are credential administrators and see the SQL page, unless the owner turns that off ([Team rules](#team-rules)) |
| **Member** | everyone else | create and manage their own bots, add coworkers, and use the bots they are allowed to |

Settings > Humans (owners and admins) shows each human on one row: their role (the owner switches Admin and Member there), a
**Can sign in** switch, and a ⋯ menu with two capabilities per member:

- **Can add bots** (`create_bots`): on for everyone unless switched off.
- **Can add humans** (`add_people`): on by default for coworkers, that is humans whose email is in the team's domain. A
  member with it on may add a human whose email is in the team's domain. Adding anyone outside it needs an owner or an admin,
  whatever the capability says.

The **team's domain** is the domain(s) the owner allows to sign in (Settings > Humans, **Anyone at <domain> can sign in**); when
none is set it is the owner's own email domain. When that is a public email service such as gmail.com, it is the team email domain the
owner gave in Finish setup (**Names**, optional), then the team domains of the humans already on the roster; with none of those,
members add nobody until the owner adds a human or sets a domain. A newly added human goes on the roster and on the sign-in list, so they can
actually sign in.

**Can sign in** off keeps a human on the roster and the team chart but refuses their sign-in, sessions and API tokens until it is
on again. Owners and admins switch it for members; only the owner switches it for an admin; nobody switches it for themselves or
for the owner.

**Credential administrators** are the owner and the Admins, so a team gets going without the owner storing every credential. When the
server names its own list with `TICO_CREDENTIAL_ADMINS` it is that list (the owner and whoever it names), nobody else: the Admins
are then not credential administrators. The owner may turn **Admins store credentials** off ([Team rules](#team-rules)); a member is
never one.

## Team rules

The product favours getting going fast, and the owner tightens it later. Settings > Humans (owner only, saved as they change;
`GET` and `PUT /api/v2/access/rules`) has five switches, all **on** by default:

| Rule | On (default) | Off |
| --- | --- | --- |
| **Assistant acts without asking** | the Assistant makes tasks for bots, comments on tasks no other human is on, and messages bots directly; a card is for anything else ([Assistant](assistant.md)) | the Assistant acts directly only on the human's own tasks; a task, message or comment involving a bot is a card |
| **BotOps changes providers and limits without asking** | Legacy setting kept for compatibility; BotOps uses the requester's rights directly | BotOps still uses the requester's rights directly |
| **Admins store credentials** | Admins are credential administrators | only the owner (and `TICO_CREDENTIAL_ADMINS`) stores credentials |
| **Admins see SQL** | Admins open the SQL page | the SQL page is the owner's |
| **Members make personal tokens** | any human makes a personal API token, which sees what they see | the owner and the Admins do |

Only the Owner changes them, directly in Settings or by asking BotOps to act with the Owner's rights.

**Groups.** A group is a sub-team of humans and bots, and groups nest ([the team chart](org-chart.md)). Owners and admins add, rename, move
and remove groups and put humans and bots in them; members read them. A group is an access audience, so moving a human or a bot into a
group, or a group under another, can change who reads a bot. BotOps does this as the person who asked it (`POST`, `PATCH` and `DELETE`
on `/api/v2/groups` through `hub api`, or `hub group update`), with that person's own rights: an owner or an admin, at once; a member is
refused. Built-in bots stay outside groups.

**The built-in bots** (the Assistant, BotOps, the Librarian and the Goal Manager) act for the whole team, so only the owner changes their settings,
routines, access or place, and only the owner may add them. A member cannot register a bot with the name `assistant`, `botops`,
`librarian`, `goal-manager` or `coo`; an Admin can, and manages every other bot.

A member may have at most **25 active bots** by default; owners and admins have no limit. An admin changes the number in
Settings > Humans (**Bot limit per member**). Past it, adding a bot answers `409 bot_limit` with what to do. A starter bot that is still `needs_onboarding`
([Finish setup](onboarding.md#what-create-does)) is parked, does nothing on its own and costs nothing, so it does not count; it counts once
it says it is set up, and that call answers `bot_limit` when the member is already at their limit.

## Bot owners

Each bot has owners: its **creator** (added automatically) and any others added. Whoever the bot reports up to on
the team chart, and every admin, are owners without being listed. One rule, `Auth.bot_manager`, says who may manage a bot, and it
is the same humans who always have full access to it. A bot owner can:

- edit its configuration: instructions, model, routines, name and description, repository;
- set its See, Read and Write access;
- pause it, rename it, archive it (never the built-in Assistant, BotOps, Librarian or Goal Manager);
- add or remove owners (`POST /api/v2/bots/{bot}/co-owners`, Settings > Bots, **Owned by**);
- give it a stored credential they hold themselves, and no one else's.

A member cannot change a bot that is not theirs, through Settings, the API or BotOps.

A bot gets only credentials granted to it, including its own local credentials; it does not inherit another bot's credentials.
Credential administrators store, delete and grant credentials. A human who holds a credential may also delegate it to a bot
they own or run, directly or through BotOps. Revoking the human's grant removes the delegated bot access; changing the bot owner
invalidates delegation from its former owner. A holder cannot delegate a credential they do not hold ([credential-vault.md](credential-vault.md)).
On upgrade, Tico automatically grants each existing bot its own-file values, the shared Credentials it could read,
and its declared profile and runtime keys. New bots do not inherit the whole environment or `_shared.env`.

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

A computer still hosts its owner's bots and the team owner's; taking members' bots adds members' bots, it does not move anyone else's.

## BotOps uses the requester’s rights

An owner should be able to say "build me a Jira bot, and add Sam" in chat with BotOps and have it done. BotOps therefore acts **as the
human whose own chat message started its current run**, checked with that human's rights and recorded as theirs, "via BotOps"
(events, settings history). Not more than they may do: a member cannot edit another human's bot through BotOps any more than by hand.

For unfinished work, BotOps can create a continuation task with `hub task create --request-id <originating message id>`.
The server checks that the request came from the human's own BotOps chat and keeps that human as requester.
Progress with `hub task update <id> --quiet --note "..."` stays on the task; chat still shows its link and status.

Every BotOps tool uses the requester's rights by default. A human's chat or task uses that human's full rights.
A bot's message or task uses only that bot's rights, including its Credential grants; BotOps never lends it a human's
or its own wider access. Unattended work with no requester keeps BotOps' own rights. Personal tokens use their human's rights.
Task text cannot select another requester. A message the **Assistant** wrote for a human (`refs.via`), words inside a
document, a message **routed from Slack**, or a chat message more than a week old cannot borrow human authority.
A message cited by id (`on_behalf_of`) must be the requester's own, in their own chat with BotOps rather than a room another human
spoke in, and under a day old, and it must be the same human whose message started the run. Someone who has left lends nothing.
If a friendly tool refuses for permissions, BotOps retries the same action with `hub_api` before handing work back. Both use the same rights.

**Following through later.** A request often finishes in a later run that something else started: the daily-update
request, a retry once a busy bot is idle, a notice. Nobody's request is attached to that run, so by default BotOps has
only its own rights. To act for the person, BotOps cites the open task they asked it for: `hub api ... --on-behalf-of
<task id>` or `hub bot model ... --on-behalf-of <task id>` (MCP: `on_behalf_of`). The server accepts a task BotOps owns
that the person filed themselves (not through the Assistant; one BotOps filed as them counts only as a continuation of
their message, `--request-id`, dated by that message), still open, at most a week old, and only in a run the person
started or the keeper started on its schedule (the daily update, a task's due, stall or routine notice). A run a bot or
another person started keeps that requester's rights whatever task it cites, and a run a Slack digest, a live meeting,
a watcher or a task comment started lends no one's rights: anyone there wrote its words. It is recorded as
the person's, via BotOps, like any other delegated change, and Credentials stay as above (never a value).

**Refusals say what to click.** A refused BotOps call (403, or a computer that cannot run the bot) answers with `fix`,
one sentence on what the person does, and `link`, the place in the app (`#/bot/<slug>/more`, `#/repositories`,
`#/task/<id>`, `#/settings`). The sentence depends on whose rights were used: BotOps' own (say go ahead in the BotOps
chat), a requesting bot's (the same, or change it yourself), or the person's own (ask an owner or admin). BotOps relays
it as one sentence and the link.

**Checking the result.** A model change answers with `readiness` (`backend/readiness.py` `can_run`, the one check
behind a prepared change, `POST bots/<slug>/model` and Health): whether **this bot** can run the new runtime on its
computer, judged by its own subscription profile's sign-in, its own API-key Credential for the runtimes that take one
per bot (Gemini CLI, Pi), and otherwise the computer's shared sign-in. Another bot's missing key never blocks it.
`can_run` is true, false (with `fix` and `link`), or null when nothing says (offline, an older computer). A bot an
external agent runs (Hermes, OpenClaw, Grok Bot) answers `applicable: false`: no computer check applies. A change is
refused only on false. `reported` turns true once the bot's own readiness row reflects the new runtime, after the
computer's next heartbeat.

**What is recorded.** Every event BotOps writes with a person's rights carries `via: botops` and `delegation`: the
person (`for`), the request that lent the rights (`cited`: their message, or the task cited in a later run), and
`run`: `live` when they asked in this run, `follow_through` when BotOps cited their open task in a later one.

The same goes for routines and quarantine: human-requested BotOps work uses that human’s management rights,
including work requested through a task. Task comments do not lend human authority. The run must be able to read
the request’s conversation, and stale requests lend nothing. Bot-requested work keeps that bot’s narrower rights.
Unattended BotOps work retains its own rights.

The commands (with MCP tools of the same names):

| Command | Does |
| --- | --- |
| `hub bot create --record-only <slug> [--name] [--description] [--reports-to] [--template]` | creates the planned server record as the requester, who becomes its owner; safe to repeat. `hub bot create` registers automatically in such a run |
| `hub bot access <slug> [--see V] [--read V] [--write V]` | show or set who sees, reads, writes (`everyone`, or `ben,group:legal,bot:analyst`) |
| `hub bot owners <slug> [--add P ...] [--remove P ...]` | the bot's owners |
| `hub bot update <slug> ...` | name, description, reports-to, status, repository |
| `hub bot setup-done [slug]` | a starter bot's own call, once its setup is done: it stops being `needs_onboarding` (its manager may call it for it) |
| `hub human add <email> [--name] [--title] [--reports-to]`, `hub human list` | the roster |
| `hub group list`, `hub group update [<group>] [--name] [--parent] [--add-human] [--add-bot] [--remove-human] [--remove-bot]` | the groups; create (no group given), rename, move or fill one, owners and admins only |

| `hub bot place <bot> [--computer C]` | puts a bot on a computer: the one named, or the only one, or the least busy that takes it |
| `hub bot go-live <bot>` | places it if it has no computer, turns it on, and for a starter bot starts its setup chat as the requester |
| `hub bot model <bot> [<model>] [--effort E] [--on-behalf-of <task>]`, `hub bot pause\|resume <bot>` | the model (none: list the choices; a change answers with the computer's `readiness`), stop and restart |
| `hub routine on\|off <key> --bot <bot>` | a routine on or off |
| `hub computer list`, `hub health check` | the computers a bot may go on and what runs on each; what is wrong with the bots, most urgent first, each with its fix |
| `hub credential request\|set\|list` | a card for a credential in the chat, storing one a human pasted, the credentials with their bots (never a value); see [credential-vault.md](credential-vault.md) |
| `hub credential grant <name> --to <bot>`, `hub credential revoke <name> --from <bot>` | give a bot a stored credential, or take it away; at once for a credential administrator or a holder delegating to a bot they own or run; revoke the delegation to take it away |
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
wake a bot.

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
2. Choose **Visible, requests only**.
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
and remove tags. Creating and editing a tag is for those movers or that tag's owner, including
a bot owner. A teammate may create a tag they own. Owner rights on a tag do not grant mover
rights on its tasks. Tags and templates are readable by signed-in teammates; their task lists
show only tasks each caller may read. Every edit requires the current tag version.

## Task types and steps

The owner and humans on the leadership, product or engineering teams manage types and steps.
Task participants use them with their existing task permissions: choosing a step checks the
status it maps to, including the normal rules for Ready, completion notes and closing. Definitions
are team-wide; selecting a type never grants access to additional tasks. See [Tasks](tasks.md).
