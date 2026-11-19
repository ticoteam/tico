---
name: run-lifecycle-and-paid
description: Build and run the company's lifecycle email programmes, paid search and paid social, and the conversion tests that connect them, then report what each returned. Lifecycle - maps customer states, writes each sequence spec (trigger, goal, exits, timing) before any copy, and keeps mail deliverable (SPF, DKIM, DMARC, one-click unsubscribe, complaint rate). Paid - sets a break-even cost per customer, a weekly check that ends every campaign in HOLD, FIX, TEST or INVESTIGATE, and rules for scaling and cutting inside the budget. Testing - fix what is plainly broken, size a test before it starts, never stop early, read the interval. Use when a task says email sequence, drip, onboarding or welcome emails, win-back, newsletter, deliverability, spam, unsubscribe, Google Ads, Meta ads, LinkedIn ads, PPC, paid acquisition, ad budget, CPA, ROAS, CAC, conversion rate, CRO, A/B test, split test, sample size or "is this result significant".
metadata:
  function: marketing
  roles: [email-owner, search-ads-owner, social-ads-owner, cro-owner, marketing-operations-manager, growth-manager, retention-specialist]
  actions: [read, draft, send, spend, change-records]
  review: none
---
# Run lifecycle and paid

When to use: an email programme, a paid channel or a conversion test needs building, running or reading; or the weekly
check on any of them is due.

Words used here: **lifecycle email** is mail triggered by what a person did or did not do (signed up, stalled, bought,
went quiet), as opposed to a **broadcast** sent to a list on a date. **A sequence spec** is the one-page plan for a
series, written before the copy. **Break-even cost** is the most the company can pay for one new customer without
losing money on them. **A counter-metric** is the number that must not get worse while the main one improves.

Tico words: **the owner** is the person who runs the team and gives OKs. `knowledge/<file>.md` is a file in your own
knowledge folder, kept between runs. **A task** is one piece of work with its own thread. **An ask** is a question put
to a person on a task, and **an OK** is their yes to the exact version shown (`tico task ask <task-id> --to <person>
--kind ok "<question>" --show page:<n> --version N`). **A page** is an HTML report published into Tico with `tico page publish
<file.html> --title "<title>"`. **A Tool** is a connection to an outside system (the site, mail, ads, analytics) that
this bot has been granted. **Growth Experiments** is the pipeline for a change that carries a prediction: its
**Go-live page** shows the change and the predicted number, and a person OKs it before it runs. **The Brand card** is
the company's published voice, names and allowed claims (`tico brand show`).

This skill is three parts that share one set of numbers: Part A lifecycle email, Part B paid, Part C tests. Run the
part the task needs. The words themselves come from `write-marketing-copy`.

## What great looks like

- **Money in, money out, one page.** For every programme and campaign you can say what it cost, what it returned in
  paying customers counted from the company's own records, and whether to do more of it.
- **Mail that arrives and is wanted.** Authenticated, sent only to people who agreed, easy to leave, with complaint
  rates well under the mailbox providers' limits. Each email has one job.
- **Paid that is run, not watched.** Every campaign gets one of four verdicts each week and the action is taken the
  same day. Budgets move toward what pays back and away from what does not.
- **Tests that mean something.** Sized before they start, run to the planned end, read as a range. Few, and on the
  things that matter. What is plainly broken is fixed, not tested.
- **Nothing learned twice.** Every result, including the failures, is on file where the next run will read it.

## First time at a company

Read `knowledge/lifecycle-and-paid.md`. If the setup answers are there, skip this. If not, ask the owner these in one
numbered message, save the answers there with the date, and start on the defaults at once.

1. What is a new customer worth, and what may we pay for one? Default: if `measure-unit-economics` has run, I use
   its numbers and your payback target. If not, I compute the gross margin an average customer brings in their first
   12 months from the billing records. That is the break-even cost. The target is one third of it, a placeholder
   and not a benchmark, until you set another. I show you both numbers.
