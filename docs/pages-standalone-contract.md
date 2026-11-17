# Pages stand alone: build contract

Working contract for the parallel build of "Pages stand alone" (Chris, Nov 17 2026). Every worker builds against this
file. Do not change it in a worker branch; if it is wrong or missing something, say so in your report and the
coordinator changes it. It is folded into docs/pages.md and deleted before merge.

## The model

- **A page is its own thing.** Its id is today's page id (the `bot_files.id` / `pages.file_id`, `file-<hex>`). Ids do not
  change, so `requirement_pages`, `page_verdicts`, `page_rounds`, pins and links in messages keep working.
- **Versions** stay in `bot_file_versions` for now. `bot_file_versions.actor` is the writer of that version (a person
  `human:x` or a bot `bot:x`). Wave 2 moves storage out of `bot_files`; nothing in wave 1 may depend on
  `bot_files.bot_id` meaning "owner".
- **Links.** A page links to any number of targets, each link with a role. Everything is many-to-many: one page can be
  the Spec of several tasks, a task can have several Specs, a page has many writers, a page links to other pages.
- **Task documents become pages.** A task document (Spec, QA report, Release report, Invoice, or any other name a bot
  gave, e.g. Email) is a page linked to the task with the document's name as the role. `task_documents` is migrated into
  links and then no longer read (drop the table in wave 2).

## Schema (one new migration, made with `python3 scripts/new_migration.py`)

```sql
CREATE TABLE IF NOT EXISTS page_links (
  id TEXT PRIMARY KEY,
  page_id TEXT NOT NULL REFERENCES bot_files(id) ON DELETE CASCADE,
  target TEXT NOT NULL,         -- 'task:<task id>' | 'bot:<bot id>' | 'human:<slug>' | 'goal:<goal id>' | 'page:<page id>'
  role TEXT NOT NULL,           -- 'writer' | 'related' | a document name: 'Spec', 'QA report', 'Release report', 'Invoice', or any task_documents.canonical() name
  created_by TEXT NOT NULL,     -- actor
  created_at TEXT NOT NULL,
  UNIQUE(page_id, target, role)
);
CREATE INDEX IF NOT EXISTS page_links_target ON page_links(target, role);
CREATE INDEX IF NOT EXISTS page_links_page ON page_links(page_id);
```

Roles: `writer` and `related` are lowercase and reserved; every other role is a document name spelled by
`task_documents.canonical()` (`qa-report` -> `QA report`). A document role is only valid on a `task:` target.

Backfill, in the same migration (idempotent; `INSERT OR IGNORE`):

1. Every `pages` row: `bot:<bot_files.bot_id>` role `writer`.
2. Every page whose `bot_files.task_id` is set: `task:<task_id>` role `related`, or role `Spec` when `pages.spec` is set.
3. Every pin `place='task:<t>'`, `item='page:<p>'`: `task:<t>` role `related`.
4. Every `task_documents` row: make sure the file has a `pages` row (insert with cadence `none`), then link
   `task:<task_id>` with role `task_documents.name`. Also link `bot:<bot_files.bot_id>` role `writer` for it.
5. Every distinct `bot_file_versions.actor` of a page that starts with `human:`: link that person role `writer`.

## Rules

**Who may write a new version of a page** (`page_links.may_write(c, auth, who, page_row) -> bool`):
- the owner role; or
- an actor linked to the page with role `writer` (bot or person), unless the bot is archived; or
- anyone who may comment on a task the page is linked to in any non-`writer` role (the same check task documents use
  today: `task_reads.comment_rights(c, auth, who, task)`, used by `media.py` task_attach); or
