# Tico commands for goal work

Run only the commands below. They exist today. For any other, run `tico <group> --help` first.
`<...>` is yours to fill; `[...]` is optional; `a|b` is a choice.

Not commands, so never run them: --json on task, goal or kpi commands, proposal show, proposal list with --all, kpi list
with --all, kpi readings, and any `--help` or `--version` probe of a name not listed here. Use the lines below instead.

## Read the strategy
- `tico strategy show`: the published strategy: summary, vision, the sections, bets (id, statement, wrong-if), their goals and
  health, and `proof_since` (when How we know they want it last changed; null when empty).
- `tico strategy versions`: published versions, newest first. `tico strategy diff --from <n> --to <n>`: what changed between two.
- `tico goal strategy-bot`: your level, whether the regular checks run, and `asked_today` per person.
- Level 3 only: `tico goal strategy-bot-decide "<reason>" '<changes json>' --goal-id <goal>`: creates or reorders bot tasks
  (`playbooks/acts-on-its-own.md`).
- Level 2 and up: `tico goal strategy-bot-merge <person> "<title>" "<the one question>" <task-id> <task-id>...`: one item for
  the same question several bots ask one person (`playbooks/recoveries.md`).
- `tico goal company`: the vision and description (read only).
- `tico strategy vision --task <id> "<vision>"`: only on the server's vision question, the vision the owner sent there, in
  their words (`playbooks/vision-question.md`).

## Goals
- `tico goal list`: your goals and the chain above them. `tico goal list --all`: every live goal (`--status red,yellow` narrows it).
  `tico goal list --owner <bot-or-person>`: someone else's.
- `tico goal show <id>`: one goal: owner, status, bet, body, its KPIs and recent check-ins.
- `tico goal checkin-list <id>`: the goal's check-ins, newest first.
- `tico goal refresh [--goal <id>]`: the status pass. Returns each goal whose colour changed, with the reason, and any
  `suggested` disagreement with a colour a person set. Use it after the readings are posted.
- `tico goal checkin <id> "<their words>" --signal on_track|at_risk|off_track --from <owner>`: records the owner's own
  words. Only when they said them. Leave `--signal` out when they did not.

## KPIs
- `tico kpi list`: every KPI with its latest reading and colour. Filters: `--goal <id>`, `--unlinked`, `--owner <me|bot|person>`.
  `--archived` adds the archived ones.
- `tico kpi show <id>`: one KPI: definition, versions, the goals using it with their targets, every reading with its id and
  evidence. `--effective` leaves out readings a correction replaced.
- `tico kpi log <kpi-id> <value> "<how it was computed>" --period-start <YYYY-MM-DD> --period-end <YYYY-MM-DD> --evidence "<link or note>" --quality measured|partial|estimate --definition-version <n>`:
  posts one reading for a period. A correction adds `--supersedes <reading-id>`.

## Proposals (the owner confirms)
- `tico proposal create --kind <kind> [--goal <id>] [--kpi <id>] --payload-file <file.json> --reason "<one sentence>"`:
  files one proposal. The command returns the proposal's id once it is saved. That means it is filed: do not list proposals after it to check.
- `tico proposal list [--status pending|confirmed|rejected|all] [--goal <id>] [--kpi <id>]`: pending by default.
  Use `--status all` to see every one. Read the rejected ones for the same goal before you propose again.
- Kinds and payload shapes: `playbooks/README.md`.

## Tasks
- `tico task show <id>`: one task: title, body, owner, requester, status and history. Several ids in one call are allowed.
- `tico task list [--owner <bot-or-person>]`: tasks, optionally one owner's. Look here for an open task before you file one.
- `tico task done <id> -m "<the result, in a few plain sentences>" --version N`: finishes a task you own.
  Every task you are given ends with this command.
- `tico task create --owner <person> --title "<question>" --body "<the facts and what you need>"`: a question or a source
  request to a person, only as `playbooks/check-ins.md` and `playbooks/owner-chat.md` say.

## Questions and messages
- `tico message send <bot> "<question>" --needs-action`: asks another bot; it replies in its own time. `tico message send <bot> "<answer>"`: answers a bot that asked you.
- `tico message send <person-or-bot> "<text>"`: a message, only when a playbook says to send one.

## What customers do and say (`playbooks/keep-proof-current.md`)
- `tico growth numbers`: new customers by month and channel, the share kept at 30, 90 and 180 days, and the economics.
- `tico meeting search "<words>" [--since <YYYY-MM-DD>]`, then `tico meeting read <id>`: team meetings, never private ones.
- `tico task list --owner <bot> --updated-since <ISO time> --brief`, then `tico task show <id>`: a customer-facing bot's work.

## Market, team and health
- `tico market briefing latest`: the Librarian's latest weekly briefing: the bets it touched and its items.
- `tico market show <entity-id>`: one market entity: its events and links. `company/self` is the team's own entry.
- `tico market delta`: what changed in the market recently, with sources.
- `tico doc search --market "<topic>"`: searches the market pages.
- `tico lens list`: the lenses. `tico lens run <id>`: runs one (`playbooks/run-a-lens.md`).
- `tico lens show <run id>`: a lens run that was still running: its status and result.
- `tico team show`: the team chart. `tico group list`: the groups.
- `tico group update <id> ...` and `tico org reports-to <who> <manager>`: an org change the owner asked for, where the server
  lets you (`playbooks/owner-chat.md`).
- `tico health check`: what is running, blocked or unhealthy for the owner.

## Never run these
They belong to the owner or to the server. Do not run them, not even with `--help`.
- `tico strategy draft`, `tico strategy bet`, `tico strategy publish`.
- `tico goal status` (colours are never set by hand), `tico kpi create`, `tico kpi update`, `tico kpi target`, `tico kpi link`,
  `tico kpi unlink`. A change to a goal, a KPI or a target is a `tico proposal create`.
- `tico proposal decide`: a person's own decision; a bot is refused.
