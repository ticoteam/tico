A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme marketing data check, Mon 2026-09-28

Sample output for Acme, a fictional studio-software team. Nothing in the CRM or analytics has been
changed. First draft, not yet reviewed.

**Headline: 84% of campaign traffic tagged to the convention (last week 71%); 91% of new leads have a
source; median time to first sales touch 26 hours against a 24-hour target.**

## Fix first
- **One link carries an email address** (`utm_content=` a subscriber's address) in the 2026-09-24
  newsletter, "Studio tips". Replace with `utm_content=tips-link-2`. Owner: email-owner.

## Tagging (source: traffic-by-source export, 2026-09-21 to 2026-09-27)
| Where | Is | Should be | Owner |
|---|---|---|---|
| Pilates owners social ads | `utm_medium=Paid Social` | `paid-social` | search-ads-owner |
| Partner newsletter, 2026-09-22 | `utm_medium=newsletter` | `email` | Omar |
| Expo landing QR code | untagged, lands as direct | `utm_source=studio-expo&utm_medium=event` | events |

## Sources (source: CRM read 2026-09-28)
- 118 new leads; 11 without an original source, 9 of them from the "Book a walkthrough" form, which has
  no hidden source field. Fix: add it (form owner: Marco).

## Handoff (source: CRM read 2026-09-28; target in knowledge/handoff.md)
- 34 leads became ready for sales. Median 26 hours to first touch; slowest 4 days; 3 still untouched.
- Weekend trial sign-ups wait longest (median 51 hours). Proposed: a Monday 09:00 queue check.

## Four-week trend
| Week | Tagged | With source | Median touch |
|---|---|---|---|
| 09-07 | 64% | 86% | 31h |
| 09-14 | 69% | 88% | 29h |
| 09-21 | 71% | 90% | 27h |
| 09-28 | 84% | 91% | 26h |
```
