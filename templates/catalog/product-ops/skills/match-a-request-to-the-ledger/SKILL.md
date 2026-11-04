---
name: match-a-request-to-the-ledger
description: Decide whether a feature request is the same need as a ledger row, a close but different need, or a new one, and record the account, source and date without merging away a real difference.
---
# Match a request to the ledger

When to use: each time a request arrives, before you add it to `knowledge/requests.md` or merge it into a row. Read it
again when a merge changes a row's account count.

Steps:
1. Write the need in one sentence: who is trying to do what, and what stops them. Keep the customer's own words in
   quotes if the source has them. A feature name is not a need.
2. Search the ledger by the need, the product area and the words the customer used, plus two synonyms. Open each
   candidate row and read its need and its notes. A title match alone is not a match.
3. Test the match. The same need has the same goal and the same blocker. Same goal with a different blocker, or a
   different user, is close but different.
4. Pick one outcome:
   - Same need: add the account and source to the row. If the account is already on it, add the date only.
   - Close but different: a new row, with a "see also" link to the other row both ways and one line on the difference.
   - New: a new row with the need, area, account, segment, source and date.
5. Check the segment before you merge. If the new account is a different kind of customer (for example one site or
   several), record the segment on the row. Do not let the merge hide it.
6. If the account is missing from the request, ask the sender once with `hub task ask <id>`. Do not guess an account.
7. Recount distinct accounts for the row. One account that asked three times is one account.

Checks before you finish:
- Every request ends in exactly one row, and every row has a need in one line.
- Each account appears once per row, with where and when it asked.
- Every merge states its reason in the row's notes.
- Each close-but-different pair links both ways.
- The row holds no promise, date or commitment about the roadmap.

Example (generic, invented):
- Request from support: "I need the class list as a spreadsheet for a substitute teacher." Account A, ticket, 2026-09-14.
- Search: "class list export", "roster download", "print roster". Row 14 is "Export the class roster to a spreadsheet",
  with 9 accounts.
- Test: the blocker is the same (the list must leave the app as a spreadsheet). Outcome: same need. Account A is added
  with the ticket and date. Count moves from 9 to 10 accounts.
- A second request asks to "print the class roster" for the front desk. The blocker is a print layout, not a file.
  Outcome: close but different. New row 22, linked to row 14 both ways, with the line "print layout, not a file export".
