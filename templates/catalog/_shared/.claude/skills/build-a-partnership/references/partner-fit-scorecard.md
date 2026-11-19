# Partner fit scorecard

Score each candidate before any outreach. Six questions, each 0 to 5, each with the evidence written beside it. A
score without evidence is a 0. The scale and the cut-offs are starting defaults for this library, not a tested
model; adjust them once the team has its own results to compare against.

## The six questions

| # | Question | 5 looks like | 0 looks like | Where to find out |
|---|---|---|---|---|
| 1 | **Shared customers.** Do they serve the customers the team serves or wants? | A countable overlap today, or their customer is exactly the team's "Who we serve" | A different buyer, size or market | CRM fields, onboarding notes, their customer page, their case studies |
| 2 | **Complementary, not competing.** Does working together make each product more useful? | Customers use both side by side; neither replaces the other | They sell a substitute, or are building one | Their product pages, their roadmap if public, what customers say |
| 3 | **Their reason to care.** What do they gain, in terms they are measured on? | A clear gain: revenue, keeping their customers, filling a gap customers ask them about | Nothing they would notice | Their partner page, job postings for partner roles, what their customers ask for |
| 4 | **Their ability to act.** Can they actually do their part? | A named person owns partners; they have done this before; an open API or a working referral process | Nobody owns it; closed product; past partners went nowhere | Their partner directory, developer docs, a conversation |
| 5 | **The team's ability to support them.** Can the team hold up its side with the attention it has? | Fits inside the active-partner limit; no engineering needed, or engineering agreed | Needs custom work nobody has agreed to | `knowledge/partnerships.md`, the engineering team's queue |
| 6 | **Reputation and risk.** Would the team be glad to be named beside them? | Well regarded by shared customers; clean terms; no disputes | Complaints from shared customers; legal trouble; terms that forbid what is planned | Reviews, shared customers, news search, their terms (`check-a-partner-claim`) |

Total out of 30.

## Reading the score

- **24 or more,** with no question below 3: pursue now.
- **18 to 23:** pursue if a slot is free and the weak question can be fixed; say how.
- **Below 18,** or any of questions 1, 2 or 6 at 0 or 1: do not pursue. Record why.

Three overrides:

- A candidate who already sends customers without being asked goes to the top regardless of total. Thank them, and
  ask what would make it easier.
- A direct competitor, or anyone on the off-limits list, is out regardless of total. A partial competitor needs a
  lawyer's ground rules before the first conversation.
- A very large company is not automatically a good partner. Large partners move slowly and the team will be one of
  hundreds. Score question 4 on the specific group and person, not the brand.

## Evidence rules

- Every score names its source and date. "Their website, read 2026-11-18" is a source. "They seem a good fit" is not.
- Overlap is a count from the team's own records, or it is marked "not known". Do not estimate it from the size of
  their customer base.
- Demand for an integration is the number of customers who asked, from tickets, calls and lost-deal notes, with
  the period. One loud request is one request.
- Use only what is public or the team's own. Do not ask a shared customer to disclose the candidate's confidential
  terms, and do not ask an employee of the candidate for anything they should not share.
- Comparing customer lists with a candidate (account mapping) happens only after both sides agree how, and shares
  the least needed: a count of overlap first, names only with consent or under an agreement that covers it.

## Extra checks for an integration partner

Before scoring question 5 above 2:

- Is the need shown by customers asking, by name, more than a handful of times?
- Does the candidate have a documented way to integrate that the team is allowed to use, and under what terms?
- Who on the team would build it and keep it working, and have they agreed? Building is an OK; see Autonomy.
- Who maintains it when either product changes? An integration is a standing cost, not a project.
- Is there one shared customer willing to try it first?
- What does each side do to tell customers it exists?

## Extra checks for a reseller or distributor

- Do they already sell to the buyers the team wants, and can they show it?
- Who supports the customer after the sale, and is that written down?
- Can the team still learn who its end customers are and how they use the product?
- What happens to those customers if the relationship ends?
- Have their own terms, and their suppliers', been read (`check-partner-terms-before-resale`)?

## The record

Keep the scored list in `knowledge/partnerships.md`, one row per candidate:

| Candidate | Type | Q1 | Q2 | Q3 | Q4 | Q5 | Q6 | Total | Key evidence | Decision | Date |
|---|---|---|---|---|---|---|---|---|---|---|---|

Rescore a "not now" candidate when something changes: a new product on their side, a new owner of partnerships,
a jump in the overlap count. After two quarters of real results, compare early scores with which partners actually
produced customers, and change the questions or cut-offs that did not predict it. Note the change and its date.