2. What is the monthly budget for each paid channel? Default: the budget on each channel's record
   (`tico growth channel show <slug>`). With no budget on record I spend nothing: I prepare the plan and ask once.
3. Who may we email, and how did we get their addresses? Default: only people who gave their address to the company
   directly. US rules apply (marketing mail is allowed until the person opts out). For addresses in the EU, UK or
   Canada I send marketing mail only with recorded consent. Bought, rented or scraped lists are never used.
4. How often may one person hear from us? Default: at most two marketing emails in any seven days, with a sequence
   taking priority over a broadcast. Receipts and other service messages are not counted.
5. What counts as a conversion? Default: a paying customer in the billing records. What an ad platform or mail tool
   reports is a signal for steering, never the count that is reported.
6. Which tools and sending domain do we use? Default: what is connected under Tools. Marketing mail goes from its own
   subdomain, separate from the mail people in the company send by hand.

## Steps

### Part A: lifecycle email

1. **Map the states.** From the records, count how many people are in each state today and how many moved in the
   last 28 days: signed up, reached first value, paying, expanding, going quiet, lapsed, cancelled. Define each state
   by something a record shows. The step where the most people stall is where the first programme goes.
2. **Check the foundation before any send.** Work the deliverability checklist in
   `references/lifecycle-programs.md`: SPF, DKIM and DMARC pass and align with the From domain; marketing mail has
   one-click unsubscribe; the complaint rate is under 0.1%; bounces and unsubscribes are suppressed; each segment has
   a lawful basis. A failure here is fixed first. Sending more mail from a broken base makes everything worse.
3. **Write the sequence spec before the copy.** One page per programme, in `knowledge/lifecycle-and-paid.md`:
   trigger, goal (one number from the records), who is included and excluded, exit conditions, each email's job,
   timing and action, the main metric and its counter-metric, and the holdout. Build in this order unless the state
   map says otherwise: onboarding to first value, a stalled or abandoned step, after purchase, going quiet and
   win-back, then broadcasts. `references/lifecycle-programs.md` has a spec for each.
4. **Write and send.** One email, one job, one action (`write-marketing-copy`). Before each programme goes live and
   before each broadcast, run `check-a-send-before-it-goes` when the team has it; without it, use the pre-send list
   in the reference. Programmes inside the limits in Autonomy go live on your own.
5. **Read it weekly.** Delivered, bounces, complaints, unsubscribes, clicks, and the goal number against the holdout.
   Never judge by opens: mail apps fetch images on their own, so open counts are inflated. A programme that does
   not beat its holdout after its planned sample is rewritten or switched off.

### Part B: paid search and social

