# Chargebacks: contest every one, and how to win

Numbers and rules here were read on 2026-11-18, mostly from processors' and vendors' guides. Card network rules
change twice a year. The dispute record in the processor is the authority for the reason code, the due date and the
fee; read it first.

## How a chargeback works

1. The cardholder tells their bank (the issuer) that a charge is wrong.
2. The issuer files a dispute through the card network with a reason code. The money and usually a fee leave the
   company's account at once.
3. The processor notifies the company and gives an evidence due date.
4. The company answers once with evidence (called representment or a dispute response), or accepts. This skill
   always answers.
5. The issuer decides. The network's later stages (pre-arbitration, arbitration) cost more and are rarely worth it.

The reader is an analyst at the cardholder's own bank who has minutes, decides by the reason code, and has already
heard the customer's side. The evidence must answer that code directly.

## The clock

- **The processor's due date is the one that binds.** At Stripe it is commonly 7 to 21 days from the dispute and
  shows on the dispute as the evidence due date. The network windows behind it are longer (about 30 days for a Visa
  response and 45 for a Mastercard second presentment), but the company never gets that long.
- **Usually one submission.** At Stripe, evidence can be submitted once per dispute. Assemble everything, check it,
  then submit.
- **A missed date is an automatic loss.**
- Set the action date at least two business days before the due date.

## Contest every one

The owner's rule (2026-11-18): every chargeback is contested. There is no amount floor and no case where thin
evidence means accept. Check in this order, then submit.

1. **Was the charge already refunded?** If yes, respond with the refund record (date, amount, processor reference)
   under "credit already issued". This is the easiest win and avoids paying twice.
2. **Is the evidence list for this reason category complete (below)?** If not, gather everything that exists, mark
   the rest "does not exist" or "could not reach", and submit the best file anyway. Log what was missing so the
   gap gets fixed for next time.
3. **Does the customer deserve money back on the record?** Contest anyway, and note on the matter that a refund is
   owed. The file states only what the record shows and claims nothing the record contradicts. If the company wins,
   refund the customer after the dispute closes; if it loses, the money has already gone back and nothing more is
   paid.
4. **Record the fee.** Processors charge a dispute fee and some charge a second fee to contest (Stripe: $15, plus
   $15 to counter since June 2025, the second returned on a win, per third-party guides; read the current fee in the
   account). It is a cost of the dispute, not a reason to accept.

Rules that hold in every case:
- Do not refund a charge that has an open dispute. The money has already gone back; a refund sends it twice. A refund
  the customer deserves goes out after the dispute closes.
- If the customer agrees to drop the dispute, ask them to tell their bank and to confirm it in writing to the
  company, and still submit evidence with that message attached. Only the bank can withdraw a dispute.
- An inquiry or retrieval request (a question from the bank before a dispute) should always be answered, and a
  refund at that stage can prevent the chargeback.

## Evidence by reason category

Networks number their codes differently and change them. Work from the category in the dispute record.

### "I did not make this purchase" (fraud, unauthorized)
Prove the cardholder took part.
- The address and security-code checks at payment, and the result of any 3-D Secure authentication. An
  authenticated payment usually shifts fraud liability to the issuer.
- The customer account: creation date, login history, the email and phone on it, and that they match the cardholder.
- IP address and device identifier for the order.
- Delivery to the billing address, or the service performed at the cardholder's own address, with proof.
- Messages from the customer about the order, before or after.
- **Earlier undisputed purchases on the same card.** Under Visa's Compelling Evidence 3.0 rule for its card-absent
  fraud code, two earlier transactions on the same card, each 120 to 365 days old and not disputed or reported as
  fraud, that share at least two data elements with the disputed one (IP address, device identifier, shipping
  address, account login), one of which is the IP address or device identifier, establish a history that can defeat
  the dispute. Processors differ on how the 120 to 365 days are counted; follow the processor's form.

### "It never arrived" / "the service was not provided"
- Goods: carrier tracking showing delivery to the address on the order, with date; signature or photo if any.
- Services: the booking, the check-in and check-out record, photos of the completed work, a completion message,
  and anything the customer said afterwards that shows they knew it happened.
- Digital: access or download logs with dates, IP addresses and the account used.
- If it was late, not missing: the delivery date and the customer's messages around it.

### "It was not as described" / "it was defective" / "poor quality"
- The description, photos or scope as shown at the time of sale (dated copy).
- Proof that what was delivered matches it.
- The return or redo policy as shown at checkout, and that the customer accepted it.
- Whether the customer asked for a return, a redo or a refund before disputing, and the company's answer. An
  unanswered offer to fix the problem is strong evidence.
- A direct answer to each specific fault the cardholder claimed.

