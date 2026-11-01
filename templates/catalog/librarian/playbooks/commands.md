# Commands

Use these, with the arguments shown. `<...>` is a value you fill in; `[...]` is optional. Each line says what the command
does and what it returns. For any other command, run `hub <group> --help` first. Never guess a command or a flag.
`hub` has no `--version` flag; `hub whoami` says who you are.

## Answer a question

- `hub doc search "<words>"`: matching sections with the doc's id, path, title and excerpt. `--team` for team docs only,
  `--manual` for the Tico manual only.
- `hub doc read <id or path>`: one doc in full, with its version, updated date and author. An indexed external doc:
  `hub doc read external:<id> --revision <commit>`.
- `hub doc list [--prefix <folder>/] [--limit 500]`: internal docs by path, with id, version and updated date.
- `hub doc history <id or path>`: the doc's versions.
- `hub doc link-list`: the linked docs, with address and description.
- `hub doc fetch <url>`: a public link as text, and its links. It runs on this computer.
- `hub file search "<words>"`: files you may read, by title and text. `hub file read <id>`: one file as text.
- `hub sql "<one SELECT>"`: read-only SQL, for example
  `SELECT id, kind, about, claim FROM market_insights WHERE status = 'new'`.

## Map and docs

- `hub doc write _librarian/<name>.md --title "<title>" --body-file <file.md> --note "<what changed>"`: creates or replaces
  one of your docs. Write only under `_librarian/` and `FAQ.md`.
- `hub api GET "docs?limit=500&cursor=<next_cursor>"`: the next page of the doc list, when `hub doc list` has a cursor.

## Market

- `hub market find "<name>"`: entities by name, alias or domain, with their ids.
- `hub market show <entity id>`: one entity, its edges, its evidence and its last ten events.
- `hub market delta [--since 1d]`: what changed in the graph, grouped by entity.
- `hub market report --kind <new-entity|edge|property-change|correction|question|other> --claim "<one sentence>" [--about "<name>"] [--source <url>] [--quote "<words>"] [--source-ref <ref>]`:
  files an insight. Prose only; the graph does not change. Returns the insight id.
- `hub market apply <insight id> --source-kind <kind> --quote "<words>" --our-read "<one line>"`, with
  `--entity-type --entity-name --summary --tier` for a new entity, or `--edge-src --edge-rel --edge-dst` for an edge:
  writes the evidence, then the entity or edge, and marks the insight applied. It cannot set a property such as a price.
- `hub market resolve <insight id> --status <merged|rejected|needs-human> --resolution "<one sentence>"`: closes an
  insight without writing to the graph.
- `hub market import <file.jsonl> [--dry-run] --note "<where it came from>"`: a verified batch, one JSON line per entity
  or edge with its evidence. It is applied as one insight; `--dry-run` shows the result without keeping it.
- `hub market sweep [--today YYYY-MM-DD]`: files the run. Each needs-human insight becomes one task for the owner.
- `hub market page <name>`: one market page, for example `customer-themes` or `coverage-universe`.
- `hub market page <name> --body-file <file.md>`: rewrites the whole page from Markdown.
- `hub market refresh [--today YYYY-MM-DD]`: rewrites the weekly delta. It changes only on a Monday.
- `hub market watch add <entity id> <url> --kind <pricing|careers|changelog|docs|home|other>`: watches one page.
- `hub market watch list [--entity <entity id>]`: the watched pages and when each was last read.
- `hub market watch check`: reads the watched pages now.
- `hub market watch stop <watch id>`: stops one watch. The row is kept.
- `hub market briefing post --week <YYYY-Www> --title "<title>" --body-file <file.md> [--items-file <items.json>]`:
  posts the week's briefing by hand. The same week again replaces it. Only when a person asks.
- `hub market briefing latest`: the newest briefing, with its items and the bets it touches.
- `hub strategy show`: the published strategy: its bets and what each one cites.
- `hub lens list --market` and `hub lens run where-to-play --answers-file <file.json>`: the market lenses, and a lens run.

Market sizing (`hub market define|segment|size|figure|sources|spend`): the commands are in `playbooks/size-the-market.md`.

## Listening: posts routed to you

- `hub listening item list --destination market`: posts waiting for you, oldest first, with id, url and text.
- `hub listening item resolve <id> --status <accepted|rejected|duplicate> [--ref <insight id>] [--reason "<one sentence>"]`:
  accepted with the insight id it became, or rejected with a reason.
- `hub listening show <id>`: one post, its run and its decisions.

## Meetings

- `hub meeting read <meeting id>`: a transcript. Only when the task itself says its text was cut short.

## Tasks and messages

- `hub task list [--owner <id>] [--status open]`: tasks, with ids, titles and status. Check it before you create a task.
- `hub task show <task id>`: one task, with its history.
- `hub task create --owner <person id> --title "<title>" --body "<text>"`: one task for a person, when a playbook says so.
- `hub task update <task id> --note "<text>"`: adds a note to a task.
- `hub task update <task id> --status done --note "<result>"`: finishes a task you own. The requester closes it.
- `hub message send <bot or person> "<text>"`: a message. Only where a playbook names the recipient.
