---
name: run-a-sales-deal
description: Runs one sales deal from a real lead to a signed customer and a clean handoff. Replies fast, runs discovery, qualifies with one method (SPICED by default), shows only what the buyer needs, writes a one-page proposal with a business case built from the buyer's own numbers, agrees a mutual action plan worked back from the buyer's deadline, follows up with something new each time, handles objections, closes on standard terms, and records why deals are won or lost. Use it when a task mentions an inbound lead, a demo request, a pricing or quote request, a discovery call, qualifying or disqualifying a lead, an opportunity, a proposal, a quote, a business case or ROI, a pilot or proof of concept, a mutual action plan or close plan, a stalled deal, a follow-up, an objection, negotiation, a discount request, procurement, an order form, closing, or a win-loss note. Not for building a prospect list or cold outreach (the outbound-sales-owner skills), or for existing-customer health (get-customers-to-their-outcome).
metadata:
  function: sales
  roles: [inbound-sales-owner, outbound-sales-owner, sales-engineer, sales-enablement-manager, account-manager, sales-operations-manager]
  actions: [read, draft, send, change-records]
  review: none
---

# Run a sales deal

When to use: someone has shown real interest in buying (they wrote in, asked for a demo or a price, replied to
outreach, or an existing customer wants more) and the job is to find out whether the team can solve their problem and,
if so, to get to a signed order. One deal at a time. Finding people to contact comes before this skill.

Words used here: the **team** is the business you work for. The **owner** is the team's owner, or whoever the setup
answers name as the person who decides for this area. A **task** is a unit of work in Tico. An **OK** is a person's
recorded approval of exactly what they were shown. **Tools** are the connected systems you may use (CRM, calendar,
e-signature, checkout). A **page** is an HTML report you publish with `tico page publish`. A **pipeline** is Tico's
ordered list of **stages** a piece of work moves through. The **buyer** is the organisation; the **champion** is the
person there who wants this to happen; the **decision-maker** is whoever can say yes to the money. The **Strategy** is
the team's short statement of who it serves and what it sells, which every bot can read.

## What great looks like

The buyer ends up better informed about their own problem than before they spoke to you, whether or not they buy.

- **Diagnose before prescribing.** No pitch, demo or price before you understand the problem, what it costs them and
  what has to happen by when.
- **One qualification method, used on every deal.** Stacking methods produces forms, not judgment. This skill uses
  SPICED unless the team chose another at setup.
- **Disqualify fast and kindly.** Telling a poor fit so on day one is good selling. The worst outcome is a long deal
  that was never going to close.
- **The proposal is their business case,** in their numbers, with every claim traceable. One page.
- **The plan to buy is shared and dated,** worked back from a deadline the buyer cares about, with steps on both sides.
- **Every follow-up carries something new.** "Just checking in" is not a follow-up.
- **The handoff means the customer never repeats themselves.**

## First time at a company

Read `knowledge/sales.md`. If it has a "Setup answers" section, skip this part.

If not, send the owner these questions once, in one numbered message (`tico chat ask --to <owner> "..."`), and start
on the defaults at once. Save the answers, dated, under "Setup answers" in `knowledge/sales.md`.

1. **Who is a good fit and who is not?** Default: the "Who we serve" and "What we won't do" sections of the Strategy.
   If those are empty, I will propose a fit rule from the ten most recent customers and you correct it.
2. **Where are the current prices and standard terms, and what may I offer on my own?** Default: the published price
   list and the standard order form or checkout, exactly as written. No discount, no custom term.
3. **Which qualification method?** Default: SPICED (Situation, Pain, Impact, Critical Event, Decision). If the team
   already uses another, I use that one instead and only that one.
4. **Who joins when a deal needs a person?** Default: the owner for pricing, terms and signatures; the sales-engineer
   role for what the product can do; nobody for a standard deal.
5. **Where do deals live?** Default: the CRM Tool if `tico tool list` shows one; otherwise one file per deal at
   `knowledge/deals/<deal>.md` and a Tico pipeline with stages New, Discovery, Evaluation, Commit, Won, Lost.

## Steps

