---
name: match-an-equity-grant-to-approval
description: Match each grant, issuance, exercise or transfer from the Equity Manager's change log, or the cap table export, to the minute or consent that approved it, checking holder, number, class, price and date, and list any gap without editing the cap table.
---
# Match an equity grant to approval

When to use: each month, and whenever the pack is built, to find cap table changes with no approval.

Steps:
1. Take each change from the Equity Manager's log (`equity-manager`), or from the cap table export on the task if that bot is not in this team. Note its date, holder, number, class and price.
2. Search `knowledge/minute-book.md` for the minute or consent that covers it: its date, its resolution wording, and whether it is signed.
3. Check the resolution covers this change: the same holder, number, class and price, and a date on or before the grant date.
4. If the match is missing, partial or wrong, list it in `knowledge/approvals-needed.md` as "none found" or with the gap named.
5. Tell the Equity Manager, who owns the log, and put each gap on the next board pack or a consent for counsel.
6. Do not edit the cap table, and keep no copy of it.

Checks before you finish:
- Every matched approval quotes its resolution and its date.
- A different number or holder is a gap, not a match.
- Each approval is on or before the grant date, or flagged.
- Nothing in the cap table was changed.

Example (invented, for illustration): "Options, 1 September 2026, to Marco, 5,000 shares. Covering consent: dated 28 August 2026, approves 5,000 options to Marco. Match, dated before the grant."
