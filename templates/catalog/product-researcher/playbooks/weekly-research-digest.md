# Weekly research digest

Schedule: Thursdays at 10:00 team time (routine `weekly-research-digest`), after setup. Also run by hand. Budget 45 minutes. The outcome is one page for the product team: what users
said this week, which opportunities gained evidence, and one thing worth testing. You contact no one.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/opportunities.md` (the outcome and the open decision), `knowledge/privacy.md` and last week's digest.

## 2. Collect what is new

    hub meeting search --since YYYY-MM-DD
    hub doc search "interview"

Also tasks that carry interview notes or feedback exports (`hub task list`), and the Customer Insights Analyst's latest
report if the team runs one. List each source: what it is, when, and whether you can read it.

## 3. Snapshot each new source

Follow `playbooks/write-an-interview-snapshot.md` for each new interview or call, and a short batch summary
for feedback exports. Save under `knowledge/snapshots/`.

## 4. Update the opportunities

For each need or pain in the new snapshots, find its line in `knowledge/opportunities.md` or add one. Update
the source count and the dates. Label: **anecdote** (one source), **emerging** (two), **pattern** (three or
more, from different people). Move anything that does not serve the outcome to the parking list.

## 5. Pick one thing to test

The opportunity with the most evidence and the least testing. Name its riskiest assumption and one cheap way
to test it that a human can run in a week. Do not choose the solution. Follow `skills/choose-the-next-test/SKILL.md`.

## 6. Write the digest

`reports/YYYY-MM-DD-research-digest.md` in the shape of `knowledge/examples/research-digest.md`: the finding
first, new snapshots, opportunities with source counts, the thing to test, what you could not read. Then
`hub file publish reports/YYYY-MM-DD-research-digest.md`. Competitor facts you found go to
`hub market report`, not into this digest.

## 7. Finish

Commit, then `hub task update <id> --status done --note`: the headline, sources read and unread. Always finish it.

## When a source fails

A call with no transcript, or a folder you cannot open, is named as a gap. "No new interviews" means you read
the sources and found none, never that you could not look.
