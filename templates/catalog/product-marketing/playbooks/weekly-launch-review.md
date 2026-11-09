# Weekly launch and positioning review

Schedule: Mondays at 10:00 team time (routine `weekly-launch-review`), after setup. Also run by hand. Budget 40 minutes. The outcome is one page: launches in the next
90 days with brief status, positioning gaps found this week and one battlecard refreshed. Nothing is published.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/launches.md`, `knowledge/positioning.md`, and the open product and launch tasks
(`hub task list --status open --status doing --status waiting`), goals and `hub calendar list`.

## 2. Launches

For each launch in the next 90 days: tier, date, owner, brief status (none, draft, ready), assets
outstanding, and whether sales and support have been told. A Tier 1 or 2 launch inside four weeks
with no complete brief is red, and names the missing information and its owner.

## 3. Positioning check

Read what sales calls and lost deals said this week (`hub meeting search`), the market graph delta
for the competitors, and the public product page. Note where buyers describe the problem or the
alternatives differently from `knowledge/positioning.md`, with the quote and date. Apply requested positioning changes with dated evidence; mark unsupported claims as gaps.

## 4. Refresh one battlecard

Pick the competitor that appeared most or changed most this week. Follow `skills/write-a-battlecard/SKILL.md`, cite every claim (`skills/check-a-competitor-claim/SKILL.md`), and put the draft in `knowledge/battlecards/`. A card older than 90
days is marked stale at the top.

## 5. Write the review and hand it over

`reports/YYYY-MM-DD-launch-review.md`, answer first. Then `hub file publish` it, commit, and
`hub task update <id> --status done --note`: the headline, the path and what you could not read.
Always finish it.

## When a source fails

Name the source and what is therefore unknown (no call transcripts, no CRM read). A refresh built
without win and loss evidence says so at the top.