6. **Earn the right to spend.** Three things first: tracking is trusted (run `check-campaign-tagging`, then compare
   last month's platform conversions with the billing records and write down the gap); the break-even and target
   cost per customer are on file; and each ad lands on a page that repeats its promise. A missing one is a FIX, and
   budgets do not go up until it is done.
7. **Set the structure and the plan.** Follow `references/paid-operating-plan.md`: a brand campaign on the
   company's own name as a standing line whenever paid search has a budget, kept separate from non-brand; existing
   customers excluded from acquisition; few campaigns, so each gets enough conversions to learn from; bidding chosen by volume (`choose-a-bidding-strategy-from-volume`); about 70% of budget on what is proven
   and 30% on tests. Write the month's plan as a page: budget by campaign, the expected customers and cost each, and
   what would make you move money.
8. **Run the weekly check.** For each campaign, in this order: spend against plan, traffic, conversions, creative,
   landing page, tracking. Give each campaign one verdict and act on it the same day:
   - **HOLD**: inside target with enough data. Leave it alone; edits restart the platform's learning.
   - **FIX**: a known fault with a known remedy (a disapproved ad, a dead link, wasted search terms, a winner capped
     by budget). Do it now.
   - **TEST**: working, and a specific change might do better. File it in Growth Experiments.
   - **INVESTIGATE**: the numbers do not add up (platform and billing disagree, a sudden jump or drop). This
     overrides the other three: nothing is scaled or cut on numbers you do not trust.
   An input you cannot get is written MISSING, never guessed. Use the narrow skills for the detail:
   `read-search-terms-for-waste`, `diagnose-a-rising-cost-per-result`, `spot-creative-fatigue`.
9. **Scale and cut.** Judge a campaign only after it has spent three times the target cost per customer or reached
   30 conversions (a default: before that, say "too early"). Paying back under target: raise its budget about 20% at
   a time, with three to five days between raises. Above break-even after a fair run: cut or pause. Between the two:
   HOLD and test the page or creative. All of it inside the monthly budget.

### Part C: conversion and tests

10. **Find the leak, then fix before you test.** Use `find-the-leak-in-a-funnel` to find the step losing the most
    people. Walk the page in impact order: the offer and who it is for, the headline, the action, the order of the
    page, trust, objections, friction. Anything plainly broken (a dead button, a form that fails on phones, a price
    that disagrees with the ad) is fixed at once and logged. Testing whether a broken thing should be fixed wastes
    the traffic.
11. **Design the test.** Follow `references/testing.md` (and `plan-a-test-before-it-starts` when the team has it): one
    change; a written prediction; one main metric and one counter-metric; the sample size computed from the baseline
    and the smallest lift worth having (`size-a-test-before-it-starts`); the end date fixed in whole weeks. If the
    traffic cannot reach the sample in eight weeks, do not run it: say so, and either test a bolder change or ship and
    watch. File it in Growth Experiments with a Go-live page.
12. **Read it at the end, once.** Check the split first (a lopsided split means the test is broken). Then the
    difference with its 95% range, and the counter-metric (`read-a-test-without-peeking`). Verdict: ship, drop, or
    inconclusive, which is a real answer. Record it in `knowledge/tests.md` and the result label on the task.

### Every month

13. **Report and re-plan.** Publish the page in Output. Log the KPIs (`tico kpi log`; `--help` shows the form). Move
    next month's budget toward what paid back, and file the next three changes as tasks labelled with their
    `channel:<slug>`.

## Gates

- **Consent.** No marketing mail to an address without a lawful basis on record. Nobody in `knowledge/do-not-email.md`
  is mailed, ever. No bought, rented or scraped list.
- **Authentication.** No bulk send unless SPF, DKIM and DMARC pass and align. Checked 2026-11-18: Gmail and
  Outlook.com require this from anyone sending them more than 5,000 messages a day, Yahoo requires it from bulk
  senders without naming a number, and all three reject or junk mail that fails.
- **Leaving is one click.** Marketing mail carries the one-click unsubscribe headers and a visible link, plus the
  company's postal address. An unsubscribe is honoured at once (the legal outer limits are longer).
- **Complaints.** Keep the spam complaint rate under 0.1%. At 0.1% in a day, the programme gets INVESTIGATE. At 0.3%
  all marketing sends pause until the cause is found. Both figures are Gmail's published thresholds.
- **Frequency.** The cap in question 4 holds across all programmes together.
- **No spend above budget.** Not for a day, not to "catch up". No new channel, account or payment method on your
  own.
- **Tracking first.** No budget increase while a campaign's verdict is INVESTIGATE or its conversions are MISSING.
- **Reported results come from the company's records**, with window, source and date. A platform's own conversion
  count is labelled as the platform's.
- **Brand never flatters the totals.** The brand campaign runs by default, and its results are always reported on
  their own line, never blended into non-brand or paid totals.
- **No test without a sample size and an end date. No winner before the end.** Stopping the first time a result
  looks good can turn a 5% false-alarm rate into one above 25%.
- **Claims.** Ad and email copy passes the claims gate in `write-marketing-copy`. Testimonials pass
  `check-a-testimonial-before-it-is-mailed`.
- **Ad platform policy.** Read the platform's current policy for restricted categories (housing, jobs, credit,
  health, alcohol, politics) before targeting. When in doubt, file it for the legal owner.

## Autonomy

Do on your own, without asking:

- Read everything. Publish reports inside the team.
- Fix the base: authentication records through a granted Tool, suppression, a broken link, a disapproved ad, wasted
  search terms, tagging.
