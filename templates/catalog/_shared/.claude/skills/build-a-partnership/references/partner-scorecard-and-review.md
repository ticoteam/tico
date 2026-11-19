# Measuring partners and the joint review

## What to count, per partner, each month

Every figure from a Tool, with its date range. A figure that could not be read is "not read".

| Measure | How it is counted | Source |
|---|---|---|
| Leads registered | Complete registrations accepted in the month | The registration log in the partner's file or the CRM |
| Time to answer a registration | Working hours from receipt to accepted or refused | The same log |
| Leads worked | Registered leads with a first reply inside the team's reply time | CRM, mailbox |
| Customers won, sourced | New paying customers meeting the written "sourced" definition | CRM matched to billing |
| Customers won, influenced | Existing opportunities the partner helped close, by the written definition | CRM notes |
| Revenue from partner customers | Payments received from those customers in the period | Billing Tool, `tico finance` |
| Fees owed and paid | From the agreement and the payments above; paid on time, yes or no | Finance |
| Retention of partner customers | Still paying at 90 days and 12 months, against other customers from the same period | `tico growth cohort`, billing |
| For integrations: connected customers | Customers with the integration active | Product Tool |
| The partner's side | What they did that they said they would: people trained, leads passed, content published | The 30-60-90 plan and their updates |

Rules:

- **Sourced and influenced are always shown apart.** Adding them together overstates what partners brought.
- **Counts first.** A percentage needs a base of 20 or more. Below that, show the counts.
- **Compare a partner with itself over time** and with the team's other channels on cost per customer won. Do not
  rank partners of different types against each other on one number.
- **Retention is the tiebreaker.** A partner whose customers leave fast is costing money however many it sends.
- **Do not credit a partner with revenue the record does not support.** The monthly attribution audit decides.

## The monthly attribution audit

1. List every customer credited to a partner this month.
2. For each, open the record and check it against `knowledge/rules-of-engagement.md`: was there a complete
   registration or recorded introduction, dated before the team's first sales contact? Was there already an open
   opportunity?
3. Mark each: sourced, influenced, or not attributable.
4. Fix mislabels before fees are listed for Finance. If a fix lowers what a partner expected, tell them first, with
   the evidence, before the statement arrives.
5. Report on the partner page: records checked, mislabels found, fixed.

With more than 50 credited customers a month, audit a sample of 50 picked evenly through the list, plus every
disputed one.

## Partner stages

Use one stage per partner on the page, and move it only on evidence:

- **Candidate:** scored, not yet in conversation.
- **Proving:** a first win is under way. Limit: 30 days.
- **Launched:** terms agreed, 30-60-90 plan running.
- **Producing:** at least one sourced customer in the last 90 days, or steady integration use.
- **Lapsed:** no activity for 90 days and the dated next step did not happen. Told, slot freed.

The count that matters on the page is partners in Producing, not partners signed.

## The joint review, each quarter

For every partner in Launched or Producing. One page, sent two working days ahead, and a conversation only if there
is something to decide.

Build it as HTML and publish for the team with
`tico page publish reviews/partner-<partner>-<YYYY>-Q<n>.html --title "<Partner> review, Q<n> <YYYY>"`. A partner
cannot open a Tico page, so send them the same content as an attachment or in the email body
(`tico email draft ... --attach <file>`), after removing anything internal: the fit score, other partners' numbers,
the team's margins.

Sections:

1. **What we set out to do together.** One sentence, from the term sheet.
2. **Three shared measures,** agreed with the partner at launch, each with last quarter, this quarter and the
   target. Typical set: customers won together; revenue or retention of those customers; one measure of the
   partner's own gain (their retained customers, their services revenue, their product's usage). At least one of
   the three is theirs.
3. **What each side said it would do, and did.** Two short columns. Include what the team missed.
4. **What is in the way.** At most three items, each with an owner and a date.
5. **Next quarter.** Three to five commitments in total, each with one owner on one side and a date.
6. **One decision** needed, if any, with the team's recommendation.

Ask every time: "What should we be doing that we are not, and what should we stop?" and "Has anything changed in
what you are measured on?" Partnerships drift apart when one side's goals change and nobody says so.

Afterwards, the same day: a recap under 120 words, the partner's file updated, tasks created for what the team owes.

## Reading the whole set, each quarter

- **Concentration:** if one partner brings most partner revenue, that is a risk to name, and a reason to learn what
  makes them work.
- **What producers have in common:** compare their early fit scores, type and how they were found with the lapsed
  ones. Change the scorecard and the search accordingly.
- **Cost of the route:** fees plus the team's time on partners, per customer won, beside the same figure for other
  channels (from the measure-unit-economics skill). If the partner route costs more and retention is no better,
  say so and propose what to change.
- **Capacity:** if every active slot is in Producing, propose raising the limit, with the evidence.
- **Terms:** if partners keep asking for the same change, propose a new version of the standard terms for one OK.

## Decisions the page should end with

Each with evidence and an owner:

- partners to add, from the scored list;
- partners to prune, and how they will be told;
- one change to terms, kit or rules, if the quarter showed a need;
- anything that needs the owner's OK, raised as a task with the page attached.
