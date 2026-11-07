# Weekly paid social review

Schedule: Mondays at 09:00 team time (routine `weekly-social-ads-review`), after setup. Also run by hand. Budget 45 minutes. The outcome is one page: how each campaign and segment did against target, where money was wasted, which creative is wearing out, the tests that can be read, and three changes prepared for review. Nothing is changed in an ad account.

Done when: the review is published; every campaign has a result or "unmeasured" with its reason; every fatigued ad has a replacement status; every test that can be read has its reading; and each of the three changes carries its evidence and a check date.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/targets.md`, `knowledge/creative.md`, `knowledge/tests.md`, `knowledge/changes.md` and last week's review. For every change applied in the last two weeks, note what it was meant to do; step 6 checks it.

## 2. Read the numbers

From the attached exports (or read-only reporting, once connected): per campaign and per ad set, the last 7 days and the 7 before: spend, impressions, frequency, CPM, link clicks, link click-through rate, primary conversions and cost per primary conversion. Group campaigns by segment using the name prefix in `knowledge/targets.md`. A campaign with no tracked primary conversion is "unmeasured" and is judged on nothing. Check each export's total against the sum of its rows. A gap is named in the review, not filled.

## 3. Check each campaign against its target

Compare cost per primary conversion with the campaign's own target. Over target two weeks running: diagnose it with `skills/diagnose-a-rising-cost-per-result/SKILL.md`. Under target but above the weekly ceiling: say so. Never judge a segment's campaign against another segment's target.

## 4. Find the waste and the fatigue

- Ad sets that spent more than one target cost per result with no primary conversion in seven days, and have been live for at least seven days. Propose the pause or the restructure, with the spend it affects.
- Live ads with fatigue, using `skills/spot-creative-fatigue/SKILL.md`. Each one gets a replacement status in `knowledge/creative.md`.
- Retargeting audiences whose frequency is above the cap set in `knowledge/targets.md`.
- Audiences that spend and never convert.

## 5. Read the tests

For each test in `knowledge/tests.md`: has it reached its count and its earliest read date? If so, read it with `skills/read-an-audience-or-creative-test/SKILL.md` and record the reading. If not, write the date it can be read beside it.

## 6. Check last fortnight's changes

For each change applied 14 or more days ago: before, after, and whether it did what it was for. Write the result into `knowledge/changes.md`. A change that did not work is a line, not a secret.

## 7. Choose three changes

Rank by money at stake. For each one: the exact edit (campaign or ad set, setting, from, to), the segment it serves, the evidence, the expected effect and the date you will check it. A budget move between segments is not yours to make: write it as a question to the marketing lead (`marketing-lead`) on its own line. Prepare each change with `playbooks/prepare-a-social-ads-change.md`. This review applies nothing.

## 8. Write and hand over

Write `reports/YYYY-MM-DD-social-ads.md` in the shape of `knowledge/examples/social-ads-review.md`, then run `hub file publish reports/YYYY-MM-DD-social-ads.md`. Update `knowledge/creative.md`, `knowledge/tests.md` and `knowledge/changes.md`, commit this repository, then run `hub task update <id> --status done --note "..."`: the headline, the report path, and what you could not read.

## When the export is missing

Say which report is missing and for which dates. Ask once with `hub task question "<question>" --on <person> --parent <id>`, and finish with what you have. Never fill a gap from last week's numbers.
