---
name: "review-the-books-for-unusual-entries"
description: "Test a period's journal entries for the patterns that most often hide errors or misuse: duplicates, round amounts, after-close postings, entries from outside finance, and balances left in suspense. Use before saying a period is ready to call closed."
---

# Review the books for unusual entries

## When to use
Before you say a period can be called closed, and when a person asks whether a set of entries is clean.

## Steps
1. Get the full population: every journal entry for the period from the system export, including automated and reversed entries. Check it is complete: the entry count and total match the system's own report for the period. If they do not, stop and list the gap. Test nothing on an incomplete population.
2. Run these tests one at a time. Keep the list of entries each test flags.
   a. Duplicates: same amount, same counterparty or account, within 60 days.
   b. Round amounts: amounts that are multiples of 1,000 and above the review limit.
   c. Timing: entries dated on a weekend or public holiday, and entries posted after the close date but dated in the period.
   d. Source: manual entries made by someone outside the finance group, or entries made and approved by the same person.
   e. Reversals: entries reversed in the same period, and reversals with no original.
   f. Suspense and clearing accounts with a balance at period end.
   g. Uncategorized or default-account items still open at close.
   h. Unusual pairs: a debit and a credit to accounts that rarely move together.
3. Use the review limit in `knowledge/close-checklist.md`. If none is set, review the 10 largest entries and say the limit is missing.
4. For each flagged entry, record the date, amount, account, poster, and the document that supports it. Supported means a document is named, not remembered.
5. Sort the list: unsupported entries first, then duplicates, then the rest. An unsupported entry above the limit is an escalation.
6. Write the result: entries tested, flags per test, the unsupported entries with owner, and the verdict for the period: clean, or not clean with the blockers.

## Checks before you finish
- The population check ran before any test, and its result is written down.
- Each flagged entry has a document, or says "no document found".
- No entry is cleared because it looks normal. "Looks fine" is not support.
- Flags per test add up to the flagged entries, allowing for entries that hit more than one test.
- You changed no entry. Proposed corrections go to the accountant as lines.

## Example (invented)
412 entries tested. The export matched the system's report (412 entries, same total).

- Round amounts: 9 flagged above the limit. 8 supported by invoices or contracts. 1 unsupported: 25,000.00 posted to consulting, no document. Escalated.
- Duplicates: 2 flagged within 60 days. Both supported: one is a rebill, one is a correction with the original reversed.
- Timing: 1 entry dated 2026-10-30, posted 2026-11-02 by a user outside finance, no document. Escalated.

Verdict: not clean. The 25,000.00 entry and the late posting are blockers.
