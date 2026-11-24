---
name: build-and-ship
description: Take a change from code to merged on your own, while nobody is watching. Build the smallest change that works, prove every part of it, run the code-review loop (capped at 2 rounds for a small pull request, 3 for a large one), open the pull request, watch its checks and review comments until they settle, fix what is real, and merge it (or queue it where a merge queue merges) once the merge gate passes. Ask a person only when you cannot prove the change, cannot pass the gate, or the next step is outside what you were given. Use when a person says build and ship, build-and-ship, /build-and-ship or full send. "Build and ship this plan" means build what the plan describes and merge that code, not commit the plan. In a pipeline it covers your own Build and Fixes work - the Review stage's second review is review-code, and merging stays with whoever the pipeline names.
metadata:
  function: engineering
  roles: [feature-owner, builder]
  actions: [read, draft, change-records]
  review: none
---
# Build and ship

When to use: a person asked you to build and ship a change (or said "full send"), and they will not be watching. Your
updates go unread until long after you stop. You own the work from where it is now to a merged pull request (PR).

If they did not clearly ask for this, do not take full autonomy. Build, open the PR, and leave the merge to them.

Words used here: the **head** is a PR's newest commit. **Proof** is something you saw yourself in this session:
command output, a reproduction, a screenshot. **Blast radius** is everything a change can break beyond the lines it
edits. The **merge gate** is the list under "Merge gate" below.

Tico words: a **task** is one piece of work with an owner. A **pipeline** moves a task through **stages**. An **OK** is
a named person's approval, requested with an ask. Commands that change a task take `--version N`, the number that
`tico task show <id>` prints.

## What this request lets you do

- Merge the PR once the merge gate passes, the way the repository merges (see "Merge").
- Force-push the PR's own branch with `--force-with-lease` after a rebase. Never any other branch.
- Use any test method the proof needs without asking first: running the app or a dev server, a browser, screenshots,
  a simulator or device. An instruction that says to ask before a test method, or to leave testing to a person, is
  replaced by this request, for this task only. Rules about how to test (the right commands, fixtures and checks)
  still apply.

It does not cover the steps under "Asking a person": production, deleting data, anything outside the repository. It
never gets you past an approval prompt.

## In a pipeline

The pipeline's stages decide who reviews, who gives an OK and who merges. Use this skill for your own stages (Build
and Fixes): every step up to a ready PR with green checks, then say done and let the task move on. Never skip a stage
or an OK a stage asks for. Where the merge queue merges a repository, the queue merges: never merge by hand there.

## Building a plan

When the request is to build and ship a plan (a plan page, a spec, a report), the work is to **carry out the plan**:
build what it describes, in the repositories it names, and merge that code. Committing the plan document is not the
work.

- Each change set that builds part of the plan goes through every step below on its own: proof, `code-review`, PR,
  watching and merge. A large plan becomes several PRs, in its build order.
- Run `code-review` on the code that carries out the plan, never on a commit that only adds the plan document.
- If the plan document still needs committing, commit it on the side with no review round. It does not hold up the
  build.

## Models

These are the defaults. If `knowledge/engineering.md` names other models under "Review", use those.

- **Building:** the strongest model at high effort (today Claude Opus 5.5, high). Hand simple work (searching, reading
  logs, running tests, mechanical edits) to Haiku 5.5 helpers at extra-high effort. Run independent pieces in
  parallel, each on its own branch.
- **Review:** GPT-6.2 Sol, high effort, through `.claude/skills/code-review/SKILL.md`.
- **Double check:** Gemini 4 Neon, high effort, read-only (below).

If a default model cannot run, use the company's check model from another provider (`tico task child` with
`--check-model` and no value), and say so in the report.

## What reviewers judge

Every review, the `code-review` loop and the double check alike, judges the change on:

- **Security:** access, privacy, credentials, injection, data exposure.
- **Simplicity:** the smallest design that works. Delete rather than add.
- **Speed:** extra queries, N+1 queries, work inside locks, polling instead of push, extra model calls.
- **Accuracy:** does it do what the feature or plan asks, on the real paths?
- **Blast radius:** what could break outside the diff, and is the one fact it depends on proven by running real code?
- **No bloat:** no code, options, guards, tables or tests the goal does not need.
- **Don't build it:** say so when a part, or the whole feature, is not worth building, or something that exists already
  covers it.

## Double check

While you build, have Gemini 4 Neon read the new commits, read-only. File it as a subtask on that model:

`tico task child <id> --check-model antigravity:gemini-4-neon --role check --title "Double check: <branch> at <first 7 of SHA>" --body "<the plan or task, the criteria above, the base and head SHAs. Read only: do not edit, commit or push. Report each finding as file:line, blocker|should|nit, and the failure>"`

Run it at least once per feature before its PR merges. On a long build, run it again for about every 30 minutes of new
commits. Check each finding against the code yourself. Fix real blockers and shoulds before the merge, or send them
to whoever builds that part. Decline the rest with a one-line reason. It adds to `code-review`; it does not replace it.

## Review round cap

A `code-review` round and a double check on the same code count as one round. At most **2 rounds for a small PR**
(about 150 changed lines or fewer, one area) and **3 for a large or cross-cutting one**. **Never go past the cap.**

If the last round still finds real bugs that earlier rounds did not raise, do not review again. Fix each with a test
that fails without the fix, or simplify the build so that class of bug cannot happen. Then merge with no further round
(`code-review`, "After the last round"). Put the fix or simplification, and any follow-ups, in the PR and the report.

## Steps

