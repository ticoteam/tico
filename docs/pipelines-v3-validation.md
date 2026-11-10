# Pipelines v3: validation

The checks that prove the Tico 0.6.0 pipelines build. Each one comes from the "Done when" of a phase in the
[Roadmap](pipelines-v3.md#roadmap) or from [Proof](pipelines-v3.md#proof) in the build spec,
[Pipelines v3](pipelines-v3.md). A phase is done when every check in its section passes. A change to a check changes the
spec in the same PR.

Scenario numbers (scenario 1 to 52) are the numbered list in
[Scenarios the rig and the spot checks must pass](pipelines-v3.md#scenarios-the-rig-and-the-spot-checks-must-pass-52).
Each scenario is listed under the first phase whose build it needs.

## The fault rig, in every phase

The rig is the same in every phase. It grows with each phase's scenarios.

- **It runs the real engine.** A simulator runs the real engine against a fake clock, fake bots and a fake GitHub. How to
  check: the rig imports the engine's own `move()`, `waiting_on()` and never-stuck check, not copies.
- **Every run replays.** Each run takes a seed, so any failure can be replayed exactly. How to check: rerun a failing
  seed; it fails at the same step.
- **It runs in CI, not on a person's machine.** How to check: the rig is a CI job, and its result is on the PR.
- **Fake bots** finish, stop early, comment and stop, crash mid-run, ask, snooze, file sub-tasks, or never answer. How to
  check: each of these behaviors shows up in the rig's run report.
- **Faults:** a crash between any two statements of a move. Lost, late, repeated and out-of-order webhooks. Wakes that
  vanish. Bots paused, quarantined or out of credit. Two movers at once. Clock jumps. How to check: each fault shows up
  in the rig's run report.
- **After every step** the rig checks the seven rules that hold at all times. A run fails on the first break:
  1. Every open task has at least one named wait.
  2. Every bot's turn has a live run, a queued wake, a next try time, or has gone up a level.
  3. Every person's turn shows exactly once in that person's Needs you.
  4. Stage, owner and status change only through `move()`.
  5. An on-enter action happens once per visit, and is undone by moving back or closing.
  6. A forward move never overfills a stage.
  7. Nothing wakes a bot about a snoozed, Done or Closed task.
- **At the end of every run** the rig stops the faults and checks that every task reaches Done, Closed, Snoozed, a
  by-hand stage or a person's turn. How to check: no task is left waiting on a bot forever.
- **It counts runs.** How to check: no task uses more than 7 bot runs without a change before a person is asked.

## Phase 1: the engine and General

- **The rig is green on 10,000 seeded runs.** How to check: the CI rig job runs 10,000 seeds and every seed passes, with
  the rules above checked after every step.
- **One writer: the lint test.** How to check: the lint test passes on the branch, and fails when a write to a task's
  stage, owner or status is added anywhere outside `move()`.
- **One writer: the trigger.** How to check: a direct database write to a task's stage, owner or status outside `move()`
  is refused by the trigger (scenario 6).
- **The new Tico Team org exists and runs General on 0.6.0-rc.1.** How to check: the org's version reads 0.6.0-rc.1, and
  General tasks there go from To do to Done.
- **The old Tico Team stops taking releases.** How to check: its version stays on the release it had, and 0.6.0
  pre-releases go only to the new Tico Team.
- **Rig scenarios for this phase pass:** 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 36, 37, 39, 42, 43, 45, 46, 47, 48 and
  49. How to check: each scenario is a named case in the rig, green in CI.

## Phase 2: Product

- **The rig passes its page, limit, by-hand and handoff scenarios.** How to check: these scenarios are green in CI.
  - Page: 14, 16 and 27.
  - Limit: 11.
  - By hand: 20.
  - Handoff and links: 23, 24 (closing the open Engineering task), 25, 26, 38 and 52.
  - Also needing a pipeline of more than one stage: 18, 21, 22 and 41.
- **One real idea goes from Ideas to Done on the new Tico Team, with In development filing a General task for the
  Builder.** How to check: the Product task's history shows Ideas, Spec, In development, Validate and Done. In
  development filed a General task owned by the Builder, linked to the Product task, and it ended Done. No hand database
  fix was made.

## Phase 3: Engineering

- **The rig passes with a fake GitHub that loses, delays and repeats webhooks and drops PRs from the queue.** How to
  check: the rig's fake GitHub runs all four faults, and these scenarios are green in CI: 15, 17, 19, 24 (one whose PR
  merged stays open), 28, 29, 30, 31, 32, 33, 34, 35, 40, 44, 50 and 51.
- **One real PR goes through Merge and Release.** How to check: an Engineering task on the new Tico Team shows PR merged
  met in Merge and In a release met in Release, then Done. The PR's merge commit is in the history of the published
  release.
- **0.6.0 is published.** How to check: the 0.6.0 release exists on GitHub, is not a draft, and has no dash in its tag.

## Phase 4: proof, then rebuild the orgs

- **10 real tasks go from idea to Done on the new Tico Team with zero hand database fixes.** Each one is a real product
  idea that goes from Ideas to Done: spec approved by a person, built, reviewed twice, review page approved by a person,
  merged, released, checked in production. How to check: each task's history shows every stage in order and ends Done. A
  hand fix is a bug: it gets fixed and the count starts again at zero.
- **Every ask is in Needs you.** Every time a person is asked, it is in Needs you with the thing shown and buttons that
  work. How to check: for each of the 10 tasks, the spec OK and the review page OK were answered from Needs you, with
  the page on the screen.
- **"Waiting on nothing" on Health reads 0 the whole time.** How to check: Health › Tasks shows 0 waiting on nothing at
  every hourly check during the 10 tasks.
- **Then the other orgs are rebuilt from scratch on 0.6.x.** How to check: each one runs 0.6.x, set up fresh, with no
  data carried over.
- **In production, for good.** The same checker runs every hour on every org and shows on Health (S15). How to check:
  Health on every org shows the hourly result. Any task found waiting on nothing is a bug report, filed by Tico against
  itself.
