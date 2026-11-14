# The software factory

Tico's recommended way to build software with a team of bots. It covers how an idea, a bug or a small change becomes a
reviewed, tested, documented and released change. Each role is a bot, and a person is involved only where one has to
be. Tico Team builds Tico this way. This page is a recommendation, not a rule the server enforces: rename the roles and
drop the ones you don't need.

The work runs through two pipelines a software company gets by default: [Product](pipelines-v3.md#product) (Ideas →
Spec → In development → Validate) and [Engineering](pipelines-v3.md#engineering) (On deck → Build → Review 1 → Fixes 1 →
Review 2 → Fixes 2 → QA → Merge → Release). Each stage has an owner and says what it takes to leave; the roles below own
those stages.

## The roles, in order

| # | Role | Starter template | What it does |
|---|---|---|---|
| 1 | Feature Owner | `feature-owner` | One per area: takes each feature from pitch to production in one task and one session: the pitch, the Spec page, the build and both Fixes, the QA page, the check in production |
| 2 | Product Manager and Engineering Manager | `product-manager`, `engineering-manager` | Each leads its team and manages its pipeline: picks up what is late or stuck and decides what happens next |
| 3 | Reviewer 1 | `reviewer`, preferably on a different model company from the Feature Owner's tasks | Review 1: a Review page for the newest commit |
| 4 | Reviewer 2 | `reviewer`, on another model (Codex if the company has it) | Review 2: a second review of the updated change |
| 5 | Release Owner | `release-owner` | Owns Merge and Release: sees the PR through the merge queue and into a release |
| 6 | Security | `security-engineer` (Security Engineer) | Tests each release build; holds a release only for serious problems (below) |
| 7 | Technical Writer | `technical-writer` (Technical Writer) | After each release, updates the help docs for the changes people will see and writes the release notes |
| 8 | DevOps | `devops-engineer` (DevOps Engineer) | Failed builds and pipeline problems; watches the factory's cost and speed |

The **Feature Owner** (`feature-owner`, the Product Owner and Builder templates in one) owns an area of the product,
or a customer segment, and takes each feature from a pitch of 280 characters to production in one task: a person OKs
the pitch, the spec and the release, and it writes the spec, the code, the fixes and the proof pages itself. The
Builder template stays in the catalog for teams that want one, but no default chart has a Builder. The **Product Manager** (`product-manager`) and the **Engineering Manager**
(`engineering-manager`) each lead their team's bots and keep their pipeline's tasks moving: when one stops, the Manager
reassigns, moves back, moves on, snoozes or closes it, and never does a stage's work. A small team can
let one bot cover Security and DevOps. Start with a Feature Owner, Reviewer 1 and Reviewer 2, and add the
rest as your release pace grows.

The starter templates were written for a person-in-the-loop team: a reviewer drafts a review for a human to post,
for example. Edit each bot's instructions to match its role here, or ask BotOps to do it. When reviewers
are authorized to submit their own reviews, configure a [separate reviewer GitHub App](github-app.md#separate-identity-for-reviewers)
and use `tico repo review` with the full reviewed head and `approve` or `changes`. Record the GitHub receipt on
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

1. **Product decides what, engineering decides how.** Feature ideas go to the Product Owner first. Bugs and small
   changes go straight to Engineering as a task.
2. **Prefer different models for checks.** Reviewers and QA prefer a bot of a different model company than the
   Builder. Set this per bot (its task model, or its model), or use a check model on a sub-task
   (`tico task child <task> --check-model`).
3. **Reviewers advise; the Builder decides.** The Builder answers each comment it doesn't take with a one-line
   reason on the task. There is no second round of the same review after fixes.
4. Validation skills attach evidence (tests, screenshots) to the task; a person OKs each release from its notes and
   before/after screenshots.
5. **No person in the loop by default**, except where Tico requires one: publishing, spending, sending and hiring.
   Those are OKs a person gives on the task ([Asks and OKs](tasks.md#asks-and-oks)).

## A change, from task to merge

The Builder opens its PR, links it to the task and pushes freely; Build is done when the PR is open and its checks
pass. Each review is a Review page for the PR's newest commit, attached to the task with
`tico task attach <task> page:<file id> --req "<requirement>" --version N`; Jev checks it is a real review. A push
after a review needs a page for the new head. In Fixes 2 the Builder fixes what Review 2 found and says done (at once, when it found nothing). In QA it
attaches the QA page (what changed, before and after screenshots or other proof, the checks at the head and what both
reviewers found), Jev checks it and a person OKs it, which ships the change. On entering Merge, Tico queues the PRs in the repository's merge queue
([Merge queue](releasing.md#merge-queue)); no bot runs a merge command. If GitHub drops a PR from the queue, the
Release Owner pushes a fix to the PR's branch or queues it again with `tico task queue <task> --version N`. The task
leaves Release once the merge is in a published release (a repo that publishes no releases: once merged).

## Visual specs

A spec can be a visual page. The Product Owner publishes it on the task it describes with
`tico page publish <file.html> --spec --task <task>`. Tico's styles are inlined into the page at publish, so it
reads as Tico does. Every task filed under that task, and their own sub-tasks, gets the spec's page link and text in its body.
A spec that is narrowed to some readers, or on a private task, gives the link only.

Every page a person reads to decide is built the same way, with the `build-the-page` skill
(`templates/catalog/feature-owner/skills/build-the-page/`; the Reviewer, Builder and Release Owner templates
carry the same copy). Seven templates: a bug spec (the report, the root cause, the goal, then the fix before and
after), a feature spec for a screen and one for a change with no screen (the goal, then the change before and after),
a Review page, the Feature Owner's QA page (the result and what was not checked, then each acceptance criterion with its
result and proof), the release report for a batch (a changelog for customers with a screenshot per change, then why
we are confident, the plan and at most three risks), and the Checked in production page. Each opens with what it is
and what to decide in two or three lines, then real
screenshots of the app at phone (390 px) and desktop width with red numbered arrows, then the details. Screenshots are
never drawn: production through the read-only view (`tico bot app-shot`), and the branch through the demo
(`scripts/release_shots.py`). The skill's `page.py` embeds the pictures, checks the page against those rules, and draws
the published copy at both widths, which the bot looks at before it attaches the page. In Validate, the Product Owner
does the checking itself; a person never does. Existing bots take the skill and their playbooks at their next template
refresh ([Bots follow their template](updates.md#bots-follow-their-template)).

## When Security holds a release

Security holds a release only for:
- secrets exposed in code or logs
- a way around sign-in or permissions
- one customer seeing another's data
- an exploitable injection or similar hole
- a new dependency with a known critical flaw

Everything else becomes a follow-up task. The Engineering Manager may release anyway, with a written reason on the
release.

## What each release carries

The Release Owner keeps one checklist per release:
- a QA pass for each change people will see
- the security result
- the release notes

A private task is left out of the release notes and docs unless it has a public title, which is used in its place.
Nobody is asked to name a private task.

## After launch

For features with a success measure, the Customer Insights Analyst reports "did it work?" 14 days after launch. The
Product Owner uses that report at the next triage.