### "I cancelled" / "I did not agree to be charged again" (recurring)
- The signup or checkout page as it was that day, showing the recurring terms, the price, the billing interval and
  how to cancel, and the record of the customer's consent (the tick or click, with timestamp and IP).
- The reminder or receipt sent before or at each renewal.
- The account log: no cancellation request before the billing date, or the date of the request and that billing
  stopped after it.
- Use of the service, or delivery, after the date they say they cancelled.

### "I returned it" / "I was promised a refund" (credit not processed)
- The refund policy as shown at checkout, and acceptance.
- That nothing was returned, or that the return fell outside the policy, with dates.
- Or the refund already issued, with its reference.
- Every message about the refund, in order.

### "I was charged twice" / "the amount is wrong"
- Two separate orders, each with its own receipt, items and delivery; or one charge only.
- The price shown and accepted at checkout against the amount charged.

### Processing and authorization codes
These concern how the payment was run, not the customer's experience. Submit the processor's authorization record
and whatever else it holds. A weak file still beats an uncontested loss.

## The evidence file

- **Cover letter, one page.** Order and dispute references; the cardholder's claim in one line; the answer in one
  sentence; three to five numbered points, each tied to an exhibit; the request ("the merchant asks that the dispute
  be resolved in its favor"). No argument about the customer's character. No law.
- **Exhibits, in the order the letter cites them.** Each one labeled, with the key line marked. Exports and
  screenshots as they are, each with its date. The first page of the terms plus the relevant section, not forty pages.
- **One customer only.** Mask other names and data, and never include a full card number.
- **Fit the form.** Processors cap file size and page count and ask for evidence in named fields. Put each item in
  its proper field and keep the whole file short enough to be read in a few minutes.

Cover letter model:

```
Re: Dispute [reference] - [card network] - Reason: [as stated]
Merchant: [Company legal name] ([descriptor on the statement])
Transaction: [date], [amount], order [number]

The cardholder states that [claim in one line]. The merchant's records show otherwise: [answer in one sentence].

1. [Fact with date.] Exhibit 1.
2. [Fact with date.] Exhibit 2.
3. [Fact with date.] Exhibit 3.
4. The cardholder accepted the merchant's [terms / cancellation policy / refund policy] on [date] by [method].
   Exhibit 4.
5. [Any contact with the cardholder about this charge, and its result.] Exhibit 5.

The merchant asks that this dispute be resolved in its favor.
```

Where the record supports the cardholder on a point, drop "the merchant's records show otherwise" and set out the
facts the record holds, in order. Never state anything the record contradicts: the file is contested on the truth.

## After the decision

- **Won:** the funds return. Record it. If the matter notes the customer deserves money back, refund it now and tell
  them, within authority. Otherwise nothing is sent to the customer about it.
- **Lost:** record the reason given. Further network stages add fees of their own; go on only with the approver's
  OK and a clear new point. A lost chargeback does not decide whether the customer legally owes the money, but
  chasing it (a collections agency, a small-claims case brought by the company) is the approver's decision, never
  automatic, and is rarely worth it for a small amount.
- **Either way:** add the dispute to the monthly count, by reason category.

## Keep the rate down

Card networks monitor each merchant's ratio of disputes and fraud to sales, and fine or restrict merchants above
their lines. Guides read in November 2026 give these figures; sources conflict on details, so confirm with the
processor:

- Visa's monitoring program (VAMP): a merchant is "excessive" at a ratio of 1.5% from 1 April 2026 in the United
  States, Canada, the European Union and Asia-Pacific (it was 2.2% before), once it also has at least 1,500 fraud
  and dispute events in the month. Acquirers often act earlier than Visa's line.
- Mastercard's excessive chargeback program: commonly given as 100 or more chargebacks in a month with a ratio of
  1.5%; some guides give 1.0%.

Report the company's monthly ratio next to these lines in the readiness check. The levers, in order of effect:

1. A statement descriptor the customer recognizes, and a receipt at every charge.
2. A refund made quickly when it is owed, before the customer goes to the bank.
3. Cancellation that is as easy as signup, with a confirmation message.
4. A reminder before each renewal.
5. Consent captured and stored for every sale: the page as shown, the click, the time, the IP address.
6. Delivery or completion proof stored for every order.
7. The processor's dispute-alert and automatic-resolution programs, where the fee per alert is less than the cost
   of the disputes they stop.
8. Authentication (3-D Secure) on orders that look risky.

How long to keep evidence: cardholders can usually dispute for about 120 days after the charge or the expected
delivery, and the earlier-purchase evidence above reaches back up to 365 days. Keeping consent, delivery and account
records for at least 18 months covers both. That is a practical floor, not a rule.
