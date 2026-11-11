# Run an experiment

How to make a CRO bet and read it. Use it inside a CRO task in Growth Experiments: in Building, to make the real thing
for `playbooks/build-a-bet.md`; in Running, to read it for `playbooks/run-and-measure.md`. Those two playbooks own the
stage moves, the Go-live page and the Results page. This one adds what a CRO bet needs.

Outcome: a file in `knowledge/experiments/<slug>.md` with the change, its sample size and its stop rule, the numbers for
the Go-live page, and, at the end, a read-out.

---

## 1. Check the page can be changed

Read `knowledge/pages.md` and the "never change" list in `knowledge/channel-playbook.md`. Stop if the page is on it, if
the change touches a price shown to an existing customer or any legal text, or if it touches a price, plan, trial,
discount or checkout setting. Close the task with the reason, or ask the page's owner one question on it.

## 2. Pick: a test or a known fix

Follow `skills/size-a-test-before-it-starts/SKILL.md`.

- **A test** when it can finish in 4 weeks at the page's traffic. Write down n per arm, the run time and the stop rule.
- **A known fix** when it cannot. Only a well-known problem qualifies: a broken form, a slow page, a missing price. It is
  measured before and after, over the same number of days on each side. Write on the Go-live page, in plain words:
  "This is not an A/B test. The page gets too few visitors for a test to finish in 4 weeks, so we compare the weeks
  before with the weeks after." A change that is not a known fix waits for more traffic, or goes to a busier page.

## 3. Write the file

Write these into `knowledge/experiments/<slug>.md`, with the task's id:

- **The change**: exactly what changes, on which page, for which segment. A picture of it as it will look.
- **Main number**: one step, its exact definition and the event that counts it.
- **Baseline**: the rate, its count, its window and the date it was pulled.
- **Smallest change worth having**, in points.
- **For a test**: planned n per arm, eligible visitors a week, planned weeks, the split.
- **For a known fix**: the before window, and the after window of the same length.
- **Guardrails**: one or two numbers that must not get worse, each with its limit.
- **Stop rule**: the planned end; the sample ratio check (a test only); the guardrail limits; the one reason to stop
  early, a broken page.
- **Who puts it live**: from `knowledge/pages.md`.

These fill the Go-live page `playbooks/build-a-bet.md` asks for. The prediction is the baseline and the smallest change
worth having, as a number and a date. Then go on with `playbooks/build-a-bet.md` (the brand check and the page).

## 4. Put it live, after the OK

Only once a person has OK'd the Go-live page. A test is started in the testing tool by the person who runs it, unless a
granted Tool lets you: `playbooks/run-and-measure.md` says how to file the exact change for them. Record on the task the
date it started and the planned end.

## 5. While it runs

Your Watch routine reads the count in each arm, the sample ratio check and the guardrails. Never the main number, or the
difference between the arms, before the planned end. A guardrail breach goes to the page's owner as a question at once.
A broken page stops the test, the same way it went live.

## 6. At the planned end

Follow `skills/read-a-test-without-peeking/SKILL.md` for a test: won, lost or inconclusive, with its interval. For a
known fix, compare the before and after windows with their counts, and say what else changed in those weeks. Write the
read-out in the experiment's file. Then `playbooks/run-and-measure.md` tags the result and writes the Results page. For
a test, only a read of won counts as worked.

## 7. Learn

Record the result for the page in `knowledge/pages.md`, and what the bet taught about the job in `memory/learnings.md`.
A result that suggests a page belongs on the never-test list is a question to the page's owner, not an edit.
