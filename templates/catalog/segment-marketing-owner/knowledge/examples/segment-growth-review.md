A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Creators segment growth review, week of 2026-11-03

Sample output for Acme, a fictional creator tools company. Nothing has been shared or changed. First draft, not yet reviewed.

**Headline: Creators reached 84 new customers in October, 93% of the goal of 90, at $56 each against a $60 ceiling. Search Ads carries most of the spend; the Social Ads share is an estimate.**

## The number (October 2026)
| Measure | Reading | Goal or ceiling | Source |
|---|---|---|---|
| New creators | 84 | goal 90 a month | `hub growth cohort list --month 2026-10`, sign-up export on task 4402 |
| Spend on creators | $4,300 | budget $4,500 (96% used) | Search Ads actual; Social Ads estimate |
| Bot cost on creators | $380 | | estimate, split by new customers |
| Cost per creator | $56 ($4,680 ÷ 84) | ceiling $60 | computed |

The creators count is 84 to 93: nine customers have no segment and may belong to creators.

## Split
| Channel | Creators spend | Method | Creators new |
|---|---|---|---|
| Search Ads | $3,100 | direct, actual: campaigns named for creators | 58 |
| Social Ads | $1,200 | audience split, estimate: $2,000 × 60% | 26 |

Reconciled: 84 creators + 57 other segments + 9 with no segment = 150 new customers in the channels for October.

## Tests
- **Creator-only search ad groups** (task 4410, `channel:search-ads`, Live since 2026-10-20). Day 14 of 21 on 2026-11-03: 21 creators, $820 spend. Too early to close.
- **Creator sign-up page check** (task 4418, `channel:cro`, On deck). Waits for the growth meeting on Monday 2026-11-09.

## Asks
- Task 4421 to the Conversion Rate Optimization Owner: find where creators drop off on the first step of sign-up, by 2026-11-10. No spend. Filed 2026-11-03.
- Task 4399 to the Search Ads Owner: quote for two more creator-only ad groups. Answered 2026-10-29: about $900 a month, estimated. The quote goes to the growth meeting as an idea. It does not start from the quote.

## Message
- Creator message v3 (`knowledge/message.md`, changed 2026-10-30). "Set up your page in 10 minutes": approved (`hub brand show --section claims`, source: onboarding timing study, 2026-10-14). "Used by 300 creators": not approved, removed. The Brand Owner was asked on task 4406 whether it can be added with its source.

## Decisions waiting
- Marketing Team Manager: decide on task 4418 at the Monday 2026-11-09 growth meeting.
- Marketing Team Manager: confirm the November creator budget of $4,500.

## Could not read
- The sign-up export for 2026-10-29 to 2026-10-31 was not attached. October counts come from `hub growth cohort list` and may move when the last three days land.

## Left out
- Three support messages about invoices in the creator inbox: left for the Support Owner.

## Sources
- `hub growth cohort list --month 2026-10`, read 2026-11-03.
- Spend for Search Ads and Social Ads for October, from the source named in `hub growth source list`, read 2026-11-03.
- Sign-up export on task 4402, 2026-11-02.
- Tasks 4399, 4406, 4410, 4418 and 4421, read 2026-11-03.
```
