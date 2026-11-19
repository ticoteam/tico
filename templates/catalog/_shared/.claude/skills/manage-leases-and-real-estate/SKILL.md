---
name: manage-leases-and-real-estate
description: Decide whether the company needs physical space at all, and manage whatever it has. Tests the need first (a company run mostly by bots usually needs an address, not an office), compares no space, flexible space, a sublease, a 3PL and a direct lease on whole-term cost, reviews a lease or letter of intent clause by clause before signature, abstracts every signed lease into a register of critical dates, counts renewal and termination notice deadlines and gives notice exactly as the lease requires, checks rent bills and operating expense statements, and handles subleases and move-outs. Use when a task says lease, office, warehouse, 3PL, coworking, virtual address, registered agent, landlord, rent increase, escalation, CAM, operating expenses, letter of intent, LOI, renewal option, notice date, lease expiry, holdover, sublease, assignment, personal guarantee, security deposit, estoppel, restoration or do we need an office. Not for general contract review (review-a-contract) or day-to-day office requests.
metadata:
  function: finance
  roles: [operations-manager, office-manager, finance-owner, controller, contracts-manager, general-counsel, legal-owner, vendor-manager]
  actions: [read, draft, send, spend, change-records]
  review: professional
---
# Manage leases and real estate

When to use: when anyone asks for space or questions the space the company has, when a lease, sublease or letter of
intent needs review, when a critical date comes inside its lead time, when a rent bill or operating expense statement
arrives, when the company moves, shrinks or stores goods somewhere new, and once a quarter to check the register.

Words used here: **the company** is the tenant. **The lease** is the signed lease and every amendment, letter and
exhibit that changes it, read together. A **critical date** is any date on which a right is gained or lost.
**Rentable area** is what rent is charged on; **usable area** is what the company actually occupies. **Operating
expenses** (also called CAM or additional rent) are the building costs passed to tenants on top of base rent. A
**sublease** is the company letting its space to someone else, or taking space from another tenant. The **abstract**
is the one-page summary of a lease kept in `knowledge/leases/<site>.md`.

This skill is written for US commercial leases. Commercial tenants get far less protection from statute than
residential ones: almost everything is whatever the lease says, and the rest is state and local law. Rules here are
US defaults; read the lease first and the state's law where the lease is silent.

## What great looks like

The company pays for exactly the space it uses, on the shortest commitment that works, and is never surprised by a
date. A lease is usually the largest fixed promise a small company makes, often with the owner's personal guarantee
behind it, so the expert's first move is to question the need. A company whose work is done by bots needs a legal
address and somewhere for mail; it may need a room when people meet, and somewhere for stock or a lab if it makes
hardware. Each of those can be bought by the day, the month or the pallet before it is bought by the decade.

When a lease is right, the expert reads every word before signature, prices the whole term and not the first month,
and knows where tenants lose money: area measured generously, operating expenses with no cap and no audit right, an
option to renew lost by a notice sent a day late or by the wrong method, a guarantee nobody tried to limit, and a
bill for restoring the space on the way out. After signature, the lease becomes a list of dates, each with an owner.

## First time at a company

Read `knowledge/leases.md`. If it has a "Setup" section, skip this. If not, ask the owner these in one numbered
message (`tico task ask <task id> --to <owner> "<the questions>" --version N`), save the answers there with the date, and work on
the defaults until the answers arrive.

1. What space and addresses does the company have today (leases, subleases, coworking, storage, a 3PL, a registered
   agent, a mailing address), and where are the signed documents? Default: search for them
   (`tico doc search "lease OR sublease OR license agreement OR landlord"`, `tico email search "rent OR landlord OR
   lease"`, and rent, storage and coworking vendors in the book) and build the register from what is found.
2. What does the company need a place for: people who must be together, customers or visitors, stock, a lab or
   workshop, a legal address? Default: a legal address and mail only. Everything else has to be shown.
3. Who signs for space, who is the lawyer, and will the owner ever give a personal guarantee? Default: the owner
   signs; a lawyer reviews anything longer than 12 months; no personal guarantee without the owner's own decision,
   with its limits negotiated first.
4. What may the bot commit to on its own? Default: flexible space that can be cancelled on 30 days' notice or less,
   inside the occupancy budget. Anything longer, and any deposit, needs an OK.
5. Where are the people who would use a space? Default: count work locations by metro area from the payroll
   provider. Never put a person's home address on a page.

## Steps

**A. Test the need (before any search, and at every renewal)**

1. **Write down who would use the space and for what**, from real records: the team roster, calendar history of
   in-person meetings (`tico calendar list`), badge or booking data if a space exists, stock levels and shipments
   for goods. A wish is not a need; ask what breaks without the space.
2. **Climb the ladder only as far as the need forces**, with `references/space-decision.md`: no space; an address
   service; rooms by the day; a flexible membership; a sublease; a direct lease. For goods: a 3PL before a
   warehouse.
