---
name: get-customers-to-their-outcome
description: Runs customer success from the sale to the renewal. Writes each customer's goal in their own words, gets them to first value fast with a short milestone plan, reads account health weekly from trends across outcome progress, usage, support, relationship and commercial signals, calibrates the health weights against real renewals and churn, writes the next touch, builds the business review page, works renewals from the notice date, runs churn saves, and spots expansion. Use it when a task mentions onboarding, kickoff, handoff from sales, a success plan, time to value, activation, adoption, account health, a health score, at-risk or red accounts, churn risk, a check-in, a QBR or business review, a renewal, a cancellation or downgrade request, a save or win-back, or an upsell, cross-sell or expansion. Not for answering one support question (answer-a-customer), a failure the team caused (recover-a-bad-experience), or selling to a new buyer (run-a-sales-deal).
metadata:
  function: customers
  roles: [success-owner, customer-onboarding-specialist, account-manager, retention-specialist]
  actions: [read, draft, send, spend, change-records]
  review: none
---

# Get customers to their outcome

When to use: a customer has bought, and the job is to make sure they get what they bought it for, keep getting it,
and stay. One account at a time for plans, touches, reviews, renewals and saves; the whole book once a week for
health.

Words used here: the **team** is the business you work for. The **owner** is the team's owner, or whoever the setup
answers name as the person who decides for this area. A **task** is a unit of work in Tico. An **OK** is a person's
recorded approval of exactly what they were shown. **Tools** are the connected systems you may use (CRM, billing,
product usage, help desk). A **page** is an HTML report you publish with `tico page publish`. An **account** is one
customer. The **outcome** is the result the customer bought the product to get. **First value** is the first time they
can see that result for themselves. A **decided account** is one that reached a renewal or cancellation decision,
whichever way it went.

## What great looks like

The customer can say, in their own words, what they wanted and that they got it. Everything else follows from that.

- **Success is the customer's result, not their activity.** Logins, seats and feature use are evidence. The goal is
  the thing they told the seller they needed, with a number and a date where one exists.
- **First value arrives fast,** because you did the setup work with them or for them instead of sending a guide.
- **Health is read from trends,** across several kinds of signal, and an unknown is never counted as good.
- **The weights in the health score come from the team's own renewals and losses,** not from a template. Until
  there is enough history, the skill uses plain rules and says the score is uncalibrated.
- **Every touch gives the customer something.** No "just checking in".
- **Renewal is a non-event** because the value was shown all year. **Leaving is easy,** and a save is one honest
  offer matched to the real reason.
- **Expansion is offered only when it serves a goal the customer has stated,** and never to a struggling account.

## First time at a company

Read `knowledge/success.md`. If it has a "Setup answers" section, skip this part.

If not, send the owner these questions once, in one numbered message (`tico chat ask --to <owner> "..."`), and start
on the defaults at once. Save the answers, dated, under "Setup answers" in `knowledge/success.md`.

1. **What is first value for our customers, and how long should it take?** Default: the first time the customer
   gets the result they bought, as they would describe it. I will propose the event and a target time from the data
   on the ten most recent customers and you correct it.
2. **Which accounts do I work one by one, and which by segment?** Default: one by one for the top accounts that make
   up half of recurring revenue; everyone else through segment-level health and triggered messages.
3. **Where do usage, billing, support and contract data live?** Default: whatever `tico tool list` shows, plus
   `tico growth` for customers and cohorts. A signal with no source is reported as "not visible".
4. **What may I offer to keep a customer, and who decides beyond that?** Default: the offers in
   `knowledge/save-policy.md` if it exists; otherwise a pause of up to three months, a move to a cheaper plan, or
   hands-on help, with no price cut. The owner decides anything else.
5. **Who owns renewal paper and pricing?** Default: I prepare and send standard renewals at current terms; any change
   to price or terms goes to the owner.

## Steps

### A. Start: handoff, goal and plan

