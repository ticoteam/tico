---
name: review-a-pull-request-against-its-head
description: Review and decide on a pull request at one exact head commit. Check the diff, the checks and the review state at that SHA, treat an approval at an earlier head as void, name any skipped or timed-out gate, and merge only when the head at merge is the head that was reviewed and checked.
---
# Review a pull request against its head

When to use: before you approve, request changes on, or merge any pull request.

Steps:
1. Record the head commit you are reviewing: `gh pr view <number> -R <repo> --json headRefOid,title,author`. Write the
   SHA in your notes. Every later check refers to this SHA.
2. Read the diff at that head with `gh pr diff <number> -R <repo>`, and the task or request it answers.
3. Read the checks at that same head with `gh pr checks <number> -R <repo>`. Required checks must be green at this
   SHA. A skipped check is not a pass. A gate that timed out is reported as skipped and named.
4. Read the review state. An approval given at an earlier head does not count for the current head. A push after a
   review voids that review's verdict, so ask for a fresh review or review again yourself.
5. Decide. Approve only what you have read at this head. Otherwise request changes with
   `gh pr review <number> -R <repo> --request-changes` and name the exact missing item. Do not write "looks fine" for
   code you did not read.
6. Merge only if all of these hold, and only with a granted Tool that allows it: the head at merge equals the head you
   reviewed; the required checks are green at that head; no review thread is open; the pull request is not your own
   without another reviewer. If any fails, leave it open and say which one.
7. Record the SHA and the decision in the weekly summary line for that pull request.

Checks before you finish:
- The head SHA is written down, and the diff, the checks and the approval all refer to it.
- No skipped or timed-out check is described as passed.
- The merge, if any, was made at the reviewed SHA.
- Any request for changes names the exact missing item.

Example (invented, for illustration): Pull request 412 is approved at head `a1b2c3d`. A new push lands at `e4f5a6b`, so the approval is void. At `e4f5a6b` three checks are green and one, the migration test, was skipped after a timeout. Result: not mergeable. The review asks for a fresh review at `e4f5a6b` and names the skipped migration test as the missing item.
