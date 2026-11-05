# Pages

A page is an HTML file a bot publishes to show people what it did or found: a weekly engineering review, a market
scan, a launch plan. It changes only when its bot publishes a new version. Tico's built-in cards hold live numbers;
a page holds what the bot wrote.

A page is a kind of [file](files.md): it has the file's title, versions, maintainer bot and visibility, plus a
cadence, the date its data is from, and the page that replaced it, if any. **Library › Files** lists pages with every
other file.

## Publishing

    hub page publish reports/weekly-review.html --title "Engineering weekly" --cadence weekly [--as-of 2026-10-27]
        [--task ID] [--replaces PAGE] [--page PAGE --base-version N]

- **The same path again** adds a version of the same page.
- **`--cadence`** says how often the bot means to update it: `none`, `daily`, `weekly` or `monthly`.
- **`--as-of`** is the date the data is from, when that differs from the publish date.
- **`--task`** files the page on a task and pins it to the top of that task, for whoever can see the task.
  Without it, the page belongs to the turn's task or conversation, as a file would; `--scope bot` makes it
  bot-wide.
- **`--replaces`** names an older page of the same bot that this one takes over. The older page then says
  **Replaced by** and links here.

A bot may add a version to another bot's page with `--page ID --base-version N` when the page is filed on its turn's
only task. The maintainer and scope stay the same, and the version names the bot that published it. A stale base
version returns 409; pinning a page to the task alone grants no version rights.

Over MCP the same tools are `hub_page_publish`, `hub_page_list`, `hub_page_show` and
`hub_page_archive`.

Task turns receive up to ten pages and ten linked Team meetings as titles, dates and ids, never content.
`hub task context ID` (`hub_task_context`, `GET /api/v2/tasks/{id}/context`) reads that list. Bots read the content
with `hub file read ID` and `hub meeting read ID`; `--notes` reads only the saved meeting notes.
Each read checks current access and whether every reader of every carried task may read the source. A bot-wide or
team-pinned page does not qualify merely because the bot can open it; a person's Dashboard or Growth pin can make
a page shared context. A page filed on a task with a bot party qualifies for that task, without making it context for
other tasks. Several carried tasks get their intersection. Access changes apply to the next read, including direct
file and blob addresses and retried runner claims. Outside task turns, a bot attached by a private meeting's owner may
read that saved meeting.

Scoped task turns and Make-page attempts use the reviewed method/path allowlist in
`backend/scoped_routes.py`. Other routes return `403`, including raw SQL, CSV/exports, saved run
history, admin APIs and event streams. File downloads must name an individually in-scope source.
Ordinary task turns retain checked messaging, status and credential-card writes. Scoped BotOps turns
cannot use requester delegation to reach administration APIs.
Make page can read task context, pages, files and meeting transcripts, and publish its page; it has no
conversation or task-history read. The runner follows work with its separate runner identity.

Allowed JSON responses, claims, steering inputs and saved retries project structured source references.
A known out-of-scope page, file, blob or meeting disappears with its metadata and copied review labels.
Generated answer prose keeps source provenance too. Legacy choices without provenance lose labels that
exactly match an out-of-scope source title attached to the task. Free text, unknown references and
references to tasks or conversations stay unchanged. Only the named runner `refs_json` copy follows its
structured `refs`; JSON written inside ordinary text is never parsed.

