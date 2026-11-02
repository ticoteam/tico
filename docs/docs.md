# Docs

**Docs** is where a team keeps what its humans and bots should know. Search covers internal docs,
indexed external content and the metadata of other links.

| | Internal docs | External sources |
|---|---|---|
| What it is | Markdown written, pasted or imported in Tico | GitHub documentation or a link that lives elsewhere |
| Where the text is | In Tico, with its full history | Public GitHub text is indexed in Tico; other links store metadata |
| Who can read | Everyone on the team, and every bot | Everyone on the team, and every bot |
| Who can change | Everyone; an owner or bot administrator can lock one | Whoever added it, an owner or a bot administrator |
| Opening one | In the page (read, edit, history) | Source details and indexed previews, with Open original |

## Internal docs

A doc has a **title**, a **path** (`sales/pricing.md`: folders are the parts before the last `/`) and a Markdown
body. The page lists them grouped by folder.

- **Write** one with **New doc**: a title, a path (optional; it defaults to a slug of the title), a Markdown
  textarea with a **Preview** toggle, and an optional "what changed?" note. Save with the button or
  Cmd/Ctrl+S. Text is shown through the same sanitizing Markdown renderer as the rest of the app, so a pasted
  `<script>` or `onerror=` does nothing.
- **First visit.** While Docs holds nothing a person wrote or linked, the Librarian asks "Where are your docs?" above
  the list: links become sources at once and it reads them and builds the map of the docs. **Skip for now** is yours
  alone; **Set up with the Librarian…** in the `...` menu asks again ([librarian.md](librarian.md#first-run-setup)).
- **Import** a file from the `...` menu: `.md`, `.markdown`, `.txt`, `.html`, `.docx` or `.pdf`, up to 20 MB. It becomes
  a Markdown doc (headings, lists, links, bold and italic, and tables are kept; a PDF gives its text, and a scan with no
  text is refused). The title is the first `#` heading, else the file name.
- **Every change is a version.** **History** lists them (who, when, the note; "by Ops" for a bot), shows any old
  version, and **Restore** saves it as a new version. Nothing is rewritten or lost.
- **Editing together.** Saving sends the version you opened. If someone (or a bot) saved first you get "Ben saved
  version 5 while you were editing. Your text is still here." and choose to keep yours (replacing theirs on the next
  Save) or load theirs.
- **Lock.** An owner or bot administrator can lock a doc. A locked doc is read by everyone and changed only by owners
  and bot administrators (`403 locked` for anyone else, restore included). Locking is not a version.
- **Archive** removes a doc from the list and search and frees its path; its history is kept.

Bots use the same docs through the `hub` CLI and the MCP tools:

| Command | Tool | |
|---|---|---|
| `hub doc list [--prefix sales/]` | `hub_doc_list` | paths, titles, who changed them last |
| `hub doc read <id\|path>` | `hub_doc_read` | one doc in full, with its version |
| `hub doc search "words" [--manual]` | `hub_doc_search` | internal and external content, linked metadata and the Tico manual (`--manual`: only it) |
| `hub doc write <path> --title T (--body-file F \| stdin) [--note N]` | `hub_doc_write` | creates or replaces; on a version conflict it reads again and retries once |
| `hub doc history <id\|path>` | `hub_doc_history` | the versions |
| `hub doc link-list` | `hub_doc_link_list` | the linked docs |

A bot's writes show in the history as its own ("by Ops"). A locked doc refuses a bot.

## External sources

Choose **Add source** beside **New doc**, paste a URL and optionally name it. **External sources** stays
reachable beside the independently scrolling internal-doc tree. Open a source to browse its files,
read a preview, **Open original**, edit its scope or choose **Refresh now**. Editing the original stays at its source.

Public GitHub repository, folder (`tree`) and file (`blob`) URLs are indexed automatically, including existing
public GitHub links after upgrade. Branch names containing slashes are supported; choose **Scope > Reference**
when the URL is ambiguous. Roots follow the default branch. Every refresh uses one fixed commit; an unsuccessful
refresh preserves the previous searchable copy. Checks run every six hours on the server, without a model or
Librarian computer. Other URLs and private repositories remain **Link only**, with searchable metadata and the
Librarian's existing on-demand fetcher where supported. Private GitHub sources show **GitHub access needed**;
their content stays unindexed. Private indexing is deferred.

GitHub indexing includes `.md`, `.markdown`, `.mdx`, `.rst` and `.txt`, excluding generated/dependency content,
binaries, symlinks and credential-like names. Defaults: 500 files, 1 MiB per file, 25 MiB per source. Narrow the
folder when a source exceeds them; the UI shows a skipped count. Operators can set
`TICO_DOC_SOURCE_MAX_DOCUMENTS`, `TICO_DOC_SOURCE_MAX_FILE_BYTES` and `TICO_DOC_SOURCE_MAX_BYTES`.
Anonymous GitHub API limits can delay large imports; queued work resumes without discarding completed files.
An existing suitable GitHub App grant can speed public reads, but never enables private indexing.

All sources use the team Docs audience and linked-doc permissions: anyone may add; the adder, owner and bot
administrators may edit/remove. Removal and scope changes immediately hide the old scope from retrieval.
Existing `linked-docs` endpoints and IDs remain supported.

## The Tico manual

Every install carries the manual for its own release: the `docs/*.md` its image ships, as a **read-only** collection kept
apart from the team's docs. It is built in memory when the server starts and rebuilt when the release changes; it is never
stored in the team's docs, so it cannot be edited (`405 read_only`), listed with `hub doc list`, synced or backed up as
team content. `GET /api/v2/docs/search?collection=company|manual|all` (default `company`, which is what the Docs page uses)
and `hub doc search` (all: matching team and manual sections ranked together) label each manual result `Tico manual` with its
file (`docs/backups.md`), a link to that page (the GitHub file at the release tag, `main` on a build with no version) and the
section's excerpt. `hub doc read manual:<name>` (`GET /api/v2/docs/manual/{name}`) reads one page; `GET /api/v2/docs/manual`
lists them.

## Search

The search box (and `GET /api/v2/docs/search`) looks through internal docs, external contents and links together. Internal docs are ranked by matching
sections, with title and heading matches weighted most. Each result includes the section, its anchor and an excerpt of
its text. SQLite FTS5 narrows the candidates where available; otherwise the same section ranking scans the docs.
A question written as a sentence keeps all meaningful words and maps old Tico words using the glossary's "Instead of"
column. Librarian maps and logs under `_librarian/` rank below source docs. Linked docs match title, description and address.
External sections use SQLite FTS5 with database-side ranking and bounded passage results, rather than scoring
full external bodies in Python. `source_id` filters to one source. Results are labelled **Internal**, **External**,
**Linked**, or **Tico manual**. External results carry `external:<id>`, commit, freshness, `citation_url` and
`trust: untrusted_external`. `hub doc read external:<id> --revision <commit>` returns reference text and provenance.
Markdown citations link to source lines at that commit; MDX/`.rst` cite the whole file without an invented anchor.
External text never supplies instructions or action authorization. External search requires SQLite FTS5.

## Ask the Librarian

**Ask the Librarian** is the rail on the right of Docs (a button and a full-screen sheet on a phone). It asks the Librarian, the
built-in bot for the team's docs, which answers with citations (`docs/librarian.md`). Bots and the Assistant ask it with `hub doc ask`.

## Upgrading

The old document mirror (a worker importing linked repositories, the "Current docs" view, Bot Notes, Proposed
changes and Doc Updater's link-repository setting) is gone. On the first start of a version with Docs, each linked
documentation source and each approved documentation repository becomes a linked doc, added by "Tico"; this runs
once. The old `documents` tables stay in the database, unused. Bot reference material stays in each bot's repository,
reachable from the bot's page.

## API

All of it is in the stable v2 contract ([openapi/v2.json](openapi/v2.json), tag **Docs**); every write needs an
`Idempotency-Key`. Reads are open to every signed-in human and bot.

| | |
|---|---|
| `GET /api/v2/docs?path_prefix=&limit=&cursor=` | `{"docs": [{"id", "path", "title", "updated_at", "updated_by", "updated_by_name", "locked", "version"}], "next_cursor"}` |
| `POST /api/v2/docs` `{"path"?, "title", "body", "note"?}` | `{"doc"}`; `409 path_taken` for a path in use. `body` is stored exactly as sent, leading and trailing whitespace included (here and on `PATCH`); the other fields trim |
| `GET /api/v2/docs/{id}` | `{"doc": {..., "body", "created_by", "created_by_name", "archived"}}` |
| `PATCH /api/v2/docs/{id}` `{"version", "title"?, "body"?, "path"?, "locked"?, "archived"?, "note"?}` | `{"doc"}`; `409 version_conflict` (with the current `version`, `updated_by_name`), `403 locked`, `403 forbidden` for `locked` by anyone but an owner or bot administrator |
| `GET /api/v2/docs/{id}/versions` | `{"versions": [{"version", "current", "title", "path", "actor", "actor_name", "created_at", "note", "size"}]}` |
| `GET /api/v2/docs/{id}/versions/{n}` | `{"version": {..., "body"}}` |
| `POST /api/v2/docs/{id}/restore` `{"version"}` | `{"doc"}`: the old version saved as a new one |
| `POST /api/v2/docs/import` (multipart `file`, `title`?, `path`?) | `{"doc"}` |
| `GET /api/v2/docs/search?q=&limit=` | `{"results": [{"type": "internal", "id", "path", "title", "excerpt", "score"} \| {"type": "linked", "id", "title", "url", "kind", "description", "score"}]}` |
| `GET /api/v2/linked-docs` | `{"linked": [{"id", "title", "url", "kind", "host", "description", "added_by", "added_by_name", "created_at", "updated_at"}]}` |
| `POST /api/v2/linked-docs` `{"url", "title"?, "description"?}` | `{"linked"}`; `409 already_linked` |
| `PATCH /api/v2/linked-docs/{id}` `{"title"?, "description"?, "url"?, "archived"?}` | `{"linked"}` |

Writes are recorded in the activity log with the actor (`docs.created_at`, `docs.updated_at`, `docs.restored`,
`docs.locked`, `docs.imported`, `docs.linked`, ...). The Assistant's fast-path lookup (`GET /api/v2/context/search?source=docs`)
covers internal, indexed external and linked docs too.

The code is `backend/docs.py` (with `backend/docs_import.py` for file conversion) and `ui/docs-page.js`,
`ui/docs-editor.js`, `ui/docs-search.js`. Tests: `backend/tests/test_docs.py` and `ui/tests/docs.cjs`.

Archive removes a doc from the active list and search. **Undo** brings it back immediately.
Choose **Archived** on Docs to browse archived docs, open one and choose **Restore**. Restore keeps its history;
if another doc has taken its path, Tico gives the restored doc a new path.

## Source API

- `GET /api/v2/doc-sources?limit=&cursor=` lists source metadata/status.
- `POST /api/v2/doc-sources {url, title?, description?, reference?}` saves and queues a source.
- `PATCH /api/v2/doc-sources/{id}` edits metadata/reference or removes with `archived: true`.
- `POST /api/v2/doc-sources/{id}/refresh {}` coalesces refresh requests.
- `GET /api/v2/doc-sources/{id}/documents?limit=&cursor=` lists included file metadata.
- `GET /api/v2/docs/external/{id}?revision=` returns untrusted text and provenance.

Background text/FTS writes happen in small batches; one small transaction publishes the active snapshot.
Deleted files disappear only after complete enumeration and a successful replacement. A confirmed loss of
public GitHub eligibility hides cached content. Previous revisions remain readable briefly for search/read
races, while removal and scope/visibility checks still apply. The worker makes only fixed-origin GitHub API
requests and never executes repository content or starts a general website crawler.
