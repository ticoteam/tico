# Pages

A page is an HTML file a bot publishes to show people what it did or found: a weekly engineering review, a market
scan, a launch plan. It changes only when its bot publishes a new version. Tico's built-in cards hold live numbers;
a page holds what the bot wrote.

A page is a kind of [file](files.md): it has the file's title, versions, maintainer bot and visibility, plus a
cadence, the date its data is from, and the page that replaced it, if any. **Library › Files** lists pages with every
other file.

## Publishing

    tico page publish reports/weekly-review.html --title "Engineering weekly" --cadence weekly [--as-of 2026-10-27]
        [--task ID] [--replaces PAGE]

- **The same path again** adds a version of the same page.
- **`--cadence`** says how often the bot means to update it: `none`, `daily`, `weekly` or `monthly`.
- **`--as-of`** is the date the data is from, when that differs from the publish date.
- **`--task`** files the page on a task and pins it to the top of that task, for whoever can see the task.
  Without it, the page belongs to the turn's task or conversation, as a file would; `--scope bot` makes it
  bot-wide.
- **`--replaces`** names an older page of the same bot that this one takes over. The older page then says
  **Replaced by** and links here. An Assistant turn must be able to read and manage the older page as its person,
  and both pages must be filed on the request's task.

A bot writes only its own pages. Over MCP the same tools are `tico_page_publish`, `tico_page_list`, `tico_page_show` and
`tico_page_archive`.

Task turns receive up to ten pages filed on or pinned to the task and ten linked meetings, newest first, as ids,
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
  and publisher; an older one opens in place, and **Latest** on the status line goes back. **What's changed?** shows
  the page's visible text, one line per heading, paragraph, list item or table row, as a line diff against the
  version before; an earlier version can be picked, and **Close** or Esc returns to the page. A version that changed
  only styles or images shows "No text changes".
- **Leave out the title, version and date.** The viewer shows them above every page, so a page starts with its
  content, not its own title heading or a "v3, updated Nov 12" line.
- **A task** shows the pages pinned to it as cards at the top, a replaced page left out. **Link page** searches
  existing pages and links the chosen one. Any person, or bot acting as itself, who can read the task may link a page
  they can read; the link keeps the page's own access rules. Other task readers see only pages they can open, with no
  count of hidden pages.
  **Make page** sends a description to your Assistant in a fresh session and temporary workspace. It acts with
  your normal rights, reads task references through the normal tools, and publishes and pins the page with you as
  author. The page gets the normal task audience; copying source content is accepted and does not narrow it.
  The request shows progress or **Not made · Open chat**. An older runner leaves it queued with
  **Update this computer to make pages · Retry** until updated. A full task (60 pins) refuses the request or
  publication without storing a page. Publishing unchanged HTML adds no version.
  The task place's `can_link` controls linking; it grants no card editing or pin arranging rights (`can_edit`).
  **Unlink** is available to whoever linked it, the task's owner or requester, and the org owner.
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

`tico page list --task ID` and MCP's `tico_page_list` include both filed and linked pages with the same paging.

**Who acts.** A bot publishes during its turn. Your Assistant makes task pages as you, recording its authenticated
attempt alongside your authorship. People archive and restore. BotOps archives for a person with that
person's rights, and asks before a hard delete, which only the owner may do.
