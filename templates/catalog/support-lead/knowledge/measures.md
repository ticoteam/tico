# Support measures

What each time measure counts, so a number in a summary says what it measured. How fast is fast enough is
the team's own call and lives in `targets.md`, with its date and who set it.

## Definitions

- **First response time**: from the start point to the first public reply an agent sent the customer.
  Internal notes do not count.
- **First resolution time**: from the start point to the first time the ticket is set to solved. A ticket
  that is reopened keeps this value.
- **Full resolution time**: from the start point to the last time the ticket is set to solved, so a reopen
  extends it.
- **Requester wait**: the time a ticket sits in New, Open or On-hold. Time in Pending, when the ticket waits on
  the customer, is left out.

These are one help desk's definitions. Another tool may count the same words differently, so compare numbers
only when both use the same definition, and quote the definition beside the number.

## Start and end points

- The start point is the customer's first message when the support mailbox was read. It is the task's creation
  time when only tasks were read. Say which one the figure uses.
- A target written in business hours is measured on business hours. Say which calendar was used.

## How to report a spread

- Give the median (the middle value) and the slowest tenth (the 90th percentile) together, with the count behind
  both.
- An average on its own hides one long ticket. It moves for a month after that ticket closes.
- Under ten items, the percentile is noise. Give the count and the longest time instead.

## Sources

- 2026-11-01: Zendesk help centre, "About native Support time duration metrics" (undated page): definitions of
  first reply, first resolution, full resolution and requester wait.
- 2026-11-01: Metabase, "Metric: Support, first response time" (undated vendor metric guide; search excerpt
  read, not the full page): median and 90th percentile preferred to averages because response times are skewed.
