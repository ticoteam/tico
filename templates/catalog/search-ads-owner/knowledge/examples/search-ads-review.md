A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Paid search, week of 2026-10-26 to 2026-11-01

Sample output for Gearline Cycle Care, a fictional bicycle repair business with two segments, commuters and fleets. Nothing has been changed in any ad account. First draft, not yet reviewed.

**Headline: $3,830 spent. Commuter repairs on Google are under target at $39.02 a repair, but $391 went on search terms that never convert. Fleet quotes are 22 per cent over target, on too few quotes to call.**

## By campaign (source: Google Ads and Microsoft Ads exports, 2026-10-26 to 2026-11-01, pulled 2026-11-02 09:00)

| Campaign | Platform | Segment | Spend | Primary result | Cost per result | Target | Last week |
|---|---|---|---|---|---|---|---|
| Commuter repairs | Google Ads | Commuters | $2,380 | 61 repairs | $39.02 | $45 | $44.10 |
| Fleet quotes | Google Ads | Fleets | $660 | 3 quotes | $220.00 | $180 | $196.40 |
| Brand | Google Ads | Brand, reported apart | $210 | 22 bookings | $9.55 | none | $10.20 |
| Commuter repairs | Microsoft Ads | Commuters | $580 | 14 repairs | $41.43 | $45 | first week tracked |

Google and Microsoft counts are shown side by side and never added. Export check: the Google rows sum to $3,250 and match the Google total; the Microsoft row is $580; the platforms add to the $3,830 headline.

## Where the money went nowhere

- **Commuter repairs (Google): 9 search terms spent $391 with no conversion in the week.** The largest: "bike repair course near me" ($112, people learning to repair, not buying), "free bike repair near me" ($94, people wanting a free service), and "bike repair jobs" ($71, job seekers). The check against the converting terms in the same export showed that the word "free" alone would also block "free quote bike repair", which converted four times, so the free negative is an exact term.
- **Fleet quotes shares an ad group with "bike repair near me", a consumer search.** One ad answers two kinds of search, so the quote cost cannot be read cleanly. This is the structure change below.

## Fleet quotes: too few to call

Three quotes a week is about 13 in the last 30 days. That is under the 15-conversion minimum for Target ROAS and under a workable Target CPA of about 30 a month. The campaign stays on a manual bid with a cost-per-click cap. The $180 target stays as the goal, not a bid target. Next check on 2026-12-01, at the 30-day count.

## Three changes, prepared for review

1. **Add 14 negative keywords to Commuter repairs, from the search terms export.** Nine are phrase negatives on three classes (learner, job seeker, do-it-yourself). Five are exact negatives for single terms, including "free bike repair near me". The list is attached, and each negative was checked against the converting terms: none blocks one. Evidence: $391 spent last week on terms with no conversion. Expected effect: that spend stops buying clicks that do not convert, and any capped budget moves to repair terms. Check on 2026-11-16: cost per repair over the two weeks since it is applied.
2. **Test a price-first headline against the current headline in Commuter repairs.** Question: does "Commuter repairs from $49, pickup included" lower the cost per repair against "Same-day repairs near you"? One idea: the price. Its basis: the price list dated 2026-10-28 (a basic service from $49, with pickup listed in the same price). Launch on approval. The earliest read is seven days after launch, or the day each variant reaches 30 repairs if later. At this volume, about 2026-11-16 if launched on 2026-11-09. The test is recorded in `knowledge/tests.md`.
3. **Send the fleet keywords to a fleet landing page, in their own ad group.** The fleet ad group sends people to the commuter repair page, which asks for a same-day slot, not a quote. Prepared: a new ad group "Fleet: maintenance contracts", with its own final URL, the fleet quote page. That page was checked for a quote form and for the fleet price band dated 2026-10-28. Applied only when asked. The page itself goes to `cro-owner` with the ad text and the search terms as evidence. Check on 2026-11-16, and read the cost per quote once there are 15 quotes.

## Last fortnight's changes

- 2026-10-12: seven negatives added to Commuter repairs (learner and job seeker classes). Cost per repair $47.30 before, $41.80 over the two weeks after. Kept.

## Needs a human

- Microsoft Ads counts phone calls as repairs, and the export does not split them, so the 14 Microsoft repairs may include calls. Routed to `marketing-ops` to split call conversions from bookings.

## Could not read

- Repairs completed: no CRM access, so repairs are counted at booking.
- Fleet quote acceptance: not in the export, so quotes are counted, not won.
- App store search ads: none connected this week, so no result is shown.
```
