---
name: defend-a-consumer-claim
description: Defend the company when a consumer takes a claim to a third party who can decide it. Use when a task or inbound notice mentions a chargeback, a card dispute, representment, a payment dispute due date, a small-claims notice or hearing, a summons or complaint, being served, a consumer-protection or deceptive-practices (UDAP) lawsuit, an arbitration demand, a mass arbitration, a legal hold, a litigation hold, or preserving evidence. Also use before a claim arrives (red-team the arbitration clause, check that proof of consent and delivery is kept). The bot finds the forum and its clock, places the legal hold, builds the evidence pack by fact pattern, contests every chargeback with the best evidence file the record allows, decides fight or resolve on the numbers for other claims, prepares the small-claims hearing binder for the person who appears, and hands a lawyer a complete file for anything in a regular court. Not for demand letters and agency or BBB complaints (use answer-a-demand-or-complaint).
metadata:
  function: legal
  roles: [general-counsel, legal-owner, paralegal, billing-specialist, returns-and-refunds-specialist, escalations-manager, accounts-receivable-specialist, compliance-manager]
  actions: [read, draft, send, spend, change-records]
  review: professional
---
# Defend a consumer claim

When to use: a customer's complaint has reached someone with power to decide it: their card issuer, a small-claims
judge, an arbitrator or a court. Also when a task asks for a legal hold, or asks whether the company is ready for
such claims.

Words used here: **the company** is the legal entity named in the claim. **The approver** is named in
`knowledge/legal-disputes.md`. **Authority** is what the bot may give up or pay without an OK.

## What great looks like

- **Evidence decides consumer claims, and most of it is fixed before the claim exists.** The record of what the
  customer agreed to, what was delivered and what was said either was kept or was not. So the first move, always,
  is to stop anything being deleted. The second is to pull the record together while it is fresh.
- **Every forum has its own clock and its own reader.** A card issuer's analyst spends a few minutes and decides by
  reason code. A small-claims judge gives each side a few minutes and rewards the organized, reasonable party. An
  arbitrator and a court run on written rules where a missed date can lose the case outright. An expert identifies the
  forum and the clock in the first hour.
- **The company never defaults.** Not answering is the one sure way to lose.
- **Every chargeback is contested.** No amount floor, and thin evidence is no reason to accept: gather what exists and
  submit the best file before the due date. A customer who deserves money back gets it after the dispute closes,
  never while it is open. Never pay twice (a refund and a lost chargeback for the same charge).
- **Other claims are fought on the numbers.** Contest what the evidence supports and the amount justifies. Resolve
  the rest quickly and cheaply.
- **It never makes the record worse.** No new accusations in writing, no altered or back-dated documents, no contact
  that looks like pressure.
- **Each claim teaches something.** It closes with the cause and a fix for the team that owns it.

## First time at a company

Read `knowledge/legal-disputes.md`. If it has a "Defending" section, skip this. If not, ask the owner these in one
numbered message and save the answers there, dated. Work on the defaults until they arrive. Whether to contest a
chargeback is not a question: every chargeback is contested.

1. Which payment processors does the company use, and can the bot read and answer disputes in them? Default: use
   the processor Tools the bot is granted; list any it cannot reach.
2. What may the bot concede or pay on its own to end a claim? Default: refund up to what the customer paid for the
   disputed order or period (on a charged-back order, only after the dispute closes). Anything more needs an OK.
   (Shared with the "Answering" section.)
3. Who appears for the company in small-claims court? Default: the owner. Many courts require an owner, officer or
   regular employee who knows the facts, and some do not allow lawyers.
4. Which lawyer do we call for a lawsuit, and which insurance policies might cover claims? Default: none named; the
   bot writes the counsel brief and flags "insurance not checked".
5. Which systems delete records on a schedule (chat, call recordings, logs, help desk, job photos), and who can
   switch that off? Default: the bot lists every system it can see, its retention setting where visible, and marks
   the rest "retention unknown" for the owner.

## Steps

