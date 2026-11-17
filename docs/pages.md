# Pages

A page is a versioned HTML file people and bots can publish. It can hold a weekly engineering review, a market scan
or a launch plan. Tico's built-in cards hold live numbers. A page changes when a writer publishes a new version.

A page is a kind of [file](files.md): it has the file's title, versions and visibility, plus a cadence, the date its
data is from, and the page that replaced it, if any. **Library › Files** lists pages with every other file.

## Pages stand alone

A page has its own id (`file-<hex>`), versions and audience. It is linked to what it is about, and each link has a
role. A page can have many links, and a task can have many pages: one page can be the Spec of two tasks, and a task can
have a Spec, a QA report and any number of other pages.

- **Targets:** `task:<id>`, `bot:<id>`, `human:<slug>`, `goal:<id>` or `page:<id>`.
- **Roles:** `writer` (a page's writers), `related` (the default), or a document name on a task: `Spec`, `QA report`,
  `Release report`, `Invoice`, or any name a bot gives. A task's document is a page linked to that task with the
  document's name as its role.
- **Who may write a version.** The team owner, a linked writer (a person or an active bot), someone who may comment
  on a linked task, or someone who may manage the page. Publishing adds the version's author as a linked writer.
- **Who may read:** unchanged. A link never widens who can see a page. A page made on a private task stays private when
  it is linked elsewhere.
- **Who may link or unlink:** anyone who may write the page. A task link also needs read access to that task, and a
  document role needs comment access to it. The last writer cannot be unlinked.
- **Approvals.** A stage's OK covers all pages linked with the required document role at their submitted versions,
  for that task only. A later version shows as changed since OK. Approval requests covering multiple pages include
  `approval.pages` (each with `file_id` and `version`) and `approval.page_version` (sorted `file@version` entries
  separated by commas). Send that key as `page_version` when answering. Single-page requests keep their existing shape.

## Publishing

    tico page publish reports/weekly-review.html --title "Engineering weekly" --cadence weekly [--as-of 2026-10-27]
        [--task ID [--as ROLE]] [--page ID] [--replaces PAGE]

- **The same path again** adds a version of the same page. **`--page ID`** adds a version of that page, whatever its
  file name, if you may write it.
- **`--cadence`** says how often the bot means to update it: `none`, `daily`, `weekly` or `monthly`.
- **`--as-of`** is the date the data is from, when that differs from the publish date.
- **`--task`** files and links a page to the task. The page shows in the task's Files tab for readers who can open it.
  Without it, the page belongs to the turn's task or conversation, as a file would; `--scope bot` makes it
  bot-wide.
- **`--as ROLE`** with `--task` links the page to that task with the role instead: `Spec`, `QA report`,
  `Release report`, `Invoice` or any name. Without `--page`, the task's one page with that role gets the version; if
  it has none, a page is made; if it has more than one, the publish is refused and names their ids, and you pass one
  of them with `--page`.
- **`--replaces`** names an older page of the same bot that this one takes over. The older page then says
  **Replaced by** and links here. An Assistant turn must be able to read and manage the older page as its person,
  and both pages must be filed on the request's task.

Anyone who may write a page publishes its versions: a person, or a bot as itself. Over MCP the same tools are
`tico_page_publish`, `tico_page_link`, `tico_page_unlink`, `tico_page_list`, `tico_page_show` and `tico_page_archive`.

## Links and task documents

    tico page link file-1 --task T1 --as Spec
    tico page unlink file-1 --task T1 --as Spec

The target is one of `--task`, `--bot`, `--person SLUG`, `--goal` or `--page`. `--as` is the role, `related` when left
out. Unlink takes the same target and role that linked the page. `tico page show ID` includes links and writers in its JSON
read. `tico page list` narrows by `--linked TARGET` (any role), `--role ROLE` and
`--writer ACTOR`; the filters combine.

A task's document is a page. `tico page publish spec.html --task ID --as Spec` writes the task's Spec: a new version of
the page it has with that role, or a new page. Publishing under a document role also submits the version for the task's
approval. `tico task attach ID spec.html --doc Spec` does the same. Put a change note in a task comment. Ordinary uploads
continue the ordinary file of that name and leave document pages intact.

Task turns receive up to ten pages linked to, filed on or pinned to the task and ten linked meetings, newest first, as ids,
titles, versions and dates. `tico task context ID` (`tico_task_context`, `GET /api/v2/tasks/{id}/context`) reads that
metadata list. It includes only sources the caller may read. A bot uses its own normal read rights in every turn,
whether or not it is working on a task. The task supplies references; the bot pulls content with `tico file read ID`
and `tico meeting read ID` (`--notes` for saved meeting notes).

Each read checks current access; a task link or pin grants no additional access.

**What a page may hold.** One `.html` file, up to 10 MB. It runs no script and loads nothing from elsewhere. Tico
removes scripts and outside addresses when the page is published, and the answer lists what it removed
(`unembedded`). Charts are inline SVG and pictures are `data:` addresses, as in
[Visuals in replies](visuals.md#what-gets-embedded-in-a-page). Put the headline and the key numbers first: a card
shows the top of the page.

## Who can see it

A page starts with **Everyone who can see where it's filed**. Its maintainer bot and anyone who may manage it can
choose **Who can see it** in the full view's **⋯** menu: humans, groups (including their nested groups and bots) and bots.
This only narrows the file's inherited visibility. A pin, task link, live block or embed never widens past it.
The maintainer and page managers always pass the audience check. Only managers see the chosen names and lock icon.

`tico page publish report.html --audience ben,group:legal,bot:analyst` sets it when publishing.
`tico page access ID` shows it to managers; `tico page access ID --audience everyone` clears it.
MCP uses `tico_page_publish`'s `audience` and `tico_page_access`. A new version keeps the setting unless publishing sets
one. `PATCH /api/v2/pages/{id}` accepts the access audience object, or `null` to clear it. Everyone still needs access
to where the page is filed. Restricted pages use a per-reader visual address, never the shared hosted copy.

Narrowing access removes revoked page text from the Rules Owner's read task, its edit history, comments, notes and saved run
output, including queued work. The Rules Owner rechecks the asker and its run on later reads. Signed visuals also recheck
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

A page can carry four live tags. The server replaces each one, when the page is shown, with a short list made for
the one person or bot viewing it. The page itself stays static: no script runs and nothing is fetched.

- `<tico-tasks waiting-on="viewer" [bot="SLUG"] [parent="TASK"] [limit="N"]></tico-tasks>`: the tasks waiting on the
  viewer, as links. Empty, it says "Nothing waiting on you".
- `<tico-task id="TASK"></tico-task>`: one task's title, status and a link. Nothing when the viewer may not see it.
- `<tico-files task="TASK" [kind="page|document|image|data|media|video|audio"] [limit="N"]></tico-files>`: the files on a task
  the viewer may see. A page links to its page view, any other file to the task. `video` and `audio` mean `media`.
- `<tico-kpi bot="SLUG" name="KPI" [period="7d|30d"]></tico-kpi>`: one of the bot's automatic KPIs
  ([KPIs](goals-and-kpis.md)), its current value and its change on the previous period. `name` is the KPI's key:
  `tasks_done_7d`, `first_response_min`, `approval_rate_30d`, `failed_runs_7d` or `cost_7d`, or its name. `period` is
  optional. Set it only to the KPI's own window; any other value makes the tag show nothing.
- `<tico-kpi id="KPI_ID"></tico-kpi>`: a stored KPI, such as "Booked demos" ([KPIs](goals-and-kpis.md)). The id is in
  `tico kpi list`. It shows the latest reading's value and unit, and its change on the reading closest to one cadence
  period before (the previous reading when the KPI has no cadence), such as `▲ 3 demos vs previous week`. `period`
  does not apply to `id`. Readings marked partial, and readings a correction replaced, are not used. For a KPI with a
  cadence, the change is left out when that reading is more than two periods older than the latest. The value still
  shows.

A KPI tag shows nothing when the viewer may not read its owner, when the name or id is not a KPI, when a stored KPI is
archived, or when there is no reading yet. An automatic KPI tag also shows nothing when the bot's history includes
private work, even for its manager. Private work is a private task the bot owned, requested or carried into its next
run. It also includes a private task that a chat message to the bot was about. This rule does not apply to stored KPIs.
A stored KPI owned by a bot is hidden from a viewer who may not read that bot, as its API route hides it. A KPI never
shows a zero for a missing value.

`limit` defaults to 10 and is at most 50. Links open the task in the app in a new tab. A list uses the same rules as
Needs you, the task page and the Library, so a viewer never sees a title, a count or a name they could not open.
A page with live blocks is shown only through its visual address, never the shared hosted copy, since each viewer
gets a different page.

## Where pages show

- **A card** shows the page at the card's width, at a small, medium or large size. Tap it to open the page full size at
  `#/page/<id>`, on a computer or a phone. Without a widget view (below) a card shows the top of the page.
- **Widget view.** In a card, Tico's height script sets `data-tico-view="card"` on the page's `<html>` and
  `data-tico-size` to `small`, `medium` or `large`. A card is as tall as the page, up to 180 px for `small` (what a
  sidebar pin draws), 320 px for `medium` and 520 px for `large`; a taller page fades at the bottom. At full size the script
  sets `data-tico-view="full"` and `data-tico-size="full"`. Design for the card's width and the small height first: a
  headline and one or two numbers. Use `[data-tico-size=medium]` or `[data-tico-size=large]` to show more, such as a
  chart or a list. A page's own CSS can give a card its own layout and keep the full report for `full`:

      .card-only { display: none; }
      [data-tico-view=card] .full-only { display: none; }
      [data-tico-view=card] .card-only { display: block; }
      [data-tico-view=card] body { height: 100vh; overflow: hidden; }

  The `100vh` rule applies only in the card view: the page fills the card at its full size and does not scroll. A page with no
  such CSS looks as it always did. The bot's guide (`AGENTS.md`, "Widget view") has the short form.
- **Dark by default.** Tico shows pages dark, like the app. A page that colours neither `<html>` nor `<body>` gets a
  dark background (`#191b1a`) and light text (`#e8eae9`) from Tico's height script; a page that sets its own dark
  text and no background stays light. A page with its own background keeps its look. Design pages dark: a dark
  background, light text, and chart colours that read on dark.
- **The full view** has one header line: back, the title, the status line and **⋯**. The page sits right below it
  on the app's own surface, with no border or box around it. **⋯** holds **Who can see it** (for whoever may manage
  the page, with the audience under it), **What's changed?** and **Version history** (when the page has more than one
  version), **Archive** (for whoever may manage it), **Download** and, for the owner, **Delete for good**
  ([Files](files.md#deleting-a-file-for-good)). **Version history** lists every version, newest first, with its date
  and writer. An older one opens in place, and **Latest** on the status line goes back. Under the title, link chips
  show each role and target and any approved version. Writers can add or remove links and use **Edit** to save a new
  version. **What's changed?** shows
  the page's visible text, one line per heading, paragraph, list item or table row, as a line diff against the
  version before; an earlier version can be picked, and **Close** or Esc returns to the page. A version that changed
  only styles or images shows "No text changes".
- **Leave out the title, version and date.** The viewer shows them above every page, so a page starts with its
  content, not its own title heading or a "v3, updated Nov 12" line.
- **A task** has a card under its title for each Spec, with its version, writers and other tasks that share it.
  **Chat** opens first. **Files** lists linked pages and task files and offers **Link a page or file** and **New page**.
  Other page documents and Markdown files have their own tabs.
  **Make page** sends a description to your Assistant in a fresh session and temporary workspace. It acts with
  your normal rights, reads task references through the normal tools, and publishes and pins the page with you as
  author. The page gets the normal task audience; copying source content is accepted and does not narrow it.
  The request shows progress or **Not made · Open chat**. An older runner leaves it queued with
  **Update this computer to make pages · Retry** until updated. A full task (60 pins) refuses the request or
  publication without storing a page. Publishing unchanged HTML adds no version.
- **Library › Files** shows page links and writers. Filter by **Kind**, **Linked to** or **Written by**.
- **Pinned** to the Dashboard, a team page or a bot's sidebar ([Company](overview.md)), a page shows as a card there.
  The full view lists where it is pinned, among the places the reader may open.

Pages show inline when the install has a visual address (`TICO_VISUAL_ORIGIN`) or Tico file hosting
([Visuals in replies](visuals.md#self-hosting)). Without either, a page is an **Open** card with its title and status
line, and opens in the file viewer's sandboxed preview. Pages have no scripts, so they look the same there.

## Archiving

The maintainer bot, people who manage it, admins and the owner archive a page (`tico page archive ID`, or **Archive**
on the full view). An archived page leaves lists and search and still opens from a link. **Restore**
(`tico page archive ID --restore`) or a new version brings it back. Versions are kept.

## API

Stable v2 (`GET /api/v2/openapi.json`, tag **Pages**):

| Call | What it does |
|---|---|
| `POST /api/v2/tasks/{tid}/make-page` | Ask your Assistant with `text` (up to 8,000 characters) |
| `GET /api/v2/tasks/{tid}/make-page` | Your latest request: `making`, `made`, `failed`, `update_required`, or null |
| `POST /api/v2/pages` | Publish: raw HTML with the fields in the query, or JSON with `name` and `text` or `content_base64`. `page` writes a version of that page; `task` with `as` links the page to the task with that role. Several pages with that role: 409 `ambiguous` with their ids (pass `page`) |
| `GET /api/v2/pages/{file_id}` | The page and its pins. Its `links` list readable targets (`target, role, name, by, at`). A task document link includes `approval: {"version": n}` once that task has approved it. `writers` lists `actor, name, versions, last_at` and `may_write` tells the caller whether they can publish a version |
| `POST /api/v2/pages/{file_id}/links` | Body `{"target": "task:<id>", "role": "Spec"}` (`task:`, `bot:`, `human:`, `goal:` or `page:`; `role` defaults to `related`). Returns the page read |
| `DELETE /api/v2/pages/{file_id}/links?target=...&role=...` | Removes that link. Returns the page read |
| `GET /api/v2/pages?ids=a,b` | Up to 50 pages in that order, leaving out any the caller may not read |
| `GET /api/v2/pages?bot=&task=&linked=&role=&writer=` | Pages newest first, paged with `limit` and `cursor`, all filters optional. `task` and `bot` include pages linked to them in any role, and pages filed or stored there; `linked` is a target in any role; `role` is a link's role; `writer` is an actor who wrote a version |
| `GET /api/v2/files` and `GET /api/v2/files/search` | Both take `linked`, `role` and `writer`. Page rows and hits include writers, up to five links and a count of the remaining visible links |
| `GET /api/v2/tasks/{id}/files?all=1` | Task files and linked pages, with links on page rows |
| `PATCH /api/v2/pages/{file_id}` | `cadence`, `as_of`, `replaced_by`, `archived`, `audience` (null clears it) |

`src` is a fresh address to frame the current version, or `null` when this install cannot show pages inline. A page
the caller may not read is a 404. `replaced_by` is `null` when the caller may not read the newer page. Lists carry no
totals, so a count never says what a reader cannot see. On task reads, page document entries include `page_id`,
`links`, and `changed_since_ok`.

The owner can import a version with `written_by` and `written_at` in the publish request. Both fields are audited.

## Existing pages and approvals

The migration keeps page ids and versions. It adds links from existing page writers, task filings, task pins and
task documents. Document entries gain a page record if needed. Version authors still come from
`bot_file_versions.actor`. The storage bot remains part of the file record.

`requirement_pages_current` allows one current row per `(visit_id, requirement_id, file_id)` where
`replaced_at IS NULL`. `requirement_pages_history(visit_id, requirement_id, created_at)` supports earlier OKs.

## Access fields and clients

Managers get `audience` on single-page reads and writes, and a small `restricted` flag on summaries and cards.
Other readers get neither field.

`tico page list --task ID` and MCP's `tico_page_list` include pages linked to the task in any role, and pages filed there,
with the same paging.

**Who acts.** A bot publishes during its turn. Your Assistant makes task pages as you, recording its authenticated
attempt alongside your authorship. People publish, link and archive pages too; until pages leave their bots, a person's
page is stored under their Assistant, which nothing shows as the owner. BotOps archives for a person with that
person's rights, and asks before a hard delete, which only the owner may do.
