---
name: tag-and-count-friction
description: Tag each developer question with the integration step it hit, count distinct threads per step, and decide which steps are real friction themes with a named fix.
---
# Tag and count friction

When to use: each pulse, after the questions are swept, and before you add an entry to `knowledge/friction-log.md` or call
a theme real.

Steps:
1. Tag from a fixed list so counts line up across weeks: keys or sign-up, install, auth, first call, pagination, webhooks,
   rate limits, errors, sandbox, versioning, docs navigation, other. Add a new step only when "other" holds three or more
   threads you can name.
2. Tag each question with exactly one step: the one where the developer stopped. If a failed signature check caused a
   timeout later, the step is the signature check.
3. Count distinct threads. Three posts in one thread are one thread. Two developers hitting the same error in one thread
   are still one thread.
4. Apply the rule. Two or more distinct threads in the window is a theme. Five or more in a month is an escalation to the
   requester (AGENT.md, Escalating).
5. For each theme, write the fix that would remove it and its owner: a doc change for the Technical Writer, a product change
   for Product, a clearer error for engineering, a help-centre sentence for the Librarian. A theme with no fix yet stays
   marked "fix not named".
6. Record it in `knowledge/friction-log.md`: the step, this period's count, the last period's count, the dated links, the
   fix and its owner.
7. When a fix ships, watch the next four weeks. A theme closes when its count stops. If the count does not drop, the fix
   did not work, and the entry says so.

Checks before you finish:
- Every counted thread has a link and a date.
- No thread is counted twice in one window.
- Each theme has at least two distinct threads. Anything below that is listed as "watch", not as a theme.
- Each theme names a fix and an owner, or says why none is named yet.
- The four-week follow-up for each closed fix is dated in the log.

Example (invented, for illustration): Between 2026-09-24 and 2026-10-01, five posts hit the webhook signature step. Two of
them are in one thread, so the count is four distinct threads: a theme. The fix goes to the Technical Writer, a raw-body
note in the quickstart. The log reads: "previous 3, this period 4, fix proposed 2026-10-01, check 2026-10-29."
