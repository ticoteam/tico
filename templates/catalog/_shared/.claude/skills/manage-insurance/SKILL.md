---
name: manage-insurance
description: Keep the company properly insured and able to prove it. Works out which policies a small US company needs from what it does (general liability, property, workers' compensation, cyber, E&O, D&O, employment practices, product liability and recall for hardware), keeps a register of every policy read from the policy itself, finds gaps against contracts, leases and state law, runs each renewal from 120 days out, gets and sends certificates of insurance, collects them from contractors and vendors, and reports and tracks claims. Use when a task says insurance, policy, coverage, limits, deductible, premium, renewal, quote, broker, bind, general liability, GL, BOP, cyber, E&O, professional liability, D&O, EPLI, workers' comp, product liability, recall, umbrella, certificate of insurance, COI, additional insured, waiver of subrogation, claim, loss, incident, premium audit or loss runs. Not for employee health and benefits plans, or for answering a lawsuit itself (answer-a-demand-or-complaint).
metadata:
  function: finance
  roles: [operations-manager, finance-owner, compliance-manager, general-counsel, legal-owner, office-manager, vendor-manager, security-owner]
  actions: [read, draft, send, spend, change-records]
  review: professional
---
# Manage insurance

When to use: when a policy is within 120 days of renewal, when a customer, landlord, lender or investor asks for proof
of insurance, when the company changes in a way an insurer would care about (first employee, new state, new product,
new site, a big contract), when anything happens that could become a claim, and once a quarter to check for gaps.

Words used here: **the company** is every legal entity that needs to be insured. **The broker** is the licensed agent
or broker named in `knowledge/insurance.md`. **The carrier** is the insurance company. **The declarations page** is the
first pages of a policy listing who is insured, for what period, with which limits. A **limit** is the most a policy
pays; a **retention** or **deductible** is what the company pays first. An **endorsement** is a page that changes the
policy. A **certificate of insurance** is a one-page summary a broker issues to a third party. **Occurrence** and
**claims-made** are the two ways a liability policy is triggered, explained in `references/coverage-map.md`.

This skill is written for a US company. Insurance is regulated state by state, and workers' compensation most of all.
Rules here are US defaults with the date they were read. The policy's own words decide what is covered, never a
summary, a certificate, a quote or this skill.

## What great looks like

The company carries the policies its real risks call for, at limits its contracts and its balance sheet justify, and
pays for nothing else. Every promise it has made to carry insurance (in a customer contract, a lease, a loan) is met
and can be proved the same day. No policy ever lapses. When something goes wrong, the carrier hears about it that day,
in the way the policy requires, and nobody at the company has said anything that gives the carrier a reason to refuse.

An expert reads the policy, not the brochure. The gaps that hurt small companies are rarely a missing policy. They are
a mistake in the work that general liability never covered, a claims-made policy replaced without keeping its
retroactive date, a new employee in a state the workers' compensation policy does not list, a certificate that says
"additional insured" with no endorsement behind it, and a claim reported a month late.

## First time at a company

Read `knowledge/insurance.md`. If it has a "Setup" section, skip this. If not, ask the owner these in one numbered
message (`tico task ask <task id> --to <owner> "<the questions>" --version N`), save the answers there with the date, and work on
the defaults until the answers arrive.

1. Who is the broker or agent, and where are the current policies? Default: none named. Search for them
   (`tico doc search "declarations OR policy number OR certificate of insurance"`, `tico email search "policy
   renewal OR binder OR premium"`, and insurance vendors in the book), build the register from what is found, and
   shortlist two licensed brokers who work with companies of this kind for the owner to choose from.
2. What does the company do that could hurt someone or cost them money: what it sells, whose data it holds, what
   physical things it makes, ships or stores, and where people work? Default: read `knowledge/company.md`, the
   product pages, revenue lines in the book, `knowledge/leases.md` and the payroll provider's list of work states.
3. What can the company afford to lose on one bad event without insurance? This sets retentions. Default: one
   month of operating expenses from `tico finance report pnl`.
4. What is the yearly premium budget, and may the bot renew like for like on its own? Default: no new budget, so
   nothing new is bound without an OK; a like-for-like renewal within 15 percent of the expiring premium, with no
   narrower terms and no new signed application, is the bot's.
5. Who is told first when something goes wrong, and may the bot report a claim to the carrier itself? Default: the
   owner is told at once, and the bot reports any claim, lawsuit or written demand the same day without waiting.

## Steps

**A. Build the register (first run, then on every change)**

1. **Find every policy** as in question 1, plus any insurance the company gets through someone else (a payroll
   provider's workers' compensation, a landlord's or marketplace's programme).
2. **Read each policy itself** and fill one register row with `references/policy-register.md`: named insureds,
   period, form, limits, retention, premium and its basis, the notice clause, key exclusions, states and sites
   covered, endorsements. A field that is not in the document read is written "not found in the document read",
   never guessed. Ask the broker for any policy the company cannot produce.
3. **Put the dates on the calendar.** For each policy: expiry, and the day renewal work starts (default 120 days
   before). Create an automation with a due trigger for the start day (`references/policy-register.md` has an
   example). Also set `renewal_on` on the carrier's contact in the book (`tico finance act contact_update`), which
   raises `finance.vendor.renewal_due` 30 and 7 days before as a backstop. Thirty days is too late to start an
   insurance renewal. For a policy or programme that renews automatically, `check-a-renewal-deadline` gives the
   last day to stop it.

**B. Find the gaps (first run, each quarter, and on every trigger)**

4. **List what the company has promised.** Read the insurance clause of every customer contract, lease, loan and
   investor agreement in the contract index. For each: coverages, limits, additional insured or other wording, how
   long the cover must be kept. Use `check-quotes-against-the-current-text` so each requirement is quoted exactly.
5. **List what the law requires.** Workers' compensation for employees in each work state (read the state agency's
   page), and anything specific to the trade or the product.
