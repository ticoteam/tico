# Weekly paid media review

Schedule: Mondays at 09:00 team time (routine `weekly-paid-media-review`), after setup. Also run by hand. Budget 40 minutes. The outcome is one page: how the money
did, where it was wasted, and three changes prepared for review. Nothing is changed.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/targets.md`, `knowledge/changes.md` and last week's review. For every change applied
in the last two weeks, note what it was meant to do; step 5 checks it.

## 2. Read the numbers

From the attached exports (or the read-only reporting access): per campaign, the last 7 days and the
7 before: spend, impressions, clicks, conversions, cost per conversion, and conversion value where
there is one. A campaign with no conversion tracked is marked "unmeasured" and judged on nothing.

## 3. Find the waste

- Campaigns over their target cost per result for two weeks running.
- In the search-terms report, sorted by cost: terms that spent more than one target cost per result
  with no conversion, and terms plainly unrelated to what the team sells. Propose each as an
  exclusion (exact match unless a whole theme is wrong), with its spend. Check none of them is a
  brand term or a product the team does sell.
- Placements or audiences that spend and never convert.
- Ads unchanged for 60 days with falling click-through: creative fatigue.
- Terms that convert well but are not keywords yet: propose them too.

## 4. Choose three changes

Rank by money at stake. For each: the exact edit (campaign, setting, from, to), the evidence, the
expected effect, and how you will check it in two weeks. Apply requested changes within the stated budget and your Tools; public ad copy stays a draft until a person turns mail sending on in Tico. If you lack the necessary Tool, create one task for its owner with the exact edit and evidence.

## 5. Check last fortnight's changes

For each change applied 14 or more days ago: before, after, and whether it did what it was for. Write
the result into `knowledge/changes.md`. A change that did not work is a line, not a secret.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-paid-media.md` in the shape of `knowledge/examples/paid-media-review.md`,
then `hub file publish reports/YYYY-MM-DD-paid-media.md`. Commit, and `hub task update <id> --status
done --note`: the headline, the path, what you could not read.

## When the export is missing

Say which report is missing and for which dates, ask once with `hub task ask <id>`, and finish with
what you have. Never fill a gap from last week's numbers.
