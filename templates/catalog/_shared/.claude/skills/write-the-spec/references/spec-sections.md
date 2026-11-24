# The sections of a spec, one by one

Use it in steps 5 and 6. For each section: what it is for, what goes in, and a bad and a good line. The examples are invented.

The section ids are the ones Tico's templates use. `page.py check` requires `criteria` and `scope` in every spec, plus `pictures` in a screen spec, `behaviour` in a change spec, and `report`, `cause` and `solution` in a bug spec. Each section is an H2 the contents link. Delete an optional section with nothing to say, and its link.

## The top

### The line under the title (`<p class="desc">`)

For: everyone. What the page is and what it is for, in one plain sentence starting "This is a spec to". It is the goal: who it is for and what they can do afterwards that they cannot now. At most 40 words. Anything the OK must cover knowingly (a cost, a change customers will notice, billing, privacy) goes in it too.

- Bad: "Improve the invoicing flow."
- Bad: "Invoices go out days late because Mark done returns to the list; this spec fixes that; OK it or send it back."
- Good: "This is a spec to let a tradesperson send a job's invoice with one tap when they mark it done."

### Report and cause (bug spec only: `#report`, `#cause`)

Two one-line bullets in `#report`. The report is the customer's or teammate's own words, cut to one line, with who (by id) and where. The cause is the root cause in the code with `path:line`, found, not guessed.

- Bad: "Users report sync issues. Probably a race condition."
- Good: "Reported: 'Sent the invoice twice to the same customer', account 3052, ticket T-2291, 2026-11-09." and "Cause: `invoices/send.py:88` retries on timeout without checking the first send."

### Pictures (`#pictures`, or `#solution` in a bug spec)

For: carrying the idea. Before and after, phone then desktop, one pair per screen in the order a person meets them. At most five arrows a picture. Under each picture, one line per arrow number.

- Bad caption: "The new improved job screen."
- Good caption: "After, phone. 1: Mark done opens the invoice. 2: lines and total from the quote. 3: Send."

A change with no screen (`#behaviour`): two code blocks, the real call and real output today and with the change.

## The sections

### Done when (`#criteria`)

For: the builder, the reviewer and QA. This list becomes the QA plan. One behaviour per criterion, numbered, Given / When / Then, tagged *before release* or *after launch*. An after-launch criterion names who reads it and when.

- Bad: "Invoices should send reliably and quickly."
- Good: "4. Given Send was tapped twice, then one invoice exists and one email was sent. *before release*"

End with one line, "Not proved yet:", naming what the criteria cannot show before release, or "nothing".

### Scope (`#scope`)

For: stopping the build from growing. **In**: the parts that will exist. **Out**: what a reasonable reader would assume is included and is not, each with its reason. A "later" item says its trigger.

- Bad: "Out: anything not mentioned above."
- Good: "Out: sending with no tap: a wrong invoice costs more than a late one."

### Real cases (`#scenarios`, added by this skill)

For: proving the spec fits what actually happened. Three to five lines. Account by id and kind, what happened and when, and the criteria that now cover it. A real case the spec does not cover is listed too, with "not covered: see Out".

- Bad: "As a busy user, I want to invoice quickly so that I get paid faster."
- Good: "Account 4417, solo electrician: finished four jobs on 2026-10-27 and invoiced all four on 2026-11-01, from home. Covered by 1, 2."

A persona or a user story is not a real case. If there are no real cases, write "missing: no recorded cases; first three come from `learn-from-customers`".

### Why now and how we will know (`#why`)

For: the person deciding whether this is worth doing, and the person who will judge it later. The main number as a chart (`<figure class="chart">`), then one-line bullets: evidence (count, kinds of source, date, finding id), one exact quote, the appetite, and the measure with its baseline, target, read date and guard.

- Bad: "Many customers have asked for this and it should increase engagement."
- Good: a chart of days from done to sent (median 3.2 across 1,904 jobs, query `invoice-lag`, 2026-11-12), then "17 accounts send invoices days after the job (finding F-03, 2026-11-10)." and "Appetite: small. Read 2026-12-16."

A bug that only needs fixing says who it hurts and how often, in one line.

### Plan (`#plan`)

For: the builder, who is usually you. Steps in order, each with the files or screens it touches and the test that will prove it. Written after reading the code. Not a design document.

- Bad: "Update the backend and frontend accordingly."
- Good: "2. `jobs/done.py`: after marking done, return the draft invoice id. Test: `test_done_returns_invoice`."

### Release needs (`#release`)

For: whoever ships it. Apps or services that carry it, data changes, settings, flags, an app-store release, anything a person must do by hand. "None." where none.

### Open questions (`#questions`)

For: making gaps visible instead of hidden. Each question, who owes the answer, by what date, and what is assumed until then. Nothing here may be a question whose answer changes what is built; those are answered before publishing.

- Bad: "TBD: error handling."
- Good: "Should 'Ready to send' invoices send by themselves when the connection returns? Owner, by 2026-11-21. Assumed until then: no, the tradesperson taps Send again."

### Sections the team added

Anything `knowledge/spec-format.md` names is its own H2, after Release needs.

## What a spec does not contain

- Options that were not chosen (they are on the ideas page and in the ledger), or options for the reader to pick from.
- A timeline or a delivery date.
- Personas, user stories without a real account behind them, market sizing.
- Drawn mock-ups or wireframes.
- Any customer's name, email address or phone number.
