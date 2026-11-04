---
name: check-an-offer-against-its-band
description: Place a proposed offer or pay change in its band (below minimum, in band or above maximum), compute its compa-ratio, and report the position by reference with its band's source and date, without a recommendation on the figure.
---
# Check an offer against its band

When to use: for every offer or pay change proposed on a task, before a person applies it.

Steps:
1. Take the role family, level and location rule from the task. If any of the three is missing, the result is "cannot place"
   with the missing field named. Never guess the level.
2. Find the band in `knowledge/bands.md`. Note its minimum, midpoint, maximum, source and date. If the band is provisional or
   older than twelve months, say so in the result.
3. Place the proposed figure: below minimum, in band, or above maximum. For above maximum, give the amount over the maximum
   as a percentage of the maximum.
4. Compute the compa-ratio: proposed divided by midpoint, to two decimals.
5. Note where the nearest peers in the same role and level sit: their compa-ratios, by reference only. No names.
6. Write the result by reference (for example "offer AE-03"). Person-level figures go to the attachment for the owners named in
   `state.md`, and the local copy is deleted.
7. If the figure is above maximum, ask the approver one question on the task: what exception was approved, and by whom. If it
   is below minimum, flag it and do not suggest a number.

Checks before you finish:
- The band's source and date are named, and provisional bands are marked.
- The compa-ratio is computed from the midpoint and shown.
- No person's name or pay sits in the counts or in the report.
- The result states a position, not a recommendation on the figure.
- Anything you could not place says which field is missing.

Example (invented, for illustration): "Offer BE-07, backend engineer, level 3, location rule A. Band 110,000 to 150,000,
midpoint 130,000 (market survey, dated 2026-02). Proposed 158,000: above maximum by 5.3 percent. Compa-ratio 1.22. Nearest
two peers at 0.97 and 1.02. Question to the approver: what exception was approved for this offer?"
