# Weekly purchase requests digest

Schedule: Mondays at 09:00 team time (routine `weekly-purchase-digest`), after setup. Also run by hand on request. Budget 25 minutes. The outcome is one page: every open
request, where it stands, what blocks it and when it must be decided. Nothing is sent to a vendor.

---

## 1. Read where things stand

    hub task list --status open --status doing --status waiting

Keep the tasks that are purchase requests, plus any listed in `knowledge/vendors.md` as open. Read
last week's digest, `knowledge/criteria.md` and each request's conversation with `hub task show <id>`.

## 2. Place each request in a stage

Requested, must-haves agreed, vendors shortlisted, quotes requested (with the available Tools), comparison drafted, missing facts or Tools, decided. For each: the requester, the amount if known, the decide-by date, the
one thing that blocks it, and who owns that step. A request with no movement for 14 days is "stalled".

## 3. Advance what you can

For a request without a comparison, start `playbooks/compare-vendors.md` at once, at most two per run.
For a request waiting on a quote, draft the follow-up in the digest to send with Tools when a person has turned mail sending on in Tico. Renewals
inside 60 days that the FP&A Analyst (`spend-watcher`) listed are noted with their decide-by date; you do not decide them.

## 4. Write and hand over

Write `reports/YYYY-MM-DD-purchase-digest.md`: a headline (open, stalled, decide-by within 14 days), a
table of requests, then the drafts you prepared, then what you could not read. Then:

    hub file publish reports/YYYY-MM-DD-purchase-digest.md

Share within the requested work and intended audience with your Tools; messages to outsiders stay drafts until a person turns mail sending on in Tico.

## 5. Finish

Commit, then `hub task update <id> --status done --note`: open requests, stalled ones, the nearest
decide-by date, and which sources you could not read. Always finish the task: an open scheduled task
absorbs the next.
