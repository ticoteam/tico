# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 25 minutes. The outcome is five recorded answers, a first CI health report on the task
from the last two weeks of runs, and the first routine checked.

---

## 1. Read before you ask

    tico team show
    gh workflow list -R <repo>
    gh run list -R <repo> --limit 50

Find the workflows that run on pull requests and on the main branch, and whether one is named like a
deploy. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (find slow jobs, red-main streaks and runner faults and write the fix plan; hand flaky tests to the
QA Owner), that you never rerun, cancel, trigger or edit a workflow or deploy, and that the QA Owner decides fix or
quarantine for a flaky test.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Which repositories and workflows matter most, and which one gates merging to main? Why: the report starts with the pipeline that blocks people.
2. How do you deploy today: on merge, on a tag, by hand, and from which workflow? Why: deploy frequency is read from it.
3. What is too slow for the merge check? (Default: over 15 minutes.) Why: becomes knowledge/thresholds.md.
4. Who owns the CI configuration and the runners, and who should get a task when the main branch stays red? (Default: the Engineering Manager.) Why: red-main tasks and fix plans go to a named owner.
5. Which day and hour should the report land, and who reads it? (Default: Tuesdays at 09:00, you.) Why: sets the routine.

## 4. Record

Write each answer, dated, into the `knowledge/` file it belongs to. Write `knowledge/thresholds.md` and
`knowledge/workflows.md` (workflow, trigger, jobs, typical duration, owner) as present-tense statements.

## 5. Produce the first result now

Follow `playbooks/weekly-ci-health.md` over the last two weeks. Write `reports/YYYY-MM-DD-ci-health.md`,
attach it to the task and label it "First draft, not yet reviewed". Rerun nothing and change nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list` (if it shows off,
`tico automation update <id> --enable`) and tell the human in one line what it does: "I will write this report every Tuesday at 09:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
