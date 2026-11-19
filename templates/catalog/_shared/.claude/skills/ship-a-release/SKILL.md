---
name: ship-a-release
description: Take reviewed and QA'd work to customers safely. Proves every check, review and QA result belongs to the exact code being released, works out whether the release can be rolled back, writes numeric advance, hold and roll back thresholds before the rollout starts, rolls out in stages from the smallest audience up while watching for change against a baseline, rolls back by the numbers, checks the release in production, and publishes release notes written for customers. Use when a task is in a Merge, Release or OK release stage, or says release, ship, deploy, roll out, cut a release, canary, staged rollout, go live, hotfix, roll back, release report, release notes, changelog or what's new.
metadata:
  function: engineering
  roles: [release-owner, release-notes-writer, devops-engineer, technical-writer, feature-owner]
  actions: [read, draft, send, change-records]
  review: none
---
# Ship a release

When to use: merged or ready-to-merge work needs to reach customers.

Words used here: a **SHA** is the id of one commit. A **tree** is the exact set of files at that commit; two commits
with the same tree hold identical code. A **stage** of a rollout is one audience (not a pipeline stage). An
**install** is one running copy of the product. A **canary** is the first, smallest audience. The **merge queue** is
the line every change waits in to reach the main branch: each is tested on top of the ones ahead of it and merges
only if green. A **baseline** is what the numbers looked like before the release. A **threshold** is a number that,
once crossed, decides what happens next.

Tico words: a **task** is one piece of work with an owner. A **pipeline** moves a task through **stages**. A **page**
is an HTML file a bot publishes onto a task for people to read. An **OK** is a named person's approval, requested
with an ask. Commands that change a task take `--version N`, the number that `tico task show <id>` prints.

## What great looks like

What ships is exactly what was reviewed, tested and QA'd, and the release report shows it: every result names the
code it ran on. Releases are small and frequent. The product is built once and the same build moves through every
stage.

The numbers that mean "go on", "wait" and "roll back" are written down before the rollout starts, so that under
pressure nobody argues about them. The way back is known before it is needed. Customers read release notes that tell
them what changed for them, in their words. After the last stage, someone looks at production and records what they
saw.

## First time at a company

Read `knowledge/engineering.md` in your own repository. If it has a "Releases" section, skip this. If not, ask the
owner these in one numbered message and save the answers under "Releases" with the date. Work on the defaults
meanwhile. Role files that already exist (`knowledge/rollout.json`, `knowledge/thresholds.md`,
`knowledge/merge-gates.md`) win over the defaults.

