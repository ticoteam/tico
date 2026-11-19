# Consumer-protection lawsuits and arbitration

Two parts. Part A: a lawsuit in a regular court under a consumer-protection law. Part B: arbitration, both when a
demand arrives and when checking the company's own clause before one does.

In both, a lawyer leads and the bot's job is speed and a complete file. United States default; every rule named here
varies by state, court or arbitration provider. Law was read on 2026-11-18 unless marked "from general knowledge".

## Part A. A lawsuit under a consumer-protection law

### What it is

Every state has a law against unfair or deceptive acts and practices in consumer sales (often called a UDAP law;
examples are California's Consumers Legal Remedies Act and Unfair Competition Law, Massachusetts chapter 93A and the
Texas Deceptive Trade Practices Act). They matter more than an ordinary contract claim for three reasons:

- Many allow multiplied damages (double or triple) or a fixed minimum per violation.
- Many make the losing business pay the customer's lawyer. The fees often exceed the claim.
- Many can be brought for a class of customers.

So a $200 complaint can be a five- or six-figure exposure. Never judge one of these by the amount the customer paid.

### Day one

1. Read the summons for the answer date (commonly 21 days from service in federal court and 30 in California state
   court; the summons states the one that applies). Calendar it with an action date one week earlier.
2. Tell the approver and send the counsel brief. A company generally cannot answer in a regular court without a
   lawyer, and an unanswered complaint becomes a default judgment.
3. Give notice to the insurer if any policy might respond (general liability, errors and omissions, cyber, directors
   and officers). Late notice can lose coverage. This needs the approver's OK; ask today.
4. Place the hold, wide: the practice challenged, not just this customer.
5. Check the terms the customer accepted for an arbitration clause and a class waiver, and tell the lawyer the same
   day (part B explains why the first day matters).
6. Stop all direct contact with the customer about the dispute.

### The file the lawyer needs, by day two

Put each item in `reports/matters/<id>/evidence/` and list it in the brief with "have / does not exist / could not
reach".

1. **The statement or practice challenged, exactly as it was on the customer's dates.** The ad, page, price display,
   email or script, from version history or an archive. Not today's version.
2. **Disclosure and consent.** What the customer was shown before paying, where on the page, and the record of their
   agreement (click, tick, signature, timestamp, IP address).
3. **What this customer actually saw and did.** Their path, messages, calls, and anything showing whether they
   relied on the statement.
4. **Loss.** What they paid, what they received, refunds and credits already given.
5. **Any notice letter sent before the suit, and the company's reply.** Several statutes require notice first and
   give a short window to cure or offer (California's 30 days, Massachusetts' 30 days, Texas' 60 days). Whether the
   window was used well can cap the exposure. See `answer-a-demand-or-complaint` for those windows.
6. **Dates for the limitation period.** When the sale happened and when the customer first complained.
7. **How many customers saw the same thing.** The number of sales under the same page or practice in the period, and
   the revenue. The lawyer needs this to judge class exposure. State it once, in the private brief only.
8. **Earlier complaints about the same thing.** Tickets, reviews, chargebacks, agency complaints, with dates.
9. **The dispute terms.** The arbitration clause, the class waiver, each version in force during the period, and the
   proof this customer accepted them.
10. **Others who may share the cost.** Insurance policies, and vendors whose product or service is involved (a
    payment, messaging, advertising or tracking provider) with the indemnity section of their contract.

Write facts only. No view on whether the practice was lawful, and no commentary on the customer. See
`answer-a-demand-or-complaint/references/admission-control.md` on why internal notes are evidence.

### Fact patterns that are commonly sued on

Flag any of these to the lawyer in the first line of the brief. Each has its own statutes and specialist law.

| Pattern | What to pull first |
|---|---|
| Automatic renewal and cancellation | The signup page as shown, the consent record, renewal reminders, the cancellation steps and logs |
| Fees added late in checkout, or prices that are not the full price | Every screen of the purchase path as shown on the date |
| "Sale" or reference prices, limited-time claims | The price history |
| Reviews and testimonials (paid, filtered, written by staff, or suppressed) | How reviews are collected, moderated and displayed |
| Marketing calls and text messages | Consent records per number, opt-out handling, the sending vendor's logs |
| Website tracking, session recording, chat recording, video viewing data | Which tools run on the site, since when, and the consent banner's history |
| Website or app accessibility | The claim's specific pages and any earlier audit |
| "Made in", health, environmental and safety claims | The basis for the claim held at the time it was made |

### What the bot does not do

It does not draft or file an answer, contact the plaintiff or their lawyer, propose a settlement figure, or change the
challenged page without telling the lawyer first. If a change is needed to stop ongoing harm, save a dated copy of
the page as it was, then make the change, and record both in the hold log.

## Part B. Arbitration

### Why the clause matters on day one

If the customer accepted terms with an arbitration clause, the company may be able to move a court case to
arbitration, usually without a class. That right can be lost by acting like a litigant first: answering, taking
discovery or filing motions without raising the clause. The United States Supreme Court held in Morgan v. Sundance
(2022; from general knowledge) that the other side need not show it was harmed by the delay. So the lawyer must know
about the clause, and have the proof of acceptance, before anything is filed.

### When an arbitration demand arrives

1. Read the demand and the provider's notice. Record the provider (commonly the American Arbitration Association or
   JAMS), the rules named, the amount, the answer date and the fee due date. Calendar both dates.
