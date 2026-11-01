---
name: judge-a-review-flag
description: Decide whether a review or complaint is worth a platform flag, flag only on a named platform rule with the words that break it, otherwise reply, and log the outcome so the next batch uses only grounds the platform honours.
---
# Judge a review flag

When to use: when a review or complaint might break a platform rule, and the team is considering a flag, a
takedown request or a report.

Steps:
1. Ask whether the review is negative. If it is only negative, it is not a flag. Reply to it.
2. Find the platform rule it may break. Name the rule as the platform words it, with the page and its date.
3. Quote the exact words in the review that break the rule. If you cannot quote them, there is no flag.
4. Check the grounds list for that surface in `knowledge/surfaces.md`: is this ground one the platform has honoured
   before? If it has been rejected before, use a reply instead.
5. Check for a conflict of interest: a competitor's review, a review by someone with no account, or a review from a
   person the team has a dispute with. Name it in the log and send it to the owner.
6. If it breaks a rule, draft the flag with the rule, the quote and the page. If it does not, write the reply.
7. Log the item in `knowledge/ledger.csv` with the lever, the grounds and the date. When the platform decides, log
   the outcome, upheld or rejected, with the date. A rejected ground goes into `knowledge/surfaces.md`.

Checks before you finish:
- Every flag names a rule, a quote and a page date.
- A flag is never raised for a negative review alone.
- The log has an outcome for every flag submitted before this batch.
- If in doubt, the item is a reply.

Also: a review that reads as a first-hand account and asks readers to check the others is a reply, not a proxy flag.
A review written by staff or a vendor, disclosed or not, is checked against the platform's conflict-of-interest rule.

Example (invented, for illustration): "Review on a directory: 'the owner threatened me on the phone' is a claim about a
person's conduct, not a platform rule. Lever: reply and send to the owner with the review id. Not flagged. Another review
with a named private phone number in it: breaks the platform's personal-data rule (page checked 2026-10-30). Lever: flag,
quote the number's line, log the grounds."