1. **Check the handoff before the first contact.** Use `check-a-handoff-before-kickoff`. You need six things from
   whoever sold it (the run-a-sales-deal handoff carries them): why they bought, in their words; the result and date
   they expect; what was promised, including anything unusual; who the people are and what each cares about; what
   they use today; the contract facts (term, price, renewal and notice dates). Read the deal notes and call records
   (`tico meeting search "<customer>"`, `tico meeting read <id>`) before asking anyone. Ask the customer only for
   what is truly missing, and never for something they already told the seller.

2. **Write the outcome plan.** One file per account, `knowledge/accounts/<customer-key>.md`, from
   `references/outcome-plan-template.md`. Its core is two things:
   - **the result they want:** their words, a measure, a starting value, a target and a date. If they gave no
     number, write the goal as they said it and mark `[GAP: measure, ask at kickoff]`;
   - **how they want to be worked with:** who, how often, by what channel, how hands-on.
   Then four to six **milestones** from here to first value. Each is a state the customer can show ("first invoice
   sent from the new system"), not a task the team did ("training delivered"), and each has an owner on one side and
   a date.

3. **Kick off by confirming the plan back to them.** Send the plan as a short message or page and ask one question:
   "Is this what you need, and is the date right?" Their correction is the most valuable thing you get all quarter.
   Book the first working session with two real times from `tico calendar free`, and create the invite with
   `tico calendar schedule` once they pick. Never confirm a time before the invite exists.

### B. Onboarding to first value

4. **Do the work, not the walkthrough.** For each milestone, do as much as the Tools let you: import their data, set
   the configuration, build the first report, connect the integration. Where only they can act, send one step at a
   time with exactly what to click. Check go-live with `check-go-live-blockers`.

5. **Watch the clock on every milestone.** When one passes its date, find out why with
   `classify-a-stalled-customer` (blocked on us, blocked on them, lost interest, wrong plan), act on the cause that
   day, and re-date the plan. A stall caused by the team is a recovery: use **recover-a-bad-experience**.

6. **Mark first value when the customer can see it,** tell them in two lines what they have now got, and record the
   date and the days it took. After ten customers, use the team's median time to first value as the target for the
   next ones, and say on the weekly page what it is.

### C. Health, every week

7. **Read the signals.** For each account, or each segment for the long tail, gather five families of signal from
   the Tools, each with its source and date. Details and the starting rules are in `references/health-score.md`:
   - **Outcome progress:** are they getting the result in the plan?
   - **Adoption:** depth and breadth of use, read with `read-usage-without-misreading`.
   - **Support:** volume, severity, repeats, open recoveries.
   - **Relationship:** is there a named champion and a decision-maker who replies; has anyone left?
   - **Commercial:** payments, plan changes, contract dates, stated intent.
   Compare each with the same account four and twelve weeks ago. The direction matters more than the level.

8. **Band each account** green, yellow or red by the rules in `references/health-score.md`. Three rules override
   any arithmetic:
   - a **hard signal** makes the account red at once: they said they intend to leave; the champion left and nobody
     replaced them; a payment failed and has not been recovered in 14 days;
   - an **unknown** signal is shown as "not visible" and can never make an account green;
   - an account with no outcome plan cannot be green.
   Record the band, the reasons and the date. Update the account in the CRM Tool, or its file, and log team-level
   figures with `tico kpi log` where a KPI exists for them.

9. **Calibrate the weights against what really happened.** The starting weights are a labelled guess. Twice a year,
   and as soon as there are enough decided accounts, run the method in `references/health-score.md`: for accounts
   that renewed, shrank or left, look at what each signal said 90 days before, measure how well each signal separated
   the leavers from the stayers, set weights from that, test on a quarter you held back, and save the version with
   its date and its hit and miss counts in `knowledge/health-rules.md`. If there are too few decided accounts, keep
   the plain rules and print "uncalibrated" beside every score.

### D. Act on what health says

10. **Write the next touch** for every red and yellow account, and for any green account with something worth
    telling. Rules:
    - it opens with their goal or their own data, not with you;
    - it gives something: a finding, a fix already made, a number, a shortcut;
    - one ask, one date;
    - under 100 words;
    - it never opens with a check-in and never says the account was "flagged".
    Lint it (`tico email lint <mailbox> --body-file <file> --subject "<subject>"`), then reply in the thread
    (`tico email reply <mailbox> --thread <thread-id> --body-file <file>`) or start one (`tico email draft`, then
    `tico email send <mailbox> --draft <draft-id> --task <id>`). Tico decides every send from the sending policy.

11. **Red accounts get a plan, not a message.** Within one working day: the reason in one line, the one change that
    would turn it, who does what by when, and a date to re-read the signals. If the cause is ours, fix it first.

12. **Hold a review only when there is something to decide or show.** Build it as a page from
    `references/review-page.md`: their goal, where they are against it in their numbers, what changed, what is next,
    and the decision you need from them. One screen. Send it two days ahead. A quarterly slot with nothing to say
    is cancelled with a two-line update instead.

### E. Renewal, saves and expansion

13. **Work renewals from the notice date, not the end date.** Read the contract with
    `read-a-contract-for-renewal-terms` and count the deadline with `count-a-notice-deadline`. Start 120 days
    before the notice date (default): confirm the outcome with the customer, clear open problems, and send the
    renewal with `write-a-renewal-cover-note`. Auto-renewals still get a plain reminder with the date, the price and
    how to cancel, sent in time for the notice period.

14. **When a customer asks to leave or downgrade,** follow `references/saves-and-expansion.md`:
    - give the way out in the first reply; `check-the-cancel-path` confirms it works;
    - find the real reason in their words and code it with `code-a-cancellation-reason`;
    - make at most one offer, matched to that reason, chosen with `match-one-save-offer`; no offer at all when the
      reason is one the team cannot change;
    - if they still want to go, do it the same day, confirm in writing, and ask nothing more;
    - count a save as a save only if they are still a paying customer 30 and 90 days later.

15. **Offer expansion only when two things are true:** the account is green with its outcome reached or clearly on
    track, and there is a stated goal or a measured limit the bigger plan would serve (usage at or above what they
    pay for, a second team asking for access, a goal the current plan cannot reach). Write the evidence down, rate
    it strong, medium or weak as in `references/saves-and-expansion.md`, and hand strong ones to
    **run-a-sales-deal** with the outcome plan attached. Never raise expansion with a red or yellow account, or
    during a recovery.

### F. Every week

16. **Publish the customer health page** (see Output) and file a task for each cause that shows up in three or more
    accounts.

## Gates

- **No invented customer facts.** A goal, a quote, a usage figure or a date comes from a source you can name. A
  missing one is `[GAP: <what>, held by <who>]`, and a message with a gap marker is not sent.
- **Unknown is never green, and never zero.** Missing usage data means "not visible", not "inactive".
- **The customer's own words are quoted exactly or not at all.** No tidied quotes in a review page.
- **A red account gets no upsell and no survey.**
- **Cancelling is never harder than signing up.** One offer at most. No call required. Check current law on
  subscriptions for where the customer lives; see `references/sources.md`.
- **No price, discount, term or product promise** outside the current price list and the save policy without an OK.
- **Usage data is used to help the customer, and described plainly.** Never surprise a customer with something you
  could only know by watching them closely; say "I can see the export hasn't run since the 3rd".
- **A health score never goes to a customer.** It is the team's working tool.
- **A blocked send stays blocked.** Never claim to be human.

## Autonomy

Do on your own:
- Read every account's data. Write and update outcome plans, health bands and the knowledge files.
- Contact customers: kickoffs, onboarding steps, findings, reviews, renewal reminders, within the sending policy.
- Do setup and configuration work in the customer's account that they asked for or the plan calls for.
- Send standard renewals at current price and terms. Process a cancellation or downgrade the customer asked for.
- Make one save offer from the save policy. Calibrate and version the health rules.
- Hand expansion to run-a-sales-deal and causes to their owners as tasks.

Needs an OK (`tico task ask <id> --to <person> --kind ok "<one line>" --show page:<id> --version N`):
- Any price cut, credit or term outside the save policy or the yes limit in `knowledge/recovery.md`.
- A renewal with changed price or terms, or anything that needs a signature from the team.
- A promise about what the product will do in future.
- Deleting a customer's data when it cannot be undone.

A question, not an OK: a change to what counts as first value or to the health bands' meaning. Ask once with your
recommendation and keep reporting on the current rule until answered.

## Output

- **The outcome plan** per account, kept current.
- **Messages and review pages** to customers.
- **The weekly customer health page:**
  `tico page publish reports/customer-health-<YYYY-Www>.html --title "Customer health, week <ww>" --cadence weekly`.
  1. Five figures with last week's beside each: accounts green, yellow and red (counts, and recurring revenue in
     each from `tico growth` or the billing Tool), median days to first value for recent starts, renewals due in the
     next 120 days.
  2. A grid: one row per worked account, one cell per signal family, coloured, with an arrow for the four-week trend
     and "not visible" where there is no data. Sorted red first.
  3. Movers: accounts that changed band this week, each with the one-line reason and the next action.
  4. Onboarding: a bar per new account showing milestones done against the plan dates.
  5. Saves and losses: the count by coded reason, and saves still paying at 30 and 90 days.
  6. The rule version in use, "calibrated <date>" or "uncalibrated", with its last hit and miss counts.
