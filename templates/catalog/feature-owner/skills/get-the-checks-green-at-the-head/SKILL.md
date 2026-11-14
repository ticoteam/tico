---
name: get-the-checks-green-at-the-head
description: When a check fails on a task's pull request, or before you hand a commit on, find what failed at the exact head commit, fix the cause, and never call a check passed that did not run there.
---
# Get the checks green at the head

When to use: whenever `tico task show` says "Checks failed", and before you say done or attach the QA page.

Steps:
1. Confirm the head commit: `gh pr view <n> -R <repo> --json headRefOid`. Only checks on this commit count. Earlier
   heads do not.
2. Read `gh pr checks <n> -R <repo>`. For each failing check, open its log (`gh run view <run id> -R <repo> --log-failed`)
   and find the first real error, not the last line.
3. Decide whose it is. Your change: fix it with a test, as in `playbooks/build-a-dev-task.md`. The base branch is red too
   (the same check fails on the base's newest commit): follow "When main is red" below; do not paper over it. A check
   that fails one run and passes the next on the same commit is flaky: name it on the task with both run links.
4. Never skip, disable or loosen a test or a check to make it pass. Never rerun to green and call it fixed.
5. Push the fix and record on the task the new head and the checks that ran on it.

## When main is red

A check that failed only because the base branch was broken is not yours to fix on your branch, and not fixed by a
re-run.

1. **One task for the break.** Before you file anything, look for an open one, snoozed ones included (a plain list
   hides a snoozed task, and the bot fixing main may have snoozed its own while the fix waits to merge):
   `tico task list --q "Main red" --status open --snoozed all`. When one names the same failing test or check, do
   not file another and do not open a second fix pull request. Say on it that it blocks you, with what you saw:
   `tico task comment <main-red task> "Blocks #<your task> (PR #<your pull request>): <test> fails, <run link>"`.
   Any relation but the dependency in step 2 changes the other owner's task too, and {{app_name}} refuses it. Only when
   none is open, file one titled `Main red: <the failing test or check>, since <the pull request or commit that broke it>`,
   so the next bot finds it. Working on a private task, file it with `tico task create --from-private`
   (company-visible, in your own words: no text copied from the private task) and never `--private`; otherwise file
   it plainly, never under a private parent. A private one is hidden from the search, and a second bot files another.
2. **Wait on that task, not on a date.** Make your own task depend on it:
   `tico task relate <your task> depends_on <main-red task>`. A dependency is written on the waiting task only, so you
   may set it on another owner's main-red task; your task shows Blocked and you are woken when that task ends. When
   there is no task to depend on (main is being fixed by a pull request with no task) or {{app_name}} refuses the
   dependency, snooze naming the fix pull request, `waiting for #<fix pull request> to merge`: its merge wakes you.
   Never snooze "waiting for main" to a guessed date: a task snoozed for days sleeps on after main is fixed.
3. **After main is fixed: rebase and push, never re-run.** A re-run of the failed job tests the same merge with the
   old main and fails again on the same break. Rebase your branch on the fixed main and push, so the checks run on a
   new head:

       git fetch origin && git rebase origin/<base branch>
       git push --force-with-lease origin HEAD:<branch>

   Then record the new head on the task. A re-run is only for a flaky check, on a head whose base is already green.

{{app_name}} reads the checks itself and moves the task when they pass. You never merge: Merge is the Release
Manager's stage.