1. What are our rollout stages and who is in each? Default: canary (the team's own install or internal users), then
   everyone. If the product can release to a share of customers: internal, 5%, 25%, 50%, 100%.
2. Where do error rate and response time come from? Default: the monitoring connected under Tools. If there is none:
   the health endpoint, error counts in the logs and a check of each key page. Say in every report which one was
   used.
3. What are the roll back thresholds? Default: the table in `references/rollback-thresholds.md`.
4. Who gives the OK for a release? Default: whatever the pipeline's release stage asks for. With no ask on the stage,
   a release goes out when every gate is green and nothing in it is high risk; a high-risk item or a data change
   that cannot be undone always gets a person's OK.
5. Where are release notes published, and are customers told directly? Default: the changelog or release page only.
   Emails or in-app messages to customers need an OK each time.
6. How long does each stage soak (run while watched) before the next? Default: 10 minutes of checks, one every 60
   seconds. Set longer for changes whose problems show up slowly, such as nightly jobs or billing runs.

## Steps

1. **Open the release task.** Use the task named "Release vX.Y.Z: <what>", or create it:
   `tico task create --owner <release owner> --title "Release vX.Y.Z: <what>"`. List what is in the release: every PR
   merged since the last release (`git log <last tag>..<release commit> --merges --oneline`), each linked to its
   task. Record the release commit's full SHA and its tree (`git rev-parse <sha>^{tree}`).
2. **Bind every result to that tree.** Build a table: gate, result, SHA it ran on, run id or page. Gates are CI
   checks, code review, QA, and a security hold if any. A result counts only when it ran on the release tree. If
   code changed afterwards, compare: when only docs or release notes changed, the result stands; when behaviour,
   tests, build inputs, prompts or templates changed, rerun the gate. Check that the tests-run count is above zero
   and read each conclusion, not its colour. (Skills `verify-a-passing-check` and
   `name-the-checks-that-gate-merging` if your role has them.)
3. **Read the whole diff for what the PR titles hide.** Look for breaking changes nobody labelled: a removed or
   renamed API field, a changed default, a stricter validation, a new required setting. (Skill
   `find-hidden-breaking-changes`.) Class every data change:
   - Additive: only adds. Old code still works. Can roll back.
   - Staged: a multi-release change, each step safe by itself. Can roll back one step.
   - Destructive: drops, rewrites or narrows stored data. Cannot roll back.
   A destructive change needs a restore point or a reverse step that is named, owned and tried before the release.
   A feature flag undoes behaviour but not a data change; for each flag in the release, name who can turn it off
   and how fast. Then answer in one line: "Can roll back: yes", "no, because <reason>", or "unknown, because
   <reason>". Unknown is not yes. A destructive change must not ship in the same release as the code that first
   stops using the old data; split it.
4. **Write the thresholds before anything is released.** Copy the table from `references/rollback-thresholds.md` into
   the release task and fill it in: today's baseline for each number, measured over the same hours on recent days;
   the advance, hold and roll back value for each; which version to roll back to; the command or steps; how long
   rolling back takes. If traffic is too low for a rate to mean much, use counts and the key-page checks, and say
   so.
5. **Draft the release notes for customers.** Follow `references/release-notes.md`. One entry per change a customer
   can notice: what they can do now or what is different, who will notice, and anything they must do. No internal
   names. Changes customers cannot notice go in one closing line or are left out.
6. **Publish the release report and get the OK if one is needed.** The report is a page: what is in the release, the
   results table from step 2, the roll back answer from step 3, the thresholds, the notes. Publish with
   `tico page publish release.html --task <id> --title "Release vX.Y.Z" --as "Release report"`. When an OK is
   needed, ask once, and put the way back in the line:
   `tico task ask <id> --to <owner> --kind ok "Release vX.Y.Z? Rolls back to vX.Y.W by itself if a red threshold is hit." --show page:<file id> --version N`.
7. **Merge and build once.** Changes reach the main branch only through the merge queue. Tag the approved commit.
   Build one time and release that same build to every stage. Never rebuild between stages.
8. **Release to the canary and watch.** Take the "before" readings and screenshots first. Release. Wait until the
   install reports healthy and reports the new version. Then check each key page and each number every 60 seconds
   for the soak time. Watch for change, not for perfection: three errors that were there before are the baseline; a
   fourth, new one is a signal. Count a signal only when it shows in two checks in a row. Screenshot every signal.
9. **Decide by the table.** All green: advance. Any hold value: stop where you are, investigate for no longer than
   one more soak period, then advance if explained and harmless or roll back if not. Any roll back value: roll back
   now (step 11). The table decides. Do not reason your way past a red number.
10. **Repeat for each stage** up to everyone. Record each stage: audience, version before and after, the numbers,
    signals seen, the decision and the time.
11. **Rolling back.** Stop the rollout first. Then:
    - "Can roll back: yes" and a red threshold hit in two checks in a row: release the previous version to the
      stages already updated, by the steps written in step 4. Confirm healthy and the old version. Tell the owner
      on the release task what was hit, with the numbers.
    - "No" or "unknown": do not roll back. Reduce the harm with what can be undone (turn the feature flag off,
      pause the job), then ask the owner with the options and your recommendation.
    Either way open an incident with `debug-and-handle-incidents`. A rolled-back release is fixed forward as a new
    release through this same skill.
12. **Check production and publish the notes.** After the last stage is healthy, look at the changed flows in
    production, read-only (`tico bot app-shot` at 390 px and 1280 px), and confirm the version. Then publish the
    release notes where `knowledge/engineering.md` says. Comment on each task in the release so its owner knows it
    is live.
13. **Record and improve.** Add a row to the "Releases" log in `knowledge/engineering.md`: version, date, stages, time
    from merge to everyone, signals, rolled back or not. A manual step you did twice, or a threshold that fired
    wrongly, goes to `turn-corrections-into-gates`.

## Gates

- Nothing is released from a tree that the review, QA and check results do not cover.
- No rollout starts without the threshold table filled in, including the baseline and the way back.
- No stage is skipped. The fast lane (hotfix) is only for data loss, a security hole, an outage, or an install that
  cannot update. A hotfix still goes through the merge queue and still starts at the canary.
- A stage that reports failed, rolled back or uncertain stops the rollout.
- Never merge around the queue, never rerun a red check hoping for green, and never mark a check as passed by hand.
- A data change already released is never edited. Fix it with a new one.
- Release notes claim only what was seen working in production.
- A release with an open security hold does not go out.

## Autonomy

Alone: assemble the release, rerun stale gates, write the thresholds and notes, release through every stage when no
OK is asked for, hold a stage, stop a rollout, roll back a release marked "can roll back: yes" when a red
threshold is hit, and send release news under the company's sending policy.

Needs an OK: whatever the pipeline's release stage asks for; any release holding a high-risk item or a destructive
data change; rolling back when the answer was "no" or "unknown"; a hotfix; infrastructure spend above budget.

## Output

- The **Release report page** on the release task, opening with version, commit, verdict ("ready", "ready, needs
  OK for <item>", or "not ready: <gate>") and the roll back answer. It carries the results table, the threshold
  table and the customer notes.
- The **rollout log** as comments on the release task: one per stage with before and after versions, numbers and
  decision.
- The **published release notes**, with before and after screenshots for changes customers can see.
- A row in the "Releases" log in `knowledge/engineering.md`.

## Checks before you finish

- Does every row of the results table name a SHA whose tree is the release tree, or say why the result still stands?
- Was the threshold table filled in before the first stage, with a baseline measured, not guessed?
- Did the same build go to every stage?
- Is there a recorded decision, with numbers, for every stage?
- Does production show the new version, and did you look at the changed flows there?
- Would a customer understand every line of the notes without knowing how the product is built?

## Example

Invented example.

Release v2.4.0, commit `9be1f04`, three PRs: pause a subscription (task 412), faster order export, a fix for the
1 cent discount bug.

Results table: CI ran on `9be1f04`, 1,482 tests, 0 failed. Review of PR 231 was on `c07d2b1`; its tree differs from
the release only by the two other PRs, each with its own review at its own head. QA report on the merged preview
at `9be1f04`: pass. Data change: one new nullable column. Can roll back: yes.

Thresholds written at 09:40. Baseline from the same hour on the last five weekdays: error rate 0.4%, p95 response
time 310 ms. Roll back if error rate goes above 0.8% or p95 above 465 ms in two checks in a row, or if any key page
fails to load. Roll back to v2.3.2 by releasing the earlier build; about four minutes.

No OK needed: no high-risk item, pipeline stage has no ask. Canary released 10:02, healthy at 10:05. Ten checks:
error rate 0.4% to 0.5%, p95 300 to 330 ms, no new error types. Advance. Everyone at 10:20; ten more checks, green.

Production check: paused a test subscription, shots at both sizes. Notes published:

"**Pause your subscription.** Going away? You can now pause for up to 3 months from Account, then Subscription. You
are not charged while paused, and you can resume at any time."
"**Order exports are faster.** Large exports that took minutes now finish in seconds."
"**Fixed:** orders with a 100% discount code were charged 1 cent. If this happened to you, it has been refunded."

The last sentence was kept only after Support confirmed on the task that the refunds had gone out.