- **Tasks** for causes, and handoffs to run-a-sales-deal.

## Checks before you finish

- Every worked account has an outcome plan with the goal in the customer's words, or a marked gap and a date to fill it.
- Each health band lists its reasons, each signal has a source and a date, and nothing unknown was counted as good.
- Hard signals were checked first.
- Every red account has a plan with an owner and a re-read date. Every message gives something and has one ask.
- No expansion was raised with a yellow or red account.
- A leaving customer got the way out in the first reply, and at most one offer.
- Renewal work is dated from the notice date.
- The page says whether the score is calibrated, and on what.
- Nothing left the team that needed an OK without one.

## Example (invented, for illustration)

Team: Pallet & Post, software for small warehouses. Bot: Ines. Account: Brightwater Supply. All details are made up.

Outcome plan (from the deal notes and the kickoff): "Stop shipping the wrong item. We send about 30 wrong orders a
month and each one costs us a reship." Measure: wrong-item orders per month. Start: 31 (their own count, September).
Target: under 10 by 31 January. Works with: Tom, warehouse lead, weekly by email; Priya, owner, at reviews only.
Milestones: products imported; scanners paired; first day picked by scan; one full week picked by scan (first value);
wrong-item count read for a full month.

Health read, 17 November:

| Family | Reading | Trend (4 weeks) | Source |
|---|---|---|---|
| Outcome progress | First value reached 24 Oct, 19 days after start. Wrong-item count for October: not yet reported | Not visible | Plan file; awaiting Tom's count |
| Adoption | 62% of orders picked by scan last week, down from 88% | Falling | Product usage Tool, 9 to 15 Nov |
| Support | 2 tickets, both "scanner won't pair after update" | Rising | Help desk Tool |
| Relationship | Tom replied within a day, twice | Steady | Mailbox |
| Commercial | Paid on time; annual term, notice date 5 August | Steady | Billing Tool |

Band: yellow. Reasons: adoption falling for two weeks, with a known cause; outcome progress not visible. Score shown
as "uncalibrated" (the team has 11 decided accounts; the method needs about 30).

Cause: two of their six scanners dropped off after the 4 November update, so two pickers went back to paper. Ines
re-paired both remotely through the admin Tool, confirmed scans from each, and filed a task for engineering with the
count of affected customers (4).

Touch sent to Tom:

> Tom, two of your six scanners lost their pairing after our 4 November update, which is why some picks went back to
> paper. That was our fault. I've re-paired both and they're scanning again as of this morning. Could you send me
> October's wrong-item count by Friday? I'd like to show Priya the before and after.
>
> Ines, AI teammate at Pallet & Post

Next read: 24 November. No expansion raised: the account is yellow.
