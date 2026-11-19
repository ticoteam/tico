# Outcome plan

One per account, saved as `knowledge/accounts/<customer-key>.md`. Keep it to one screen. It is the first thing any
teammate reads before touching the account. Use the customer key and role names; keep personal details (private
phone numbers, home addresses, anything not needed to do the work) out of the file.

Fill every line from a named source. Where there is none, write the gap marker and the date you will fill it.

```
# <Customer> (key <customer-key>)
Plan version <n>, updated <date> by <teammate>. Confirmed by the customer on <date> / not yet confirmed.

## Why they bought
In their words: "<exact quote>" (<who said it>, <where>, <date>)
What it costs them today: <figure in their units, with their source> or [GAP: cost today, ask at kickoff]

## The result they want
Measure:        <what is counted, in their terms>
Starting value: <number> (<their source>, <date>)
Target:         <number> by <date>
Why that date:  <the event on their side that makes the date matter, if any>
How we will both know: <who reads the measure, from where, how often>

## How they want to be worked with
People:   <role: what they care about>, one line each. Champion: <role>. Decides renewals: <role>.
Rhythm:   <how often, which channel, who attends reviews>
Hands-on: <we set it up / we guide them / they do it themselves>
Avoid:    <anything they asked us not to do>

## What was promised at the sale
- <promise, with source>   (anything unusual: custom work, a date, a price hold)

## Milestones to first value
First value for this customer means: <the state they can see for themselves>

| # | The customer can show | Owner | Due | Done |
|---|---|---|---|---|
| 1 | | us / them | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |

## After first value
Next result they care about: <stated by them, or "not yet stated">
Risks we know of: <one line each>

## Contract facts
Plan and price: <...>   Term: <start to end>   Renews: <auto / manual>
Notice date: <date>, counted from <clause>   Payment: <method, terms>

## Log
<date>: <one line per meaningful event: milestone reached, band change, review held, promise made>
```

## Rules for filling it

**The quote.** Copy it exactly from the call record, email or form. If there is no quote, ask at kickoff: "If this
works, what will be different for you in three months?" Do not write a goal for them.

**The measure.** Something the customer already counts, or can count without new work. If they have no starting
value, the first milestone is "starting value recorded". A target with no starting value cannot be shown to be met.

**No number at all.** Some goals are real and unmeasured ("stop the Monday scramble"). Keep their words, and agree
one observable sign: "Monday's schedule is out by Friday 5pm, four weeks running."

**Milestones.**
- Four to six. Fewer than four and the first one is too big; more than six and it is a task list.
- Each is a state the customer can show, written from their side: "first payroll run from the new system", not
  "payroll module configured".
- Each has one owner and a date. Where the owner is the customer, you still own noticing when it slips.
- The last milestone before first value should be reachable within the target time from setup. If it is not, the
  plan is wrong or the product is the wrong fit; say which.
- Put the hardest dependency first: data import, a sign-off from their IT person, a connection to another system.

**Appropriate experience.** Two customers with the same goal can want opposite treatment. One wants a weekly call;
another wants never to be called. Ask once, write it down, and follow it. Getting the result while being worked with
in a way they dislike is half a success.

**Confirming it.** Send the top three sections to the customer in plain text and ask if it is right. Record the date
they confirmed. Until then the plan says "not yet confirmed" and health cannot be green.

**Keeping it current.** Change the plan when their goal changes, when a person changes, or when a milestone is
re-dated, and bump the version. A plan untouched for a quarter is a sign nobody is working the account.

## Segment plans for the long tail

For accounts not worked one by one, write one plan per segment in `knowledge/accounts/segment-<name>.md`:

- the goal most customers in the segment state, with the share who state it and where that figure comes from;
- what first value is and the event in the product that shows it;
- the three to five triggered messages that move them along, each tied to an event or its absence (no first value
  after N days, where N is the segment's median plus a margin you state);
- the trigger that moves an account out of the segment plan and into one-by-one attention: a hard signal, a reply
  that needs a person, or revenue above the line set at setup.

Segments themselves are defined in Tico Growth (`tico growth segment list`). Use those, not a private list.
