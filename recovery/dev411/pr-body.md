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
Final-head focused Python run: **168 passed, zero failures/skips, 79.95s** at head 5811ea953cc936a13a7b9dd94831802e5f940935, base 6cb30c873282cde9de26b56d507d82ba2bd78a03. Coverage: auto pickup, task lanes, subtask sessions, bot-list reads, system routines/controls, OpenAPI, milestone waits, wake rules/presets, run triggers, CLI and MCP fleet contracts.
- Acceptance covers three starts from five tasks, ranked replacement, default pickup, snooze and blocker release, Off and usage holds, waiting work freeing a place, ten independent reviews under cap one, finite computer capacity, ownership and private counter boundaries, setting revision/undo.
- Four behavioral groups fail against unchanged d2ec32ae with only synthetic cap fixtures seeded; main has no setting endpoint, so this reaches the missing pickup behavior rather than a 404. Existing usage-limit fencing already worked on main; its no-start assertion is retained in the group with pickup after release.
- Pre-merge related checks: 53 wake/auto tests and 89 related contract/CLI/session/catalog tests passed after fixing discovered regressions. Initial failures and corrected receipts are retained.
- `TICO_BROWSER_CHANNEL= node scripts/ui-tests.cjs settings-task-model`: 1 script passed, including numeric/off/unlimited saves and counters at 1440px and 390px.
- `git diff --check`: passed.

Review 1 only; no QA, per task scope. Built by gpt-6.1-sol. No local full suite, macOS/Docker build, real provider activity, live team database, routine change or release action. Hosted PR checks must pass at the exact merge head. Release publication is a separate completion gate. Rollback: set automatic starts Off.

Not verified in production.
