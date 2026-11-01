A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme tier 2 report, Thu 2026-10-01

Sample output for Acme, a fictional software team. Every ticket, issue and name is invented.
No reply has been sent and no issue filed. First draft, not yet reviewed.

**Headline: 9 tier 2 tickets open (3 waiting on engineering); the calendar-sync timezone bug caused 5 tickets this month and has no fix date.**

## Finding
- **Verdict: bug, reproduced twice.** Calendar sync shifts classes by one hour for locations outside the account's
  timezone. 5 tickets since 2026-09-03 (T-2066, T-2090, T-2131, T-2144, T-2170). Reproduced in the sandbox on
  2026-09-29, two runs: a class created at 18:00 in one zone on an account set to another appears at 17:00 after
  sync. Issue 488 reported 2026-09-29, no engineering response yet. Workaround (tested on the same version): set the
  location's own timezone under Settings, Locations. The engineering owner has been asked whether it can be
  scheduled.

## Solved this week
- T-2152: webhook "not firing" was a URL with a trailing space. Setup; reply ready to use.
- T-2158: CSV import rejected dates as 30/09/2026. Doc gap: the import doc does not say the format.
  Task to the Librarian; reply with the workaround ready.

## Waiting on engineering
| Ticket | Issue | Customers | Days since report | Last movement |
|---|---|---|---|---|
| T-2170 | 488 | 5 | 2 | none |
| T-2139 | 476 | 1 | 9 | labelled 2026-09-24 |
| T-2101 | 461 | 2 | 16 | fix in review 2026-09-30 |

## Could not reproduce
- T-2163: bookings page blank on one studio's tablet. Two runs in the named browsers, both fine. Need the browser
  version and a screen recording. Asked the Support Agent for both.

## Replies ready to use
- T-2152, T-2158 (on the Support Agent's tasks). Owner: the head of support.
```
