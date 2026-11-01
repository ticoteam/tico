# Weekly privacy desk

Schedule: Wednesdays at 09:00 team time (routine `weekly-privacy-desk`), after setup. Also run by hand. Budget 25 minutes. The outcome is one page: every open data request against its
deadline, DPAs waiting, vendor changes that owe customers a notice, and records that are stale. A summary for a
human, not legal advice. Nothing is sent and no data is touched.

---

## 1. Requests first

    hub task list --status open --status doing --status waiting

For each row in `knowledge/requests.md` that is not closed: days left to the deadline, which systems' steps are
done (check each step's task), and what is blocking. Any request inside 7 days with steps open goes to the top
and to the decision-maker today.

## 2. DPAs waiting

Each DPA task not yet decided: days waiting, and the open differences from its review. A DPA with no review yet:
follow `playbooks/review-a-dpa.md` now.

## 3. Vendors

Read the finance team's newest spend report and `hub doc search "new vendor"` for tools added since last week.
A new tool that touches personal data is a candidate subprocessor: add it to `knowledge/subprocessors.md` as
"to confirm" with the question for its owner. If customer DPAs promise advance notice of new subprocessors, list
the notice due and its date.

## 4. Records

Any row in `knowledge/processing.md` last confirmed more than six months ago, or for a system no longer in use:
list it with its owner.

## 5. Write and hand over

`reports/YYYY-MM-DD-privacy-desk.md` in the shape of `knowledge/examples/privacy-desk.md`: the headline with the
nearest deadline, requests, DPAs, vendors, stale records, then the not-legal-advice line.
`hub file publish` it, commit, and `hub task update <id> --status done --note` with the nearest deadline first.

## Done when
- Every open request in `knowledge/requests.md` has its days left, its step status and its blocker, or "none".
- Each DPA waiting has its differences, and each vendor change has its notice date or "to confirm".
- The report is published, and the task is closed with the nearest deadline first.
