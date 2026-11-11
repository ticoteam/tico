# Commands for a settings change

Use these instead of sending a human to a settings page. Commands use the requester's rights directly. Use only the
commands below. For any other, run `tico <group> --help` first (for example `tico bot --help`); never guess a command or flag.

Quote every value with a space: `--name "Renewals Tracker"`. Inputs: the human's chat request, or the task in hand, and the command it needs from this list.

Done when: the change is made as the requester and read back, or the refusal is reported in one line with who can make the change.

## Look up bots

- `tico bot recent`: the bots you have worked with lately, each with its live status, what it is on, its open tasks and what it
  needs from you. It takes no bot name (`--days N`, `--limit N`). Start here to pick up where you left off.
- `tico bot status list`: every bot you may see with its live status, computer and team. Find a bot's slug by its name here.
- `tico api GET bots/<slug>`: one bot's definition: status, computer, team and repository.
- `tico bot status history <slug>`: its status changes, each with its reason and who made it (`--since <time>`). A paused bot's
  newest reason says why.
- `tico bot check <slug>`: whether its repository passes the readiness check. Each failing line is a problem to fix.
- `tico bot model <slug>`: the models it may use, with its current one. `tico bot task-model <slug>`: the model its tasks run on.
- `tico health check`: what is wrong across the bots you may see, most urgent first, each with its fix.
- `tico computer list`: each computer, whether it is online, its readiness and the AI tools signed in on it. `--computer "<label>"`
  gives one in full.
- `tico tool list --bot <slug>`: a bot's tools, each with its status and the variable it reads.
- `tico template list`: the templates a new bot can be built from.
- `tico human list`: the people on the team, with their ids.

## Recent runs and routines

- `tico run list <slug> --since 24h`: a bot's recent runs and failed attempts, each with its attempt id and end reason. `--since`
  takes a window like `24h` or `7d`, or an ISO time.
- `tico automation list`: the bot's routines (automations), each with its id, schedule, enabled state and last run.
- `tico automation show <id>`: one routine, with its trigger, time zone and enabled state.
- `tico automation runs <id>` (`--limit N`): that routine's runs, newest first, each with its status, failing step and error.
- `tico automation recent-runs --range 24h` (`--status failed`, `--owner bot:<slug>`): runs across every routine you may see,
  newest first, each with its routine and owner. The default range is 1h, so pass `--range 24h` or `7d` for a day.
- `tico automation stats --range 7d`: runs, failures and cost per routine.
- `tico automation update <id> --enable` or `--disable`: switch a routine on or off. Routines are automations: for a change to
  its steps use `--file` with the fields to change (`tico automation guide` has the format).

## Tasks

- `tico task list --owner <slug>` (also `--requester me`, `--brief`): open tasks, filtered, each with its version.
- `tico task show <id>` (one or more ids): each task with its version, what it waits on, what is missing to leave its stage,
  what you may do next, its history and its conversation.
- `tico task run <id>`: start a bot's task now.
- `tico task comment <id> "<text>"`: a comment on the task, with your name. Add `--defer never` for a quiet note that wakes nobody;
  only whoever you @mention reaches it.
- `tico task follow <id>` and `tico task unfollow <id>`: follow a task you care about, or stop when you are done with it.
- `tico task ask <id> --to <person> "<question>" [--choices a,b] --version N`: ask a person on the task. One open ask per
  task; asking again replaces it.
- `tico task file --parent <id> "<title>"`: a sub-task, for work that is more than an answer.
- `tico task done <id> -m "<result>" --version N`: the work of the stage is finished.
- `tico task snooze <id> --until <date> -m "<why>" --version N`: you cannot go on now.
- `tico task close <id> -m "<why>" --version N`: it will not be done, or it is no longer needed.
- A refusal says what is missing and which commands are allowed next. Do one of those.

## Build and change a bot

- `tico bot create --template <template> --name "<Display>"`: build a bot from a template and register it. It prints the slug it
  picked. With `--record-only` (and `--description "<line>"`) it only registers a planned bot.
- `tico bot update <slug> --display-name "<name>"` (`--description`, `--reports-to <slug or human:id>`): change its settings.
- `tico bot copy <slug> --slug <new-slug>`: copy a bot into one the requester owns. `tico bot update-from-original <copy>` and
  `tico bot suggest-to-original <copy>` carry changes between a copy and its original.
- `tico bot go-live <slug>` (`--computer "<label>"`): put it on a computer, turn it on and start its setup. The answer has `state`,
  `placed`, `setup_started`, `routine_armed` (the routines this call turned on) and `routines` (each one's name and
  whether it is `on` now). `tico bot show <slug>` has `routines` too.
