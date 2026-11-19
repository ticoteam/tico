# The NOT-in-scope ledger

`knowledge/not-building.md` is the one list of what the team decided not to build, or not to build yet. It exists so that a decision is made once, anyone can see why, and the same request gets the same answer until something changes.

Rules:

- One row per item. A repeat request adds a requester to the row; it never adds a second row.
- A changed verdict rewrites the row in place and adds a dated line to its history. Rows are never deleted.
- No customer names, email addresses or other personal details. Requesters are task, ticket or account ids.
- No promises and no dates of delivery. The only dates are when it was decided and when to look again.
- Publish after every change: `tico file publish knowledge/not-building.md`.

## Format

Keep three sections, in this order, newest row first in each.

    # Not building

    ## Later
    ### <the item, in one line a customer would recognise>
    - Verdict: Build later, 2026-11-18, by <bot or person>
    - Problem: <who is trying to do what, and what stops them>
    - Reason: <the one specific reason it is not now>
    - Evidence on the day: <thin or pattern>; <n> distinct accounts; <kinds of source>; <finding or evidence ids>
    - Weight: <screens, settings, states, in counts>
    - Back if: <the event or number that makes it "now">
    - Look again: <date>
    - Asked by: <task, ticket or account ids>
    - History: <dated lines, added when anything changes>

    ## Don't build
    ### <the item>
    - Verdict: Don't build, <date>, by <bot or person>
    - Problem: ...
    - Reason: ...
    - Evidence on the day: ...
    - Weight: ...
    - Back if: <what would have to change; "the strategy changes" is allowed>
    - Asked by: ...
    - Instead: <what they can do today, tested on <date>>
    - History: ...

    ## Not a product problem
    ### <the item>
    - Routed to: <bot or person>, task <id>, <date>
    - Problem: ...
    - Asked by: ...

## A good reason and a bad one

A reason is good when it could not be pasted onto another row.

| Bad | Good |
|---|---|
| Not a priority right now | Two accounts have it; both already use the CSV export for the same job |
| Too complex | Needs a template editor: 6 or more settings, and every invoice change tested against every layout |
| Doesn't fit the roadmap | Only helps teams with employees; the strategy serves solo trades this year |
| Maybe later | Depends on text-message sending, which the product does not have |

## A good trigger and a bad one

A trigger is something a bot can check.

| Bad | Good |
|---|---|
| When we have time | 5 distinct accounts ask (2 today) |
| If customers want it | Under 60% of invoices equal their quote in a 90-day query |
| Next year | Text-message sending ships (task P212) |

## In a spec

A spec's **Out** lines are the ledger rows for that feature, shortened to "the item: the reason". The spec does not repeat the evidence; it links the ledger. Anything a reviewer or another bot proposes during Related input that is not accepted gets a row here in the same run.

## Example rows

Invented, for illustration.

    ## Later
    ### Send the invoice by text message
    - Verdict: Build later, 2026-11-18, by Invoicing Feature Owner
    - Problem: A tradesperson's customer does not read email on site, so the invoice is seen days later.
    - Reason: Thin evidence, and the product cannot send text messages yet.
    - Evidence on the day: thin; 2 distinct accounts; calls only; E-0412, E-0457
    - Weight: 1 new channel, 1 setting (number to send from), delivery failures to handle
    - Back if: 5 distinct accounts ask, or text-message sending ships for reminders
    - Look again: 2027-02-16
    - Asked by: accounts 2871, 4417
    - History: none

    ## Don't build
    ### Custom invoice layouts and colours
    - Verdict: Don't build, 2026-11-18, by Invoicing Feature Owner
    - Problem: An account wants invoices that look like its own business.
    - Reason: Needs a template editor (6 or more settings) for 2 accounts; the logo and business details already print.
    - Evidence on the day: thin; 2 distinct accounts; tickets only; E-0398, E-0431
    - Weight: 1 editor screen, 6 or more settings, every invoice change tested per layout
    - Back if: a pattern of accounts loses work because of the standard layout
    - Asked by: accounts 3310, 1954
    - Instead: Settings, Business, Logo. Tested 2026-11-18 on a test account.
    - History: none
