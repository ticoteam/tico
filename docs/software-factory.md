# The software factory

Tico's recommended way to build software with a team of bots. It covers how an idea, a bug or a small change becomes a
reviewed, tested, documented and released change. Each step has a named role, and a person is involved only where
one has to be. Tico Team builds Tico this way. This page is a recommendation, not a rule the server enforces: rename
the roles, drop the ones you don't need, and change the steps in Settings → Tasks.

## The roles, in order

| # | Role | Starter template | What it does |
|---|---|---|---|
| 1 | Builder | `pr-reviewer` (Senior Software Engineer), set to build | Builds the change, does every fix step, merges under the Head of Engineering's rules |
| 2 | Reviewer 1 | `pr-reviewer`, preferably on a different model company from the Builder | Technical review: bugs and correctness, as comments on the ticket |
| 3 | Reviewer 2 | `pr-reviewer`, on a third model if you have one | A second review of the updated change, only for larger or risky changes |
| 4 | QA | `issue-triage` (QA Engineer) | Runs the acceptance tests before release, and again live after release |
| 5 | Release Manager | `release-notes` (Release Manager) | Cuts the release, rolls back if needed |
| 6 | Security | `security-engineer` (Security Engineer) | Tests each release build; holds a release only for serious problems (below) |
| 7 | Technical Writer | `docs-writer` (Technical Writer) | After each release, gets one task listing its tickets, updates the help docs for the changes people will see and writes the release notes |
| 8 | DevOps | `devops-engineer` (DevOps Engineer) | Failed builds and pipeline problems; watches the factory's cost and speed |

Two leads sit above the line. **Head of Product** (`product-lead`) decides *what* to build: it triages feature ideas,
writes the spec and acceptance tests, and keeps the roadmap. **Head of Engineering** (`engineering-lead`) decides
*how*: it splits an approved spec into Dev tickets, picks the Builder and sets the merge rules. A small team can
skip Reviewer 2, or let one bot cover Security and DevOps. Start with the Builder, Reviewer 1 and QA, and add the
rest as your release pace grows.