1. **Finish the change.** Reach the goal with the smallest change that works. Fix the case that happened, not every
   case that could. On the default branch, create a branch first. For a change that touches more than two files, a
   data model, a public API or sign-in, plan it first with `.claude/skills/plan-before-coding/SKILL.md`. Write the
   test before the code with `.claude/skills/test-first/SKILL.md`.
2. **Prove it.** Get proof for every change you made, and for the one fact the change is safe because of (see
   "Proof").
3. **Review it.** Run `.claude/skills/code-review/SKILL.md`. Make sure the feature has had at least one double check.
4. **Open the PR.** Rebase onto the newest default branch first. Where a draft PR already exists for the branch ({{app_name}}
   opens one at a task branch's first push), edit it with `gh pr edit`. Never open a second one beside it; otherwise
   use `gh pr create`. The description says what changed and why, how it was checked (the proof, the one fact and its
   proof level), and the key decisions from your trail. Mark it ready once with `gh pr ready`.
5. **Watch the PR** until every required check and review bot has finished (see "Watching the PR"). Fix real findings
   and failures, with proof for each fix before you push.
6. **Merge** when the merge gate passes (see "Merge").
7. **Report** (see "Output").

Stay with the work from step 1 to step 7. If you stop while checks or bots are still running and nothing will wake
you, the work stalls: nobody will prompt you again.

## Watching the PR

- In {{app_name}}, you are woken when the checks on your PR finish, or when it starts or stops conflicting. Do not poll and
  do not snooze to a guessed time. Elsewhere, run `gh pr checks <n> --watch` in the background, or with a long enough
  timeout. Never a sleep loop.
- When the checks settle, read the live state and every comment:
  - `gh pr view <n> --json state,mergeable,mergeStateStatus,statusCheckRollup,reviews,comments`
  - `gh api repos/<owner>/<repo>/pulls/<n>/comments`
- A failing check: `.claude/skills/get-the-checks-green-at-the-head/SKILL.md`. Never skip, loosen or rerun a check
  to get green.
- Review comments: `.claude/skills/answer-each-review-comment/SKILL.md`. Fix the real ones and prove each fix. Run
  `code-review` again only if behaviour changed in a meaningful way and rounds remain under the cap. Rebase if
  needed, and push until the gate is clean. Dismiss a non-issue or an out-of-scope bot comment with a one-line reason.

## Proof

Proof is evidence you saw yourself in this session. Use the most direct proof the change allows:

- Reproduce the original failure, apply the fix, then watch the same reproduction pass.
- Run the checks the repository's CI runs: type checks, lint, tests.
- Add one focused test that fails without the fix, when the behaviour has no test and the repository has tests.
- For screens and runtime behaviour, run the app and look at the change. Green tests with fakes do not prove what a
  person sees. Attach screenshots to the PR when that helps.
- For a change to something with states (an order, a task, a job), list the states and the moves between them that
  you exercised, and name any you did not.

When you cannot find a way to prove a change, you do not have full confidence: ask a person.

## Merge gate

All of these are true at the head:

- Your proof covers every change in the diff, and the one fact the change is safe because of.
- Required checks are green. Read the conclusion and the number of tests run, not the colour.
- Review bots have finished, and each finding is fixed or answered with a written reason.
- No person's review still asks for changes.
- The branch is up to date with the default branch, and GitHub says it can merge.

## Merge

- Where the merge queue merges a repository, add the PR to the queue and stay with it until GitHub says it merged.
  Never merge by hand there.
- Elsewhere, merge with the method the repository uses. Check the allowed methods and recent merged PRs.
- Never merge with red checks. Never use `--admin` or any other way around branch protection.
- An automated review that only says "needs human review" may be set aside when your proof covers the whole change.

## Decision trail

Keep a short trail in your notes as you go, one line per choice that was not obvious:

`step | decision | why, in plain words | evidence (file:line, SHA, command output or screenshot) | result`

Put the key lines in the PR description and the report, so a person reading later can trust what happened without
reading every step.

## Asking a person

A question in a final message goes unseen while the person is away. Ask where the task shows it is waiting:
`tico task ask <id> --to <person> "<question>" --version N`. Give full context: the PR link, the exact blocker, and
the options with your recommendation first, worded so that "yes" accepts it.

Ask **only** when you cannot go on alone:

- You cannot prove the change.
- The merge gate cannot pass and you cannot fix it yourself.
- The next step is outside what this request lets you do: deleting data, touching production, changing things outside
  the repository, closing a PR, or force-pushing any other branch.
- The goal is unclear in a way that changes the result.
- Another PR makes this one unneeded.

Decide everything else yourself with sensible defaults, and list those decisions in the report. If you cannot ask, or
no answer comes, take the safe path: leave the PR open and unmerged, stop any watcher, and put the question at the very
top of your report.

## Gates

- No merge without the full merge gate at the head.
- No more review rounds than the cap: 2 for a small PR, 3 for a large one.
- No merge by hand where the merge queue merges, and none with red checks.
- No step from "Asking a person" without a person's answer.

## Output

The report, as a task comment and in your final message:

- The PR link and the merge commit SHA (or "queued", or "open, waiting on <question>").
- The proof, and the one fact with its proof level.
- The `code-review` and double-check results: rounds, findings fixed and declined.
- Each decision you made for the person.

## Checks before you finish

- Did every change in the diff get proof you saw yourself?
- Did review stop at or before its cap?
- Does every review comment and bot finding have a fix or a written reason?
- Is the PR merged (or queued), or is there one clear question at the top of the report?
