Fixes six Tasks bugs from the 2026-10-26 bug hunt (items 10, 11, 12, 17, 19, 20, numbered as in the report); nothing left open in scope.

Dev 506. Base `39005141d004708d401670a52eaede7593e6f5bd`, head `7eac011394498a79297ec6193d9b5b670ae7c6e8`.

## What changes
- **10. Delete with a check model.** `task_check_models` joins `TASK_ROWS`, so a task filed with a check model deletes (was a 500 on the foreign key) and its row comes back on restore. A new guard test fails if any table keyed to `tasks(id)` is missing from task_delete's lists.
- **11. Type deleted while its tasks are in the trash.** Chose "those tasks stay restorable": a restore whose type is gone puts the task on General with no step and reports `type` in its unlinked list (like a retired step). Refusing the type delete instead would block it for the 30 days until purge, with no way for a person to purge.
- **12. Roadmap buttons.** Needs you and Decide no longer offer Done, Reopen or Close on a steps-only (Product roadmap) item, matching the task window. The server now marks each Needs you item `steps_only`, so this does not depend on the types list being loaded; Decide shows "Open to move it" instead of Done, its D key does nothing on such a card, and a yes/no on the person's own roadmap item goes as a comment, not a status. A declined roadmap item is no longer on its requester's Needs you list (it stays on its Declined step on the board and public roadmap; the requester still gets the decline notice), so there is no Close to take it off both.
- **17. Public step names.** A new step on a type that is private by default (the Product roadmap) gets no public name unless one is given; a public-by-default type still uses the step name. The type editor always shows the public step name field and no longer copies the internal name into a blank one. `TaskStepInput.public_name` accepts "" (OpenAPI regenerated).
- **19. Needs you limit.** Needs you reads every owned active task (and declined) instead of stopping at `tasks()`'s 500; `tasks(limit=None)` reads all.
- **20. Duplicate titles.** The Tasks owner confirmed Yes. One normaliser, `hubdb.title_key` (case-fold, trim, collapse inner whitespace, in Python since SQLite lower() is ASCII-only), now drives the create and rename refusals, the Task Manager's duplicates and the stale review's twin. Stored titles are unchanged. docs/tasks.md says so.

## Tested (at head `7eac01139449`)
- New tests, each failing on base `39005141d004` (run on a base checkout with only the tests copied in): 10 backend tests failed there and pass here. `test_a_finished_twin_or_one_under_another_parent_does_not_block` is a guard that passes on both.
- `ui/tests/needs-you-roadmap.cjs` (new) and `ui/tests/task-pipelines.cjs` (extended) fail on base and pass here (Chromium headless).
- Related backend suites, 40 files (task_delete, tasks_board, roadmaps, hubdb, ask_*, check_model*, task_manager, stale_review, openapi_v2, task_*, tasks_*, needs_you_roadmap, duplicate_titles and others): **455 passed**.
- Browser: decide, decide-done, decide-default, needs-you-done, needs-you-groups, needs-you-approvals, needs-you-stale, bot-needs, tasks-page, roadmap-settings all pass.
- Guard: `backend/tests/test_openapi_v2.py` passes with `docs/openapi/v2.json` regenerated.

## Not checked
- Full backend suite and the full UI suite (CI runs them). Chrome stable, WebKit, Mac/Windows fonts. Live data: no migration; existing step public names are untouched.
- Merge overlap: Dev 504 (bug hunt 1) also edits `_retitle`'s status list in hubdb.py; whichever lands second merges main in.

counts as qa: 12 and 17 are user-visible (QA checks them on the deployed build).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
