# Monthly evidence and controls page

Schedule: the first of each month at 10:00 team time (routine `monthly-controls-page`), after setup. Budget 40 minutes.
Nothing in any system is changed. The page goes to the Operations Manager; each control owner gets one evidence request.

Inputs: `knowledge/controls.md`, `knowledge/evidence-log.md`, the evidence folder (through `hub doc search` and
`hub doc read`), `knowledge/acknowledgements.md`, `knowledge/vendor-reviews.md`, the vendor register, and last month's page.

Outcome: one page in the shape of `knowledge/examples/controls-page.md`: the controls missing evidence for a closed period
(first), the controls due this month with owner and exact evidence, access reviews due, policy acknowledgements outstanding,
vendors without a security review, and what could not be read.

Done when every control due last month is marked collected, claimed or missing; every control due this month has an owner
and an exact evidence line; each owner who must act has a task; the page is published; and the task note carries the
headline.

---

## 1. Close last month

For every control due last month, check the evidence folder and `knowledge/evidence-log.md`. Mark each control:
- **collected**: a file is filed, logged, and dated inside the period;
- **claimed**: the owner says it is done, with no file;
- **missing**: nothing on file.

A missing item for a closed period is the first line of the page. Follow `skills/file-evidence-for-a-control/SKILL.md` for
each file you check.

## 2. Open this month

List each control due this month from `knowledge/controls.md`: the owner, the exact evidence (for example "screenshot of the
backup restore test, with its date, from the backup console"), and the due date. Quarterly and yearly controls appear in
their month.

## 3. Access reviews

In the first month of a quarter, start `playbooks/access-review.md` for each in-scope tool. List last quarter's reviews that
are still undecided, with their owners.

## 4. People and vendors

From `knowledge/acknowledgements.md`: people who joined more than 14 days ago without accepting the policies, and yearly
acceptances overdue. From `knowledge/vendor-reviews.md` and the vendor register: vendors that hold team or customer data
with no security review, or one older than a year.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-controls.md` in the shape of `knowledge/examples/controls-page.md`, then:

    hub file publish reports/YYYY-MM-DD-controls.md

For each control owner who must act, create one task with the exact evidence, the control reference and the due date:
`hub task create --owner <human>`. Commit, then:

    hub task update <id> --status done --note "..."

The note holds the headline (controls missing for a closed period, controls due this month), the page path, and what could not
be read.
