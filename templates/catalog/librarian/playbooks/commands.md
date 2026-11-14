# Commands

Use these, with the arguments shown. `<...>` is a value you fill in; `[...]` is optional. Each line says what the command
does and what it returns. For any other command, run `tico <group> --help` first. Never guess a command or a flag.
`tico` has no `--version` flag; `tico whoami` says who you are.

## Answer a question

- `tico doc search "<words>"`: matching sections with the doc's id, path, title and excerpt. `--team` for team docs only,
  `--manual` for the Tico manual only.
- `tico doc read <id or path>`: one doc in full, with its version, updated date and author. An indexed external doc:
  `tico doc read external:<id> --revision <commit>`.
- `tico doc list [--prefix <folder>/] [--limit 500]`: internal docs by path, with id, version and updated date.
- `tico doc history <id or path>`: the doc's versions.
- `tico doc link-list`: the linked docs, with address and description.
- `tico doc fetch <url>`: a public link as text, and its links. It runs on this computer.
- `tico file search "<words>"`: files you may read, by title and text. `tico file read <id>`: one file as text.
- `tico sql "<one SELECT>"`: read-only SQL, for example
  `SELECT id, kind, about, claim FROM market_insights WHERE status = 'new'`.

## Map and docs

- `tico doc write _librarian/<name>.md --title "<title>" --body-file <file.md> --note "<what changed>"`: creates or replaces
  one of your docs. Write only under `_librarian/` and `FAQ.md`.
  To change a doc someone else saved last, read it first and add `--base-version <the version you read>`;
  a refusal names who saved since: read it again and redo your change on their version.
- `tico api GET "docs?limit=500&cursor=<next_cursor>"`: the next page of the doc list, when `tico doc list` has a cursor.

## Market

- `tico market find "<name>"`: entities by name, alias or domain, with their ids.
- `tico market show <entity id>`: one entity, its edges, its evidence and its last ten events.
- `tico market delta [--since 1d]`: what changed in the graph, grouped by entity.
- `tico market report --kind <new-entity|edge|property-change|correction|question|other> --claim "<one sentence>" [--about "<name>"] [--source <url>] [--quote "<words>"] [--source-ref <ref>]`:
  files an insight. Prose only; the graph does not change. Returns the insight id.
- `tico market apply <insight id> --source-kind <kind> --quote "<words>" --our-read "<one line>"`, with
  `--entity-type --entity-name --summary --tier` for a new entity, or `--edge-src --edge-rel --edge-dst` for an edge:
  writes the evidence, then the entity or edge, and marks the insight applied. It cannot set a property such as a price.
  `--last-verified-at YYYY-MM-DD` sets the date the entity was last checked, on `--entity-id` or on a new entity; that date
  starts the staleness clock (60 days, 30 for a core company). Only a plain YYYY-MM-DD date is sent; a timestamp is refused, not cut.
- `tico market resolve <insight id> --status <merged|rejected|needs-human> --resolution "<one sentence>"`: closes an
  insight without writing to the graph.
- `tico market import <file.jsonl> [--dry-run] --note "<where it came from>"`: a verified batch, one JSON line per entity
  or edge with its evidence. It is applied as one insight; `--dry-run` shows the result without keeping it.
- `tico market sweep [--today YYYY-MM-DD]`: files the run. Each needs-human insight becomes one task for the owner.
- `tico market page <name>`: one market page, for example `customer-themes` or `coverage-universe`.
- `tico market page <name> --body-file <file.md>`: rewrites the whole page from Markdown.
- `tico market refresh [--today YYYY-MM-DD]`: rewrites the weekly delta. It changes only on a Monday.
- `tico market watch add <entity id> <url> --kind <pricing|careers|changelog|docs|home|other>`: watches one page.
- `tico market watch list [--entity <entity id>]`: the watched pages and when each was last read.
- `tico market watch check`: reads the watched pages now.
- `tico market watch stop <watch id>`: stops one watch. The row is kept.
- `tico market briefing post --week <YYYY-Www> --title "<title>" --body-file <file.md> [--items-file <items.json>] [--answers-file <answers.json>]`:
  posts the week's briefing with its tagged items and the Market page's answer lines (`playbooks/write-the-briefing.md`,
  step 3). The same week again replaces it; without `--answers-file` the week's answers stay.
- `tico market briefing latest`: the newest briefing, with its items and the bets it touches.
- `tico strategy show`: the published strategy: its bets and what each one cites.
- `tico lens list --market` and `tico lens run where-to-play --answers-file <file.json>`: the market lenses, and a lens run.

Market sizing (`tico market define|segment|size|figure|sources|spend`): the commands are in `playbooks/size-the-market.md`.

## Listening: items sent to you

There is no command and nothing to fetch or close. The "Market listening" automation gives you its items as text in a
turn: each with its tag, title, site and link. File what is worth keeping with `tico market report` (above), with the
item's link as `--source` and the post's words as `--quote`.

## Meetings

- `tico meeting read <meeting id>`: a transcript. Only when the task itself says its text was cut short.

## Tasks and messages

- `tico task list [--owner <id>]`: open tasks, with ids, titles and versions. Check it before you create a task.
- `tico task show <task id>`: one task: its version, what it waits on, what is missing and what you may do next, with its history.
- `tico task create --owner <person id> --title "<title>" --body "<text>"`: one task for a person, when a playbook says so.
- `tico task comment <task id> "<text>"`: adds a note to a task.
- `tico task done <task id> -m "<result>" --version N`: finishes a task you own.
- `tico task ask <task id> --to <person> "<one sentence>" --version N`: asks a person on the task. One open ask per task.
- `tico message send <bot or person> "<text>"`: a message. Only where a playbook names the recipient.