The starter templates were written for a person-in-the-loop team: a reviewer drafts a review for a human to post,
for example. Edit each bot's instructions to match its role here, or ask BotOps to do it. When reviewers
are authorized to submit their own reviews, configure a [separate reviewer GitHub App](github-app.md#separate-identity-for-reviewers)
and use `hub repo review` with the full reviewed head and `approve` or `changes`. Record the GitHub receipt on
the review task as well. Required GitHub approvals are dismissed on push, so a changed head needs a current
approval before merge even when the team's task review policy allows a fixes-only delta.

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
  - drop Reviewer 2
  - run the reviewers on lower-effort or cheaper models, in a different family from the Builder where one is available
  - don't cut the Builder

## Rules that make it work

1. **Product decides what, engineering decides how.** Feature ideas go to Head of Product first. Bugs and small
   changes go straight to Head of Engineering as a Dev ticket.
2. **Prefer different models for checks.** Reviewers and QA prefer a different model company from the Builder;
   when none is free or healthy, a same-family checker takes the work. Tico records the configured and executed
   families on the check. Set this per bot (its model), or use a check model on a subtask (see [Tasks](tasks.md)).
3. **Reviewers advise; the Builder decides.** The Builder answers each comment it doesn't take with a one-line
   reason on the ticket. There is no second round of the same review after fixes.
4. **QA runs tests it didn't write.** Head of Product writes the acceptance tests in Given/When/Then form. Each is
   tagged *check live*, *watch live* (unsafe to trigger in production: payments, sending, deleting, third-party
   actions) or *not checkable live*.
5. **No person in the loop by default**, except where Tico requires one: publishing, spending, sending and hiring.
   Each release asks a person for one approval, covering its roadmap moves, release notes and docs.
6. **A blocked ticket stays at its step** and says why it is blocked and who it waits on.

## Dev tickets: the steps

In Settings → Tasks, rename the default **Engineering** type to **Dev tickets** and replace its steps with these.
Renaming keeps the type with Head of Engineering, so a roadmap item follows its Dev tickets (below), and so does a type
you create named Dev tickets. Move any tasks off a step before you delete it or change its status.

| Step | Status | Moved there by | What happens |
|---|---|---|---|
| On Deck | open | Head of Engineering | Ready to build, Builder picked |
| In Progress | doing | Builder | Building |
| Review 1 | review | Builder | Reviewer 1 comments |
| Review 1 Fixes | doing | Builder | Builder acts on the feedback (skipped if there is none) |
| Review 2 | review | Builder | Reviewer 2 comments on the updated change (skipped for small changes) |
| Review 2 Fixes | doing | Builder | Builder acts on the feedback, then merges |
| In QA | review | Builder | QA runs the acceptance tests |
| QA Fixes | doing | QA | A test failed; the Builder fixes it and moves the ticket back to In QA |
| Pending Release | review | QA | Passed; waits for the next release, where Security tests the release build. Arriving here is a note to the Release Manager, not a wake |
| Post Release QA | review | Release Manager | Shipped; QA runs the *check live* tests and watches the *watch live* items for 48 hours |
| Done | done | QA | Post-release QA passed |
| Closed | closed | Head of Engineering | Closed without shipping, e.g. a duplicate, superseded or dropped ticket |

Done and Closed fold into one strip at the end of the board: those tickets are under the Done and Closed tabs. A waiting
ticket stays at its step with a Waiting tag.

- **Small changes** skip Review 2. Use Reviewer 2 for sign-in, permissions, customer data, billing and anything that
  sends.
- **Changes nobody will see** skip In QA and QA Fixes.
- **A failure after release** goes back to QA Fixes for a hotfix, or the Release Manager rolls back.
- **Reviewers and Security only comment.** They never move a ticket: a reviewer or Security bot that tries is refused
  and told to finish its review task with a verdict (`hub task done <id> --verdict approve|changes --head <sha>`). QA
  gives `pass` or `fail`. The Builder is woken once with the verdict and the head it judged, even while the ticket
  waits on someone else's open subtask; a verdict given while the ticket is Blocked moves it once the hold clears.
- **One check per head.** A ticket has one open Review 1, Review 2 or QA subtask for a pull request head; a second request
  for the same check on the same head is refused with the open one's id.
- **Tico files the check.** Each check step has a pool of bots, set on the step in Settings → Tasks. Entering the step
  files its check to a free pool bot, preferring another model company than the Builder, or leaves it "Waiting for a
  reviewer" until the first one finishes a check ([Tasks](tasks.md#check-pools-who-takes-a-tickets-check)).
- **Moving a step wakes nobody unless a rule says so.** A Dev tickets type comes with three editable rules: **QA Fixes**
  wakes the Builder, **In QA** wakes QA only, and **Pending Release** is a note to the Release Manager. Change them in the
  Rules page (`hub rule list --scope-kind type --scope-id <type>`).
- **A ticket at a working step (In Progress or a Fixes step) shows its age at that step** on its card (`step_since`
  on the task), amber past 4 hours. Waiting time is not counted: the clock restarts when the ticket comes back to
  work. After 2 hours with no activity (no update, checkpoint or message), its bot owner is woken once for a status,
  even when the ticket stands as at its last checkpoint and the plain status check would be held, and unless it waits on
  something recorded (a question, a snooze, a due date, a blocker, a subtask) or a run of it is going. After 4 hours at
  the step, the type's manager (Head of Engineering), or else the owner's manager, is woken once to unblock or reroute
  it. Each happens once per stay at the step. A step's flow sets its own hours (`status_hours`, `late_hours`), on any
  board; a Dev step without them uses 2 and 4.
- **A turn that ends and leaves its ticket in progress with nothing queued wakes its owner once.** Ten minutes after
  the owner's last run on a working ticket, or on an open check below it, finished with nothing live, queued or held
  after it and nothing recorded to wait on, the owner is woken once per stay at the step, a reviewer included.
- **Head of Engineering works by exception.** On the **Manager** profile it has no step on any ticket: a ticket's
  steps, its subtasks' results, checks, verdicts and late or backlog notices are notes for its next run, not wakes.
  About hourly (Settings > Routines, *Engineering exceptions*) Tico builds the board's exceptions list and wakes it
  only when the list has an entry the last one did not:
  - a ticket, or the open check below it, with no live, queued or held run for 45 minutes and nothing recorded to wait
    on, or at a check step with no check filed and none waiting, with its owner and since when;
  - a run on it that failed, was cut or is uncertain, with no later run that finished (one entry however many cuts);
  - a check waiting for a reviewer for 45 minutes, or at once when the step's pool is empty;
  - a builder over its step's slots, or a step over its backlog;
  - outages: GitHub unreadable at a review move (one entry for every ticket it holds), a pool bot crashed, limited,
    quarantined or offline;
  - a refused move Tico stopped retrying, or one rule refusing a ticket twice in a day with no retry pending;
  - an open pull request that conflicts, with its number and head.

  It reads the list any time with `hub pipeline` (`exceptions`), and fixes the cause by hand: moves a ticket
  (`hub task update <id> --step ...`), reassigns it (`--owner`) or closes a duplicate. People and tasks given to it
  still wake it at once. On the Lead profile it keeps the late and backlog notices instead, and gets no list.
- **Bots move a ticket by its step** (`hub task update <id> --step "Review 1"`), not by its status. Several steps
  share a status, and setting a status lands on the first step that has it.

## The roadmap

Feature ideas live in the **Product roadmap** type: Suggested → Exploring → Spec → Approved → Building → Ready to
ship → Shipped (or Declined). Head of Product moves them. A roadmap item's Dev tickets are its children or the
tickets it is blocked by, and the item moves to Building when the first one is In Progress, Ready to ship when every
one is at Pending Release or later, and Shipped when every one is Done ([Public roadmaps](roadmaps.md) has the
rule). In Settings → Tasks, give Spec and Approved the public step name "Planned", and Building and Ready to ship
"In progress"; otherwise each step shows publicly under its own name. Shipped goes out with the release notes, after
post-release QA. Publishing a roadmap is covered in [Public roadmaps](roadmaps.md).

## When Security holds a release

Security holds a release only for:
- secrets exposed in code or logs
- a way around sign-in or permissions
- one customer seeing another's data
- an exploitable injection or similar hole
- a new dependency with a known critical flaw

Everything else becomes a follow-up Dev ticket. Head of Engineering may release anyway, with a written reason on the
release.

## What each release carries

The Release Manager keeps one checklist per release:
- a QA pass for each change people will see
- the security result
- the release notes
- the roadmap items moved to Shipped

## After launch

For features with a success measure, the Customer Insights Analyst reports "did it work?" 14 days after launch. Head
of Product uses that report at the next triage.