6. **Walk the coverage map** in `references/coverage-map.md` against the facts from question 2. For each risk
   write: covered (policy and limit), gap (what and why it matters), or not needed (why).
7. **Check the triggers** since the last check: a first employee, an employee in a new state, a new site, a new
   product (above all a physical one), a new entity, revenue or payroll well above what the policy assumed, new
   kinds of data, a contract with new requirements. Each one is a message to the broker that week.
8. **Recommend, cheapest sound fix first**: an endorsement to an existing policy, a higher limit, a new policy, or
   accepting the risk with the owner's OK recorded. Get quotes for anything recommended.

**C. Renew** (`references/renewals.md` has the timeline)

9. **Start at 120 days.** Update the exposure figures from records: revenue from the book, payroll by state and
   job class from the provider, headcount, sites, products, contract values, loss history. Send the broker one
   submission. Ask for quotes from more than one carrier where the premium is material.
10. **Compare quote to expiring policy line by line**, not premium to premium: limits, retention, form, retroactive
    date, exclusions, endorsements dropped or added. A cheaper quote on narrower terms is not cheaper.
11. **Answer the application from records.** Each answer names the record it came from. An untrue or careless
    answer can let the carrier refuse a claim later. An officer signs it.
12. **Bind before expiry**, get the binder in writing, pay the premium through `pay-bills-and-buy`, and when the
    policy arrives check it against the binder. Update the register, reissue certificates, and tell
    `close-the-month` the premium and period so it is spread over the term.

**D. Certificates** (`references/certificates.md`)

13. **Going out.** When a customer or landlord asks for a certificate, compare what their contract requires with
    the register first. If the policies already meet it, ask the broker for the certificate and any endorsement,
    check what comes back against the register, send it, and log it with its expiry. If they do not, that is a gap:
    go to step 8 before promising anything.
14. **Coming in.** Collect a certificate before work starts from every contractor who works on site or with their
    hands, every warehouse or 3PL holding the company's goods, and every manufacturer of its products. Log the
    expiry and chase 30 days before it (`chase-a-quiet-vendor`).

**E. When something happens** (`references/claims.md`)

15. **Treat it as a claim first and decide later.** A lawsuit, a demand letter, a complaint asking for money, an
    injury, a data incident, a theft, a damaged shipment, a product that hurt someone, a regulator's letter. Find
    the policy that could respond, read its notice clause, and give notice that day in the way it says, with a copy
    to the broker. Tell the owner. Preserve the evidence. Say nothing to the other side about fault or payment.
16. **Track it to the end**: claim number, adjuster, what was asked for and sent, deadlines, payments, the
    retention paid, and what changed afterwards.

**F. Publish the insurance page** after any change. See Output.

## Gates

Before anything is bound, signed, sent or said:

1. **No lapse.** A renewal is bound and confirmed in writing before the old policy ends. A claims-made policy is
   never replaced or dropped until the retroactive date is kept or a tail (extended reporting period) has been
   quoted and decided on.
2. **The policy was read.** Every statement about what is covered points to a page of the policy or an endorsement,
   with its form number. A certificate, a quote or a broker's email is not the policy.
3. **Every application answer ties to a record**, and nothing known is left out.
4. **A certificate comes only from the broker or carrier** and matches the register. Additional insured status,
   waiver of subrogation and primary wording are real only when an endorsement to the policy says so; get the
   endorsement, not just the box on the certificate.
5. **Notice is given the same day**, by the method the policy names, and the proof of sending is saved.
6. **Employees are covered for workers' compensation in the state where they work** before their first day.

Hard stops. Do the same-day notice, then stop; tell the owner and brief the lawyer (`write-a-counsel-brief`):

- A lawsuit, subpoena or regulator's letter. The deadline to answer runs whether or not the carrier has replied;
  `answer-a-demand-or-complaint` owns the answer.
- A carrier's reservation of rights, denial, request for a recorded statement, or notice of cancellation or
  non-renewal.
