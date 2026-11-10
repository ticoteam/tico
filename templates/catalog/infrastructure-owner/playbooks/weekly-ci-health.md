# Weekly CI health report

Schedule: Tuesdays at 09:00 team time (routine `weekly-ci-health`), after setup. Also run by hand. Budget 45 minutes.
Nothing on GitHub changes.

---

## Inputs

- `knowledge/thresholds.md`, `knowledge/workflows.md`, and last week's report.
- Workflow runs for the last 14 days, and for the 14 days before them, for each workflow in `knowledge/workflows.md`:
  `gh run list -R <repo> --workflow <name> --limit 100 --json databaseId,headSha,conclusion,createdAt,updatedAt,event,headBranch`.
- The failed logs for flaky candidates and red streaks: `gh run view <id> -R <repo> --log-failed`. A log past retention
  is unknown, not clean.

## Steps

1. **Read where things stand.** `tico task show <id>`, then the inputs above. For each fix planned last week, check
   whether it landed and whether its number moved.
2. **Read the runs.** For each workflow, over the last 14 days and the 14 before: runs, failure rate, median and slowest
   duration. Use medians over the window, not single runs.
3. **Find the flaky candidates.** A flaky candidate is a commit with a failed run and then a passed run, with no new commit
   between them. Read the failed log and name the test. Apply the threshold in `knowledge/thresholds.md` (starting
   default: two such flips in two weeks). A candidate whose log cannot be read is "unknown", not flaky. Classify each
   failure with `skills/classify-a-failed-run/SKILL.md`. You name the flips; the QA Owner keeps the flaky-test register
   and decides fix or quarantine.
4. **Find red-main streaks and deploys.** For each stretch where the main branch's merge check stayed red, record the
   first failing run and commit, the end, the length, and the cause if the log shows one. Count the deploy runs and the
   failed deploys from the deploy workflow. Before you count a deploy as a success, check it with
   `skills/verify-a-passing-check/SKILL.md`.
5. **Check slow jobs against the threshold.** A job is slow when its median passes the threshold in
   `knowledge/thresholds.md` (starting default: over 15 minutes for the merge check). Name the step that grew, if the
   log shows it.
6. **Write three fix plans.** Choose by time saved times people blocked: the slowest merge-gating job, the runner or
   workflow fault that stopped the most runs, the most common failure cause. For each: the file and job, the change, the
   expected effect, and how to verify it. A change to a test is a hand-off to the QA Owner, not one of the three.
7. **Write and hand over.** Write the report, `tico file publish` it, commit, then run `tico task done <id> -m "..." --version N` with the headline and the path.

## Output

`reports/YYYY-MM-DD-ci-health.md`, in this order, in the shape of `knowledge/examples/ci-health.md`:

- **Headline:** the merge check's failure rate from infrastructure faults, and the slowest job's median with its change.
- **Flaky tests, handed to the QA Owner:** test, flips with run ids and dates, suspected cause. The decision is the QA Owner's.
- **Slowest jobs:** median over the last 14 days against the 14 before.
- **Main branch red:** each streak, with its first bad run and commit.
- **Faults:** runner, network, rate-limit and workflow faults, each with its count and the runs it hit.
- **Deploys:** the count of deploy runs and the failed ones.
- **Fix plans:** three, each with the file, the job, the change, the expected effect, the check and the owner.
- **Could not read:** runs or logs unavailable, with the count.
- **Sources:** the workflows, the date range, and the date of the thresholds file.

## Done when

- Every workflow in `knowledge/workflows.md` appears with its runs and medians, or is named as unreadable.
- Each flaky test names the runs (id, commit, date) where it flipped, and is handed to the QA Owner.
- Each fix plan names an owner and a way to verify it.
- The report is published and the task is closed, with the headline and the report path in the note.
