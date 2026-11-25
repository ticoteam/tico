---
name: build-a-partnership
description: Finds, pitches, structures and runs partnerships with other companies, from the first idea to a partner that produces customers every quarter. Covers referral and affiliate partners, resellers and other channel partners, agencies and service partners, integration and technology partners, and co-marketing. Picks the partner type for the job, finds candidates from evidence, scores fit, writes the pitch around the partner's gain, proves one shared win before any contract, sets terms from the team's own margins, launches with a 30-60-90 plan, handles deal registration and conflict, and prunes partners that do not produce. Use it when a task mentions a partner, partnership, partner program, referral program, affiliate, reseller, channel, distributor, agency partner, integration partner, technology partner, marketplace or app-store listing, co-marketing, co-selling, deal registration, partner commission or revenue share, partner onboarding, a partner review, or a partner agreement or term sheet.
metadata:
  function: sales
  roles: [partnerships-owner, affiliates-owner, developer-advocate, outbound-sales-owner]
  actions: [read, draft, send, spend, change-records]
  review: professional
---

# Build a partnership

When to use: the team wants customers, reach or product value it can get faster through another company than on
its own, or another company has proposed working together. One partner at a time for pitching, terms and launch; the
whole set once a month for results.

Words used here: the **team** is the business you work for. The **owner** is the team's owner, or whoever the setup
answers name as the person who decides for this area. A **task** is a unit of work in Tico. An **OK** is a person's
recorded approval of exactly what they were shown. **Tools** are the connected systems you may use. A **page** is an
HTML report you publish with `tico page publish`. A **partner** is another company that works with the team for shared
gain. A **referral partner** sends leads and is paid when one buys. An **affiliate** does the same at volume through
tracked links. A **reseller** buys or sells the product under its own contract with the customer. A **service
partner** (an agency or consultant) sets up or runs the product for customers. An **integration partner** makes its
product work with the team's. A **sourced** deal exists because the partner brought it. An **influenced** deal already
existed and the partner helped it along. **Deal registration** is a partner telling the team in writing about a
prospect, to protect its claim to the fee.

## What great looks like

A partnership exists because a shared customer ends up better off. Everything else is paperwork.

- **Start from the customer, not the logo.** The best partners are the companies the team's customers already use,
  already hire or already ask about.
- **Prove one win before writing a contract.** One shared customer, helped together, tells both sides more than any
  negotiation, and gives the agreement something real to describe.
- **Lead with the partner's gain.** They have their own targets. A pitch that is about the team's growth gets no reply.
- **A few active partners beat a long list.** Each real partner costs attention: enablement, answers, joint plans.
  Signing partners is easy and proves nothing; partners producing customers is the measure.
- **Write the rules before the first disagreement:** what counts as sourced, who gets paid when two parties claim
  one customer, how long a registration lasts, when the fee is paid and when it is taken back.
- **Make it easy to be a partner:** fast answers, clear payment, material they can use without asking.
- **Prune.** A partner with no activity gets one honest conversation, then the relationship lapses on good terms.

## First time at a company

Read `knowledge/partnerships.md`. If it has a "Setup answers" section, skip this part.

If not, send the owner these questions once, in one numbered message (`tico chat ask --to <owner> "..."`), and start
on the defaults at once. Save the answers, dated, under "Setup answers" in `knowledge/partnerships.md`.

1. **What do we want partners for first?** Default: new customers in the segment the Strategy names under "Who we
   serve", through referral partners, because that is the cheapest kind to test.
2. **What may a partner be paid?** Default: I work out a ceiling from the team's own gross margin and what a customer
   costs to win today (`tico growth numbers`, `tico finance report pnl`) and propose standard terms for your OK.
   Until then I offer nothing in money. `references/partner-types-and-terms.md` has the method.
3. **Who is off limits?** Default: direct competitors, anyone in "What we won't do" in the Strategy, and any company
   the team is in a dispute with.
4. **How many active partners can we look after well?** Default: three at first. I add a fourth only when the first
   three each have a result or have lapsed.
5. **Who signs, and who is our lawyer for partner agreements?** Default: the owner signs; the review-a-contract
   skill reads every agreement, and a lawyer checks any with exclusivity, resale, or a term over 12 months.

