# Weekly test health report

Schedule: Tuesdays at 09:00 team time (routine `weekly-test-health`), after setup. Also run by hand. Budget 45 minutes.
Nothing on GitHub changes, and no test is skipped, quarantined or deleted by this run.

---

## Inputs

- `knowledge/thresholds.md`, `knowledge/suites.md`, `knowledge/flaky-tests.md`, and last week's report.
- Workflow runs for the last 14 days, and for the 14 days before them, for each suite's workflow in `knowledge/suites.md`:
  `gh run list -R <repo> --workflow <name> --limit 100 --json databaseId,headSha,conclusion,createdAt,updatedAt,event,headBranch`.
- The failed logs for flaky candidates: `gh run view <id> -R <repo> --log-failed`. A log past retention is unknown, not clean.
- The test code for each must-test path in `knowledge/thresholds.md`.

## Steps

1. **Read where things stand.** `tico task show <id>`, then the inputs above. For each fix planned last week, check whether it
   landed and whether its number moved.
2. **Read the suites.** For each suite, over the last 14 days and the 14 before: runs, failure rate, median and slowest
   duration. Use medians over the window, not single runs.
3. **Find the flaky tests.** A flaky candidate is a commit with a failed run and then a passed run, with no new commit between
   them. Read the failed log and name the test. Apply the threshold in `knowledge/thresholds.md` (starting default: two such
   flips in two weeks). A candidate whose log cannot be read is "unknown", not flaky. Classify each failure with
   `skills/classify-a-test-failure/SKILL.md`. Update the register: still flipping, fixed, or past its deadline.
4. **Decide each flaky test.** For each test in the register with no decision, or past its deadline, follow
   `skills/decide-fix-or-quarantine/SKILL.md` and record the decision with its owner and deadline. A quarantine is recorded
   here and carried out by a person unless the task asks for it and a granted Tool allows it.
5. **Find skipped and quarantined tests past their deadline.** List each one with its ticket, its age and its owner.
6. **Find paths with no test.** For each must-test path, read the test code and say whether a test asserts its behaviour
   (`skills/prove-a-test-catches-the-bug/SKILL.md`). A path with no such test is named, not counted as covered.
7. **Check slow suites against the threshold.** A suite is slow when its median passes the threshold in
   `knowledge/thresholds.md`. Name the test or step that grew, if the log shows it.
8. **Hand over product bugs.** A product bug a test exposed goes to the feature owner of its area as a Product task, with the failing
   test, its runs and the commit range (`playbooks/triage-a-failing-test.md`).
9. **Write three fix plans.** Choose by time saved times people blocked: the slowest suite, the flakiest test, the most
   common cause. For each: the test or file, the change, the expected effect and how to verify it. A change to a product file
   is not a fix plan of yours; it is a hand-off in step 8.
10. **Write and hand over.** Write the report, `tico file publish` it, commit, then run `tico task done <id> -m "..." --version N` with the headline and the path.

## Output

`reports/YYYY-MM-DD-test-health.md`, in this order, in the shape of `knowledge/examples/test-health.md`:

- **Headline:** the merge suite's flaky failure rate, and the slowest suite's median with its change.
- **Flaky tests:** test, flips with run ids and dates, suspected cause, owner, decision and deadline.
- **Past deadline:** skipped or quarantined tests whose deadline has passed, with their tickets.
- **Paths with no test:** each must-test path that has no test asserting its behaviour.
- **Suites:** median over the last 14 days against the 14 before.
- **Product bugs handed over:** each with its failing test, runs and Engineering task link.
- **Fix plans:** three, each with the file or test, the change, the expected effect, the check and the owner.
- **Could not read:** runs or logs unavailable, with the count.
- **Sources:** the suites, the date range, and the date of the thresholds file.

## Done when

- Every suite in `knowledge/suites.md` appears with its runs and medians, or is named as unreadable.
- Each flaky test names the runs (id, commit, date) where it flipped, and has a recorded decision with an owner and a deadline.
- Each product bug is handed over with a link, and each fix plan names an owner and a way to verify it.
- The report is published and the task is closed, with the headline and the report path in the note.
