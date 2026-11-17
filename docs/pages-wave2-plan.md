# Wave 2 page storage plan

Use nullable `bot_files.bot_id` for pages. Keep `bot_files` as the shared file envelope and `bot_file_versions` as the version store. A separate page table would split parent references and duplicate the joins that handle versions, bytes, privacy, pins, search and task context. A transactional rebuild changes one parent table while keeping every file ID and version in place.

## Summary

1. Start after wave 1 is live on TIDY.
2. Make `bot_files.bot_id` nullable for pages and retain the bot requirement for ordinary files.
3. Preserve page IDs, version numbers, bytes, activity, approvals, verdicts, rounds, pins and message references.
4. Use page writer links for write and management rights. Remove proven synthetic Assistant writer links.
5. Preserve each existing page's inherited read boundary and audience, including the original access checks.
6. Let people publish directly without an Assistant, and keep Make page attributed to the requester.
7. Use a bot-independent path key for repeat publishes and an explicit page ID for ambiguous series.
8. Change Files, Library, search, SQL, runner and message lookups that currently select pages by storage bot.
9. Drop `task_documents` after verifying the wave 1 backfill and carrying any documents in task trash snapshots.
10. Prove the upgrade on seeded data and compare before-and-after query plans and timings on RunsOn.

## Storage and access

Use `bot_files.id` as the permanent page identity. Use a path-based key independent of the publishing bot for implicit repeat publishes. Limit path matching to pages in the original scope that the actor can read and is linked to as a writer. Keep the existing page-ID route for cross-writer edits and return a 409 with readable candidates if several pages match. Check older rows against their earliest stored path or name when converting series. Retain the ID and version history even where a path cannot be reconstructed.

Separate the original visibility boundary from the storage bot. The original task, conversation and bot-read checks, and the version's attempt and blob checks, keep their existing effect. For migrated pages that inherited an Assistant's read policy, preserve that policy as visibility provenance. It confers no ownership or write permission. New unfiled personal pages can use a personal scope keyed to their publisher. New filed pages use their originating task's visibility. The existing audience remains a narrowing restriction. Adding a writer, task or other link never grants read access or an audience exemption. Preserve explicit bot-wide promotion only where the current policy already permits it.

Put creator or maintainer privileges on the page's writer relationship, with company owner and admin rules kept explicit. Version authors and other linked writers can write once they have read access. They do not automatically gain the ability to change an audience. When a legacy Assistant link was inferred from the storage row, remove it only if the original personal series and version authorship establish that it was synthetic. A bot's real writer link stays.

## Storage-bot dependency inventory

This inventory uses the wave 1 integration code. Recheck the call sites against the deployed wave 1 head before implementing.