3. **Price each option over the whole period** you would be committed to, on the same basis, and show what it costs
   to leave early. Use `price-the-whole-term` for the arithmetic. Recommend the lowest rung that meets the need.

**B. Register what exists (first run, then on every change)**

4. **Find every signed document** for each site: the lease, amendments, commencement letter, guarantees, consents,
   side letters. Put them in date order. A missing amendment is a question for the landlord, not a guess.
5. **Write the abstract** with `references/lease-abstract.md`, quoting clause numbers. A term that is not in the
   documents read is "not found in the documents read".
6. **Compute every critical date** and write the arithmetic. Use `count-a-notice-deadline` for notice dates. Put
   each on the calendar and create an automation with a due trigger that opens a task at its lead date (defaults in
   `references/notices-and-renewals.md`). Nobody will remind the company; a landlord has no duty to.
7. **Tell the other skills.** `close-the-month`: the payment schedule, free rent and term, since a lease longer than
   12 months generally goes on the balance sheet under US accounting rules. `manage-insurance`: the lease's
   insurance clause. `stay-on-top-of-taxes`: the address, since a site can mean local licences, property tax on
   equipment and sales tax nexus.

**C. Review a lease, sublease or letter of intent before signature**

8. **Read the whole draft**, including exhibits, rules and the work letter. Go through
   `references/lease-review-checklist.md` and build an issues table: clause, what it says (quoted), why it matters
   in money or risk, what to ask for.
9. **Check the facts the draft rests on**: the area and how it was measured, the landlord's legal name against the
   property record, permitted use against what the company will do there and against zoning, and what the building
   charged other tenants for operating expenses last year.
10. **Price the whole term**: base rent with every escalation compounded (`price-a-renewal-increase` has the
    method), operating expenses with a growth rate stated as an assumption, fit-out net of the landlord's
    allowance, deposit, restoration at the end, and the cost of leaving at each possible exit. Show cost per usable
    square foot and per person who will use it.
11. **Send the lawyer the draft with the issues table** when the gate requires one. Negotiate through whoever the
    owner names. The letter of intent is where the business terms are won; treat it as seriously as the lease even
    when it says it is not binding.

**D. Run the lease**

12. **Check every rent bill against the abstract**: the right base rent for the month, escalations applied on the
    right date and by the right amount, operating expense estimates as the lease allows. Pay through
    `pay-bills-and-buy`. A wrong bill is disputed in writing in the way and time the lease sets.
13. **Review the yearly operating expense statement** when it arrives, with the method in
    `references/lease-review-checklist.md`. The window to object is in the lease and is often short: calendar it the
    day the statement arrives.
14. **Keep promises the lease makes for the company**: certificates of insurance to the landlord at each renewal,
    estoppel certificates returned inside the lease's deadline (the owner signs; check every statement in one
    against the records first), maintenance the lease puts on the tenant. Building faults go to the landlord
    (`triage-an-office-request`).

**E. Renew, leave or sublease**

15. **Start the decision 12 months before the last notice date**, not before the expiry date. Rerun step A, get the
    landlord's proposal and at least two alternatives, and put the comparison to the owner. Leverage comes from
    having time to move.
16. **Give notice exactly as the notice clause says**, with `references/notices-and-renewals.md`: the right party
    and address, the required method, sent to arrive well before the last day, with proof kept. Exercising or
    giving up an option is asked as an OK on the exact notice.
17. **For a sublease, assignment, early exit or move-out**, follow `references/sublease-and-exit.md`.

**F. Publish the real estate page** after any change. See Output.

## Gates

Before anything is signed, sent or committed:

1. **The signed documents were read in full**, in order, and the abstract cites clause numbers. A broker's summary,
   a letter of intent or a listing is not the lease.
2. **Every critical date was counted from the lease's own definitions**, with the arithmetic written, and the
   earlier date is used where two readings are possible.
3. **The whole-term cost is on the page**, on usable area, with each assumption named.
4. **A lawyer has reviewed** any lease or sublease longer than 12 months, anything with a personal guarantee, and
   any amendment, consent, estoppel or subordination document that adds or gives up a right.
5. **Permitted use and zoning cover what the company will actually do there**, including storage of anything
   hazardous such as batteries or chemicals.
6. **The insurance the lease requires is in force before taking possession** (`manage-insurance`).
7. **A notice is sent by the method the lease names**, to the address it names, early enough to be received in
   time, and proof of delivery is saved.
8. **No notice window closes in silence.** If the decision is still open 30 days before the last safe date, ask
   the owner every working day and say what happens by default.

Hard stops. Do not reply or act; tell the owner the same day and brief the lawyer (`write-a-counsel-brief`):

- A notice of default, a demand to cure, or any threat of lockout or eviction.
- Finding the company is past its expiry date and still in the space (holdover).
- A sale, foreclosure or bankruptcy of the landlord, or a demand to sign a subordination or estoppel document.
- Any personal guarantee, or a request to extend or reaffirm one.
- A dispute over a deposit, restoration or operating expenses.
- Fire, flood or other damage to the space, an injury there, or a government taking; notify the insurer too.
- An environmental or hazardous materials problem.