**Standard terms, OK'd once.** After question 2 is answered, write the team's standard partner terms as a one-page
term sheet per partner type, publish it as a page, and ask the owner for one OK
(`tico task ask <id> --to <owner> --kind ok "Standard referral partner terms v1" --show page:<id> --version N`). From then on
you may offer exactly those terms on your own. Anything different needs its own OK.

## Steps

### A. Decide and find

1. **Name the job.** One sentence: what the team gets from partners that it cannot get as cheaply alone, and how it
   will be counted. Then pick the partner type that does that job, from `references/partner-types-and-terms.md`.
   Different jobs need different partners; do not start three types at once.

2. **Find candidates from evidence.** In this order, writing down the source of each:
   - what the team's customers already use and hire: the CRM's fields, onboarding notes, integration requests in
     tickets (`tico doc ask`, the help desk Tool), tools named on calls (`tico meeting search "<term>"`);
   - where current customers came from: any lead source that is a company or a person rather than a channel;
   - who already sends business without being asked. An informal referrer is the best first partner;
   - companies that sell to the same buyer something that is not a substitute;
   - only then, lists and directories.
   Ten real candidates beat a hundred names.

3. **Score fit** with `references/partner-fit-scorecard.md`: shared customers, complementary not competing, their
   reason to care, their ability to act, the team's ability to support them, and reputation. Check their own terms
   and anything they claim with `check-a-partner-claim`. Take the top three. Record the rest with scores for later.

4. **Find the way in.** Use `find-a-route-to-a-partner-target`: a shared customer, a teammate's contact, their
   partner manager, the person who owns the product area. A warm route through a shared customer is worth waiting
   a week for. Check the do-not-contact list first.

### B. Pitch and prove

5. **Write the pitch around their gain,** from `references/pitch-and-launch.md`. Up to 120 words, one ask:
   - the evidence of overlap ("eleven of our customers use you; four have asked us for a connection");
   - what they get, in their terms: revenue, retention of their customers, a gap in their product filled;
   - one small first step that costs them little: one customer to help together, one account to compare notes on;
   - nothing about a program, tiers or a contract.
   Share customer counts, never customer names, unless those customers have agreed. Send through Tico mail
   (`tico email draft ...`, then `tico email send <mailbox> --draft <draft-id> --task <id>`); Tico decides the send.

6. **Hold the first conversation as discovery,** not a presentation. Find out: what they are measured on; which of
   their customers this would help; how they work with partners now and what has gone wrong before; who would do
   the work on their side; what they would need from the team. Send a recap within the hour with the one next step.

7. **Prove one win within 30 days.** Pick one shared or referred customer and help them together: a lead passed and
   followed up properly, an integration set up for one account, a joint piece of work delivered. Keep a short dated
   record of who did what. If nothing happens in 30 days despite prompt work on the team's side, that is the
   answer; say so kindly and move to the next candidate.

### C. Structure

8. **Agree a term sheet before any contract.** One page, plain words, from
   `references/partner-types-and-terms.md`: what each side does; what is paid, on what, when; the written
   definitions of sourced and influenced; deal registration and how long protection lasts; the conflict rule;
   the term and how either side ends it; use of names and logos; customer data. If the partner accepts the team's
   standard OK'd terms unchanged, you may confirm them. Any change goes to the owner as an OK.

9. **Set the money from the team's own numbers, not from a market rate.** Work out, per customer: gross margin, what
   a customer costs to win through current channels, and what a partner-won customer saves or costs in support.
   The fee must leave the partner route at least as profitable as the others. Public ranges exist and are a sanity
   check only (see `references/partner-types-and-terms.md` for dated figures and their weak sourcing). Pay after the
   customer has paid, and take the fee back if the customer is refunded inside the stated period.

10. **Turn it into an agreement.** Run **review-a-contract** on the draft, whoever wrote it. Then ask for an OK to
    sign, showing the agreement and a five-line summary. Never sign, click "accept" on partner program terms, or
    confirm by email in a way that reads as acceptance, without that OK, unless it is the team's own standard terms
    unchanged. For resale, read every layer's terms first with `check-partner-terms-before-resale`.

### D. Launch and run

11. **Launch with a 30-60-90 plan,** from `references/pitch-and-launch.md`. Days 1 to 30: access, people named on
    both sides, the enablement kit delivered, the first registered lead or first joint customer. Days 31 to 60:
    their people can explain and show the product without help; first pipeline. Days 61 to 90: first closed
    customer or a decision about why not, and the regular rhythm set. For an integration, launch only when the
    documentation is live, the partner's team has been briefed, monitoring is in place, and both sides have agreed a
    date to announce it.

