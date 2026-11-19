# The use-case grid

Use it in step 4. The grid finds the cases a builder would otherwise have to guess. It is a thinking tool: the grid itself does not go into the spec. What goes into the spec is one criterion or one Out line for every cell the change touches.

## How to fill it

1. Write the actors across the top. An actor is anyone, or anything, that does something or receives something because of this change:
   - each kind of customer the feature is for;
   - the person on the other end (the customer's customer, an invited member, a recipient);
   - an admin or the account owner;
   - a teammate or bot who supports it (Support looking at the record, a job that runs at night);
   - an outside system (the email provider, the payment processor, an integration).
2. Write the states down the side (the list below).
3. For each cell ask: **can this actor be in this state when the new behaviour runs?**
   - No: leave it blank. It does not go into the spec.
   - Yes, and it works like the main path: mark it "main"; the main criterion covers it.
   - Yes, and something different must happen: write a criterion.
   - Yes, and we are choosing not to handle it: write an Out line with the reason, and say what the customer sees instead (usually today's behaviour, or a plain message).
4. Walk each real scenario through the grid. A scenario that lands in a blank cell means the cell is not blank.

## The states

| State | The question to ask |
|---|---|
| **First time** | What does someone see who has never used this, with nothing set up? |
| **Empty** | What if there is nothing to act on: no items, no history, no result? |
| **One** | Does it read correctly with exactly one (wording, layout)? |
| **Many** | What happens at 100 or 10,000: order, paging, speed? |
| **The limit** | What is the maximum (length, size, count, plan limit), and what happens at it and one past it? |
| **Wrong input** | What can be entered that is not valid, and what does the person see? |
| **No permission** | Who is not allowed to do this, and what do they see: nothing, a disabled control, a message? |
| **Offline or slow** | What if the connection drops halfway, or the other system takes 30 seconds? |
| **Fails** | What if the step fails outright? What is kept, what is undone, what is the person told, can they try again? |
| **Sent twice** | What if the action is done twice (double tap, retry, refresh)? Is there one result or two? |
| **Two at once** | What if two people, or a person and a bot, act on the same thing at the same moment? Who wins, and does the other know? |
| **A time boundary** | Midnight, month end, a time-zone difference, daylight saving, an expiry: does anything change meaning? |
| **Undo** | Can it be reversed? Until when? What if it has already had an effect (an email sent, money moved)? |
| **Existing data** | What happens to records created before this change? Do they gain the new behaviour, keep the old, or need converting? |
| **Leaving** | What if the account is cancelled, the member removed, the item deleted, while this is in progress? |
| **Other plans or settings** | Does it behave differently on another plan, role, language or device? |

Not every change touches every state. A copy change touches almost none. A change that sends, charges or deletes touches **Fails**, **Sent twice**, **Undo** and **Existing data** every time: those four are never left blank for such a change.

## From cell to spec line

A criterion from a cell:

    Given <the state, stated exactly>, when <one action>, then <what the actor sees or what record exists>.

An Out line from a cell:

    Out: <the case, in words a reader would recognise>: <the reason>. They see <what happens instead>.

Bad: "Handle errors gracefully."
Good: "Given no connection, when they tap Send, then the invoice is saved as 'Ready to send' and the screen says so."

Bad: "Out: edge cases."
Good: "Out: jobs with more than one quote: 3 of 1,904 jobs (query `multi-quote`, 2026-11-12). They see the invoice empty with the customer filled, as today."

## When the grid gets big

If more than about fifteen cells need their own criterion, the feature is doing several things. Go back to `decide-what-not-to-build` and cut an actor or a path, or split the feature into parts with one spec each.

## Example

Invented, for illustration: "marking a job done opens a ready invoice with one Send button".

| State | Tradesperson | Their customer | Email provider |
|---|---|---|---|
| First time | main | main | |
| Empty | Job has no quote: criterion 3 | | |
| The limit | Quote with 60 lines: main (the invoice screen already scrolls) | | |
| No permission | A team member who cannot invoice: Out, they see Mark done as today | | |
| Offline or slow | Criterion 5 | | Provider slow: main (the existing send queue) |
| Fails | | Address bounces: Out, existing bounce notice applies | Criterion 5 covers retry |
| Sent twice | Criterion 4 | Receives one email: criterion 4 | |
| Undo | Job marked done by mistake: Out, "Reopen job" exists today and leaves the unsent invoice as a draft | | |
| Existing data | Jobs done before release: Out, unchanged | | |
