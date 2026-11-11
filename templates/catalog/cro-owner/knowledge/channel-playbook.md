# CRO playbook

How {{company_name}} runs CRO, and what the CRO Owner may do by itself. This is the starter: the CRO Owner fills in the
blanks at setup and publishes it as the page "CRO playbook". To change a line, tell the CRO Owner on a task; it
changes this file and publishes the page again.

## The basics

- **Channel tag:** `channel:cro`. Every CRO task carries it.
- **KPI:** visits that become signups, as a percent. Read weekly in product analytics: signups divided by visits, the
  same week, the same source. It sits under the North Star.
- **Tools:** product analytics (read is enough to start) and the public web. The site's repository when connected.
- **Who puts changes live:** (filled at setup: the person who edits the pages and runs the testing tool). Until a Tool
  lets the CRO Owner change the site, this person applies each change a person OK'd.
- **What must never change or be tested:** (filled at setup). Always on the list: legal text, and a price shown to an
  existing customer.
- **A test or a fix:** a bet is an A/B test when the page has enough visitors for the test to finish in 4 weeks. The
  CRO Owner works out the sample size and the stop rule before it starts (`skills/size-a-test-before-it-starts/SKILL.md`)
  and reads it once, at its planned end. Under that, the CRO Owner makes known fixes instead: well-known problems such
  as a broken form, a slow page or a missing price. Each is a bet measured before and after, and its Go-live page says
  plainly that it is not an A/B test, and why.

## What Watch checks

Daily, at 07:00.

- Each watched page or flow: the conversion rate of each funnel step for the last 7 days, against its range, with the
  count behind each rate. "Signup form to signup: 630 of 1,500." A rate without its count is not a reading.
- Tracking: each step's event fired on every day with visitors, and no later step is above an earlier one. A broken
  event is a tracking problem first, never a slip.
- A running test's arm counts and its sample ratio check. Never its main number before the planned end.
- On Mondays, the KPI: visits that become signups, for last week.

## Where ideas come from

- Product analytics: the funnel step that loses the most people against its own best of the last 13 weeks
  (`skills/find-the-leak-in-a-funnel/SKILL.md`).
- The conversion pages as a visitor sees them, on a phone and on a computer: a broken form, a slow page, a missing price.
- What competitors' pricing and signup pages do that ours do not (`tico market delta`).
- Tests that read inconclusive: a bigger change, or another page.
- Watched pages and flows that were retired: what to try instead.

## Upkeep the CRO Owner does by itself

No person's OK needed. Done within granted Tools, or filed with the exact change for the person who puts changes live.

- Put back a page or form a person OK'd that broke or was changed: a field, a button, a link, copy that was lost.
- Fix a broken tracking event, so each step is counted again. With no Tool, file it for the person, with the event,
  the step and the day it stopped.
- Keep `knowledge/funnel.md` and `knowledge/pages.md` current.

## What needs a person

- Any new page change or test: that is a bet. A person OKs its Go-live page first.
- Anything on the "never change" list above.
- Any price, plan, trial length, discount or checkout setting.
- Stopping a test early for any reason other than a broken page.

## Never

- Read a test's main number before its planned end, or call a winner from fewer people than planned.
- Write a rate without its count, or a step with no data as zero.
- Promise a lift. A test that finds no change is a result.
