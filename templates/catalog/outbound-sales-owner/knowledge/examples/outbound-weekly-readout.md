A sample of excellent output for a fictional team. Every name in it is a stand-in and every figure is invented.

```markdown
# Outbound read-out, week of 2026-10-26 to 2026-11-01

**Headline: 3 meetings booked against a target of 4 a week (goal "Outbound meetings"). 419 sends across two sequences; 12 positive
replies, 2.9 per 100 sends. The one change to test is on the task for approval.**

Last week: 388 sends, 9 positive replies, 1 meeting.

## By sequence and step

| Sequence | Step 1 | Step 2 | Step 3 | Step 4 | Sends | Positive | Per 100 sends | Meetings |
|---|---|---|---|---|---|---|---|---|
| A: Second-site openers | 210 | 56 | 25 | 10 | 301 | 8 | 2.7 | 2 |
| B: Booking-queue question | 90 | 22 | 6 | 0 | 118 | 4 | 3.4 | 1 |

Sequence B is too few to call: 118 sends against the floor of 200 per side. Its figure is shown and not compared.

## Guardrails

| Sequence | Opt-outs | Bounces | Spam complaints | Flag |
|---|---|---|---|---|
| A | 2 (0.7%) | 5 (1.7%) | 0 | none |
| B | 0 | 2 (1.7%) | 0 | none |

Opt-outs are counted here, not as replies. Both were honoured the same day and are on the do-not-contact list.

## Meetings

- Sequence A, two meetings: an operations lead (step 2 reply) and a studio manager (step 1 reply, after a first line about a new
  location). Handed to whoever takes calls, on the task with notes.
- Sequence B, one meeting: a reply at step 1 asking for a time. Handed over the same day.

## Sends not reconciled

The mail export for 2026-10-29 was not attached. That day's sends (41 across both sequences) come from `knowledge/sequences.md` and
are marked unreconciled. The totals above include them.

## Proposed test (for approval, not run)

Sequence A, step 1. Hypothesis: a first line that names a dated fact about the account beats a first line that names the role, for
the same accounts. Control: the current first line. A 50/50 random split of the same 420 contacts, before send. Primary metric:
positive replies per unique send at 7 days. Guardrails: opt-outs; stop an arm early if its opt-outs reach twice the control's.

## Could not read

The CRM was not connected, so the open-deal check for this week's contacts used the account list and the note of the sales lead (`sales-lead`) only.

## Sources

- `knowledge/sequences.md`, lines dated 2026-10-26 to 2026-11-01.
- Sequence mailbox export for the same dates, except 2026-10-29 (not attached).
- Goal "Outbound meetings" in `tico goal list`, read 2026-11-02.
```