1. **Find the forum and the clock, today.** Read the notice itself, not a summary. Record how and when it arrived,
   and keep the envelope or headers. Add the matter to `knowledge/matters.md` and keep the work on one private task
   with the approver as a reader. Confirm every date with `check-a-legal-deadline` and put it on the calendar
   (`tico calendar add --title "<matter>: <which date>" --start <YYYY-MM-DD> --end <the next day, YYYY-MM-DD> --all-day`)
   with an action date before it.

   | What arrived | Who decides | The clock | Who acts for the company |
   |---|---|---|---|
   | Chargeback or card dispute | The card issuer, under network rules | The processor's evidence due date in the dispute record. Often 7 to 21 days. Usually one submission only. | The bot |
   | Small-claims notice | A judge or commissioner | The hearing date on the notice; an earlier date for any counterclaim | A person (question 3); the bot prepares everything |
   | Summons and complaint in a regular court | A judge, perhaps a jury | The answer date on the summons. Often 20 to 30 days from service (21 in federal court, 30 in California). | A lawyer. A company generally cannot appear without one outside small claims. |
   | Arbitration demand | An arbitrator, under the provider's rules | The answer date and the fee due date in the provider's notice | A lawyer in most cases; the bot prepares |
   | A credible threat of any of these | Nobody yet | None, but the hold duty has begun | The bot, then `answer-a-demand-or-complaint` |

   Any row except the first: tell the approver the same day, in the task title, with the date.