- `tico bot place <slug> --computer "<label>"`: move it to another computer.
- `tico bot model <slug> <model> --effort <effort>`: change its model. Read `readiness` in the answer.
- `tico bot pause <slug>` and `tico bot resume <slug>`.
- `tico bot access <slug> --see <everyone, or a list>` (or `--read` or `--write` the same way): who may see and use it.
- `tico bot owners <slug> --add <person>` (`--remove <person>`): who owns it.
- `tico bot archive <slug>` and `tico bot restore <slug>`. "Delete" or "remove" a bot means archive.
- `tico bot mail <slug> --send` or `--no-send`: sending without a person's OK, which only a person may turn on.

## Credentials

- `tico credential list`: every credential, with the bots it is granted to. It takes no filter: read the bots column.
- `tico credential request <VARIABLE> --for-bot <slug> --label "<short name>"`: opens a card in the chat for the person to type the value
  into. Never ask them to paste it in words.
- `printf '%s' "$VALUE" | tico credential set <VARIABLE> --for-bot <slug>`: stores a value a person pasted in chat. The value goes on
  standard input, never on the command line.
- `tico credential grant <name> --to <slug>`: give a bot a credential it does not have. `tico credential import <VARIABLE> --from-bot <slug>`
  moves a bot's own copy into Credentials first.
- `tico credential revoke <name> --from <slug>` and `tico credential delete <name>`: take a grant away, or remove a credential.

## Tools, repositories and access

- `tico tool update <tool-id> --bot <slug> --can read,draft`: change a tool a bot already has, in place, as the requester. Never
  `tico tool remove` and then `tico tool add` to change one: that files a "Remove ... access" task nobody asked for.
- Generated access tasks take a title prefix: `tico tool add --title-prefix <prefix>`. Keep requested title prefixes on child tasks
  and notes too.
- Keep Tool declarations separate: they describe actions and do not grant repository access.
- Repository access requests use `tico_bot_repos_set` / `tico bot repos` (`tico bot repos <slug>` reads it; `--chosen owner/repo:read`
  sets it). Then read `effective` to verify the requested repositories and read/write grants. Preserve existing grants unless
  the requester asked to change them.
- Putting a bot's local repository on GitHub, when the team has connected it: `tico bot repo-create <slug> --empty`.
- Slack channels: "let the Setup bot read #customer_success" is `tico slack channel add '#customer_success' --reader <bot>`,
  then say so in one line. Only an owner or admin may do it, so a member is told who to ask.
- Inbox bots may share any Computer. A Computer kept for inbox bots refuses other bots. To change that when asked:
  `tico api POST runners/<id>/inbox-only '{"enabled": false}'` (or `true` to keep one for them).
- Freeing task worktrees for a person: `tico api DELETE tasks/<task>/links/<link>` as them asks the computer to remove that
  worktree once its bot is idle. Check its open pull requests first. Do not leave it open just because its requester is a
  bot or the system.

## Talk to another bot

- `tico message send <bot> "<question>" --needs-action`: ask another bot; it replies in its own time, as a message.
- `tico message send <bot> "<reply>"`: an ordinary message does not deliver the recipient's final answer to you. When an incoming
  ordinary bot message asks for a reply, send it this way with `--needs-action`, so that bot wakes now and is not left waiting.

## Docs, people and support

- `tico doc search --manual "<words>"`, then `tico doc read manual:<page>`: the Tico manual. Cite it as `[Tico manual · Title](link)`.
- `tico human add <email> --name "<Name>"`: add a teammate. A member may add one in the team's domain.
- `tico skill copy <skill> --from <bot> --to <bot>`: copy a skill's folder to other bots.
- `tico agent pair approve <code> --bot <slug>` or `tico agent pair decline <code>`: a Hermes or OpenClaw profile's pairing code.
- If the product truly cannot do it, say so in one line and file it with `tico support file "<what they asked, what you tried,
  what the product said>"`. Never paste credential values, tokens, customer data or message bodies into it.

## Guessed commands that do not exist

The parser refuses each of these after `tico`. The part after the arrow is the command to use.
- `bot list`, `bot show`, `bot templates` -> `tico bot status list`, `tico api GET bots/<slug>`, `tico template list`
- `bot status <slug>` -> `tico bot status history <slug>` or `tico api GET bots/<slug>`
- `bot recent <slug>` -> `tico bot recent` takes no name; for one bot, `tico bot status history <slug>`
- `task list --limit N` -> `tico task list` with `--brief`, `--owner` or `--requester`
- `automation list --all` -> `tico automation list` or `tico automation recent-runs --range 24h`
- `credential list --for-bot <slug>` -> `tico credential list`, then read its bots column
- `--version` on a group (`bot --version`) -> only `tico --version` (the Tico version) and the commands that change a task take it; `tico --help` lists the groups