- anyone `pages.may_manage` allows today (the page's storage bot, bot admins, people who manage that bot).

Writing a version links the writer as `writer` if not linked yet.

**Who may read** is unchanged: today's file visibility (`files.file_visible`) plus the page audience. A link never
widens who can read (as pins today). A page made on a private task stays private when linked elsewhere.

**Who may link or unlink**: anyone who may write the page. To add a `task:` link they must also be able to read that
task; for a document role they must be able to comment on that task. Unlinking the last `writer` is refused.

**Approvals** (stage page requirements naming a document, `settings.document`): per task, per page, per version. A
task's approval for role R covers every page linked to that task with role R, each at the version current when it was
submitted. `requirement_pages` keeps one row per (requirement, page, version). One task's OK never approves another
task. When a page gets a new version after a task approved an older one, that task's read shows the document as
`changed_since_ok: true` with the approved version.

## API

All under the existing auth. JSON field names are final.

### Page read (`GET /api/v2/pages/{id}`, every list item, every embedded page)

Existing fields stay. Added:

```json
{
  "links": [ {"target": "task:abc", "role": "Spec", "name": "D-18463 Edit Property ...", "by": "human:chris", "at": "..."} ],
  "writers": [ {"actor": "human:steven", "name": "Steven", "versions": 2, "last_at": "..."} ],
  "may_write": true
}
```

`links` lists only targets the caller may read (a task they can't read is left out). `name` is the target's display
name (task: `D-<number> <title>` or title; bot/person name; goal title; page title).

### Publish (`POST /api/v2/pages`, raw or JSON, as today)

New fields (query or JSON):
- `page` (page id): write a new version of that page. Requires `may_write`. Any bot or person may use it.
- `as` (role) with `task`: the page is linked to that task with that role. If `page` is not given and the task has
  exactly one page with that role, that page gets the new version; none -> a new page is made and linked; more than one
  -> 409 `{"code":"ambiguous","pages":[ids]}` (the caller passes `page`).
- `written_by`, `written_at`: only the owner role may send them (an import, e.g. moving tickets from an old Tico). The
  version's `actor` and `created_at` are set to them. Every use is written to the audit log (`audit_log.py`).
- People (role `human`, and `owner`) may now publish. Until wave 2, a person's page is stored under their Assistant bot
  (the bot `chat_bot_for` picks in `assistant.py`), exactly as Make page stores it today; nothing shows that bot as the owner.

The response is today's `PagePublished` plus `links`.

### Links

- `POST /api/v2/pages/{id}/links` body `{"target": "task:<id>|bot:<id>|human:<slug>|goal:<id>|page:<id>", "role": "related"}`
  -> the page read. Task targets accept a task id, short id or `D-18463`-style number the way task routes resolve them.
- `DELETE /api/v2/pages/{id}/links?target=...&role=...` -> the page read.

### Listing

`GET /api/v2/pages` adds filters, all optional and combinable: `linked=<target>` (any role), `role=<role>`,
`writer=<actor>`. Existing `bot=` now means "linked to the bot in any role, or stored under it"; `task=` means linked to
the task in any role (or filed there, as today).

`GET /api/v2/files` (Library › Files, `file_library.py`) adds the same `linked`, `role` and `writer` filters, and each
page row in it carries `links` (as above, max 5, plus `links_more: n`).

### Task read

`task.documents` (`app.py` task read, from `task_documents.listing`) keeps its item shape and gains `page_id` (same as
`file_id`), `links` and `changed_since_ok`. It now lists every page linked to the task with a document role, newest
change first, and can hold several items with the same `name`.

`GET /api/v2/tasks/{id}/context` lists pages by links (any role) plus today's filed/pinned rule.

New: `GET /api/v2/tasks/{id}/files?all=1` keeps its shape and also includes every page linked to the task in any role
(each with `links`), so the task's Files tab can show everything related.

### CLI (`clients/commands/content.py`, `task_work.py`) and MCP

- `tico page publish FILE [--page ID] [--task ID --as ROLE] [...existing]`
- `tico page link ID --task ID|--bot ID|--person SLUG|--goal ID|--page ID [--as ROLE]` (default role `related`)
- `tico page unlink ID --task ...|... [--as ROLE]`
- `tico page list [--linked TARGET] [--role ROLE] [--writer ACTOR]`
- `tico task attach ID FILE --doc NAME` keeps working and becomes `tico page publish FILE --task ID --as NAME`.
- MCP tools mirror these: `tico_page_link`, `tico_page_unlink`, new args on `tico_page_publish` and `tico_page_list`.
- The runner's allowed hub paths (`runner-rs/src/hosts/hub_paths.rs`) allow `POST/DELETE /api/v2/pages/{id}/links`.

## UI

- **Task window:** under the title, a Spec card for each page linked with role `Spec` (title, version, writers,
  "also the Spec of D-…" when shared, Open). Tabs: Chat, then **Files N** listing everything linked to the task
  (pages with their role chip, plus the task's files), with "Link a page or file" and "New page". The per-document tabs
  from `task-docs.ts` stay for non-Spec documents.
- **Library › Files:** filter chips Kind (role), Linked to, Written by; each page row's meta shows its links
  ("Spec of D-18463, D-19385") and writers instead of only the storage bot.
- **Page view:** link chips under the title (role + target, "OK'd vN" when a task approved a version) with "+ Link"
  and remove on hover for those who may write; History lists each version's writer; an **Edit** action for people who
  may write: edit the page text in place and save as a new version (POST /api/v2/pages with `page`).
- Minimal copy, marks not words, as the style guide says.

## Moving TIDY's carried tickets (after deploy, outside this repo)

A script in the TIDY ops repo, run as the owner: for each task whose first message has `tico-carry: old=` or
`Old task: https://tidy.rocks/`, publish a page (title `Spec: <task title>`, HTML from the carried text) with
`task=<id>`, `as=Spec`, `written_by=<old author>`, `written_at=<old created>`, then edit the first message down to
`Moved from the old Tico: <link>`.

## Build rules for every worker

- Repo ticoteam/tico. Work only in the worktree you are given, on its branch. Commit as the existing git identity; no
  Co-Authored-By lines. Push your branch with `git push origin HEAD:refs/heads/<your branch>`; do not open a PR and do
  not merge.
- Never run `npm ci` / `npm install`. Never edit a shipped migration; add a new timestamped one.
- This computer is shared and busy. Run only the test files you touched, one at a time, `nice -n 15`, `-n 1`. Anything
  longer (a whole module's tests, browser tests, the selected suite, cargo builds) runs on RunsOn:
  `python3 scripts/remote.py --setup python -- <cmd>` (`--setup browser` for UI tests, `--setup rust` for the runner).
  It runs your pushed commit, so push first.
- Any SQL you add or change: EXPLAIN QUERY PLAN (indexes used, no new full scans) and old-vs-new timing on a seeded DB;
  put the numbers in your report.
- `ruff check` and pyright strict on the backend files you touch; `npx tsc --noEmit -p ui` is fine if it is quick, else
  RunsOn.
- Report: what you changed (files), tests added and their results (local or RunsOn run link), anything in this contract
  you had to assume or that looks wrong.
