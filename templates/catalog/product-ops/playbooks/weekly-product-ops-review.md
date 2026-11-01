# Weekly product ops review

Schedule: Wednesdays at 09:00 team time (routine `weekly-product-ops-review`), after setup. Budget 40 minutes. The outcome is one page: what came in, what is most
asked for, what is incomplete on the roadmap, what ships next, and who should be told. Nothing is sent.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/requests.md`, `knowledge/betas.md`, `knowledge/release-calendar.md` and last week's
review. Check whether last week's ship notices were sent (ask, do not assume).

## 2. Take in the week's requests

New tasks tagged as requests, the Customer Insights Analyst's latest report, sales and customer calls
(`hub meeting search "feature request" --since <last week>`). For each, follow
`playbooks/log-a-feature-request.md`. Count new rows and merges.

## 3. Rank the most asked

Top ten requests by distinct accounts, with segment and plan mix and the change since last week. Mark any
with ten or more accounts and no status.

## 4. Check roadmap hygiene

For each roadmap item (read only): owner, spec link, target date, date not passed. One line per
exception with who should fix it.

## 5. Betas and the calendar

Beta participants whose feedback is overdue, and betas at or past their exit date. For each of those, follow
`skills/decide-a-beta-exit/SKILL.md` and record the recommendation; the owner decides. The next eight weeks of
releases from milestones, the calendar and the Release Manager's tasks; flag weeks with two launches and launches
without a support briefing or release notes owner.

## 6. Ship notices

For each request marked shipped since last week, follow `skills/prepare-a-ship-notice/SKILL.md`: one notice per
requesting account, each checked against the released behaviour and the account's current record. Put them on the
task for the owner to review; nothing is sent.

## 7. Write and hand over

Write `reports/YYYY-MM-DD-product-ops-review.md` in the shape of `knowledge/examples/product-ops-review.md`,
`hub file publish` it, commit, and `hub task update <id> --status done --note`: the headline and what
needs a human.
