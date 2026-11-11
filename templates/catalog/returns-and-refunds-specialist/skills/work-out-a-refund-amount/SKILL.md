---
name: work-out-a-refund-amount
description: Work out the refund for one returned item from its pre-discount line amount, its share of any order discount and the shipping rule, and show each step so a human can check the figure.
---
# Work out a refund amount

When to use: every time a refund is allowed or part-allowed, before it is issued or recommended.

Steps:
1. Start from the item's line amount before any order-level discount, from the order record: unit price times quantity. Not the list price, and not the amount paid after the discount.
2. If the order had a discount, share it once across the items in proportion to each item's line amount before discount:
   item share = order discount x (item line amount / order subtotal before discount). Round to the cent once, at the end.
   If the order record already gives the net amount paid for this line, use that figure, skip steps 2 and 3, and say so.
3. Net paid for the item = line amount minus its discount share.
4. Apply the shipping rule. Refund the original standard delivery cost on a statutory whole-order withdrawal (`knowledge/returns-practice.md`), and otherwise only if the policy says so. Deduct any return shipping the policy charges to the customer, and only where no statutory right applies.
5. Apply a restocking fee only if the written policy names one, as a percentage, for this case, and never on a statutory withdrawal. On a statutory withdrawal, the only deduction allowed is for handling beyond what was needed to check the goods.
6. Refund = net paid, plus any original delivery cost refunded under step 4, minus any fee or return shipping allowed by steps 4 and 5. Never refund more for the goods than was paid for them. The delivery refund is capped separately, at the standard delivery charge paid.
7. Write the steps on the ledger line, so the figure can be checked.

Checks before you finish:
- The figure starts from the pre-discount line amount and matches the order record. The discount is applied once.
- The discount share is proportional to the item's line amount and rounded once.
- Shipping and fees follow the policy's words or the statutory rule, and the words or rule are cited.
- No flat restocking fee on a statutory withdrawal.
- The goods refund never exceeds the amount paid for them, and the delivery refund never exceeds the delivery charge paid.

Statutory example (invented, whole-order withdrawal, EU order): a €30.00 item bought with €5.00 standard delivery, no discount. Net paid for the item: €30.00. Original standard delivery refunded: €5.00. No restocking fee. Refund: €35.00.

Worked example (invented figures, a policy return, not a statutory one): an order of $60.00 with a $5.00 discount, of which one item with a line amount of $29.00 is returned. Discount share: $5.00 x (29.00 / 60.00) = $2.42. Net paid: $29.00 - $2.42 = $26.58. Return shipping the policy charges to the customer: $5.95. Refund: $26.58 - $5.95 = $20.63. Ledger line: "A-1042 item 2: line amount $29.00; order discount $5.00 on $60.00, share $2.42; net $26.58; return shipping under policy $5.95; refund $20.63."