12. **Be the easiest partner they have.**
    - Answer a deal registration within one working day: accepted, or refused with the reason. A registration is
      valid when complete; the first complete one wins unless the team already had a dated open opportunity; the
      protection has an end date (default 90 days) and can be renewed with evidence of progress.
    - Work partner-sourced leads with **run-a-sales-deal** at the same speed as any other, and tell the partner
      what happened to each one.
    - Each month, list fees owed with the evidence (customer, payment date, amount, the agreement clause) and send
      the list to the Finance Owner as a task. Recompute any disputed fee with `recompute-a-commission`. Pay on
      time; a late commission ends more partnerships than any strategy does.
    - For affiliates, review applications with `review-an-affiliate-application` and watch for abuse with
      `check-an-affiliate-for-fraud`.

13. **Hold a joint review each quarter** with every active partner, as a one-page page from
    `references/partner-scorecard-and-review.md`: what each side said it would do and did, results against three
    shared measures, what is in the way, and next quarter's plan with owners and dates.

### E. Measure and prune

14. **Every month, publish the partner page** (see Output) and audit attribution: for each partner-credited
    customer, check the record supports sourced or influenced by the written definition. Fix mislabels before fees
    are paid.

15. **Prune.** A partner with no registered lead, no joint customer and no reply in 90 days gets one message
    proposing a specific dated next step. If nothing follows, mark them lapsed, tell them plainly and warmly, and
    free the slot. Ending a signed agreement follows its notice clause and needs an OK.

## Gates

- **No customer data to a partner without a lawful basis and the customer's agreement** where the law or the
  customer's contract requires it. Counts and overlaps in aggregate are fine. Lists and names are not, unless agreed.
- **No exclusivity, most-favoured terms, non-compete or minimum commitment** without an OK and a lawyer's read.
- **With a competitor, never discuss prices, customers, territories or who will bid for what.** That is an antitrust
  matter in the US and a competition-law matter almost everywhere. If a partner is also a competitor, a lawyer sets
  the ground rules first.
- **Never set, or agree to enforce, the price a reseller charges its customers** without a legal read. US federal
  law judges it case by case, some states treat it as unlawful outright, and the EU and UK largely prohibit it. A
  suggested price that the reseller is free to ignore is the safe default.
- **Paid recommendations are disclosed.** In the US the FTC's Endorsement Guides (16 CFR Part 255) require a clear
  disclosure of a material connection such as a referral fee or affiliate link, and hold the advertiser responsible
  for telling its partners so and checking. Put the duty in the agreement and check it.
- **Fees are paid to the partner company under a written agreement.** Never to an individual employee of a
  customer or prospect, never in cash or gifts, and never to a government official or anyone acting for one.
- **No claim about a partner, their product, or the relationship** ("official partner", "certified", a logo) that
  the agreement and `check-a-partner-claim` do not support.
- **No invented numbers.** Overlap counts, expected revenue and results come from the Tools, with dates.
- **Never claim to be human. A blocked send stays blocked.**

## Autonomy

Do on your own:
- Research and score candidates. Contact them, hold discovery conversations, send recaps.
- Run a first shared win that needs no contract and no money.
- Offer and confirm the team's standard partner terms, once the owner has OK'd them, exactly as written.
- Deliver enablement, answer registrations, work partner leads, run reviews, publish the partner page.
- Prepare the monthly fees list for the Finance Owner. Pay fees that the signed agreement and the evidence support,
  if Tico's Finance rules allow you to; if an action comes back `needs_approval`, Tico has asked the right person.
- Let an informal relationship lapse.

Needs an OK (`tico task ask <id> --to <person> --kind ok "<one line>" --show page:<id> --version N`):
- Signing or accepting any agreement or program terms, other than the team's own standard terms unchanged.
- Any term outside the standard: a different fee, exclusivity, a minimum, a longer term, a custom clause.
- Paying anything not clearly due under a signed agreement.
- Sharing a customer list or any named customer's details.
- An integration that needs engineering time, or access to the team's systems for a partner.
- A public announcement, a co-branded launch, or use of the partner's name in marketing.
- Ending a signed agreement.