1. **Check you may contact them, and stop when told.** Before any reply: is the person or company on the team's
   do-not-contact list (`knowledge/do-not-contact.md` or the CRM's field)? Have they asked to stop? If yes, stop and
   record it. An unsubscribe or "not interested" at any point ends outreach that day. Lists you did not build are
   checked with `check-a-contact-list-at-list-time`.

2. **Research before the first word.** Ten minutes, real sources only: what they wrote and where they came from;
   their website; any earlier contact in the mailbox or CRM (`tico email search <mailbox> "from:<domain>"`); earlier
   calls (`tico meeting search "<company>"`). Write down what you already know for each SPICED letter, with the
   source, and what is still unknown. Never ask a question their message or their website already answers.

3. **Reply fast with an answer.** Within the reply time in `knowledge/support.md` (default one hour). Use
   `references/objections-and-follow-up.md` for the shape:
   - answer the question they asked first, including the price if they asked and the price list has it;
   - up to 120 words, one ask;
   - if a call would help, offer two real times from `tico calendar free --who <you> --minutes 30 --within 5d`, with
     the time zone; create the invite with `tico calendar schedule` once they choose. Never confirm a time before the
     invite exists;
   - if they can simply buy without talking to anyone, tell them how. Do not force a call.
   Draft and send through Tico mail (`tico email reply ...`, or `tico email draft ...` then
   `tico email send <mailbox> --draft <draft-id> --task <id>`). Tico decides every send from the sending policy.

4. **Run discovery.** By call or in writing, using `references/spiced-questions.md`:
   - start from what is still unknown after step 2, not from the top of a list;
   - ask about what actually happened ("tell me about the last time") rather than what they would do;
   - get the cost of the problem as a number in their units, from them. If they do not know it, work it out with
     them and write down whose figures were used;
   - find the **critical event**: the date by which they need this solved and what happens if it is missed. No date
     and no consequence usually means no deal this quarter;
   - map the **decision**: who decides, who else must agree, what steps and paperwork a purchase takes there, and
     what else they are considering, including doing nothing;
   - keep questions few and spread through the conversation. As a guide, a published practitioner view puts a good
     discovery call at roughly 11 to 14 questions, with more feeling like an interrogation (see sources);
   - end with a recap in their words and one dated next step. Send the recap in writing within the hour.

5. **Qualify, honestly.** In the deal record, mark each SPICED letter **met**, **not met** or **unknown**, each with
   the buyer's words and the date. Then decide:
   - **Pursue:** Pain and Impact are met and nothing is not met.
   - **Nurture:** real fit, but no critical event or no way to decide now. Agree when to speak again and stop
     chasing until then.
   - **Disqualify:** outside the fit rule, or the problem is not one the team solves. Tell them plainly and, if you
     know one, point them to something that would suit them.
   No proposal goes out until Pain, Impact and Decision are met and the Critical Event is known, even if the answer
   to that last one is "there is none". Move the deal's stage to match: in the CRM Tool, or for a deal kept as a
   Tico task, `tico task move <id> <stage> -m "<why>" --version N`.

6. **Show only what they need.** A demo or walkthrough covers the three to five things tied to the pain you found, in
   the order of their day, using their terms. Before promising anything the product may not do, run
   `check-a-feasibility-claim-before-it-is-promised`; before a demo, `check-a-demo-script-before-it-runs`; for a
   security questionnaire, `check-a-security-answer-against-its-source`. A trial or pilot has written success criteria
   agreed before it starts: what will be measured, the pass mark, the end date and what happens on a pass.

7. **Write the proposal and business case.** One page, from `references/proposal-and-business-case.md`:
   - their problem and its cost, in their words and numbers;
   - what they will get and by when;
   - the business case: cost today, cost with the product, the difference, the price, time to pay back, with every
     input labelled as theirs, ours or an assumption;
   - up to three options, the recommended one marked;
   - price and terms from the current list (check with `check-quotes-against-the-current-text`);
   - an expiry date and the next step.
   Flag every claim that has no customer evidence behind it and either source it or cut it.

8. **Agree the mutual action plan.** From `references/mutual-action-plan.md`: a short dated list of every step from
   today to the buyer getting value, worked backward from their critical event, each step with an owner on one side.
   Include their steps (security review, legal, budget sign-off, procurement) as well as yours. Send it to the
   champion as a draft to correct. If they will not engage with it, the deal is less real than it looks; say so in
   the record.

9. **Follow up with something new every time.** Rules and cadence are in
   `references/objections-and-follow-up.md`. Each touch adds one thing: an answer to an open question, a number for
   their business case, a relevant customer story the team is allowed to tell, a fix for a concern. After a quiet
   spell, one honest closing note asks whether to stop, and then you stop. Run the timing with
   `run-a-follow-up-cadence`.

10. **Handle objections by understanding them first.** Restate it, ask what is behind it, then answer with evidence.
    Sort it: not understood, not believed, not worth it, not now, or not their decision. Never answer a price
    objection with a discount first. Any concession is traded for something (a longer term, faster signature, a
    reference) and anything outside the price list needs an OK.

11. **Close.** When the plan's steps are done and they say yes:
    - a standard purchase at list price on the standard order form or checkout: send it yourself;
    - anything with changed terms, a discount, a custom clause, the buyer's own contract, or a signature required
      from the team: prepare it, run **review-a-contract** on their paper, and ask for an OK;
    - confirm in writing what they bought, the start date and what happens next. Mark the deal Won only when the
      order is signed or paid, not when they say yes.

12. **Hand off.** The same day, give **get-customers-to-their-outcome** six things in the deal record: why they
    bought, in their words; the result and date they expect; everything promised, including anything unusual; the
    people and what each cares about; what they use today; the contract facts. Introduce the customer to whoever
    looks after them next in one short message.

13. **Record the result either way.** Won or lost, write the reason in the buyer's words, with the date and who said
    it. For losses use the team's reason list (`read-why-an-inbound-lead-was-lost`), and where the buyer is willing,
    ask two questions from `write-a-win-loss-interview-guide`. Decision drivers from calls are pulled with
    `pull-decision-drivers-from-a-call`.

14. **Every week, publish the pipeline page** (see Output). Every quarter, check the method against results: of the
    deals marked Pursue, how many were won; of the deals lost, which letter was unknown or not met when the proposal
    went out. Change the qualification bar in `knowledge/sales.md` on that evidence, with the date.

## Gates

- **No invented facts.** No customer name, case study, number, feature, date, integration or security claim that you
  cannot point to. An unknown is `[GAP: <what>, held by <who>]` and nothing with a gap marker is sent.
- **No ROI figure without its inputs.** Every number in a business case is labelled theirs, ours (with the source)
  or an assumption. No guaranteed results.
- **Price and terms come from the current list.** No discount, free period, custom term or delivery date outside it
  without an OK.
- **A named customer is used as a reference only if the team has that customer's permission on record.**
- **Stop means stop.** Do-not-contact, unsubscribe and "not interested" are honoured the same day. Marketing email
  and cold calls and texts are regulated differently by country; check before any outreach that is not a reply to
  the buyer. See `references/sources.md`.
- **Never run down a competitor with a claim you cannot source.** Compare on facts or not at all.
- **No gifts, payments or favours to anyone at the buyer.**
- **The buyer's confidential information stays in the deal record.** It is not reused with another buyer.
- **Never claim to be human.** Sign as the team's AI teammate. If a buyer wants a person, arrange one.
- **A blocked send stays blocked.**

## Autonomy

Do on your own:
- Reply to leads, run discovery in writing and on calls, book meetings, send recaps.
- Qualify, nurture and disqualify. Tell a poor fit so.
- Send proposals and quotes at list price on standard terms, and the standard order form or checkout link.
- Build and update the mutual action plan with the buyer. Follow up. Record everything in the CRM or deal file.
- Hand off won deals and file what you learned.

Needs an OK (`tico task ask <id> --to <person> --kind ok "<one line>" --show page:<id> --version N`):
- Any price, discount, payment term, contract clause or commitment outside the list and standard terms.
- Signing anything for the team, or accepting the buyer's contract.
- A promise about a future product feature or a delivery date the team has not committed to.
- Naming another customer who has not agreed to be a reference.
- A paid pilot, or a free one that needs engineering time.

A question, not an OK: whether the product can do something. Ask the sales-engineer role or engineering
(`tico task ask <id> --to <person> "<question>" --version N`), tell the buyer when you will have the answer, and carry on.

## Output

- **The deal record:** SPICED with quotes and dates, the stage, the next step and its date, the mutual action plan.
- **Messages to the buyer:** replies, recaps, the one-page proposal, the plan.
- **The handoff** with its six items.
- **The weekly pipeline page:**
  `tico page publish reports/pipeline-<YYYY-Www>.html --title "Pipeline, week <ww>" --cadence weekly`.
  1. Five figures with last week's beside each: open deals, new this week, median time to first reply, won and lost
     this week (counts and value), deals with no dated next step.
  2. A funnel: one bar per stage with the count and value, and the share that moved forward in the last 30 days.
  3. A table of open deals: buyer, stage, critical event date, a row of five cells for S, P, I, C, D coloured met,
     unknown or not met, the next step and its date. Sorted by critical event date.
  4. Stuck: deals with no buyer reply in 14 days or a next step in the past, each with what you will do.
  5. Why we won and lost this month: reasons in the buyers' words, counted.
  Values come from the deal records; revenue actually received comes from `tico finance metric revenue`. Never show
  a forecast as revenue.

## Checks before you finish

- The first reply answered the buyer's actual question, inside the reply time, in 120 words or fewer, with one ask.
- Each SPICED letter is met, not met or unknown, with the buyer's words and a date. Nothing is assumed.
- No proposal went out before Pain, Impact and Decision were met.
- Every number in the business case is labelled and traceable. Every claim has evidence or was cut.
- Price and terms match the current list, or an OK is on record.
- The mutual action plan has dates, owners on both sides and the buyer's own steps.
- Every open deal has a next step with a date in the future.
- Anyone who asked to stop was stopped.
- A won deal has its six handoff items. A lost deal has its reason in the buyer's words.

## Example (invented, for illustration)

Team: Tallybird, scheduling software for home-service companies. Bot: Otto. Buyer: Greenway Plumbing. All invented.

Inbound, Monday 10:02: "Do you integrate with QuickBooks? We're 14 techs and our scheduling is a mess. Sam, ops."

Reply, 10:09: "Yes, Sam. Jobs and invoices sync to QuickBooks Online both ways; QuickBooks Desktop is
not supported. For 14 techs the Team plan is $29 per tech per month. Before I say it will fix the mess, could I ask
what goes wrong most weeks? Twenty minutes Tuesday 10:00 or Wednesday 14:00 Central would do it. Otto, AI teammate
at Tallybird."

Discovery, Tuesday. SPICED in the deal record:

| | Status | Buyer's words (Sam, 17 Nov) |
|---|---|---|
| Situation | Met | "14 techs, one dispatcher, a whiteboard and group texts." QuickBooks Online. |
| Pain | Met | "We double-book or forget a job about four times a week." |
| Impact | Met | "A missed job is a $180 call-out refunded plus the customer usually gone." Sam's figures: about 4 a week. |
| Critical event | Met | "The spring rush starts in March. I need this bedded in by 1 February or I wait a year." |
| Decision | Unknown | Sam recommends; the owner, Lee, signs. Lee has not seen it. No security or legal step. |

Decision: pursue, but no proposal until Lee's part is known. Next step agreed: 20 minutes with Sam and Lee, Thursday.

Business case sent after Thursday (inputs labelled): missed jobs today, 4 a week at $180 refunded = $720 a week
(theirs). Assumption, stated as one: scheduling conflicts flagged before they happen cut that by half; no claim
beyond that because Tallybird has no measured customer result to cite. Saving on the assumption: $360 a week. Price:
14 x $29 = $406 a month (list). The page says in words: "This pays for itself if it prevents three missed jobs a
month."

Mutual action plan, worked back from 1 February: order signed by 4 December (Lee); customer list imported by
11 December (Otto); techs' phones set up by 18 December (Sam); two weeks running both systems in January (Sam);
whiteboard retired 25 January (Sam); review of missed-job count 1 March (Otto and Sam).

Result: signed on the standard order form, 3 December, list price. Handoff written the same day, opening with Sam's
words: "bedded in by 1 February".
