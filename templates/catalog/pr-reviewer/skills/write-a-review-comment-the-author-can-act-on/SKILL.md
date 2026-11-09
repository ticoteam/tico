---
name: "write-a-review-comment-the-author-can-act-on"
description: "Write one review comment that an author can act on without a call: a label that says whether it blocks, the exact location and quoted code, what happens and why it matters, and what would resolve it."
---
# Write a review comment the author can act on

When to use: for every comment in a draft review. Use it to check each comment before it goes on the task, and to rewrite
any comment that says "this is wrong" with no reason.

Steps:
1. Choose the label first: `issue (blocking):` when the change must not merge as it is, `issue:` for a real problem that
   can wait, `question:` when you cannot tell from the diff, `suggestion:` for a better way, `nit:` for a preference,
   `praise:` for something done well. Only an `issue (blocking):` stops a merge, and only when the change ships a bug,
   breaks a rule the repository states, or costs money or data to undo. Anything else is at most an `issue:`.
2. Name the location: file and line, and quote the one or two lines the comment is about.
3. State what happens, in the code's terms: "this returns the old total when the list is empty" is a finding; "this looks
   off" is not. Name the input or the condition that triggers it.
4. Say why it matters in one clause: the user who is affected, the data that is wrong, the check that is skipped.
5. End with what would resolve it: a change, a test to add, or a question for the author. If there are two ways, give the
   one you would choose and say the other is acceptable.
6. Keep the tone about the code. No "you forgot", no "obviously". Thank the author only when the thanks is specific.
7. Count the blocking comments. More than three blocking comments on one pull request usually means the design needs a
   conversation first; say that once, at the top.

Checks before you finish:
- Every blocking comment names what happens, not only what looks wrong.
- Every comment has a location and a quote.
- No comment asks for a change you would not make yourself without saying why.
- No nit is labelled blocking.

Example (invented, for illustration): "issue (blocking): `billing/refund.py:88` rounds each line before summing. With three
lines of 3.333 the refund is 9.99, not 10.00, so the customer is short by a cent. Add a test for that case, or round the total
once."
