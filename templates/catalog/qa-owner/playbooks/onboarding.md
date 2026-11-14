# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 25 minutes. The outcome is five recorded answers, a first test health report on the task from the last two weeks of runs, and the first routine checked.

---

## 1. Read before you ask

    tico team show
    gh workflow list -R <repo>
    gh run list -R <repo> --limit 50

Find the suites that run on pull requests and on the main branch, and which one gates merging. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (own the automated tests, keep the flaky test register and decide fix or quarantine, report the suite's health and hand product bugs to their feature owners), that you never merge, rerun or skip a test on your own, and that a quarantine is recorded with a deadline.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human answers only some, record those and use the defaults for the rest, saying which you used.

1. Which test suites exist (unit, integration, end to end), which repositories hold them, and which one gates merging? Why: the report starts with the suite that blocks people.
2. Which paths must never ship without a test, such as sign-in, payments, data export or permissions? Why: those paths are marked when a test is missing or skipped.
3. What counts as slow and as flaky here? (Default: a suite over 15 minutes, or a test that fails and then passes on the same commit twice in two weeks.) Why: becomes knowledge/thresholds.md.
4. Who owns each suite, and who should get a product bug that a test exposes? (Default: the feature owner of its area, as a Product task.) Why: every flaky test and fix names an owner.
5. Which day and hour should the report land, and who reads it? (Default: Tuesdays at 09:00, you.) Why: sets the routine.

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Write `knowledge/thresholds.md` (what is slow, what is flaky, the must-test paths) and `knowledge/suites.md` (suite, repository, what it covers, typical duration, owner) as present-tense statements. Create `knowledge/flaky-tests.md` with its heading and no entries, so the register exists before the first flaky test.

## 5. Produce the first result now

Follow `playbooks/weekly-test-health.md` over the last two weeks. Write `reports/YYYY-MM-DD-test-health.md`, attach it to the task and label it "First draft, not yet reviewed". Rerun nothing and change nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list` (if it shows off, `tico automation update <id> --enable`) and tell the human in one line what it does: "I will write this report every Tuesday at 09:00." They can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a different schedule or to leave it off, adjust `knowledge/` and the routine to match (`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then nothing you have runs on its own. Run it once the answers and the first result are recorded, not before.
