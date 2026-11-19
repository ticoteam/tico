---
name: plan-before-coding
description: Turn an approved spec into a short engineering plan before any code is written. Trace everything the change touches, compare two different designs, settle open questions by looking them up or running a throwaway prototype, put only real decisions to a person (each with a recommended answer), and split the work into small pull requests that each carry their own proof. Use at the start of a Build stage, or when a task says plan, design, architect, approach, tech spec, implementation plan, break this down, how should we build this, refactor, migration or rewrite. Also use when a change touches more than two files, a data model, a public API or sign-in, or when two attempts at the same change have already failed. Skip it for a one or two file change with an obvious approach.
metadata:
  function: engineering
  roles: [feature-owner, builder, software-architect, engineering-manager]
  actions: [read, draft]
  review: none
---
# Plan before coding

When to use: a task has an approved spec (what to build and why) and nobody has yet decided how to build it.

Words used here: a **PR** is a pull request, one reviewable change to the code. A **one-way door** is a change that is
hard or impossible to undo once it ships. A **prototype** is throwaway code on a branch that is never merged, written
only to answer a question.

Tico words: a **task** is one piece of work with an owner. A **pipeline** moves a task through **stages**. A **page**
is an HTML file a bot publishes onto a task for people to read. An **OK** is a named person's approval, requested
with an ask. Commands that change a task take `--version N`, the number that `tico task show <id>` prints.

## What great looks like

A bot that has never seen this codebase could build the change from the plan, and would build the same thing the
planner had in mind. The plan holds only the decisions a builder cannot make alone: where the new behaviour lives,
which piece owns each bit of state, the names, the order of the PRs, and how each PR is proven. It is shorter than the
code it describes.

Every open question is closed before the build starts. Facts were looked up or tested, never asked. Decisions were put
to a person once, in one message, each with a recommended answer. The first PR can land the same day, and no PR is too
big to review in 15 minutes.

## First time at a company

Read `knowledge/engineering.md` in your own repository. If it has a "Planning" section, skip this. If not, ask the
owner these in one numbered message and save the answers under "Planning" with the date. Start work on the defaults;
do not wait.

1. Is there a map of our systems (services, data stores, outside services)? Default: none. Build
   `knowledge/system-map.md` as you trace, one section per system, and add to it on every plan.
2. Which changes are one-way doors that need your OK before the build starts? Default: deleting or rewriting stored
   data, changing a public API, webhook or file format, changing sign-in or permissions, changing how money is
   charged, and a paid outside service above budget.
3. How big may one PR be? Default: what a reviewer can read in 15 minutes, which is about 400 changed lines not
   counting tests and generated files. Above that, split.
4. Who answers product questions that come up while planning? Default: the Feature Owner on the task. A question that
   would change the approved spec goes to the person who gave the spec its OK.
5. Does a plan need a person's OK before building? Default: no. The plan is a page on the task and the build starts
   at once, unless the plan contains a one-way door.

## Steps

1. **Read the task and the spec.** Run `tico task show <id>`. It lists the task's pages; open the spec page with
   `tico file read <file id>`. Write the goal as one sentence and copy the acceptance criteria (the checks that say the
   work is done). If the spec has no acceptance criteria, that is a product gap, not yours to fill: ask the Feature
   Owner with `tico task ask <id> --to <feature owner> "<the question, with the criteria you would propose>" --version N`.
2. **Decide whether to plan at all.** One or two files and an obvious approach: write three lines as a task comment
   (what changes, which files, how it is proven) and start building. Anything bigger gets the steps below.
3. **Trace what the change touches.** Follow the real path through the code, not the folder names: the entry point
   (screen, API route, job or command), every function it passes through, every store it reads or writes, and every
   consumer of whatever you will change. Consumers include other callers, background jobs, exports, API clients,
   mobile apps on old versions, and code in another language reading the same data. Write each as `file:line`. Add
   what you learn to `knowledge/system-map.md`.
4. **Sketch two designs that really differ.** Differ in where the state lives or where the boundary sits, not in
   naming. For each, write the types or record shapes, the function signatures, and which module owns what. Choose
   with these tests, in order:
   - Each piece of state has exactly one owner.
   - There is one way to do each task, so the next bot cannot pick the wrong one.
   - Callers need to know the least.
   - It can be undone. Prefer a feature flag, an additive change, or a staged rollout over a switch-over.
   - It is boring. A plain, well-known approach beats a clever one.
   If the design needs the same workaround in three places, the shape is wrong: redesign instead of patching. If the
   existing code makes the change hard, plan one PR that reshapes the code with no behaviour change, then the PR
   that changes behaviour. Never mix the two.
5. **Close every open question.** List them and sort each into fact or decision.
   - A fact is anything a lookup or a run can settle: what a library does, whether a query is fast enough, what an
     outside API returns. Settle it yourself. Read the source, or write a prototype and record the command and its
     output in the plan. Never ask a person for a fact.
   - A decision is a product call, a preference, or how much risk to take. Put all of them to the right person in
     one numbered message, each with your recommended answer worded so that "yes" accepts it. For decisions that can
     be undone, carry on with your recommended answer and note it. Wait only for one-way doors.
