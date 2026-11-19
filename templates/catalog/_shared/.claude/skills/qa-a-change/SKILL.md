---
name: qa-a-change
description: QA a change before it is released by using the product the way a real customer would, on the exact build that will ship, and report with evidence what works, what is broken and what was not checked. Sets up and keeps a project-local verify skill (how to launch, check, drive and clean up the app), runs each acceptance criterion on the real flow with before and after screenshots, explores odd paths with short timed charters (empty states, slow network, phone size, double submits, wrong permissions), turns every bug into a failing test, and checks the change again in production after release. Use when a task is in a QA or Validate stage, or says QA, test this, verify, validate, smoke test, exploratory test, regression check, acceptance test, QA report, try the build, or does it work.
metadata:
  function: engineering
  roles: [qa-owner, feature-owner, builder]
  actions: [read, draft]
  review: none
---
# QA a change

When to use: a change has passed review and someone must find out whether it really works before customers get it.

QA has one job: lower the chance that a problem reaches a customer, and raise how much of the change has been proven
to work. A QA pass that found nothing and checked little has done neither.

Words used here: a **build** is the product made from one exact commit. A **SHA** is that commit's id. A **verify
skill** is a small skill kept in the product's own repository that says how to launch, check, drive and clean up the
app. A **charter** is a one-line mission for a short, timed session of exploring. A **probe** is one deliberate try.

Tico words: a **task** is one piece of work with an owner. A **pipeline** moves a task through **stages**. A **page**
is an HTML file a bot publishes onto a task for people to read. An **OK** is a named person's approval, requested
with an ask. Commands that change a task take `--version N`, the number that `tico task show <id>` prints.

## What great looks like

The tester uses the real product on the real flow, the way a customer would, on the exact build that will be
released. Every claim in the report points at something a reader can open: a screenshot, a command's output, a
record id. The report says plainly what works, what is broken, and what was not checked and why.

The odd paths get tried on purpose, not by luck. Every bug found becomes a failing test before it is fixed, so it
cannot come back unseen. "Could not check" is never written up as a pass.

## First time at a company

Read `knowledge/engineering.md` in your own repository. If it has a "QA" section, skip this. If not, ask the owner
these in one numbered message and save the answers under "QA" with the date. Work on the defaults meanwhile.

1. How do I start the product from a clean state, and which test accounts and data may I use? Default: use the verify
   skill in the product's repository; if there is none, write one (step 2) from the README and CI config. Use test
   accounts only, never a real customer's.
2. Which three to five customer flows must never break? Default: sign up and sign in, the main thing the product is
   for, and paying. Work the list out from the product's menus and docs, save it, and show it to the owner.
3. Which screen sizes and browsers count? Default: phone at 390 px wide and desktop at 1280 px wide, in Chromium.
4. Which bugs must be fixed before release? Default: critical, high and medium. Low ones are listed and left.
5. May QA touch production? Default: read-only looks only. Anything that writes data, sends a message or spends money
   happens on a preview or staging build with test accounts.
6. How long may exploring take? Default: 15 minutes or 12 probes per charter, whichever comes first, and two to five
   charters per change.

## Steps

1. **Read what was promised.** `tico task show <id>`. Read the spec, the acceptance
   criteria, the PR diff and the Review page. Start the QA plan as a table: one row per acceptance criterion, with
   how you will check it and what evidence you expect. Add rows for nearby things the diff could break; the Review
   page's risk list is the place to start.
2. **Get the verify skill ready.** Look in the product repository's skill folder (`.claude/skills/verify-<app>/` for
   Claude Code, `.agents/skills/verify-<app>/` for Codex). If it is missing, write it from
   `references/verify-skill-template.md` and prove it once end to end. If it exists, run its Doctor check. If Launch
   or Drive no longer match the app, fix the verify skill. Edit only that skill's folder, never product code, and
   say in the report that you changed it.
3. **Pin the build.** Record the full SHA of the PR head and where the build is running (preview address or local).
   Confirm with Doctor that the running version is that SHA. Every result in the report belongs to this SHA only.
4. **Capture the "before".** Take screenshots or outputs of the affected flows as they are today, at 390 px and
   1280 px. For the live product use `tico bot app-shot`, which takes screenshots at both sizes and changes nothing.
5. **Walk the happy path as a customer.** For each acceptance criterion, do it through the screen or the public API,
   starting where a customer starts. Do not set internal state to skip ahead. Capture the action and the state it
   produced: the screenshot after, the record read back, the message that was sent. Then check it lasted: reload,
   sign out and in, look at the record again.
6. **Explore the odd paths with charters.** Write two to five charters using `references/charters.md`. Each names an
   area, an odd condition and the risk you are hunting, with a time limit. Always cover: an empty state, a slow or
   dropped network, phone size, a second submit or double click, and a user without permission. During a session:
   - First show the thing working, so a later failure means something.
   - Before each probe, write the guess and the next action. One probe at a time.
   - When something fails, replay the exact steps from the same starting state before calling it a bug.
   - Then strip the steps down to the fewest that still fail.
7. **Sweep for regressions.** Run the must-never-break flows from `knowledge/engineering.md` and the neighbours of
   the diff. A flow the change did not touch but shares code with is a neighbour.
