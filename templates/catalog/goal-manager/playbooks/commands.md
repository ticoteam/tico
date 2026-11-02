# Hub commands for goal work

Run only the commands below. They exist today. For any other, run `hub <group> --help` first.
`<...>` is yours to fill; `[...]` is optional; `a|b` is a choice.

Not commands, so never run them: --json on task, goal or kpi commands, proposal show, proposal list with --all, kpi list
with --all, kpi readings, and any `--help` or `--version` probe of a name not listed here. Use the lines below instead.

## Read the strategy
- `hub strategy show`: the published strategy: summary, vision, bets (id, statement, wrong-if), their goals and health.
- `hub strategy versions`: published versions, newest first. `hub strategy diff --from <n> --to <n>`: what changed between two.
- `hub goal strategy-bot`: your level, whether the regular checks run, and `asked_today` per person.
- Level 3 only: `hub goal strategy-bot-decide "<reason>" '<changes json>' --goal-id <goal>`: creates or reorders bot tasks
  (`playbooks/acts-on-its-own.md`).
- Level 2 and up: `hub goal strategy-bot-merge <person> "<title>" "<the one question>" <task-id> <task-id>...`: one item for
  the same question several bots ask one person (`playbooks/recoveries.md`).
- `hub goal company`: the vision and description (read only).

## Goals
- `hub goal list`: your goals and the chain above them. `hub goal list --all`: every live goal (`--status red,yellow` narrows it).
  `hub goal list --owner <bot-or-person>`: someone else's.
- `hub goal show <id>`: one goal: owner, status, bet, body, its KPIs and recent check-ins.
- `hub goal checkin-list <id>`: the goal's check-ins, newest first.
- `hub goal refresh [--goal <id>]`: the status pass. Returns each goal whose colour changed, with the reason, and any
  `suggested` disagreement with a colour a person set. Use it after the readings are posted.
- `hub goal checkin <id> "<their words>" --signal on_track|at_risk|off_track --from <owner>`: records the owner's own
  words. Only when they said them. Leave `--signal` out when they did not.

## KPIs
- `hub kpi list`: every KPI with its latest reading and colour. Filters: `--goal <id>`, `--unlinked`, `--owner <me|bot|person>`.
  `--archived` adds the archived ones.
- `hub kpi show <id>`: one KPI: definition, versions, the goals using it with their targets, every reading with its id and
  evidence. `--effective` leaves out readings a correction replaced.
- `hub kpi log <kpi-id> <value> "<how it was computed>" --period-start <YYYY-MM-DD> --period-end <YYYY-MM-DD> --evidence "<link or note>" --quality measured|partial|estimate --definition-version <n>`:
  posts one reading for a period. A correction adds `--supersedes <reading-id>`.

## Proposals (the owner confirms)
- `hub proposal create --kind <kind> [--goal <id>] [--kpi <id>] --payload-file <file.json> --reason "<one sentence>"`:
  files one proposal. The command returns the proposal's id once it is saved. That means it is filed: do not list proposals after it to check.
- `hub proposal list [--status pending|confirmed|rejected|all] [--goal <id>] [--kpi <id>]`: pending by default.
  Use `--status all` to see every one. Read the rejected ones for the same goal before you propose again.
- Kinds and payload shapes: `playbooks/README.md`.

## Tasks
- `hub task show <id>`: one task: title, body, owner, requester, status and history. Several ids in one call are allowed.
- `hub task list [--owner <bot-or-person>]`: tasks, optionally one owner's. Look here for an open task before you file one.
- `hub task update <id> --status done --note "<the result, in a few plain sentences>"`: finishes a task you own.
  The requester closes it. Every task you are given ends with this command.
- `hub task create --owner <person> --title "<question>" --body "<the facts and what you need>"`: a question or a source
  request to a person, only as `playbooks/check-ins.md` and `playbooks/owner-chat.md` say.

## Questions and messages
- `hub question ask <bot> "<question>"`: asks another bot. `hub question answer <message-id> "<answer>"`: answers one asked of you.
- `hub message send <person-or-bot> "<text>"`: a message, only when a playbook says to send one.

## Market, team and health
- `hub market briefing latest`: the Librarian's latest weekly briefing: the bets it touched and its items.
- `hub market show <entity-id>`: one market entity: its events and links. `company/self` is the team's own entry.
- `hub market delta`: what changed in the market recently, with sources.
- `hub doc search --market "<topic>"`: searches the market pages.
- `hub lens list`: the lenses. `hub lens run <id>`: runs one (`playbooks/run-a-lens.md`).
- `hub lens show <run id>`: a lens run that was still running: its status and result.
- `hub team show`: the team chart. `hub group list`: the groups.
- `hub group update <id> ...` and `hub org reports-to <who> <manager>`: an org change the owner asked for, where the server
  lets you (`playbooks/owner-chat.md`).
- `hub health check`: what is running, blocked or unhealthy for the owner.

## Never run these
They belong to the owner or to the server. Do not run them, not even with `--help`.
- `hub strategy draft`, `hub strategy bet`, `hub strategy publish`.
- `hub goal status` (colours are never set by hand), `hub kpi create`, `hub kpi update`, `hub kpi target`, `hub kpi link`,
  `hub kpi unlink`. A change to a goal, a KPI or a target is a `hub proposal create`.
- `hub proposal decide`: a person's own decision; a bot is refused.
