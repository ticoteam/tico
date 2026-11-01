---
name: check-a-handbook-page-is-current
description: Before a handbook page is quoted in a policy answer or a checklist, check its date, whether a human called it out of date, and whether another page disagrees, and report current, stale, conflicting or unknown, never quoting a page that fails the check.
---
# Check a handbook page is current

When to use: before you quote any handbook or policy page in an answer, a checklist item or a draft, and before you rely on a page the
Librarian cites.

Steps:
1. Open the page the Librarian cited (`hub doc read <path>`), read it whole, and note its title, its date and its owner, if the page
   names one.
2. Check `knowledge/stale-pages.md`. If the page is listed there, the result is "stale". Do not quote it.
3. Check the page's date against any policy change named in the task or the roster. If the page predates a change a human has
   mentioned, the result is "stale".
4. Search for another page on the same topic (`hub doc search "<topic>"`). If it says something different, the result is
   "conflicting". Keep both, with their dates.
5. If the page has no date, or cannot be read in full, the result is "unknown". Say what was checked.
6. If none of the above applies, the result is "current". Quote the sentence you need, with the title and date.
7. Where a result is not "current", route the question as the policy playbook says and send one task to the Librarian naming the
   page and the problem.

Checks before you finish:
- Each quote carries a title and a date.
- A stale or conflicting page is never quoted as the answer.
- An "unknown" result says what could not be read.
- The result is written in the answer's first line, in one word.

Example (invented, for illustration): "Parental leave page, dated 2024-03-01, read in full. Not in stale-pages.md. A second page
on the same topic, dated 2025-06-10, gives a different notice period. Result: conflicting. Both shown with dates; one task to
the Librarian for the policy owner."
