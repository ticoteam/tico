---
name: word-a-fix-status
description: Choose the right status words for a bug fix (reported, in review, shipped in a version, verified fixed, reopened), so that a customer or teammate is told only what the record shows, with its date and source.
---
# Word a fix status

When to use: before you tell a customer, the Support Agent or the team that a bug is being worked on, shipped, or fixed.

Steps:
1. Find the state in the record: the issue's state, its last movement, and the release notes for the version in
   question. Write the date and the source beside the state.
2. Use one of these statuses, and no other:
   - **Reported**: the bug is written up and filed. Nothing is known about a fix.
   - **In review**: a fix exists in a change under review. Name the change, not its outcome.
   - **Shipped in <version>**: the release notes for that version name the fix. Say the version.
   - **Fixed, verified**: the reported case was seen working, on a stated version, by you or a person who tested it.
   - **Reopened**: a verified fix stopped working, or a case that was shipped still fails.
3. Name what is still unverified in the same sentence: "shipped in 4.2; not yet confirmed on the customer's plan."
4. Never move a status up without the evidence named in step 2. A merged change is "in review" until a release
   names it, and "shipped" is not "fixed".
5. Set the reopen trigger when the status is shipped or fixed: the symptom that would show the fix did not hold, and
   who should hear about it.
6. In a customer reply, use the plain form: "A fix shipped in version 4.2. We are confirming it works on your plan and
   will tell you if anything is still wrong." Do not promise a date unless the owner of the fix gave one.
7. Check the thread before you post an update. If you already sent a newer status to this customer, correct the
   earlier one in the same thread rather than sending a second, contradicting message.

Checks before you finish:
- The status word is one of the five, with its date and source.
- Anything unverified is named in the same sentence.
- No date appears that the fix's owner did not state.
- The customer reply matches the record, not the hope.

Example (invented, for illustration only): an issue was merged on 2026-10-20 and listed in the release notes of version
4.2 on 2026-10-28. The status is "shipped in 4.2, 2026-10-28, from the release notes". The reply says the fix shipped in
4.2 and that the team is confirming it on the customer's plan. The status is not "fixed" until a case on the plan has
been seen working.