**What a page may hold.** One `.html` file, up to 10 MB. It runs no script and loads nothing from elsewhere. Tico
removes scripts and outside addresses when the page is published, and the answer lists what it removed
(`unembedded`). Charts are inline SVG and pictures are `data:` addresses, as in
[Visuals in replies](visuals.md#what-gets-embedded-in-a-page). Put the headline and the key numbers first: a card
shows the top of the page.

## Who can see it

A page starts with **Everyone who can see where it's filed**. Its maintainer bot and anyone who may manage it can
choose **Who can see it** in the full view: humans, groups (including their nested groups and bots) and bots.
This only narrows the file's inherited visibility. A pin, task link, live block or embed never widens past it.
The maintainer and page managers always pass the audience check. Only managers see the chosen names and lock icon.

`hub page publish report.html --audience ben,group:legal,bot:analyst` sets it when publishing.
`hub page access ID` shows it to managers; `hub page access ID --audience everyone` clears it.
MCP uses `hub_page_publish`'s `audience` and `hub_page_access`. A new version keeps the setting unless publishing sets
one. `PATCH /api/v2/pages/{id}` accepts the access audience object, or `null` to clear it. Everyone still needs access
to where the page is filed. Restricted pages use a per-reader visual address, never the shared hosted copy.

Narrowing access removes revoked page text from Counsel's read task, its edit history, comments, notes and saved run
output, including queued work. Counsel rechecks the asker and its run on later reads. Signed visuals also recheck
the person and any bot acting for them; old five-field addresses expire immediately and reloading gets a new one.
Archived bots match no audience. Messages and runner context keep only “A page you can't open” for a page outside
the reader's audience, or “A deleted page” after deletion. Deletion purges the content and keeps its audience and
identity provenance; saved citations also show only the deleted-page placeholder. Copied review labels, answers and
file history follow the same restriction. Recreating identical content creates a live identity whose review can be
answered; the old page stays deleted. Group membership changes refresh open pages;
deleting a group or person, or archiving a bot, removes their explicit page grants. An empty audience leaves access
to the page's managers.

## The status line

Tico draws one line above every page, as a card and at full size. The page cannot change it.

| The page | The line |
|---|---|
| Current | `Updated 3h ago by Head of Engineering · v4 · weekly` |
| With `--as-of` | `… · data from Oct 27` |
| Past 1.5× its cadence | `Not updated in 11 days · v4 · weekly` |
| Replaced | `Replaced by <title>`, linked to the newer page |

## Live blocks

A page can carry three live tags. The server replaces each one, when the page is shown, with a short list made for
the one person or bot viewing it. The page itself stays static: no script runs and nothing is fetched.

- `<tico-tasks waiting-on="viewer" [bot="SLUG"] [parent="TASK"] [limit="N"]></tico-tasks>`: the tasks waiting on the
  viewer, as links. Empty, it says "Nothing waiting on you".
- `<tico-task id="TASK"></tico-task>`: one task's title, status and a link. Nothing when the viewer may not see it.
- `<tico-files task="TASK" [kind="page|document|image|data|media|video|audio"] [limit="N"]></tico-files>`: the files on a task
  the viewer may see. A page links to its page view, any other file to the task. `video` and `audio` mean `media`.

`limit` defaults to 10 and is at most 50. Links open the task in the app in a new tab. A list uses the same rules as
Needs you, the task page and the Library, so a viewer never sees a title, a count or a name they could not open.
A page with live blocks is shown only through its visual address, never the shared hosted copy, since each viewer
gets a different page.
Visual addresses issued in a task turn keep that turn's scope. Live task blocks list only carried tasks, and live
file blocks apply the same source checks as direct reads. Make-page turns record these file reads in their source log.

## Where pages show

- **A card** shows the top of the page at the card's width. Tap it to open the page full size at `#/page/<id>`, on a
  computer or a phone.
- **The full view** has the page's versions (an older one opens in place) and, for whoever may manage it,
  **Archive**. The owner also has **Delete for good** ([Files](files.md#deleting-a-file-for-good)).
- **A task** shows the pages pinned to it as cards at the top, a replaced page left out. **Link page** searches
  existing pages and links the chosen one. Any person, or bot acting as itself, who can read the task may link a page
  they can read; the link keeps the page's own access rules. Other task readers see only pages they can open, with no
  count of hidden pages.
  The task place's `can_link` controls linking; it grants no card editing or pin arranging rights (`can_edit`).
  **Unlink** is available to whoever linked it, the task's owner or requester, and the org owner.
- **Pinned** to the Dashboard, a team page or a bot's sidebar ([Overview](overview.md)), a page shows as a card there.
  The full view lists where it is pinned, among the places the reader may open.

Pages show inline when the install has a visual address (`TICO_VISUAL_ORIGIN`) or Tico file hosting
([Visuals in replies](visuals.md#self-hosting)). Without either, a page is an **Open** card with its title and status
line, and opens in the file viewer's sandboxed preview. Pages have no scripts, so they look the same there.

## Archiving

The maintainer bot, people who manage it, admins and the owner archive a page (`hub page archive ID`, or **Archive**
on the full view). An archived page leaves lists and search and still opens from a link. **Restore**
(`hub page archive ID --restore`) or a new version brings it back. Versions are kept.

## API

Stable v2 (`docs/openapi/v2.json`, tag **Pages**):

| Call | What it does |
|---|---|
| `POST /api/v2/pages` | Publish: raw HTML with the fields in the query, or JSON with `name` and `text` or `content_base64` |
| `GET /api/v2/pages/{file_id}` | `page`: `file_id, title, bot, bot_name, version, updated_at, updated_by, cadence, as_of, stale_days, replaced_by, archived, task_id, src`; `pinned`: where it is pinned |
| `GET /api/v2/pages?ids=a,b` | Up to 50 pages in that order, leaving out any the caller may not read |
| `GET /api/v2/pages?bot=&task=` | Pages newest first, paged with `limit` and `cursor`; `task` includes pages filed on or pinned to that task |
| `PATCH /api/v2/pages/{file_id}` | `cadence`, `as_of`, `replaced_by`, `archived`, `audience` (null clears it) |

`src` is a fresh address to frame the current version, or `null` when this install cannot show pages inline. A page
the caller may not read is a 404. `replaced_by` is `null` when the caller may not read the newer page. Lists carry no
totals, so a count never says what a reader cannot see.

Managers get `audience` on single-page reads and writes, and a small `restricted` flag on summaries and cards.
Other readers get neither field.

`hub page list --task ID` and MCP's `hub_page_list` include both filed and linked pages with the same paging.

**Who acts.** A bot publishes during its turn. People archive and restore. BotOps archives for a person with that
person's rights, and asks before a hard delete, which only the owner may do.

## Make page

On a task, the Tico dot box takes a description and **Make page**. Your Assistant reads only the task's shared
context list, writes one HTML page, and files and pins it on that task. You appear as the author; the Assistant
keeps the page and its version retains the attempt that published it. No scripts run.
Each request starts a fresh session in a scratch directory deleted when the turn ends, with its initiating message
and shared task context. Personal learnings, the private company layer, earlier Assistant history, unrelated queued
tasks and later chat messages stay out of that turn. Conversation, thread and memory tools are not offered; direct
private context reads return 404. Files leave the scratch directory only through the page publish API and never
through a later turn's upload scan.

The card says **Making page…** until the page is pinned. A turn that ends without a pin says **Not made · Open chat**.
The card survives reopening the task. Publishing identical HTML again still completes that request, retaining the
existing version. A task with 60 pins returns **This task has 60 pins**; unlink a page to make room.
Publication checks the initiating request and every logged file or meeting read again. Removing a source from the
context or making a meeting Participants only refuses publication with 403. A link never widens a source's access.

`POST /api/v2/tasks/{id}/make-page {text}` sends your own Assistant message with the server's `refs.make_page` mark;
other message routes refuse that mark with 403. `GET` on the same route returns your latest request's status
(`making`, `made`, or `failed`), or null. These controls are for the person acting as themselves. The Assistant uses
the existing `hub page publish FILE --task ID` / `hub_page_publish` tool, without `--source`.
