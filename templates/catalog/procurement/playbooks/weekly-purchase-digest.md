# Weekly purchase requests digest

Schedule: Mondays at 09:00 team time (routine `weekly-purchase-digest`), after setup. Also run by hand on request.
Budget 25 minutes. Nothing is sent to a vendor, signed or bought.

Inputs: the purchase-request tasks still open (`hub task list --status open --status doing --status waiting`), last
week's digest, `knowledge/criteria.md`, `knowledge/vendors.md`, and each request's conversation.

Outcome: one page in the shape below, with the comparisons advanced in this run attached.

Done when every open request has a stage and a next step, each request with no comparison has one started or a reason
it cannot be, the digest is published, and the task note carries the headline.

---

## 1. Read where things stand

    hub task list --status open --status doing --status waiting

Keep the tasks that are purchase requests, plus any open item in `knowledge/vendors.md`. Read last week's digest and
each request's conversation with `hub task show <id>`. Note what moved since last week.

## 2. Place each request in a stage

Use one stage per request: **requested**, **must-haves agreed**, **vendors shortlisted**, **quotes requested**,
**comparison drafted**, **blocked** (a fact or Tool missing), **decided**. For each, record the requester, the amount if
known, the decide-by date, the one thing that blocks it, and who owns the next step. A request with no movement for
14 days is **stalled**.

## 3. Advance what you can

- A request with no comparison: start `playbooks/compare-vendors.md`, at most two per run.
- A request waiting on a quote: draft the follow-up for a person to send. It leaves only when the task asks, mail
  sending is on in Tico and a granted Tool allows it.
- A renewal inside 60 days that the FP&A Analyst (`spend-watcher`) has listed: note its decide-by date. Do not decide it.

## 4. Write and hand over

Write `reports/YYYY-MM-DD-purchase-digest.md`:
- headline: open requests, stalled requests, and the nearest decide-by date, in one sentence;
- a table: request, requester, amount, stage, blocker, owner, decide-by;
- the comparisons and drafts prepared this week, each with its path;
- security questions still open, by vendor;
- could not read: each source that failed, and what that leaves unknown.

Then:

    hub file publish reports/YYYY-MM-DD-purchase-digest.md

Commit, then:

    hub task update <id> --status done --note "..."

The note holds the headline, the nearest decide-by date and the sources you could not read. Finish the task even when
nothing is open: a quiet digest is one line. An open scheduled task is absorbed by the next run.