2. Read the clause the customer accepted, in the version in force on their date, and the provider's current consumer
   rules and fee schedule on the provider's own site. The clause and the rules together decide who pays what, where
   and how the hearing happens, and whether small-claims court is an option.
3. Tell the approver and the lawyer the same day. Place the hold.
4. **Get the invoice paid on time.** Under consumer rules the business pays most of the fees, and the provider can
   decline or close the case if it does not. In California, statutes treat a business that pays more than 30 days
   late as in breach of the arbitration agreement, which can send the case back to court with sanctions. The
   California Supreme Court held in Hohenshelt v. Superior Court (August 2025) that those statutes stand, while
   allowing relief where the late payment was not willful or grossly negligent. Do not rely on that relief: put the
   fee date on the calendar with an owner the day the invoice arrives, and ask for the OK to pay at once.
5. Check the small-claims route. Most consumer arbitration rules let either side take a claim that fits to
   small-claims court instead. For a small claim that is often cheaper for the company than the arbitration fees. It
   is the approver's decision.
6. Build the evidence pack by fact pattern, exactly as for any other forum. Arbitrators decide on documents and
   short hearings.
7. Count similar demands. If the same law firm or the same wording appears on many demands, tell the lawyer at once.
   The American Arbitration Association's mass-arbitration rules apply from 25 similar demands and carry their own
   fee schedule; the fees scale with the number of cases whatever their merit.

### Check the company's own clause before a claim arrives

Run this in step 9 of the skill. It is the list a consumer lawyer works through to defeat a clause (the National
Consumer Law Center publishes such a list for consumer advocates). Answer each with the evidence, and mark each
"holds / weak / missing". Report the result; a lawyer changes the clause, not the bot.

1. **Was an agreement formed?** Can the company produce, for any given customer, the screen they saw, the notice of
   the terms on it, and the action they took to accept (a tick box or a clearly labeled button, with timestamp)? A
   link in a page footer that nobody had to notice is the weakest form. Courts look at how conspicuous the notice was
   and whether the customer did something unambiguous (for example Berman v. Freedom Financial Network, 9th Cir.
   2022; from general knowledge).
2. **Which version applies?** Is every version of the terms kept with its dates in `knowledge/terms-history.md`, and
   when terms changed, is there proof that existing customers were told and continued?
3. **Is it fair enough to enforce?** One-sided features invite a finding that the clause is unconscionable: only the
   customer must arbitrate, a distant hearing place, a shortened time to bring claims, limits on remedies the statute
   gives, fees shifted to the customer, a clause the company can change at will.
4. **Class waiver and public injunctions.** Does the clause waive class claims, and does it try to bar a customer
   from seeking an injunction for the public? Some states, California in particular, refuse to enforce the second.
5. **Who decides whether the clause applies?** Does it clearly give that question to the arbitrator, and name the
   provider's rules?
6. **Scope.** Does the clause cover the kind of claim the company actually faces (statutory and advertising claims,
   not just "this contract")?
7. **Small-claims carve-out.** Present, and for both sides?
8. **Opt-out.** Did the customer get a period after signup to opt out (30 days is common)? Are opt-outs recorded?
9. **Fees.** Does the clause match the provider's consumer fee rules, or promise something the company will not
   want to pay at scale?
10. **Provider.** Is the named provider still taking consumer cases under this clause? The American Arbitration
    Association keeps a registry of consumer clauses and may decline to administer cases under a clause that is not
    registered or does not meet its consumer due-process standards. Record whether the clause is registered.
11. **Mass arbitration.** If the clause has terms for many similar claims (batching, staged hearings), are they
    balanced? A court refused to enforce one provider's lopsided mass-arbitration rules in Heckman v. Live Nation
    (9th Cir. 2024; from general knowledge).
12. **Conduct.** Has the company ever litigated a customer claim in court without raising the clause?

Whether to keep an arbitration clause at all is contested among business lawyers since mass arbitration made fee
exposure scale with the number of claimants. That is an owner and lawyer decision; the bot reports the facts above
and the company's dispute volume.