| Surface | Dependency to replace or preserve |
|---|---|
| `backend/files.py` publish, `writer_bot`, `target`, `find`, `open_row`, `add_version` | The storage bot currently selects a row and salts the series key, and direct human publish resolves an Assistant. Keep bot and runner turn authentication, then write a standalone page as the actual actor. Keep ordinary files' existing key and publish path. |
| `backend/assistant.py` Make page and `backend/pages.py` publish callback | The Assistant still runs the turn. Page storage, blob ownership and pin attribution follow the requester. Keep authenticated attempt and originating task checks. The Make page status query already identifies work by task scope and attempt. |
| `backend/pages.py` management, audience, representation, pinning and `bot=` | Remove storage-bot comparisons in management and writer derivation. Keep audience exemptions independent of editable links. Build bot-filtered lists from links. Keep the old response fields as empty strings if clients still require them. |
| `backend/page_links.py` and `backend/task_documents.py` | `may_write` currently falls back to storage-bot-based management. Document recording can infer an actor from the file row. Use writer relationships, the authenticated writer and version attribution. |
| `backend/media.py`, `backend/files.py` task attachments, `backend/task_pages.py` adoption | The compatibility document upload picks a storage bot from the caller or task. New document rows are pages. Adoption by an approval must detach the existing file in the same transaction. Ordinary uploads with the same name must never append to the page. |
| `backend/files.py` visibility, tombstone context and task ref redaction | `file_visible` and the deleted-content context use the bot column. Use original visibility provenance for pages. Give scope lookups an index so task redaction finds detached rows without walking the bots table. |
| `backend/files.py` bot Files rail, mentions, edit, date correction and GitHub links | The rail and mention queries constrain by storage bot. Use linked pages for bot lists and still filter each result for visibility. Use the relevant version writer for source attribution. Keep ordinary file date correction and generic edits from overwriting page versions or filing scope. |
| `backend/file_library.py` lists, team filter and search | The library filters on `f.bot_id` and concatenates it to form display names. Handle botless rows, use relationships for bot and team page filters, keep search results behind the same visibility check and preserve pagination. |
| `backend/file_reviews.py` path lookup, review freeze and self-review | Existing repo-path lookups use the file bot. For a page use the author of the specific version. Keep the ordinary file behavior and check the plan for path and digest lookups. |
| `backend/sql.py` guarded file view and page manager callback | The SQL view currently uses storage bot for inherited visibility and has a legacy document-table rule. Use the original page visibility policy, keep provenance private and preserve the SQL view's existing bot limits. |
| `backend/demo_seed.py`, `backend/devcopy.py`, `backend/task_delete.py` | Seed the page and its writer relationship. Copy live page links into rehearsal databases. Remove live references to the old table and account for its data in historical trash snapshots. |
| ID-based consumers | Cards, pins, events, task context and privacy, message references, visual reads, stage effects, verdicts, blob storage and the Rules Owner's read copies mostly join by file ID, task or version. Keep those joins and verify that none of their row projections requires a non-null bot. |

## Migration and rollback

Add one timestamped Python migration using the migration runner's foreign-key-off mode. Create a new `bot_files` table with the same file IDs and constraints, copy rows, detach pages and rebuild the indexes. Capture and restore the table's change triggers and the blob-media triggers that reference it during the swap. Keep the incoming foreign keys on pages, versions, activity and links pointed at `bot_files`. Check the schema, trigger behavior and foreign-key integrity both on a new database and after a seeded upgrade.

Before dropping `task_documents`, prove its live rows are present as role links and its historical rows in restorable task trash snapshots retain their role. The runtime task-documents module stays because it implements behavior over `page_links`. A failed migration rolls back under the migration runner. After it commits, an old binary cannot safely write detached pages. Restoring a pre-migration backup restores the old schema and loses subsequent writes unless they are recovered separately. Plan to deploy from a verified backup and use a forward repair if writes have already resumed.

Main risks are accidental reader expansion from links or audience bypasses, path collisions between independent pages, replay of publish idempotency keys across deployment, and missing trigger or FK behavior after the swap. Legacy human pages can still inherit an Assistant's read policy even though they have no storage bot. Check that distinction explicitly and confirm what should happen to future policy changes on the old bot before changing the inherited audience.

## Validation after wave 1 is live

- Upgrade a seeded database with bot pages, human pages under Assistant bots, legacy task documents and ordinary files. Check ID and version history, document links, requirements, verdicts, rounds, pins, trigger delivery and restore of old task trash.
- Publish as a person with no usable Assistant and as a bot or authenticated runner. Cover independent same-name series, explicit page-ID writes and link revocation.
- Compare allowed and denied reads through pages, byte URLs, task context, runner reads, bot Files, Library, search, SQL and message references. Include a private task linked to a public one, a restricted audience and a newly linked writer who is outside that audience.
- Capture `EXPLAIN QUERY PLAN` and median before-and-after timings with about 20,000 files and 50,000 links. Cover the bot and team lists, implicit publish, task redaction, ordinary attachment lookup, search and repo-path review reads. Run focused regressions and `scripts/check.sh` on RunsOn.
