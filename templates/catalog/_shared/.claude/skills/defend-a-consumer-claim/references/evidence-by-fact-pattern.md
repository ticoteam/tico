# Evidence by fact pattern

Pick the pattern that matches what the customer says, not what the company thinks happened. A claim can match two;
pull both lists. For each item record: have / does not exist / could not reach.

## Pull these for every claim

| Item | Where it usually lives | What it proves |
|---|---|---|
| The terms and policies in force on the customer's date | `knowledge/terms-history.md`, the site's version history, a web archive | What the deal was |
| Proof the customer accepted them: the screen as shown, the click or signature, timestamp, IP address | Signup and checkout logs, the e-signature Tool | That it was their deal |
| The order, booking or account record | The order system, CRM, booking Tool | What was bought, when, for how much |
| The payment record, and every refund or credit | The processor, `tico finance search` | What was paid and returned |
| Every message both ways, in date order | `tico email search`, the help desk, chat and SMS Tools, call logs | What each side said, and when |
| Who the customer is: name, email, phone, address, and that they match the payer | The account, the processor | That the right person is claiming |
| Earlier dealings with this customer | The account history | Pattern: repeat purchases, earlier disputes |

## Rules of authenticity

- Export records as they are, with their dates and identifiers. A system export beats a screenshot; a screenshot
  beats a description.
- Label every screenshot with the date it was taken. To show how a page looked in the past, use version history or
  an archive copy and say which.
- Never rebuild a record from memory, edit one, or fill a gap. A missing record is reported as missing.
- A statement of what a person did or saw is written by that person, dated and signed.
- Keep the original file; work on a copy. Record where each item came from and the date pulled.
- One customer only. Mask other people's data and all but the last four digits of any card.

## 1. "I never ordered this" / "that was not me"

They must show they did not take part. The company wins by tying the order to the cardholder.

- The checks at payment: address match, security code, 3-D Secure result.
- IP address and device identifier of the order, and the same for the customer's earlier, undisputed orders.
- The account: when it was created, the email and phone on it, logins around the order.
- Delivery to the billing address or to an address the customer used before; for a service, that it was performed
  at the cardholder's own address.
- Anything the customer said that shows knowledge: a reply to the confirmation, a support request, a review.
- Earlier undisputed charges on the same card (see the chargebacks file for the card-network rule that uses them).

If missing: for a chargeback, submit what exists anyway; every chargeback is contested. In any other forum, without
a link between the order and the cardholder, resolve. Either way, check whether the order was real fraud, and if so
tell the team that owns fraud screening.

## 2. "It never arrived" / "nobody came"

They must show non-delivery. The company wins with proof of delivery or performance to the right place.

- Goods: carrier tracking with the delivery date and address, signature or delivery photo.
- Services: the booking; arrival and departure records (app check-in, location log, door or gate log); photos taken
  on the job with their timestamps; the completion message sent to the customer; the worker's signed statement.
- Digital: access or download logs with account, date and IP address.
- What the customer said afterwards: a tip, a rating, a message about the result, a rebooking.
- If late rather than missing: the promised date, the actual date, and what the terms say about timing.

If missing: tracking that shows "label created" or delivery to a different address does not prove delivery. Resolve.

## 3. "It was not what you promised" / "poor quality" / "defective"

They must show a gap between the promise and the result. The company wins by showing the promise as actually made,
that the result met it, and that it offered the remedy its policy gives.

- The listing, quote, scope of work or description as shown at the time of sale.
- Evidence of what was delivered: photos, inspection or quality-check records, the completed checklist.
- The customer's complaint in their own words, with the date. Compare the date with any deadline in the policy for
  reporting problems.
- The remedy offered (redo, replacement, return, partial refund), when, and the customer's answer. An offer the
  customer refused or ignored is among the strongest items.
- The return or redo policy as accepted, and whether the customer followed it (returned the item, allowed a revisit).
- For a defect: batch or serial records, and other complaints about the same batch (give these to the approver, not
  to the forum, unless a lawyer says otherwise).

If missing: where the description was vague or the result was not recorded, the customer's account will usually be
believed. Resolve, and file the operations task for recording completion.

## 4. "I cancelled and you kept charging" / "I never agreed to a subscription"

They must show a cancellation or a lack of consent. The company wins with clear consent to recurring billing and an
account log that shows no cancellation before the charge.

- The signup page as shown on the customer's date: the price, the interval, that it renews, how to cancel. All of it
  placed before the button, not behind a link.
- The consent record: tick or click, timestamp, IP address.
- The confirmation sent after signup, and every renewal reminder and receipt.
- The account log: cancellation requests with dates, by any channel (also check email, chat and voicemail, not only
  the cancel button).
- Use after the date they say they cancelled: logins, deliveries, bookings.
- How cancellation works: the steps, as a customer sees them.

If missing: where the customer asked to cancel by any channel before the charge, they are right even if they did not
use the button. Refund the charges after that request. Automatic-renewal laws vary by state and several are strict
(California's among them); a pattern of these claims goes to the approver and a lawyer.

## 5. "You refused my refund" / "I returned it"

They must show they qualified for a refund. The company wins with the policy as accepted and the facts that put the
customer outside it, or with proof the refund was already made.

- The refund policy shown at checkout and its acceptance.
- The return: tracking, date received, condition on arrival with photos. Or no return at all.
- The refund already issued: date, amount, processor reference, and when it should have reached the customer.
- Each message about the refund, in order, including any promise a teammate or bot made. A promise made in writing
  binds in practice, whatever the policy says.

If missing: where a teammate or bot promised a refund, pay it.

## 6. "You damaged my property" / "I was hurt"

Insurance leads. Do not admit, pay or settle before the insurer has been told (an OK is needed to notify; ask on day
one).

- The job record: who was there, when, what was done.
- Photos before and after, with timestamps. The customer's photos and messages.
- The incident report written at the time by the person present, signed.
- Contractor details: agreement, insurance certificate, and who the contract says is responsible.
- The policy documents: general liability, workers' compensation if a worker was hurt.
- For an injury: nothing beyond the above. No medical opinions, no speculation on cause. A lawyer at once.

## 7. "Your ad or your price was misleading" / "hidden fees"

Treat as a possible consumer-protection claim (part A of the UDAP file) even when it arrives as a chargeback.

- The ad, listing or price display as shown on the date, every screen of the purchase path, and the final total.
- When each fee first appeared in the path.
- The basis for any claim made in the ad (a test, a count, a source) as it existed at the time.
- How many customers went through the same path in the period (private brief only).

Fee-disclosure rules exist at the federal level for some industries and in some states for most consumer sales;
which apply depends on what is sold and where. Flag for a lawyer.

## 8. "You kept calling or texting me" / "I never agreed to marketing"

Lawyer at once. These laws carry fixed damages per call or message.

- The consent record for that number: what the customer was shown, when and how they agreed.
- Every message or call to that number, with dates and content, from the sending vendor's logs.
- Opt-out requests by any wording or channel, and the date sending stopped.
- The vendor's contract.
- Stop all marketing to that number today.

## 9. "You misused my data" / "there was a breach"

Hand to `privacy-manager` and a lawyer the same day. The bot's part is the hold and
`log-breach-facts-for-counsel`. Pull nothing beyond the universal list unless the lawyer asks.
