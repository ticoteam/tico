# When something happens

The two ways a claim is lost before it starts: the carrier heard too late, or someone at the company said or paid
something first. Everything here guards against those two.

## What counts

Treat any of these as a possible claim the moment it is known. Whether it is "really" a claim is decided after
notice, not before.

- A lawsuit, arbitration demand, subpoena, or a letter from a lawyer or regulator.
- Any message asking the company for money or blaming it for a loss.
- An injury to anyone on the company's premises, at its event, by its product, or to an employee at work.
- A data incident: lost device, wrong recipient, intrusion, ransomware, a vendor's breach involving company data.
- Theft, fraud, or a payment sent to the wrong party.
- Fire, water, break-in, damaged or lost stock, a damaged shipment.
- A product defect, a pattern of similar failures, or a safety complaint.
- An employee's complaint of discrimination, harassment, unpaid wages or wrongful dismissal.

## The first day

1. **Stop further harm.** Make the place or system safe. Reasonable steps to limit the loss are expected and
   usually covered; improvements are not.
2. **Open a claim file** and a task. Write the facts with times: what happened, when the company first knew, who is
   involved, what has been done. Facts only, no opinions on fault.
3. **Preserve evidence.** Photos, the damaged item, logs, messages, the packaging, the contract. Tell the people
   involved to delete nothing. For a legal claim, record the hold as the legal skills describe.
4. **Find the policy that could respond** in the register, and check the others: one event can touch several (an
   injury by a product can involve product liability and, for an employee, workers' compensation).
5. **Read the notice clause** and give notice that day, in writing, the way it says, to the address or portal it
   names, with a copy to the broker. For a claims-made policy the claim usually has to be reported while the policy
   is still in force or within a short window after, so check the expiry first. Say what happened, when, where,
   who, the policy number, and that more will follow. Save proof of sending.
6. **Tell the owner**, with the deadline that matters most.
7. **Say little outside.** To the other party: an acknowledgement that the message was received and is being
   looked at. No apology that admits fault, no promise, no payment, no opinion on coverage.

## Special cases on the first day

- **Lawsuit or demand letter.** Send it to the carrier (this is called tendering the claim) and to the lawyer.
  The court's or letter's deadline keeps running. `answer-a-demand-or-complaint` owns the reply; nothing is
  answered before the carrier has had the chance to appoint or approve a lawyer, unless the deadline forces it.
- **Data incident.** Call the cyber policy's breach hotline before hiring any lawyer or forensic firm; many
  policies pay only for the ones the carrier approves. Record the facts with `log-breach-facts-for-counsel`. Legal
  duties to notify people have their own clocks, decided by counsel.
- **Product that hurt someone or could.** Hard stop to the owner and a lawyer within the hour. The duty to report to
  the Consumer Product Safety Commission runs 24 hours from obtaining reportable information, and the CPSC says an
  investigation into whether to report should not exceed 10 working days (CPSC "Duty to Report", read 2026-11-18).
  Notify the product liability carrier, and the recall carrier if there is one. Keep every affected unit.
- **Employee injury.** Get the person care first. Report to the workers' compensation carrier and file the state's
  first report of injury; the deadline is the state's, so read its agency page.
- **Damaged or lost shipment.** The carrier's own claim deadline comes first (`check-a-claim-before-the-deadline`);
  the company's cargo or property policy is notified as well.
- **Fraud or a misdirected payment.** Call the bank at once to try to recall it, then notify the crime or cyber
  carrier. Report to law enforcement if the policy or the bank asks for it.

## After notice

1. Record the claim number, the adjuster, and everything asked for, with dates. Answer requests within two
   business days from records.
2. **Reservation of rights, denial, a request for a recorded statement or an examination under oath:** hard stop.
   The owner and a lawyer decide the response.
3. **Proof of loss.** Property claims often need a sworn statement by a date in the policy. Calendar it. An officer
   signs.
4. **Do not repair, replace or dispose** of damaged property beyond what stops further harm until the adjuster has
   seen it or agreed in writing.
5. **Settlement.** The carrier may need the company's consent, or the company the carrier's. Either way it is the
   owner's OK.
6. **Money.** Book the retention and any costs the company pays to the claim; book recoveries when received. Tell
   `forecast-cash-and-budget`.

## Closing

- Final letter or payment saved; the file complete.
- Root cause and what changed, in three lines, on the task.
- The register row updated (remaining aggregate limit if the claim used some).
- The incident listed for the next renewal application. It must be disclosed.

## The claim file

```
reports/claims/<date>-<short name>/
  facts.md        (timeline with times and sources)
  notice.pdf      (what was sent, to whom, proof)
  policy.md       (which policies, the notice clause quoted)
  log.md          (every contact: date, who, what)
  evidence/       (photos, documents, exports)
```

Share it to the claim's task only. It can contain personal and legal material.
