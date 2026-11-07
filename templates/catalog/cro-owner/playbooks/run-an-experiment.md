# Run an experiment

Use when a proposal in `knowledge/proposals/` is ready to test, or when a task asks for a test. Budget 60 minutes for the
brief. The test itself runs for its planned weeks.

Outcome: a brief in `knowledge/experiments/<slug>.md` that the page owner can approve, and a Marketing task for it, with
the test's status tracked until its read-out.

Done when the brief has every field below, the page owner has approved or declined it in a task, and the file states
whether anyone started the test.

---

## 1. Check the page is testable

Read `knowledge/pages.md`. Stop if the page is on its never-test list, if the change touches a price shown to an existing
customer or any legal text, or if the page is a checkout step. Say why in a task to the page owner.

## 2. Size it

Follow `skills/size-a-test-before-it-starts/SKILL.md`. If the run needs more than six weeks, the page is too small for this
test. Say so and propose a bigger change or another page. Do not write a brief for a test that cannot finish.

## 3. Write the brief

Write these fields into `knowledge/experiments/<slug>.md`:
- Hypothesis: "If we show X to Y, then Z changes, because W", with the evidence and its count and window.
- Page and segment.
- Primary metric: one step, its exact definition and the event that counts it.
- Baseline: the rate, its count, its window and the export date.
- Smallest change worth shipping, in points.
- Planned n per arm, eligible entrants a week, planned weeks.
- Guardrails: one or two metrics that must not get worse, each with its limit.
- Stop rules: the planned end; the sample ratio check; the guardrail limits; the one reason to stop early, a broken page.
- Decision: who reads the result, and who applies a winning change.
- Status: `proposed`, `approved`, `running` or `read`.

## 4. Hand it to the page owner

Open the Marketing task with the brief's path in the body:

    hub task create --owner me --type idea --label channel:<slug> --title "..." --body "..."

Ask the page owner whether to start it:

    hub task question "Start the test as briefed?" --on <owner> --parent <id> --option "Start it -> the owner starts it in the testing tool and the status moves to running" --option "Change the brief -> I revise the sample and the run time"

Never start or stop a test yourself unless the task asks for it and a granted Tool allows it.

## 5. While it runs

Each Monday, read the count in each arm, the sample ratio check and the guardrails. Do not show the primary metric's rate or
difference before the planned end. When the owner confirms the test has started, move the task:

    hub task update <id> --step Live

## 6. At the planned end

Follow `playbooks/weekly-conversion-check.md` step 6 and `skills/read-a-test-without-peeking/SKILL.md`. Set the outcome with
`hub task update <id> --step <Succeeded|Failed|Inconclusive>`. A test stopped early is `Dropped`, with the reason in its
note.

## 7. Learn

Record the result for the page in `knowledge/pages.md`, and what the test taught about the job in `memory/learnings.md`. A
result that suggests a page belongs on the never-test list is a question to the page owner, not an edit.
