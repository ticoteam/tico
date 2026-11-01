# Booking practice

The standing method behind `playbooks/weekly-bookings-page.md` and the three skills. `booking-rules.md` holds this team's
rules. This file holds how reminders, no-shows and clashes are handled, and what the evidence says, so each run does not
re-derive it. Figures name their source and the year.

## Reminders and no-shows

- Reminders reduce no-shows, according to the healthcare evidence. A systematic review of telephone and SMS reminders for
  hospital appointments (Hasvold and Wootton, 2011, from the published abstract and search summary; not read in full) found
  a reduction in non-attendance in its pooled studies. The effect is reported as a share of the baseline no-show rate,
  so check the paper before you quote a percentage.
- In the same review, staff phone calls did better than automated messages, and the lead time made little difference
  between a reminder the day before and one a week before. This is healthcare evidence; a salon, a studio or a repair
  visit may behave differently. Test before you assume.
- Wording mattered in one large test. Berliner Senderey and colleagues (PLoS One, 2020) randomised about 161,000 scheduled
  appointments with a valid mobile number across 596 outpatient clinics in one health system in Israel, to nine SMS versions
  sent five days ahead. No-show was 21.1 percent under the routine message and 14.2 percent under a message framed around
  guilt. Each version was tested only against the routine message. The framing question for a booking business is one of
  tone the team is willing to use, not a rule to copy.
- Vendor blog claims of reductions of 30 to 60 percent were not verified and are not used here.

## How to test a reminder change

1. Change one thing: the wording, the send time, or the channel.
2. Keep the old message as the control for the same kind of booking, for at least four weeks.
3. Count no-shows for each version, with the bookings in each group written down.
4. Report the change only when the counts are large enough to read. Small counts are an observation, not a result.

## Definitions to keep fixed

- **No-show**: booked, not attended, not cancelled before the cancellation window closed.
- **Late cancellation**: cancelled inside the cancellation window.
- **Clash**: two bookings for the same person, room or crew at overlapping times, or one booking that breaks an opening hour
  or a required buffer.

Write these into `knowledge/booking-rules.md` as the team's own rule when the owner confirms them. Count with them every week.

## Time zones and daylight saving

- Read every booking time in the calendar's own zone and name the zone on the page.
- The weeks around a daylight saving change are the most common source of double bookings and missed slots. Check those
  bookings by hand against the export.
- A booking made in one zone and shown in another is a clash until the zone is confirmed.

## Deposits and fees

- A deposit or fee is quoted only as the written rule states it. A waiver is a person's decision, recorded in `exceptions.md`.
- Consumer rules on deposits and cancellation fees differ by place. Check the local rule before a fee is written into the
  rules. This file does not set one.

## Sources

- Hasvold and Wootton, systematic review of telephone and SMS reminders for hospital appointments (2011): abstract and search
  summary read 2026-11-01; full text not read.
- Berliner Senderey et al., "It's how you say it: systematic A/B testing of digital messaging cut hospital no-show rates", PLoS
  One, 2020 (e0234817): PubMed Central article read 2026-11-01, results section and abstract.
