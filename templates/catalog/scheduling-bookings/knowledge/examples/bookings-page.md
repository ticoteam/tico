A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme bookings, week of Mon 2026-10-05

Sample output for Acme, a fictional cleaning team. Every customer and time is invented. Nothing was sent, moved or charged.
Export read 2026-10-04 (task T-77), times in studio local time. First draft, not yet reviewed.

**Headline: 42 bookings this week, 1 double booking to fix on Thursday, 3 reschedule requests (2 with options, 1 blocked by the cancellation window), 3 no-show or late-cancellation drafts ready.**

## Clashes
- **Thu 2026-10-08, 10:00, crew B**: Harbour Pilates (booked 2026-09-22) and Elm Street (booked 2026-09-29) both hold crew B.
  Harbour Pilates was booked first. Options for Elm Street that keep the 30-minute buffer: Thu 13:00 or Fri 09:00.
  A person confirms which move.

## Reschedule requests
| Customer | Asked for | Rule check | Options that fit |
|---|---|---|---|
| Birch Hill | move Tue 09:00 to Wed | inside 48 hours; the rules allow a move with no fee if the new slot is booked now | Wed 09:30, Wed 14:00 |
| Maple Studio | move Fri to Mon | fits the window; deposit unchanged | Mon 10:00, Mon 15:00 |
| Corner Yoga | cancel Sat 08:00 | 12 hours' notice, inside the 24-hour window; the rules charge the late-cancellation fee | none; the fee is stated as the rules say, and a person decides any waiver |

## No-shows and late cancellations (since 2026-09-28)
- **Lakeside Yoga, Fri 15:00, no-show**: draft ready. The rules charge no fee for a first no-show.
- **Oak Row, Sat 10:00, no-show**: second no-show in 60 days; draft ready. The rules charge 50 percent of the booking; the draft states that and offers a rebooking link.
- **Corner Yoga, Sat 08:00, late cancellation**: draft ready, stating the fee as the rules state it.

Counts for `knowledge/no-show-patterns.md`: 2 no-shows and 1 late cancellation from 42 bookings; all three from the web booking form.

## Open slots
- 6 open slots this week that the rules allow. 2 of them sit in the 14:00 block on Wednesday, which fits Birch Hill's move.

## Rule questions for a person
- Is a 45-minute buffer needed after a deep clean? The rules say 30; two crews asked on 2026-10-01. Owner to decide.

## Could not read
- The export has no time zone field for the Saturday bookings. They are read as studio local time and marked as such.

## Sources
- Bookings export 2026-10-04 (task T-77); `knowledge/booking-rules.md`; `knowledge/exceptions.md`
```