6. **Split the work into PRs.** Each PR makes one change, passes its checks alone, and leaves the product working.
   List them in landing order. For each PR write:
   - what it depends on,
   - the files it may touch,
   - the one change it makes,
   - the result someone can see when it lands,
   - its proof: the behaviour its first failing test will name (see `test-first`), the live check with a concrete
     scenario and what counts as a pass, and for speed-sensitive work the metric, how it is measured, today's number
     and the number that would fail.
   A change to stored data is always at least three PRs: add the new shape, move the data and the readers, remove
   the old shape. Old and new code must both work at every step.
7. **Name what the plan does not prove.** Under "Review focus", list the three to five inputs or failure modes the
   spec implies but no PR tests (for example: a very large account, two people saving at once, an expired session).
   Under "Undo", say how each PR is reversed and which ones cannot be.
8. **Check the plan against itself.** Every acceptance criterion maps to a PR. No line says "to be decided",
   "handle edge cases" or "add tests" without naming the case or the test. Names and types match across PRs. If the
   plan is longer than the code will be, cut it.
9. **Publish and start.** Build the plan as a page (use the shared `build-the-page` skill if you have it) with a
   diagram of the systems touched. Publish with `tico page publish plan.html --task <id> --title "Plan: <goal>"`.
   If the plan holds a one-way door, ask for the OK now:
   `tico task ask <id> --to <owner> --kind ok "Plan for <goal>: OK to <the one-way door>?" --show page:<file id> --version N`.
   Otherwise start the first PR straight away.
10. **Keep the plan true.** When the build shows the plan was wrong, change the plan page first (publish again with
    `--page <file id>`), then the code. A plan nobody updates is worse than none.

## Gates

- No code on a branch meant for merging until the plan exists, for any change above the size in step 2. Prototype
  branches are exempt and are never merged.
- Every PR in the plan has a proof that can fail. "It builds" and "looks right" are not proofs.
- The plan never changes what the spec asks for. If the best design needs a different spec, stop and take it to the
  Feature Owner. Product decides what; engineering decides how.
- A one-way door does not start without its OK.
- Two designs or none. A plan that shows only the design you thought of first has skipped the step that finds the
  better one.
- A question that a run could answer is never sent to a person.

## Autonomy

Alone: read the code, trace, write and delete prototypes, choose the design, split the PRs, publish the plan, start
the build, and change the plan as you learn.

Needs an OK: a one-way door from the list in `knowledge/engineering.md`; spending above budget; a
change to the approved spec (the Feature Owner, or the person who gave the spec its OK).

## Output

- One **Plan page** on the task: the goal in one line, a diagram of the systems touched, the two designs side by side
  in a table with the choice and the reason, the PR table (order, depends on, files, change, visible result, proof),
  decisions made with the recommended answers, "Review focus" and "Undo".
- New or changed sections in `knowledge/system-map.md`.
- For a decision that is expensive to reverse, a short decision record. If your role has the skill
  `write-an-adr-from-a-decision`, use it; otherwise write the decision, the options and the reason as five lines in
  `memory/decisions.md`.

## Checks before you finish

- Could a bot with no access to you build this from the page alone?
- Does each acceptance criterion point at a PR, and each PR at a proof that can fail?
- Is every consumer found in step 3 either unchanged or covered by a PR?
- Did every fact get settled by a lookup or a run, with the evidence in the plan?
- Is each remaining question a real decision, with a recommended answer?
- Is any PR over the size limit, or doing two things?

## Example

Invented example.

Task 412, "Let a customer pause a subscription for up to 3 months". Spec approved, four acceptance criteria.

Trace: the billing job `jobs/renew.py:41` reads `subscriptions.status`; so do the customer page, the admin export
`exports/subs.py:88`, and the mobile app through `GET /api/subscription`.

Two designs. A: add a `paused` value to `status`. B: keep `status` and add a `paused_until` date. The trace decides
it: A adds a new status value that the export and old mobile versions do not know, so each consumer needs a change.
B leaves every consumer working and has one owner for the pause (the date). Choose B.

Fact settled by prototype: "does the renewal query stay fast with the extra condition?" Ran the query plan on a copy
of the table with 200,000 rows; it used the existing index. Command and output pasted in the plan.

Decision put to the Feature Owner: "1. A paused customer keeps read access to past orders. Recommended: yes."
Carried on with yes.

PRs: (1) add `paused_until` column, nullable, no behaviour change; (2) renewal job skips rows where `paused_until`
is in the future, with the test "a subscription paused until next month is not charged today"; (3) pause and resume
API with permission check; (4) the customer screen. Review focus: pause on the renewal day itself, pause that ends
on the 31st, two pause requests at once. Undo: PRs 2 to 4 revert cleanly; PR 1 leaves an unused column.

No one-way door, so no OK was needed and PR 1 opened the same hour.
