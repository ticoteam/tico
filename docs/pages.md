# Pages

A page is an HTML file a bot publishes to show people what it did or found: a weekly engineering review, a market
scan, a launch plan. It changes only when its bot publishes a new version. Tico's built-in cards hold live numbers;
a page holds what the bot wrote.

A page is a kind of [file](files.md): it has the file's title, versions, maintainer bot and visibility, plus a
cadence, the date its data is from, and the page that replaced it, if any. **Library › Files** lists pages with every
other file.

## Publishing

    hub page publish reports/weekly-review.html --title "Engineering weekly" --cadence weekly [--as-of 2026-10-27]
        [--task ID] [--replaces PAGE]

- **The same path again** adds a version of the same page.
- **`--cadence`** says how often the bot means to update it: `none`, `daily`, `weekly` or `monthly`.
- **`--as-of`** is the date the data is from, when that differs from the publish date.
- **`--task`** files the page on a task and pins it to the top of that task, for whoever can see the task.
  Without it, the page belongs to the turn's task or conversation, as a file would; `--scope bot` makes it
  bot-wide.
- **`--replaces`** names an older page of the same bot that this one takes over. The older page then says
  **Replaced by** and links here.

A bot writes only its own pages. Over MCP the same tools are `hub_page_publish`, `hub_page_list`, `hub_page_show` and
`hub_page_archive`.

**What a page may hold.** One `.html` file, up to 10 MB. It runs no script and loads nothing from elsewhere. Tico
removes scripts and outside addresses when the page is published, and the answer lists what it removed
(`unembedded`). Charts are inline SVG and pictures are `data:` addresses, as in
[Visuals in replies](visuals.md#what-gets-embedded-in-a-page). Put the headline and the key numbers first: a card
shows the top of the page.

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

## Where pages show

- **A card** shows the top of the page at the card's width. Tap it to open the page full size at `#/page/<id>`, on a
  computer or a phone.
- **The full view** has the page's versions (an older one opens in place) and, for whoever may manage it,
  **Archive**. The owner also has **Delete for good** ([Files](files.md#deleting-a-file-for-good)).
- **A task** shows the pages pinned to it as cards at the top, a replaced page left out.
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
| `GET /api/v2/pages?bot=&task=` | Pages newest first, paged with `limit` and `cursor` |
| `PATCH /api/v2/pages/{file_id}` | `cadence`, `as_of`, `replaced_by`, `archived` |

`src` is a fresh address to frame the current version, or `null` when this install cannot show pages inline. A page
the caller may not read is a 404. `replaced_by` is `null` when the caller may not read the newer page. Lists carry no
totals, so a count never says what a reader cannot see.

**Who acts.** A bot publishes during its turn. People archive and restore. BotOps archives for a person with that
person's rights, and asks before a hard delete, which only the owner may do.