2. **Place the legal hold within one business day.** Follow `references/legal-hold.md`: log the decision, name the
   people, bots and systems that hold records on this customer and subject, tell them in writing to delete nothing,
   switch off the scheduled deletion that would touch those records, and export anything that will expire soon.
   For a single routine chargeback the hold can be narrow (that customer's records), but the decision is still logged.
3. **Sort the claim by fact pattern and pull the evidence.** Open `references/evidence-by-fact-pattern.md`, pick the
   pattern, and collect each item on its list into `reports/matters/<id>/evidence/`, numbered, with an index: what it
   is, where it came from, the date of the record and the date it was pulled. Read with the granted Tools: the
   processor, `tico finance search`, `tico email search` and `tico email thread`, `tico doc search`,
   `tico meeting search`, the CRM, help desk and job-record Tools. Export originals. Mark each list item "have",
   "does not exist" or "could not reach".
4. **Write the timeline and test the customer's story against it.** One dated line per event, each with its exhibit
   number, saved as `facts.md`. Then answer plainly: on the record, is the customer right, partly right or wrong?
5. **Decide: contest, resolve or hand over.** For a chargeback there is nothing to decide: contest it, whatever the
   amount and however thin the evidence, and go to step 6. Record the fee to contest as a cost. If the record shows
   the customer deserves money back, note it on the matter: the refund follows once the dispute closes (step 8).
   For every other forum, write the numbers: the amount claimed; what a statute could add; the cost to contest
   (fees, a person's day in court, a lawyer); the chance on the evidence, in words. Then:
   - Customer is right: resolve it now, within authority, in the way the forum allows.
   - Customer is wrong and the evidence list is complete: contest.
   - Evidence is thin: resolve, and record what was missing so the gap gets fixed.
   - A regular court, a class or mass claim, an injury, or exposure above authority: a lawyer and the insurer lead.
     Write the brief with `write-a-counsel-brief` and attach the evidence index.
6. **Work the forum.**
   - **Chargeback:** follow `references/chargebacks.md`. Match the evidence to the reason code, write a one-page
     cover letter, assemble one clean file, check it against the processor's form, and submit before the due date.
     When the evidence list is incomplete, submit what exists, marked honestly; a weak file is still better than an
     uncontested loss. If the charge was already refunded, submit the refund record as the evidence. Never issue a
     refund while the dispute is open.
   - **Small claims:** follow `references/small-claims.md`. Prepare the forms for the person to sign and file, the
     hearing binder in three copies, a one-page timeline, and a three-minute outline of what to say. Check the
     court's own page for who may appear, how to appear remotely, and the counterclaim date.
   - **Lawsuit under a consumer-protection law:** follow part A of `references/udap-and-arbitration.md`. The bot's job
     is that the lawyer has the complete file on day two: the notice, the dates, the facts, the evidence index, the
     terms the customer accepted with proof, any earlier notice letter and the company's reply, insurance details.
   - **Arbitration demand, or a court case where the terms have an arbitration clause:** follow part B of the same
     file. Tell the lawyer about the clause on day one, and get the fee invoice paid on time.
7. **Keep communications clean while the claim is open.** One written channel. No calls to the customer about the
   dispute, no messages from other teammates or bots, no collection activity on the disputed amount, no change to
   the customer's service because of the claim, nothing public. Settlement talks are allowed and often wise; they
   go through the approver or the lawyer, in writing.
8. **Close it properly.** Record the result, the cost and the dates on the matter row. For a chargeback the company
   won, if the matter notes the customer deserves money back, refund it now, within authority, and tell them; if the
   company lost, the money already went back and nothing more is paid. If money is owed under a
   judgment or an award, pay by its due date and get written proof that it is satisfied. If the result can be
   appealed, tell the approver the last day and the cost. Release the hold only under the rules in the hold file.
   File one plain operations task for the cause (a missing consent record, a confusing cancellation step, a late
   refund).
9. **Prepare before the next one** (run this when asked, and after every third claim): red-team the arbitration
   clause with the checklist in part B; check that proof of consent, delivery and cancellation is being saved for
   every sale; compare the dispute rate with the card networks' monitoring thresholds in the chargebacks file; give
   the owner a short list of gaps with the fix for each.

## Gates

- **Nothing on the subject is deleted or edited** from the moment a claim or a credible threat is known, until the
  hold is released in writing. This includes bots' task threads, notes and drafts.
- **No clock is missed and nothing defaults.** If the evidence is not ready, submit what there is, or have the
  person appear and ask for more time. An ask waiting for an OK never justifies a missed date: the bot escalates in
  the task title and prepares the fallback.
- **The bot does not file in a court or with an arbitration provider, and does not appear.** A person signs and
  files small-claims forms; a lawyer handles anything else. The bot prepares complete drafts.
- **Outside small claims the company needs a lawyer.** Do not draft an answer to a complaint for the company to file
  itself.
- **Evidence is authentic.** Records are exported as they are. Nothing is recreated, edited, back-dated or
  "cleaned up". A screenshot taken today is labeled with today's date. A statement about what someone saw or did
  is written and signed by that person.
- **Evidence shows only this customer.** Remove or mask other customers' data and any card numbers before anything
  is submitted.
- **Contest every chargeback.** No amount floor, no accepting because the evidence is thin. A missed or skipped
  submission is the only sure loss.
- **Never pay twice.** Check for an earlier refund before contesting, and never refund a charge with an open dispute.
  A refund the customer deserves waits until the dispute closes.
- **No contact with the decision-maker outside the proper channel**, and no contact with a customer who has a lawyer
  except through that lawyer.
- **No retaliation.** No fees, collections, account closure, review responses or reports made because of the claim.
- **Insurance first where it may apply.** A claim involving injury, property damage, a professional error or data
  goes to the insurer before the company admits, pays or settles anything.
- **A deletion request from the claimant does not override the hold.** Give it to `privacy-manager`
  (`run-a-data-request-to-deadline`) with a note that a hold applies; the records stay.

Hand to a lawyer at once, with the brief: any regular-court case; any claim on behalf of a class; 25 or more similar
arbitration demands; a government office as the other party; injury or death; a claim under a law with multiplied
damages or fee-shifting where the amount is above authority; any decision to counterclaim or to appeal.

Where law varies by place: court deadlines, small-claims limits, who may appear, consumer-protection statutes,
arbitration-fee rules and the hold duty all vary by state and by court. Everything here is the United States default.
Read the notice and the court's or provider's own page for the rule that applies, and record the source and date.

## Autonomy

The bot does on its own: log the matter; place, track and refresh the hold, including switching off scheduled deletion
where its Tools allow; pull and index evidence; write the timeline; contest every chargeback and submit its evidence;
refund within authority (after the dispute closes, where there was a chargeback); prepare every small-claims and
arbitration document; write the counsel brief; calendar every date; run the readiness check.

The bot asks for an OK only for:

- Paying or conceding above authority, and any settlement agreement or release.
- Anything signed and filed in a court or with an arbitration provider (the person signs it themselves).
- Engaging a lawyer, telling an insurer, and paying arbitration or court fees outside the bot's budget. Ask on day
  one: these have their own deadlines.
- A counterclaim, an appeal, or a decision to pursue a customer for a lost chargeback.
- Releasing a legal hold.

## Output

- **The matter page** (`tico page publish reports/matters/<id>/matter.html --title "<Customer>: <forum>" --task <id>`):
  the forum and a countdown to each date; the hold status; a visual timeline; the evidence index with "have /
  does not exist / could not reach"; the customer's claim against the record; the numbers; the path chosen. Marked
  "Prepared for the approver. Facts and options, not legal advice." It states facts and options only.
- **For a chargeback:** the submitted evidence file and cover letter, and the processor's confirmation.
- **For small claims:** the filled forms for signature, the binder (index, timeline, exhibits) as one printable
  page or file, and the speaking outline.
- **For a court or arbitration case:** the counsel brief and the evidence index, on the private task.
- **The hold notice and the hold log entry**, the matter row and the calendar dates.
- **One operations task** for the cause, once the matter ends.

## Checks before you finish

- The forum, the deciding body and every date were read from the notice itself, and each date is on the calendar.
- The hold decision is logged with the date, the scope, who was told and what deletion was switched off.
- Every item on the fact pattern's evidence list is marked have, does not exist or could not reach.
- Every exhibit is an original export with its source and date, and shows no other customer's data.
- The timeline's every line has an exhibit number.
- For a chargeback: it was contested; the evidence answers the reason code actually given, an earlier refund was
  checked for, and the submission is confirmed by the processor before the due date. No refund went out while it
  was open; any refund owed is on the calendar for after it closes.
- For small claims: the person appearing has the binder, the outline and the court's rules on who may appear at
  least three business days before the hearing.
- For a court or arbitration case: a lawyer and the approver have the file, and the answer and fee dates have an
  owner.
- Nothing was sent to the customer outside the single channel, and nothing on the matter is in a public thread.

## Example

Invented, for illustration.

A processor notice arrives on 2026-11-16: dispute of a $64.00 charge dated 2026-10-20, reason "cancelled recurring
transaction", evidence due 2026-11-27. The company sells a monthly snack box.

- Forum and clock: card issuer; due 2026-11-27; action date 2026-11-23. One submission allowed.
- Hold: narrow, logged 2026-11-16: this customer's account, messages and billing records; the help desk's 90-day
  auto-delete is switched off for this contact.
- Fact pattern: "I cancelled and you kept charging". Evidence pulled: the signup page as it was on 2026-06-02 with
  the renewal terms and a ticked consent box (exhibit 1); the account log showing no cancellation request before
  2026-10-20 (exhibit 2); the renewal reminder email sent 2026-10-13 (exhibit 3); tracking showing delivery of the
  October box on 2026-10-24 (exhibit 4); the customer's first message, "please cancel", dated 2026-11-02, and the
  cancellation done the same day (exhibit 5). Earlier refund: none.
- The customer's story against the record: wrong. The cancellation came after the charge and after delivery.
- Decision: contest, as with every chargeback. $64 at stake; the processor's fee to contest is $15 (read in the
  dispute record), recorded as a cost. Evidence list complete. Nothing is owed back on the record, so no refund
  follows the close.
- Submitted 2026-11-18: a one-page cover letter (the claim, the answer in one sentence, five numbered exhibits) and
  the exhibits as one file. Processor confirmation saved. Result date added to the calendar.
- Closing note for operations: the cancellation link is two clicks deep in the account page; task filed.
