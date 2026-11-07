# Paid social method

The standing rules behind the weekly review, the creative pipeline and every change proposal. Thresholds are platform guidance or dated third-party figures, and each line says which. Figures carried from the paid-media template's method were read there on 2026-11-01; this file did not re-read them. Team rules (the step size for budget moves, the test count, the fatigue ratio) are this company's choices, set in `knowledge/targets.md`, and are not platform rules.

## Measure the result, not the click

- One primary conversion per campaign, named in `knowledge/targets.md`. Link clicks, video views, reach and engagement explain a result; they are not one.
- Check the conversion events each platform counts. An event set as primary that fires on a page view inflates the result. Mark each event primary or secondary, and report only the primary as a result.
- Keep view-through and click-through conversions on separate lines. Platforms often fold view-through into "all conversions".
- Platform counts overlap, with each other and with the CRM. Never add one platform's count to another's. Where the CRM holds a de-duplicated count, show it beside the platform counts with its source.
- Compare like with like. A link click-through rate goes with conversions per link click. Mixing all clicks with link clicks gives a wrong cost per result.
- Reporting lags and platforms restate recent days. Record the time of each pull with the reading.
- Censored values stay censored. A platform's "under 10%" is never written as 10%.

## Learning, budgets and volume

- A campaign in its learning window is not edited unless something unusual shows up, because each edit can restart learning. Meta's developer guidance advises about 50 optimisation events per ad set in seven days to exit learning (carried from the paid-media method, read 2026-11-01).
- Fewer, larger ad sets learn faster. Splitting a small budget across many ad sets multiplies the events each one needs.
- Set a target near the average cost per result of the last 90 days, not below it. A target far below history starves delivery.
- Budget moves go in steps. The team default is no more than 20 per cent in one step (a team rule in `knowledge/targets.md`). Read the effect two weeks later, not the next day.
- A budget move between segments is the Head of Marketing's decision. Within a channel, the split between campaigns is the Social Ad Owner's.

## Diagnosing a cost per result

Cost per result = CPM ÷ (1,000 × link click-through rate × conversion rate), where conversion rate = primary conversions ÷ link clicks. This makes three factors: the auction price (CPM), the click-through rate and the conversion rate. A rise in cost per result is one of them, or several. `skills/diagnose-a-rising-cost-per-result/SKILL.md` shows how to read which one moved.

## Creative fatigue

- Fatigue is a trend, not a level: frequency rising while link click-through falls, over the same weeks.
- The baseline is the ad's first full week at a steady budget with enough impressions to read. An ad with no full week has no baseline, so it is too early to call.
- Rotate before results fall for a month. A replacement is ready when the trend starts, not when the cost per result breaks target.
- A CPM rise shared by every ad in an ad set is the auction or the audience, not the creative. Say so, and do not rotate on that alone.
- Do not pause the only live ad in an ad set without a replacement ready.

## Audiences

- Prospecting (people who do not know the brand yet) and retargeting (people who visited, engaged or bought) run as separate campaigns with separate targets. Retargeting a small audience at a high frequency is cheap and hides the prospecting cost, so read the two apart.
- Exclude existing customers from prospecting where the platform allows it. A customer list is uploaded only with the consent and terms the company holds.
- Test one audience variable at a time. Record the audience size the platform estimates for each test, and whether it sits inside the platform's own range.

## Creative testing

- Each variant tests one idea: the offer, the pain or the proof. Two changes in one variant cannot be read.
- Do not call a winner from under seven days or under the conversion count in `knowledge/targets.md` (default 30 primary conversions per variant).
- Read a gap against its spread. With n conversions in one variant and m in the other, one standard error of the gap is about the square root of (1/n + 1/m) in relative terms: about 26 per cent at 30 each. Treat a gap under two standard errors (about 52 per cent at 30 each) as no winner yet. This is arithmetic on counts, not a platform rule.
- Write the test before it starts (`skills/read-an-audience-or-creative-test/SKILL.md`).

## Claims and categories

- Every claim is one a dated source supports: a price with its period, a saving with its baseline, a rating with its source and date. A claim with no basis is removed, not softened.
- Guarantees need their terms on the page. Testimonials need consent, and the quote is verbatim with its date. A composite or edited quote is not a testimonial.
- A competitor's name needs its own basis (trademark and platform rules).
- Classify the ad before setting targeting. Platforms restrict targeting for housing, credit, employment and social-issue or political ads (Meta's special ad categories). A campaign misclassified as general can be restricted or rejected after launch.

## Sources

- Meta for Developers, learning-phase guidance: about 50 optimisation events per ad set in seven days (carried from `paid-media/knowledge/paid-method.md`, read 2026-11-01).
- Account reporting notes from a live paid bot: censored values, configured versus observed budget, view-through segmentation and reporting lag, 2026 (carried from the same file, read 2026-11-01). The rules are general; the figures are not.
- Arithmetic in this file (the cost-per-result identity, the standard-error spread) is computed here, not sourced.
