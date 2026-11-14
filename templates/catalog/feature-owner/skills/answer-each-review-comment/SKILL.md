---
name: answer-each-review-comment
description: Decide each review finding on a task, act on it or answer it on the pull request with one line of reason, and check that no blocking comment was left unanswered before the task moves.
---
# Answer each review comment

When to use: whenever a pull request of yours has review comments, or a Review page is attached to its task, before you push.

Steps:
1. List every comment on the current head, with its label if it has one. A comment labelled `issue (blocking):` must be acted
   on, or declined in writing with a reason the owner accepts.
2. For each comment, decide: act on it, or decline it with one line of reason. The reason names the fact that makes it
   wrong or out of scope: "This path is not reachable: the caller checks the list is non-empty on line 40." "Out of scope
   for this task; noted on the task as a follow-up."
3. Act on it in a commit whose message names the comment: "Round the refund total once, per review on refund.py:88."
4. Reply to each comment on the pull request with the action or the reason. One reply per comment.
5. Do not argue a blocking bug away on taste. A declined blocking comment goes to the task's owner as a question
   (`tico task ask`) before you finish the task.
6. Count what is still open on the head. Finish the task only when no blocking comment is open.

Checks before you finish:
- Every comment on the head has a reply.
- Every declined comment has its reason in one line.
- No comment was resolved without a reply.
