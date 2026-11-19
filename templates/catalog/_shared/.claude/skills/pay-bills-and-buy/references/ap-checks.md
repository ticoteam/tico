# Bill checks: duplicates, matching, new vendors, timing

## 1. Is it a bill at all?

| Document | Is it payable? | Do |
|---|---|---|
| Invoice or bill | Yes | Enter it |
| Statement of account | No | Use it to check for bills you never received |
| Reminder or past-due notice | No | Find the bill it refers to; check it was not already paid |
| Quote, estimate, pro forma | No | Keep with the purchase; pay only the real bill |
| Credit note | No, it reduces what is owed | Record as a credit against the vendor |
| Order confirmation, receipt for a card charge | No, already paid | File as the receipt for the card row |
| "Renewal notice" from a company you do not buy from | No | A common scam. Check the vendor list before anything else |

## 2. The duplicate check

Run it on every bill, before coding or matching. A duplicate that reaches a payment run usually gets paid.

**Normalize the bill number** before comparing: upper case; remove spaces, dashes, dots, slashes and a leading "INV"
or "#"; remove leading zeros. "INV-0412", "inv 412" and "#000412" are the same number.

**Search three ways** (`tico finance search --kind bill --q "<vendor or number>"`):

1. Same vendor, same normalized number, last 12 months.
2. Same vendor, same amount, bill dates within 60 days. Also the same amount net of tax or with tax added.
3. Same amount and date under a similar vendor name: a trading name, a parent, a misspelling.

Search paid, scheduled, draft and void bills. Also search card and bank rows for the amount: a bill is sometimes
already paid by card.

**Call it one of three**

| Result | Test | Do |
|---|---|---|
| Duplicate | Same vendor, number and amount, or the vendor confirms | Void with the reason and the id of the bill it repeats |
| Probable resend | Same vendor and amount, number differs slightly or is missing; or a reminder attached | Hold. Ask the vendor: "Is 0412 the same as 412, paid on 21 October?" |
| Not a duplicate | A regular charge (rent, a subscription) with a new period and number; or a different order | Go on. Note what you compared |

A recurring bill is the main false alarm. Same vendor and amount every month is expected: check that the service
period is new and the number moved on.

**Found after payment**: tell the vendor the same day, ask for a refund or a credit on the next bill, record which
they chose, and note the cause (two inboxes, a resent PDF, a number typed by hand) so the rule or routine is fixed.

## 3. Coding

- Use the vendor's last coding unless the bill is for something different.
- Code by purpose. The same vendor can be cost of revenue for one bill (hosting for customers) and operating expense
  for another (an internal tool). `build-the-pnl` holds the company's rules for the split.
- A bill covering more than one month (annual software, insurance, a retainer in advance) is a prepaid: the cost
  lands in the months it covers. The close runs the schedule.
- Equipment that will be used for years may be an asset, above the company's capitalization limit; ask the
  bookkeeper or controller for the limit once.
- Sales tax, shipping and tips stay on the bill's lines as charged. Check that tax was charged where expected and not
  where the company is exempt.
- Tag the budget line and the team. An untagged bill cannot be checked against a budget.

## 4. Matching

Pick the match type by how the thing was bought.

| Bought by | Match | Bill agrees with |
|---|---|---|
| PO for goods | Three-way | The PO (item, unit price, quantity) and the receipt (quantity arrived, condition) |
| PO for a service | Three-way | The PO and a sign-off that the work was done (a task marked done, a milestone accepted) |
| Contract or subscription | Two-way | The contract's price, seats and period; and last period's bill |
| Usage (hosting, AI, telecom) | Two-way with a sense check | The vendor's usage report or the company's own usage data; flag a rise over the company's spike limit |
| Small one-off, no PO required | Confirmation | One line from the orderer: "ordered and received" |
| Utilities, tax, rent | None beyond the amount | The lease or last period; flag a change |

