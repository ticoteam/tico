---
name: read-search-console-without-misreading
description: Turn a Search Console export into search-movement numbers a reader can trust, without summing query rows, adding AI-feature reports to totals, or reading hidden queries as zero.
---
# Read search performance without misreading it

When to use: any time a report, note or draft states a click, impression, CTR or average position for the team's site,
from Search Console or from an export a person attached.

Steps:
1. Name the export before you read it: property, date range, search type, country, the date it was pulled, and whether
   it is a file a person attached or a live read.
2. Read the chart totals for the Web search type, last 28 days against the 28 days before. Record clicks, impressions,
   CTR and average position for both. These are the headline numbers.
3. Read the query table and the page table separately. Do not sum query rows to match the chart. Hidden (anonymized)
   queries stay in the chart total and drop out of the tables, so a missing query is unknown, not zero.
4. For a page you name, filter to that exact URL. Confirm which version of the URL is the one you mean (with or without
   a trailing slash, with or without www) before you read its numbers.
5. Record branded and non-branded queries as separate lines, and say that the two do not add back to the total.
6. Keep the generative-AI performance report (launched June 2026) in its own series. Its impressions already sit inside
   the Web totals, so never add the two. Quote its definitions from Search Console's own help text, not from memory.
7. Read impressions and clicks together. Rising impressions with flat clicks is more exposure, not more visits. Falling
   clicks with flat impressions is a real loss. Report the two as separate lines.
8. Write each number with its window, property, search type and export date on the same line.

Checks before you finish:
- Every number has its window, property, search type and export date.
- No sum of query rows is presented as a total.
- Hidden queries are named as unknown, not zero.
- Branded and non-branded lines are not added to the chart total.
- The generative-AI report is labelled as its own series.
- No click or impression number appears when no export was read in this run.

Example (invented numbers, for illustration only): "/pricing, Web, 28 days to 2026-10-25 against the 28 days before:
clicks 410 (from 380), impressions 9,800 (from 9,100), average position 11.2 (from 12.0). Export pulled 2026-10-26. The
query table hides low-volume queries, so it does not sum to the total."
