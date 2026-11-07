A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Paid social, week of 2026-10-26 to 2026-11-01

Sample output for Lark Coffee Club, a fictional coffee subscription with two customer segments. Nothing has been changed in any ad account. First draft, not yet reviewed.

**Headline: $5,360 spent. Home brewer prospecting on Meta is 9 per cent over target, and its main ad is wearing out. Cafe owner demos on LinkedIn are 13 per cent over target.**

## By campaign (source: Meta, TikTok and LinkedIn campaign exports, 2026-10-26 to 2026-11-01, pulled 2026-11-02 09:00)

| Campaign | Segment | Spend | Primary result | Cost per result | Target | Last week |
|---|---|---|---|---|---|---|
| Meta: HB prospecting | Home brewers | $2,140 | 41 trials | $52.20 | $48 | $45.80 |
| Meta: HB site visitors | Home brewers | $620 | 26 trials | $23.85 | $30 | $24.10 |
| TikTok: HB creator test | Home brewers | $1,180 | 14 trials | $84.29 | $55 | in test |
| LinkedIn: CO cafe demos | Cafe owners | $1,420 | 9 demos | $157.78 | $140 | $162.40 |

Platform counts are shown side by side and never added. Export check: the rows sum to each platform total ($2,760 Meta, $1,180 TikTok, $1,420 LinkedIn), and those add to the $5,360 headline.

## Creative fatigue

- **Meta HB prospecting, "unboxing v3"** (live 34 days, the only ad in its ad set): frequency 2.9, up from 2.2 four weeks ago; link click-through 0.9 per cent, down from 1.3 per cent over the same weeks. Fatigued. Replacement status: two variants in production, waiting on the price claim in change 1.
- **Meta HB site visitors, "subscribe reminder"** (live 52 days): frequency 6.1, above the cap of 5 in `knowledge/targets.md`, but link click-through steady at 1.4 per cent and cost per trial under target. Watch. No change this week.

## Where it is and is not working

- Home brewer prospecting is 9 per cent over target. CPM is flat across the four weeks, so the auction is not the cause. Click-through is the factor that moved, which points at the creative.
- Cafe owner demos are 13 per cent over target, with link click-through at 0.6 per cent. The audience was narrowed on 2026-10-12, and cost per demo has come down since, but it is still over.
- TikTok has no read yet. Its trial event changed on 2026-10-29 (see Needs a human), so its trial counts before and after are not comparable.

## Three changes, prepared for review

1. **Replace "unboxing v3" in HB prospecting with two new variants.** Variant A makes a price claim: "Under $2 a cup on the 250 g plan." Its basis: the price list dated 2026-10-30 ($48 a month for 250 g), the brew guide (10 g a cup, so 25 cups a bag, $1.92 a cup), and three public cafe menus checked 2026-10-28 that list filter coffee at $3.20 to $3.60. Variant B tests the morning-routine hook with no price claim. Keep "unboxing v3" live until the first new variant is approved and running; pausing it is next week's change. Evidence: $2,140 spent, $52.20 a trial against $48, click-through down from 1.3 to 0.9 per cent. Expected effect: a lower cost per trial if the price claim is the stronger idea. Earliest read: seven days after launch, or the day both variants reach 30 trials if later. At this spend, about 2026-11-20.
2. **LinkedIn cafe demos: cut the daily budget from $200 to $160.** That is a 20 per cent step, within the team default. It stays until cost per demo is under $140 for two weeks. Evidence: $1,420 spent, $157.78 a demo against $140, link click-through 0.6 per cent. Expected effect: fewer demos, and a lower cost per demo if the trend holds. The $40 a day is not moved to another segment; that is the Head of Marketing's call. Check on 2026-11-16: cost per demo over the last 14 days.
3. **Exclude trial starters from Meta HB site visitors.** The audience keeps serving people who have already started a trial, which the frequency of 6.1 shows. Prepared now. Applied only after the CRM list is matched to the exclusion; that export is due 2026-11-06. Expected effect: frequency under 5, the same cost per trial, and fewer impressions on people who have already converted. Check two weeks after it is applied; if applied on 2026-11-06, that is 2026-11-20.

## Waiting

- TikTok creator test: 14 of 30 trials. It cannot be read until each variant has 30 trials and seven days have passed since launch. The read date is in `knowledge/tests.md`.

## Last fortnight's changes

- 2026-10-12: LinkedIn audience narrowed to cafe owners by job title. Cost per demo $171 before, $158 over the two weeks after. Kept.

## Needs a human

- TikTok changed its trial event on 2026-10-29. Trial counts before and after the change are not comparable until `marketing-ops` confirms that the event fires once per trial. Routed to `marketing-ops` as a proposal.

## Could not read

- Trial-to-paid conversion: no CRM access, so trials are counted, not customers.
- LinkedIn demo outcome: whether a demo became a trial or a sale is not in the export.
- View-through conversions: not in these exports, so no view-through result is shown.
```
