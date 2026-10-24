## What
Assigned bot work starts automatically on a free computer turn, in rank order. Automatic starts are on by default; an optional per-bot active-task cap or Off setting controls pickup. Waiting work frees a place, and delegated review children have no task cap.

Snoozes, unfinished prerequisites, milestone waits, usage holds, readiness and computer turn allocation remain execution fences. Starts reuse existing task wakes and sessions. Settings includes the cap, undo, task counters, and a WHEN/THEN catalog entry for Start assigned tasks.

## Why
On Deck work without a pending notice previously needed a manual nudge. Pickup now discovers eligible assigned tasks at the claim boundary and moves them to In Progress atomically with the lease.

## Surfaces
- Web at desktop and phone widths; Tauri uses the same UI. Settings editor, counters and history updated.
- API v2, generated OpenAPI, hub CLI and MCP tool share revision and manager permission checks. Private tasks are excluded from unreadable counters.
- Rust runner protocol and all harnesses reuse the existing claim, task-session and lane capabilities. No harness-specific branches or Python runner changes. Older runners keep serial lane behavior.
- Human comments and other owners' task notices remain usable; automatic pickup never moves someone else's assignment. BotOps and external MCP callers use requester-scoped permissions.
- Reverse states: Off, unlimited, numeric cap, undo, existing unsnooze and prerequisite/milestone release paths. Task guide updated. Slack has no bot-settings surface; task delivery stays on the existing path.

## How it was checked
Final-head focused Python run: **320 passed, zero failures/skips, 230.07s** at head ebbf377c0885922fbd3d9ae1ccb662b8c979cd46, base1d9df212c16d851bfe9a8bbe2299197a5af96776. The23files cover every original hosted failure plus auto pickup, lanes/session roots, private inputs and reassignment, replies, review handoffs/check models, routine deletion, bot-list reads, catalog/controls, OpenAPI, milestone waits, wake rules/presets/triggers, CLI and MCP contracts.
- Merged main normally. Import unions preserve login_holds and wake_rules; CHANGELOG exactly main. GitHub confirms mergeable.
- Fixed notice-free backfill overtaking queued task/conversation inputs. Claim-transition fixtures now use the version supplied in attempt.task. Unreadable-job claims are scoped to that bot; blocked work stays blocked while a direct human question can still inspect dependency context. The ranked-completion helper uses its bot attempt token so main's preserved human-caused finished notices do not create an owed answer.
- Original hosted39918257755 failed15assertions; those failures were repaired, not waived. The first repair run155passed/1failed identified the human-impersonation helper, corrected before the320-pass run. No tests deleted, skipped or rerun unchanged to get green.
- Acceptance retains three starts from five tasks, ranked replacement, default pickup, snooze and blocker release, Off and usage holds, waiting work freeing a place, ten independent reviews under cap one, finite computer capacity, ownership/private counters, setting revision and Undo.
- Four original behavioral groups failed against unchanged d2ec32ae with synthetic cap fixtures; usage-limit no-start was already an existing fence. Historical main-control receipts retained at that base, not claimed as a new final-base run.
- Final-head `TICO_BROWSER_CHANNEL= node scripts/ui-tests.cjs settings-task-model`: **1script passed in15s**, covering numeric/off/unlimited saves and counters at1440px and390px. `git diff --check` passes.
- Fresh exact-head hosted checks: https://github.com/ticoteam/tico/actions/runs/39935859046 (in progress). Independent Review1 remains waiting for green checks via the existing delivery child. Core0.4.0 merge freeze remains until the release operator announces validation and tag completion.

Review 1 only; no QA, per task scope. Built by gpt-6.1-sol. No local full suite, macOS/Docker build, real provider activity, live team database, routine change or release action. Hosted PR checks must pass at the exact merge head. Release publication is a separate completion gate. Rollback: set automatic starts Off.

Not verified in production.