**Tolerance.** Default: the bill may differ from the order by up to 2% of the bill or 100.00, whichever is smaller.
Inside it, pay and note the difference. Outside it, resolve first. Freight and tax that the PO said would be "at
cost" are not a difference. Set the company's own tolerance in `knowledge/purchasing.md`.

**Outcomes**

| Outcome | Meaning | Next |
|---|---|---|
| Clean | Everything agrees | Approve |
| Price difference | Unit price or total differs beyond tolerance | Ask the vendor for a corrected bill or a credit note; hold |
| Quantity difference | Billed for more than arrived | Pay for what arrived if the vendor agrees in writing, or hold |
| No PO | A PO is required for this vendor or band and there is none | The orderer raises one; it follows the approval bands; note the after-the-fact order |
| No receipt | Nobody has confirmed it arrived | Ask the orderer; hold |
| Partly received | More deliveries to come | Pay per the PO's terms; keep the PO open |

A missing PO is not an error where the company does not require one. Do not invent a requirement the company never
set, and do not waive one it did.

Repeated after-the-fact POs from the same teammate or bot are a finding for the weekly page, not a reason to block
the vendor's payment for delivered goods.

## 5. A new vendor

Before the first payment:

1. **Is it really new?** Search by name, domain, tax number and bank last-four. Merge spellings with `vendor_merge`.
2. **A tax form.** US vendors: a W-9. Foreign vendors: the right W-8, and ask the tax preparer whether withholding
   applies. For payments made from 2026-01-01 the 1099-NEC reporting threshold is 2,000.00 in a calendar year, indexed
   for inflation from 2027; some states keep their own lower thresholds. Collect the form from every service vendor
   anyway: thresholds are crossed mid-year. US default; check locally. (Read 2026-11-18.)
3. **A real business.** A website on its own domain, an address, a named contact. For band 3 and above, a company
   registry or reference check.
4. **Payment details from a trusted route**: the signed contract, the vendor's portal reached from its known site, or
   a callback. Details that arrived only by email are verified as in `bank-detail-change.md`.
5. **A contact and phone number written to `knowledge/vendor-details.md`** with where they came from. This is the
   number later callbacks rely on, so it must not come from a bill alone.
6. **No conflict.** A vendor owned by, or related to, a teammate is disclosed to the owner before the first order.

Tico asks a finance admin to approve a new payee. Bring this checklist to that ask.

## 6. When to pay

- **Default: on the due date.** Not early (the cash is worth more in the bank) and not late (late fees, a worse
  relationship, a hold on service).
- **Early-payment discounts.** Work out the yearly rate: discount / (1 - discount) x 365 / (full term days - discount
  days). "2% if paid in 10 days, net 30" is 0.02 / 0.98 x 365 / 20 = about 37% a year. Take it when the cash check
  passes; a rate like that beats almost any other use of spare cash.
- **Method.** Bank transfer (ACH) by default. A wire for speed or abroad, with extra care: it is hard to recall. A
  check only when the vendor insists. A card where it earns terms or protection and the vendor adds no fee.
- **Stretching terms** is a negotiation with the vendor, asked for before the due date. Paying late without asking is
  not a cash strategy.
- **Fraud monitoring.** US banks' ACH rules (Nacha) require every business that originates ACH payments to have
  risk-based processes to spot payments made under false pretences; the second phase applied to all originators from
  June 2026. The duplicate check, the new-vendor check and the bank-detail check are those processes: keep the record
  of each. (Read 2026-11-18; ask the bank what it expects.)

## 7. After the run

- Each payment shows as left the bank, and the bill as paid.
- A returned payment: read the return reason. "Account closed" or "no account" means the details are wrong: verify
  as in `bank-detail-change.md` before resending.
- Remittance advice goes to the vendor's payables address where they ask for it: bill numbers and amounts.
- Vendor statements are compared with the book monthly for the ten largest vendors: a bill on their statement that
  is not in the book is a bill that never arrived.
- Debit balances (the company overpaid) are listed and recovered.
