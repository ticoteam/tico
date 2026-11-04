---
name: write-a-decision-ask
description: Write one decision for the support owner as a yes or no question with a topic, the evidence, the proposed answer and the cost of waiting, so the owner can answer in one line and nothing is asked twice by accident.
---
# Write a decision ask

When to use: when a summary, a routing proposal or a stuck ticket needs a human to decide something the support
team cannot decide itself.

Steps:
1. Check `knowledge/decisions-needed.md` and the open tasks for the same topic. If it is already asked, add the new
   evidence to that entry. Do not open a second ask.
2. Give it a short topic of three to six words, the same wording the decisions log uses.
3. Phrase it as a yes or no question. If there are three options, propose one and ask yes or no to it.
4. State the evidence in two lines, each with its date and source.
5. State the proposed answer, and what happens on yes and on no.
6. State the cost of no answer: which target is missed, which ticket waits, and from which date.
7. Send it once, as one task question to the owner (`hub task ask <id>` on an existing task, or
   `hub task create --owner <human>` for a new one). Record it in `knowledge/decisions-needed.md` with the date.
8. Two business days later, with no answer: ask again only if the cost is a blocked ticket or a missed target, and
   name what is blocked. Otherwise leave the entry open in the log. Never re-ask a question the log already shows as
   answered.
9. When the owner answers, write the answer and its date beside the entry. Make the change the owner approved, or
   close the entry if nothing changes.

Checks before you finish:
- The question has a topic and can be answered yes or no.
- The evidence has dates and sources, and the proposed answer is written out.
- The decisions log has one line for it.
- The ask is under 120 words, and its first line is the question.

Example (invented, for illustration only): topic "Weekend cover, 10 and 11 October". Question: "Can the on-call
owner cover the weekend of 10 and 11 October?" Evidence: the rota lists nobody for those dates (read 2026-09-28);
six tickets from the last weekend waited a median of eight hours for a reply. Proposed answer: yes, and the owner
is named in `knowledge/team.md`. On yes, the rota is updated. On no, weekend tickets wait until Monday, and the
summary shows that wait against target.
