# The software factory

Tico's recommended way to build software with a team of bots. It covers how an idea, a bug or a small change becomes a
reviewed, tested, documented and released change. Each role is a bot, and a person is involved only where one has to
be. Tico Team builds Tico this way. This page is a recommendation, not a rule the server enforces: rename the roles and
drop the ones you don't need.

In 0.6.0-rc.1 every task is in the General pipeline. The Product and Engineering pipelines, with their stages for
spec, build, two reviews, merge and release, come in later 0.6 phases: see [Product](pipelines-v3.md#product) and
[Engineering](pipelines-v3.md#engineering). Until then the roles below work General tasks and the sub-tasks they file.

## The roles, in order

| # | Role | Starter template | What it does |
|---|---|---|---|
| 1 | Builder | `builder` (Builder), set to build | Builds the change and makes every fix |
| 2 | Reviewer 1 | `pr-reviewer`, preferably on a different model company from the Builder | Technical review: bugs and correctness, as comments on the task |
| 3 | Reviewer 2 | `pr-reviewer`, on a third model if you have one | A second review of the updated change; every change gets one |
| 4 | Release Manager | `release-notes` (Release Manager) | Cuts the release, rolls back if needed |
| 5 | Security | `security-engineer` (Security Engineer) | Tests each release build; holds a release only for serious problems (below) |
| 6 | Technical Writer | `docs-writer` (Technical Writer) | After each release, updates the help docs for the changes people will see and writes the release notes |
| 7 | DevOps | `devops-engineer` (DevOps Engineer) | Failed builds and pipeline problems; watches the factory's cost and speed |

Two leads sit above the line. **Head of Product** (`product-lead`) decides *what* to build: it triages feature ideas,
writes the spec and acceptance tests, and keeps the roadmap. **Head of Engineering** (`engineering-lead`) decides
*how*: it splits an approved spec into tasks, picks the Builder and sets the merge rules. A small team can
let one bot cover Security and DevOps. Start with the Builder, Reviewer 1 and Reviewer 2, and add the
rest as your release pace grows.

The starter templates were written for a person-in-the-loop team: a reviewer drafts a review for a human to post,
for example. Edit each bot's instructions to match its role here, or ask BotOps to do it. When reviewers
are authorized to submit their own reviews, configure a [separate reviewer GitHub App](github-app.md#separate-identity-for-reviewers)
and use `hub repo review` with the full reviewed head and `approve` or `changes`. Record the GitHub receipt on
the review task as well. Required GitHub approvals are dismissed on push, so a changed head needs a current
approval before merge.

## Models and effort

- **Builder: use the strongest model at high effort.** Today that is Claude Opus. It writes the code and makes the
  final call on every review comment, so don't save money here.
- **Reviewers: prefer a different model family from the Builder.** Reviewer 1 and Reviewer 2 from model families
  other than the Builder's (for example GPT or Grok against a Claude Builder) catch what the Builder's model misses.
  It is a preference, not a gate: when no reviewer from another family is free, a same-family reviewer reviews
  rather than the change waiting. The Builder still decides what to act on.
- **For reliability, turn effort up.** Higher effort on the Builder, the reviewers and QA catches more before
  release.
- **To save money:**
  - run the reviewers on lower-effort or cheaper models, in a different family from the Builder where one is available
  - don't cut the Builder

## Rules that make it work

1. **Product decides what, engineering decides how.** Feature ideas go to Head of Product first. Bugs and small
   changes go straight to Head of Engineering as a task.
2. **Prefer different models for checks.** Reviewers and QA prefer a bot of a different model company than the
   Builder. Set this per bot (its task model, or its model), or use a check model on a sub-task
   (`hub task child <task> --check-model`).
3. **Reviewers advise; the Builder decides.** The Builder answers each comment it doesn't take with a one-line
   reason on the task. There is no second round of the same review after fixes.
4. Validation skills attach evidence (tests, screenshots) to the task; a person OKs each release from its notes and
   before/after screenshots.
5. **No person in the loop by default**, except where Tico requires one: publishing, spending, sending and hiring.
   Those are OKs a person gives on the task ([Asks and OKs](tasks.md#asks-and-oks)).

## A change, from task to merge

The Builder opens its PR as a draft and pushes freely: a draft runs no CI and gets no review request. When the change
is done it runs the checks its pull request would run on its own computer (in Tico's repository,
`python3 scripts/pr_checks.py local --ready`, which prints a receipt line for the task) and marks the PR ready once.
Each push to a ready PR runs CI again, so fixes go in one push.

Reviews are sub-tasks: `hub task child <task> --owner <reviewer> --role review --title "Review 1: PR #N"`. The task
waits on the sub-tasks it filed until they end. A reviewer submits its review at the full PR head with
`hub repo review` and finishes its sub-task. A push after a review needs a new review of the new head. The PR merges
through the repository's merge queue ([Merge queue](releasing.md#merge-queue)), by a bot or person with write access.

## Visual specs

A spec can be a visual page. The product manager publishes it on the task it describes with
`hub page publish <file.html> --spec --task <task>`. The page's styles are inlined at publish, so its mockups read as
Tico does. Every task filed under that task, and their own sub-tasks, gets the spec's page link and text in its body.
A spec that is narrowed to some readers, or on a private task, gives the link only. Build instructions are in
`templates/catalog/product-manager/skills/build-the-spec-page/SKILL.md`.

## When Security holds a release

Security holds a release only for:
- secrets exposed in code or logs
- a way around sign-in or permissions
- one customer seeing another's data
- an exploitable injection or similar hole
- a new dependency with a known critical flaw

Everything else becomes a follow-up task. Head of Engineering may release anyway, with a written reason on the
release.

## What each release carries

The Release Manager keeps one checklist per release:
- a QA pass for each change people will see
- the security result
- the release notes

A private task is left out of the release notes and docs unless it has a public title, which is used in its place.
Nobody is asked to name a private task.

## After launch

For features with a success measure, the Customer Insights Analyst reports "did it work?" 14 days after launch. Head
of Product uses that report at the next triage.