- Launch, edit and switch off lifecycle programmes to people who already receive that kind of mail, inside the
  frequency cap, with claims already allowed.
- Inside the monthly budget: start and stop ads and ad sets, shift up to 20% of a channel's budget between its
  campaigns in a week, refresh creative, add negative keywords.
- **Pause anything, at any time**: a send, a campaign, a test that is hurting customers or breaking a gate. Pausing
  never needs an OK. Say what you paused and why.
- Fix plainly broken pages. Run tests that change wording or layout on existing pages.

Needs an OK (one ask, with the page shown):

- Any spend above the monthly budget, a new channel, a new ad account or a new payment method.
- Mail to a new audience (a list, segment or country not mailed before), or a first send from a new domain.
- An offer: a discount, a credit, a guarantee, a price shown differently. A test that changes price or plans (use
  `set-and-test-prices` when the team has it).
- A broadcast to more than the limit the owner set (default: the whole list).
- Anything the legal owner flagged.

## Output

- **The monthly page**, "Lifecycle and paid" (HTML, `tico page publish ... --cadence monthly --as-of <date>`):
  1. Three lines on top: what it cost, what it returned, what changes next month.
  2. A table per channel and programme: spend, new paying customers (from records), cost per customer against target
     and break-even as a bar, and the verdict. Paid search shows brand and non-brand as separate rows, and every paid
     total is given for non-brand with brand beside it, never blended.
  3. The state map as a funnel with counts and the 28-day movement, each programme marked on the step it serves.
  4. Mail health: complaint rate, bounce rate and unsubscribe rate as small trend lines with the limits drawn.
  5. Tests: each with prediction, sample, result range and verdict.
  6. Paused, MISSING and "too early", with reasons.
- **The weekly check**: a short table, one row per campaign and programme: verdict, the one number behind it, the
  action taken.
- **Records**: `knowledge/lifecycle-and-paid.md` (setup answers, specs, the paid plan, change log),
  `knowledge/tests.md`, `knowledge/results.md`, `knowledge/do-not-email.md`.

## Checks before you finish

- Every reported customer count comes from the company's records, with window, source and date.
- Every campaign and programme has exactly one verdict, and its action was taken or filed.
- Paid search with a budget has a brand campaign, and brand is reported apart from non-brand.
- No budget was exceeded. Every budget move is in the change log with its reason.
- SPF, DKIM and DMARC were checked this month. The complaint rate is under 0.1%, or the programme is paused.
- Every send had an unsubscribe that works. You clicked it.
- Every running test has a sample size, an end date and a counter-metric. No test was read early.
- Opens were not used to judge anything.
- Anything paused is listed with the reason and what would restart it.

## Example

Invented, for illustration only.

"Weekly check, 2026-11-16, Larkfield Bikes. Target cost per customer $40, break-even $120 (first-12-month gross
margin per customer, billing records, 12 months to 2026-10-31).

| Campaign or programme | Verdict | The number | Action taken |
|---|---|---|---|
| Search, non-brand 'bike repair pickup' | HOLD | $36 per customer, 41 customers in 28 days | None |
| Search, brand | INVESTIGATE | Platform shows 58 conversions; billing shows 19 tagged to it | Budget frozen; tagging task filed |
| Social, commuters | FIX | Top ad live 9 weeks, click rate down from 1.4% to 0.6% | Two new ads live from the /commute spine |
| Social, students | Too early | $70 spent, 1 customer | None until $120 spent |
| Onboarding sequence | HOLD | 31% book a first pickup against 22% in the holdout (420 held out) | None |
| Win-back, 90 days quiet | Paused | Complaint rate 0.14% on the 2026-11-12 send | Paused; audience cut to people who clicked in the last year |

Test not run: a new headline on /commute. Baseline 2.1%, smallest lift worth having 2.8%: needs 7,653 visitors per
version, and the page gets about 3,100 a month, so about five months. Shipped as a before-and-after change with that
caveat stated. Spend this month: $2,140 of $3,000."
