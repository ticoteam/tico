---
name: read-search-terms-for-waste
description: Read a search-terms report by cost, sort each term that spent without a result, and propose exclusions and new keywords with the spend each one would have saved, without touching brand or product terms.
---
# Read search terms for waste

When to use: each weekly review, and whenever a campaign's cost per result rises without a clear cause.

Steps:
1. Take the search-terms report for the last 7 days and the 7 days before, sorted by cost. Note the date range and
   the campaign filter. If the account mixes two products or markets, filter by campaign name before counting.
2. Keep the terms that spent more than one target cost per result with no primary conversion. Those are the
   candidates.
3. Classify each candidate: unrelated to what the team sells; a competitor's name; a question that a different
   audience asks; a term that is a neighbour of a converting term; a brand term.
4. Brand terms and product terms the team does sell are never proposed as exclusions. Check the list in
   `knowledge/targets.md` first.
5. Propose each exclusion at the narrowest level that works: an exact negative for one term, a phrase negative for
   a theme. For each one, write the spend it would have stopped over the window.
6. Find the converting terms that are not keywords yet, and propose those that reach the conversion threshold in `knowledge/targets.md`, with their conversion count.
7. Write each proposal into the review with the term, the spend, the conversions, the match type and the date.
   Record the applied ones in `knowledge/negatives.md`.

Checks before you finish:
- Each exclusion has its spend and its window, and no brand or product term is among them.
- A term that has spent less than one target cost per result is "too early", not "waste". The conversion threshold in
  `knowledge/targets.md` applies to promoting a converting term to a keyword (step 6), not to waste.
- Censored or missing rows are named, not filled in.
- The exclusion list is short enough that a human can approve it in one pass.

Example (invented, for illustration): "Search terms, 2026-10-22 to 2026-10-28, campaign 'Rental search'. 'free lease template' spent 46 with no conversions (6 clicks): unrelated, phrase negative 'lease template'. 'agency rent guarantee' spent 81 with 0 conversions over 14 clicks: a competitor-adjacent question, exact negative, not a brand term. Waste stopped per week: 127."
