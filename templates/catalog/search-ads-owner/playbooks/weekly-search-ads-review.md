# Weekly search ads review

Schedule: Mondays at 09:30 team time (routine `weekly-search-ads-review`), after setup. Also run by hand. Budget 45 minutes. The outcome is one page: how each campaign did against target, by platform; where money went on search terms that never convert; which bidding strategies the volume supports; the tests that can be read; and three changes prepared for review. Nothing is changed in an ad account.

Done when: the review is published; every campaign has a result or "unmeasured" with its reason; every waste term has a class; every bidding strategy has its 30-day count and its decision; every test that can be read has its reading; and each of the three changes carries its evidence and a check date.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/targets.md`, `knowledge/structure.md`, `knowledge/negatives.md`, `knowledge/tests.md`, `knowledge/changes.md` and last week's review. For every change applied in the last two weeks, note what it was meant to do; step 6 checks it.

## 2. Read the numbers

From the attached exports (or read-only reporting, once connected): per campaign and per ad group, the last 7 days and the 7 before: spend, impressions, clicks, click-through rate, conversions and cost per conversion. Keep each platform on its own line. Add the search terms export for the same days, and impression share as a bound where the platform gives one. App store search ads, if in scope, are read on the event the goal names. A campaign with no tracked primary conversion is "unmeasured" and is judged on nothing. Check each export's total against the sum of its rows. A gap is named in the review, not filled.

## 3. Check each campaign against its target

Compare the cost per primary conversion with the campaign's own target. Over target two weeks running: name the factor, using the diagnosis section of `knowledge/paid-search-method.md`. Check the query mix and the page before you name a bid as the cause. If the conversion rate fell and the search terms did not change, check tracking with the tracking owner. Never judge a segment's campaign against another segment's target.

## 4. Find the waste

Use `skills/read-search-terms-for-waste/SKILL.md` on the terms export. Also look for:
- Keywords that spent more than one target cost per result with no conversion in seven days, and have run for at least seven days. Propose the pause or the negative, with the spend it affects.
- Ad groups with mixed themes, where one ad answers two kinds of search. Propose the split.
- Campaigns that lose impression share to budget while under target. These are candidates for a budget move within the channel.

## 5. Check the bidding

For each campaign on a target bidding strategy, read its 30-day count with `skills/choose-a-bidding-strategy-from-volume/SKILL.md`. A campaign under its minimum is not held to a target. Record each decision in `knowledge/structure.md`.

## 6. Read the tests

For each test in `knowledge/tests.md`: has it reached its count and its earliest read date? If so, read it with `skills/test-ad-copy-and-landing-page-fit/SKILL.md` and record the reading. If not, write the date it can be read beside it.

## 7. Check last fortnight's changes

For each change applied 14 or more days ago: before, after, and whether it did what it was for. Write the result into `knowledge/changes.md`. A change that did not work is a line, not a secret.

## 8. Choose three changes

Rank by money at stake. For each one: the exact edit (platform, campaign or ad group, setting, from, to), the segment it serves, the evidence, the expected effect and the date you will check it. A budget move between segments, or between search and social, is not yours to make: write it as a question to the Growth Team Manager (`growth-team-manager`) on its own line. A landing page that does not match its search goes to `cro-owner` with its evidence. A conversion that is counted wrongly goes to the tracking owner. Prepare each change with `playbooks/prepare-a-search-ads-change.md`. This review applies nothing.

## 9. Write and hand over

Write `reports/YYYY-MM-DD-search-ads.md` in the shape of `knowledge/examples/search-ads-review.md`, then run `hub file publish reports/YYYY-MM-DD-search-ads.md`. Update `knowledge/negatives.md`, `knowledge/structure.md`, `knowledge/tests.md` and `knowledge/changes.md`, commit this repository, then run `hub task update <id> --status done --note "..."`: the headline, the report path, and what you could not read.

## When the export is missing

Say which report is missing and for which dates. Ask once with `hub task question "<question>" --on <person> --parent <id>`, and finish with what you have. Never fill a gap from last week's numbers.