A question, not an OK: whether a candidate is off limits, or which of two partners to favour in a conflict the rules
do not settle. Ask once with your recommendation.

## Output

- **Per partner,** `knowledge/partners/<partner>.md`: type, fit score with evidence, people by role, the win being
  proved, the terms agreed, registrations, the 30-60-90 plan and its status, a dated log.
- **The rules,** `knowledge/rules-of-engagement.md`: the sourced and influenced definitions, registration and
  conflict rules, payment timing. Team-specific rules only; general practice lives in this skill.
- **Term sheets and agreements,** as pages for OK and as attachments to the partner.
- **The monthly partner page:**
  `tico page publish reports/partners-<YYYY-MM>.html --title "Partners, <Month YYYY>" --cadence monthly`.
  1. Five figures with last month's beside each: active partners, partners with a result this quarter, leads
     registered, customers won (sourced and influenced shown apart), fees owed.
  2. A table, one row per partner: type, stage (candidate, proving, launched, producing, lapsed) as a coloured
     cell, leads, customers, revenue from those customers, fees, the next step and its date.
  3. A bar chart of partner-sourced revenue by partner against fees paid.
  4. Launches in progress: a 30-60-90 bar per partner showing what is done.
  5. Attribution audit: records checked, mislabels found and fixed.
  6. Decisions: partners to add, to prune, terms to change, each with the evidence.
  Revenue figures come from the billing Tool or `tico finance`, matched to customers by record, with dates. A figure
  that could not be read is "not read".

## Checks before you finish

- The job partners are for is written in one sentence with how it is counted.
- Every candidate has a scored fit with sources, and the route in was through the warmest path available.
- The pitch leads with their gain, asks for one small step, and names no customer without consent.
- A real shared win, or a clear reason there is none, came before any agreement.
- The fee was worked out from the team's own margin and cost to win a customer, and is inside the OK'd terms.
- Sourced, influenced, registration, protection length, conflict, payment timing and clawback are written down.
- Nothing was signed, accepted or paid without the OK it needed. A lawyer read anything with exclusivity or resale.
- The disclosure duty for paid referrals is in the agreement.
- Each active partner has a next step with a date. Each lapsed one was told.

## Example (invented, for illustration)

Team: Ledgerleaf, bookkeeping software for independent restaurants. Bot: Rhea. Every company and figure is made up.

The job: "Win restaurant customers through the bookkeepers they already trust, counted as paying customers whose
first contact was a registered referral."

Evidence, from the CRM and onboarding notes on 3 November: 212 paying customers; 58 named an outside bookkeeper at
onboarding; nine of those 58 named the same firm, Copper Pot Accounting; two customers' lead source reads "my
bookkeeper told me". Fit score for Copper Pot: 24 of 30 (shared customers 5, complementary 5, reason to care 4,
ability to act 4, our ability to support 3, reputation 3 because it could not be checked beyond their website).

Pitch sent to Copper Pot's managing partner, through a shared customer's introduction:

> Nine of the restaurants you keep books for run on Ledgerleaf, and two came to us on your word. Their owners tell
> us month-end takes you less time since. I'd like to make that true for the rest of your restaurant clients, and
> make it worth your while. Could we pick one client you're about to onboard and set them up together, so you can
> judge it on a real case? Twenty minutes this week would do. Rhea, AI teammate at Ledgerleaf

The win, inside 30 days: one client set up jointly on 20 November; Copper Pot's month-end close for that client took one
visit instead of two (their statement, recorded with the date; not measured by Ledgerleaf).

Terms: Ledgerleaf's standard referral terms v1, OK'd by the owner on 1 October, accepted unchanged, so Rhea
confirmed them. 20% of the first 12 months' subscription payments, paid monthly after the customer pays; taken back
if refunded within 60 days; registration by email, protected 90 days; non-exclusive; either side may end with 30
days' notice; Copper Pot tells clients it receives a fee. The 20% came from Ledgerleaf's own figures: it costs about
the same as winning a customer through paid search did last quarter (Finance and Growth, September), with lower
support cost because the bookkeeper does the setup.

Day 90 review, in counts: 6 clients registered, 4 became paying customers, 1 declined, 1 still deciding. Fees owed
for the quarter listed for the Finance Owner with each customer's payment dates.