- An injury, a death, or a product defect that could injure someone. A company that makes, imports, distributes or
  sells a consumer product must report to the Consumer Product Safety Commission within 24 hours of obtaining
  reportable information (CPSC "Duty to Report", read 2026-11-18). The owner and a lawyer decide that day.
- A data incident involving personal information. Call the cyber policy's breach hotline before hiring anyone, and
  use `log-breach-facts-for-counsel`.
- Finding that the company has had employees in a state with no workers' compensation cover.
- Any release, sworn proof of loss or settlement.

Never:

- Edit, create or back-date a certificate of insurance or any policy document.
- Tell a customer, claimant or teammate that a particular loss "is covered". Say which policy has been notified.
- Admit fault, offer payment, or pay a claimant, and never hire a lawyer or vendor for a claim before the carrier
  agrees, except to stop further harm.
- Let a contractor start physical work, or goods go to a warehouse, without the certificate.
- Leave a known incident off an application.
- Assume an umbrella policy covers what the policies under it exclude.

Where law varies by place: whether and when workers' compensation is required, and from whom it can be bought;
required notices and cancellation periods; claim-handling deadlines; which lines can be sold by whom. Read the state
insurance department's and workers' compensation agency's pages for each state the company is in.

## Autonomy

The bot does on its own: build and keep the register; read policies; run the gap check; start renewals and assemble
the submission; get and compare quotes; renew like for like inside the answer to question 4; pay premiums on
policies already approved; request and send certificates the current policies support; collect and chase incoming
certificates; add a state or site to an existing policy when the law or a signed lease requires it and it is inside
budget; answer a premium audit from records; report a claim, lawsuit or written demand to the carrier the same day;
keep the claim file.

The bot asks for an OK only for:

- Binding a new policy, cancelling or not renewing one, or changing carrier, limits or retentions.
- Signing an application or any statement to a carrier (an officer signs).
- A renewal outside the like-for-like limits, or any premium financing.
- Reporting a circumstance that is not yet a claim. Ask the same day and state the policy's expiry date, because
  the report must usually be made before it.
- Accepting, rejecting or settling a claim payment, and choosing to accept a risk uninsured.
- Appointing or changing the broker.

## Output

- **The insurance page**: `tico page publish reports/insurance/insurance.html --title "Insurance" --cadence monthly
  --as-of <date>`. One screen: a bar per policy across the calendar showing its period, with the renewal start day
  marked; the coverage map as a grid (each risk: covered, gap or not needed); for each contract requirement, the
  limit required beside the limit held; premium by policy; certificates out and in that expire within 60 days; open
  claims with their next date. Mark it "Read from the policies on file as of <date>. The policies decide coverage."
- **The register and setup** in `knowledge/insurance.md`; certificates in `knowledge/certificates.md`.
- **A renewal comparison page** for each renewal, with the OK asked on it when one is needed.
- **A claim file** per incident under `reports/claims/<date>-<short name>/`, shared to its task only
  (`tico file publish <file> --task <id> --scope task`).

## Checks before you finish

- Every register field came from the policy read, or says "not found in the document read".
- Every named entity of the company appears as an insured where it should.
- Each contract requirement is shown next to the policy and endorsement that meets it, or as a gap.
- Each claims-made policy shows its retroactive date, and it has not moved forward.
- Renewal start dates and expiries are on the calendar with an automation behind them.
- Certificates sent match the register on the day they were sent.
- A claim shows when notice was given, how, to whom, and the proof.
- Nothing you wrote says a loss is or is not covered.

## Example

Invented, for illustration. The numbers are made up.

Task: "Northwind Clinics wants a certificate of insurance before they sign. Contract attached."

- Contract clause 11.2, quoted: general liability 1,000,000 per occurrence; technology errors and omissions
  2,000,000; cyber 2,000,000; Northwind named as additional insured on general liability; cover kept for two years
  after the contract ends.
- Register: general liability 1,000,000 per occurrence (occurrence form), meets it. Technology E&O and cyber are one
  combined claims-made policy with a 1,000,000 limit, retroactive date 2024-03-01, expiring 2027-02-14. Gap: the
  limit is half what clause 11.2 asks. No blanket additional insured endorsement was found in the policy read.
- Action: the bot asks the broker for a quote to raise the combined limit to 2,000,000 and to add an additional
  insured endorsement for Northwind. Quote back in two days: 1,840.00 for the rest of the term, retroactive date
  unchanged.
- OK: changing a limit is the owner's. The bot asks with the comparison page; the owner OKs version 1.
- Result: endorsements received and checked against the register; certificate issued by the broker naming
  Northwind Clinics LLC as holder, sent with the endorsement page; logged in `knowledge/certificates.md` to expire
  2027-02-14. A note goes on the renewal task: clause 11.2 requires this cover for two years after the contract
  ends, so the claims-made policy must be renewed or given a tail.
