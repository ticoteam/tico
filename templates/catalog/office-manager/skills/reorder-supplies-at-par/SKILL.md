---
name: reorder-supplies-at-par
description: Find every supply at or below its par level, work out the quantity to reach par plus two weeks of use, round to the pack size, group by supplier into one order each with the last seen price and its date, and place nothing unless the task asks and a granted Tool allows it.
---
# Reorder supplies at par

When to use: each weekly office page, and whenever someone asks what to order.

Steps:
1. Take the latest count from `knowledge/supplies.md` and its date. If nobody has logged a count since last Monday, ask once
   with `hub task create` for the person who stocks the cupboard, or use the last count with its date and say so.
2. For each item, compare the count with its par level. An item at or below par goes on the list. A par marked "estimate,
   check after a month" is listed, and the page says so.
3. Quantity to order = par + two weeks of use - count. Use the usage figure in `knowledge/supplies.md`. Round up to the
   supplier's pack size, and to the supplier's minimum order if that is larger.
4. Group the lines by supplier, one order per supplier. For each line, show the price last seen and its date. A missing
   price is a gap, shown as "price not known", not guessed.
5. Total each order. An order over the per-order limit in `knowledge/office.md` goes to the owner named there, not to a
   purchase that the bot places.
6. Put each order and its total on the weekly page, and on the task. Place an order only when the task asks for it and a
   granted Tool allows it. Otherwise name the missing access on the page.
7. Record each item's last ordered date in `knowledge/supplies.md` only after an order is actually placed.

Checks before you finish:
- Every item at or below par is listed, and nothing above par is.
- Each quantity shows par, use and count, and is rounded to pack size.
- Each price shows its date; a missing price is named as a gap.
- Every order total is under the limit or is routed to the owner.
- Nothing was ordered without the task asking and a granted Tool allowing it.

Example (invented, for illustration): "Oat milk: count 4 L, par 12 L, use 6 L a week, pack 6 L. Quantity: 12 + 12 - 4 = 20 L,
rounded to 24 L (four packs). Harbor Office Supply, price last seen 2026-09-21: 24 L at $1.85 a litre, $44.40. Order not
placed: the task asked for the list only."
