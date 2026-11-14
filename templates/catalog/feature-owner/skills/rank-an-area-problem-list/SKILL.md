---
name: rank-an-area-problem-list
description: Rank one area's problems by the distinct accounts they touch and what they cost activation, from tickets, feedback, calls and usage, keeping the source and window for every rank and listing what was left out.
---
# Rank an area's problem list

When to use: each weekly area review, and whenever a new source (a batch of tickets, a feedback theme, a set of calls) changes what the area's problems look like.

Steps:
1. Collect the evidence since the last review, by source, and note each source's date range: tasks with the area label (`tico task list --updated-since <ISO time> --label area:<slug>`), call notes (`tico meeting search` then `tico meeting read`), the feedback theme in the Customer Insights Analyst's latest weekly update, and the usage export attached to the task.
2. Map each item to one problem, named in the customer's words ("I can't connect my calendar", not "calendar integration bug"). Two items about the same failure are one problem.
3. Count distinct accounts for each problem. An account counts once per problem, however many tickets, calls or messages it has. Record how many sources each problem has.
4. Apply the rule in `knowledge/product-practice.md`: a problem is ranked only with at least 3 distinct accounts from at least 2 sources in the last 30 days. Anything below that is "watching".
5. Separate failures from requests. A request (asking for something the product does not have) goes under "requests" with its count. It never ranks above a failure.
6. If the usage export is attached, compute each problem's activation gap: activation among accounts that hit it, minus activation among accounts that did not, with both counts. Rank by gap times accounts hit. If there is no export, rank by accounts hit and mark the ranking "evidence only".
7. Write the list. For each problem: rank, the problem, accounts and sources, the gap or "evidence only", its status (pitch filed with its task id, watching, or parked with a date and a reason), and its previous rank.
8. Write "Left out": each item read that is not this area's product problem, with its reason and where it went (another area, marketing, support, billing).

Checks before you finish:
- Every number has its source and its window.
- No account is counted twice under one problem.
- The items on the list plus the items left out equal the items you read.
- Nothing is ranked above a failure because it was asked for most loudly.

Example (invented, for illustration): "Creators, 30 days to 2026-11-01. 'I can't connect my calendar when it is a shared calendar': 14 accounts, 3 sources (2 calls, 9 tickets, 1 feedback theme). Activation 21% for the 14 (3 of 14), against 44% for the 103 others (45 of 103), usage export 2026-11-02. Rank 1 by gap. Requests: 'add a Notion export', 6 accounts, 2 sources, listed under requests and not ranked."
