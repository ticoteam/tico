# The software factory

Tico's recommended way to build software with a team of bots. It covers how an idea, a bug or a small change becomes a
reviewed, tested, documented and released change. Each step has a named role, and a person is involved only where
one has to be. Tico Team builds Tico this way. This page is a recommendation, not a rule the server enforces: rename
the roles, drop the ones you don't need, and change the steps in Settings → Tasks.

## The roles, in order

| # | Role | Starter template | What it does |
|---|---|---|---|
| 1 | Builder | `pr-reviewer` (Senior Software Engineer), set to build | Builds the change, does every fix step, merges under the Head of Engineering's rules |
| 2 | Reviewer 1 | `pr-reviewer`, on a different model company from the Builder | Technical review: bugs and correctness, as comments on the ticket |
| 3 | Reviewer 2 | `pr-reviewer`, on a third model if you have one | A second review of the updated change, only for larger or risky changes |
| 4 | QA | `issue-triage` (QA Engineer) | Runs the acceptance tests before release, and again live after release |
| 5 | Release Manager | `release-notes` (Release Manager) | Cuts the release, writes the release notes, rolls back if needed |
| 6 | Security | `security-engineer` (Security Engineer) | Tests each release build; holds a release only for serious problems (below) |
| 7 | Technical Writer | `docs-writer` (Technical Writer) | Updates the help docs for every change people will see |
| 8 | DevOps | `devops-engineer` (DevOps Engineer) | Failed builds and pipeline problems; watches the factory's cost and speed |

Two leads sit above the line. **Head of Product** (`product-lead`) decides *what* to build: it triages feature ideas,
writes the spec and acceptance tests, and keeps the roadmap. **Head of Engineering** (`engineering-lead`) decides
*how*: it splits an approved spec into Dev tickets, picks the Builder and sets the merge rules. A small team can
skip Reviewer 2, or let one bot cover Security and DevOps. Start with the Builder, Reviewer 1 and QA, and add the
rest as your release pace grows.

The starter templates were written for a person-in-the-loop team: a reviewer drafts a review for a human to post,
for example. Edit each bot's instructions to match its role here, or ask BotOps to do it.

## Models and effort

- **Builder: use the strongest model at high effort.** Today that is Claude Opus. It writes the code and makes the
  final call on every review comment, so don't save money here.
- **Reviewers: use a different model family from the Builder.** What matters most is that Reviewer 1 and Reviewer 2
  come from model families other than the Builder's (for example GPT or Grok against a Claude Builder), so they
  catch what the Builder's model misses. The Builder still decides what to act on.
- **For reliability, turn effort up.** Higher effort on the Builder, the reviewers and QA catches more before
  release.
- **To save money:**
  - drop Reviewer 2
  - run the reviewers on lower-effort or cheaper models, keeping them in a different family from the Builder
  - don't cut the Builder

## Rules that make it work

1. **Product decides what, engineering decides how.** Feature ideas go to Head of Product first. Bugs and small
   changes go straight to Head of Engineering as a Dev ticket.
2. **Different models check each other.** Reviewers and QA run on a different model company from the Builder,
   so one model's blind spots don't pass their own review. Set this per bot (its model), or use a check model on a
   subtask (see [Tasks](tasks.md)).
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
| In QA | review | Builder | QA runs the acceptance tests; the Technical Writer updates the docs |
| QA Fixes | doing | QA | A test failed; the Builder fixes it and moves the ticket back to In QA |
| Pending Release | review | QA | Passed; waits for the next release, where Security tests the release build |
| Post Release QA | review | Release Manager | Shipped; QA runs the *check live* tests and watches the *watch live* items for 48 hours |
| Done | done | QA | Post-release QA passed |
| Closed | closed | Head of Engineering | Closed without shipping, e.g. a duplicate or superseded ticket |
| Won't do | declined | Head of Engineering | Dropped, with a reason |

- **Small changes** skip Review 2. Use Reviewer 2 for sign-in, permissions, customer data, billing and anything that
  sends.
- **Changes nobody will see** skip In QA and QA Fixes.
- **A failure after release** goes back to QA Fixes for a hotfix, or the Release Manager rolls back.
- **Reviewers and Security only comment.** They never move a ticket.
- **Bots move a ticket by its step** (`hub task update <id> --step "Review 1"`), not by its status. Several steps
  share a status, and setting a status lands on the first step that has it.

## The roadmap

Feature ideas live in the **Product roadmap** type: Suggested → Exploring → Spec → Approved → Building → Shipped (or
Declined). Head of Product moves them. A roadmap item's Dev tickets are its children, and the item moves to Building
and Shipped as they finish. In Settings → Tasks, give Spec and Approved the public step name "Planned", and Building
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
- the docs updated
- the release notes
- the roadmap items moved to Shipped

## After launch

For features with a success measure, the Customer Insights Analyst reports "did it work?" 14 days after launch. Head
of Product uses that report at the next triage.