Never:

- Sign, or say "agreed" in writing, to any lease term. Emails can be read as agreement.
- Give notice by email alone when the lease requires another method.
- Withhold rent to make a point. It is a default.
- Let a sublease run past the lease it sits under, or sublet without the landlord's written consent.
- Assume silence renews a lease, or that paying rent after expiry does.
- Count on the landlord or broker to remind the company of a date.

Where law varies by place: whether a late or defective option notice can ever be excused; holdover consequences;
deposit rules; what a landlord must do to re-let after a tenant leaves; transfer and rent taxes; zoning and
occupancy permits; who must make a space accessible. On that last point federal law treats both landlord and tenant
as responsible to the public for a place open to the public, and a lease can only divide the cost between them
(US Department of Justice, ADA Title III Technical Assistance Manual III-1.2000, read 2026-11-18).

## Autonomy

The bot does on its own: test the need and build the options; keep the register, abstracts and calendar; review
drafts and write the issues table; price the whole term; check rent bills and operating expense statements and
query the landlord about them; send certificates of insurance to the landlord; book flexible space inside the
answer to question 4; schedule viewings; draft every notice; pass facts to the books, taxes and insurance skills.

The bot asks for an OK only for:

- Signing a lease, sublease, amendment, letter of intent, consent, estoppel or guarantee (the owner signs).
- Sending any notice that exercises or gives up a right: renewal, termination, non-renewal, a formal dispute.
- Any commitment longer than 30 days' notice, any deposit, and any spend above the occupancy budget.
- Hiring a broker or lawyer, and starting or agreeing a sublease.
- Giving up space people currently use.

## Output

- **The real estate page**: `tico page publish reports/real-estate/space.html --title "Space" --cadence monthly
  --as-of <date>`. One screen: a row per site with a bar across the years showing the term, each option's notice
  window shaded, and escalation dates marked; monthly occupancy cost by site as a stacked bar (rent, operating
  expenses, other) with cost per regular user; the next five critical dates with days left; open issues. Mark it
  "Read from the signed leases on file as of <date>. Have counsel confirm before acting on a date."
- **A decision page** for any new space or renewal: the need in one paragraph, the options as a table on whole-term
  cost and exit cost, a chart of cumulative cost by month for each option, and the recommendation.
- **A review page** for a draft lease: the issues table, the whole-term cost, the facts checked.
- **Records**: `knowledge/leases.md` (setup and the register), `knowledge/leases/<site>.md` (one abstract per
  site), signed documents filed with `file-a-signed-agreement-in-the-index`, and every notice with its proof.

## Checks before you finish

- Every abstract term has a clause number, or says "not found in the documents read".
- Each critical date shows its arithmetic, its lead date, an owner and an automation.
- Costs are shown for the whole term and per usable square foot, with assumptions named.
- A notice names the lease, the clause it is given under, the party and address from the notice clause, and the
  method; the proof is attached.
- The page shows no person's home address.
- Books, taxes and insurance have been told what changed.
- Anything a lawyer must see is marked, with the brief attached.

## Example

Invented, for illustration. The numbers are made up.

Task: "Decide on the Elm Street office: last notice date is 2027-04-03." The lease was first abstracted on
2026-11-18, already inside the 12-month lead time, so the due trigger opened the task the same day.

- Abstract: office lease, 2,400 rentable square feet, expires 2027-09-30. Clause 3.2: one option to renew for
  three years, exercised by written notice "not less than 180 days nor more than 365 days before the Expiration
  Date", sent by certified mail to the address in clause 24. Clause 19: holdover rent is 150 percent of the last
  month's rent.
- Dates: 365 days before 2027-09-30 is 2026-09-30, so the window is open. 180 days before is 2027-04-03, a
  Saturday; the lease has no weekend rule, so the working last day is Friday 2027-04-02, and the safe date to post
  is 2027-03-03.
- Need: booking data for the last six months shows the office used on 31 of 126 working days, by two to four
  people. No stock is kept there. The legal address can move to the registered agent.
- Options over 36 months: renew at the option rent in clause 3.3, total 268,000.00 including operating expenses
  growing at an assumed 4 percent; a four-desk flexible membership two blocks away on 30 days' notice, 54,000.00;
  rooms by the day, 21,600.00 at the current usage.
- Recommendation: let the option lapse, book rooms by the day, and move the legal address. The owner OKs the
  decision page. No renewal notice is sent; the decision and its date are written on the abstract.
- Follow-through: clause 21 requires the space returned "broom clean with tenant's cabling removed"; a move-out
  task is opened for 2027-07-02 (90 days before expiry) with the restoration quote, the deposit claim and the
  address changes. The company must be out by 2027-09-30 because of clause 19.