8. **Write up each bug so it can be fixed without you.** Severity, exact steps from a known starting state, what was
   expected, what happened, evidence, and the SHA.
   - Critical: data lost or exposed, or a must-never-break flow blocked.
   - High: a main flow gives a wrong result for many customers.
   - Medium: wrong, but there is a way round it.
   - Low: looks wrong, works right.
   If an automated check failed, sort it first: product bug, flaky test, test defect, environment, or dependency.
   (Skill `classify-a-test-failure` if your role has it.) File each product bug as a child task:
   `tico task file --parent <id> --owner <builder> "Bug: <what the customer sees>"`.
9. **Every bug becomes a test before it is fixed.** The fix follows `test-first`: a test that fails on the bug, then
   the smallest fix, one commit per bug. Without that skill: do not accept a fix unless a test exists that fails on
   the old commit. After a fix, check three things on the new SHA: the bug's own steps, the original flow, and one
   neighbouring flow.
10. **Pin again if the build moved.** A new commit after your checks means your results describe an older build.
    Rerun the happy paths and everything the changed files touch, and mark which results came from which SHA.
11. **Publish the QA report.** Build the page (shared `build-the-page` skill, kind `qa`, if you have it) and publish:
    `tico page publish qa.html --task <id> --title "QA: <change>" --as "QA report"`, then attach it to the stage's
    requirement with `tico task attach <id> page:<file id> --req "<requirement>" --version N`. Give one verdict:
    **Pass**, **Pass with low issues listed**, or **Fail**. Then move the task: forward with
    `tico task move <id> next -m "<verdict and SHA>" --version N`, or back to the fixing stage (named Fixes in
    Tico's Product pipeline) with `tico task move <id> <stage name> -m "<bug list>" --version N`.
12. **Check it in production after release.** When the task reaches its Validate stage, repeat the happy path as
    read-only looks at the released version (`tico bot app-shot` at 390 px and 1280 px), confirm the version shown
    is the released one, and publish the result as the validation page.
13. **Make the next QA cheaper.** Add what you learned to the verify skill's feature files and to the charter list.
    A bug that got past review, or the same kind of bug found twice, goes to `turn-corrections-into-gates`.

## Gates

- A result counts only for the SHA it was seen on.
- A missing prerequisite, a timeout, a tool that refused, or a flow you could not reach is "not checked". It is
  listed with the reason and never counted as a pass.
- Never weaken a journey, a test or a check to make it pass. Never edit CI.
- Any critical or high bug still open means the verdict is Fail.
- Screenshots are real captures of the running build. Never drawn, never from an older build.
- The report never asks its reader to test anything. If something needs testing, QA tests it.
- No real customer data, and no writes to production.
- If two fixes for the same bug both fail, stop the fix loop and hand the bug to `debug-and-handle-incidents`.

## Autonomy

Alone: launch the build, test, write and update the verify skill, file bugs, give a Fail verdict, send the task back
for fixes, and move it forward on a Pass.

Needs an OK: the stage's own ask, when the pipeline names a person to OK the QA report
(`tico task ask <id> --to <person> --kind ok "QA report for <change>: OK?" --show page:<file id> --version N`); any
check that would send a real message to a customer or spend real money, when no test mode exists.

## Output

One **QA report page** on the task:

- The verdict, the SHA and the build's address, in the first three lines.
- Before and after screenshots side by side at 390 px and 1280 px, with red numbered arrows on what changed.
- The coverage table: each acceptance criterion, how it was checked, result, link to evidence.
- Charters run, with what each found.
- Bugs, worst first, each with its child task.
- "Not checked", with reasons.

Also: the verify skill in the product repository (new or updated), and child tasks for the bugs.

## Checks before you finish

- Does every "pass" row link to evidence captured on the pinned SHA?
- Did you act as a customer would, through the screen or public API, for every acceptance criterion?
- Did at least one charter cover each of: empty state, slow network, phone size, double submit, no permission?
- Does every bug have steps that work from a known starting state, and a failing test before its fix?
- Is the "Not checked" list honest and complete?
- Would a person who reads only the first three lines know whether this can ship?

## Example

Invented example.

Task 412, PR head `a41c9e7`, preview build. Change: "pause a subscription for up to 3 months".

Doctor: preview reports version `a41c9e7`. Before shots of the subscription screen taken at both sizes.

Happy path: signed in as test customer, pressed Pause, chose 2 months. After shot shows "Paused until 18 Jan".
Reloaded, signed out and in: still paused. Read the record through the API: `paused_until` is set. Triggered the
renewal job on the preview for that account: no charge created. Four of four criteria pass.

Charter 1: "Explore pausing on phone size with a slow network, to find double submits." Third probe: pressing Pause
twice while the first request is slow creates two pause records, and the screen shows the later end date. Replayed
from a fresh test account: same result. Fewest steps: slow network, two presses. Severity medium. Filed child task
"Bug: pressing Pause twice pauses for longer than chosen".

Charter 2: "Explore pausing as a teammate without billing permission." The button is hidden and the API returns 403.
No bug.

Fix arrived as `c07d2b1` with a test that failed on `a41c9e7`. Re-ran the bug's steps, the happy path and the resume
flow on `c07d2b1`: all pass.

Report: Pass on `c07d2b1`. Not checked: Safari, because the default list is Chromium only.
