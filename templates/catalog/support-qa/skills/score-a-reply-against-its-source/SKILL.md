---
name: score-a-reply-against-its-source
description: Score one sent support reply on accuracy, tone, completeness, policy and next step against the written source it should match, check the critical items first, and record the reason and a quoted sentence for each score.
---
# Score a reply against its source

When to use: when you score a sent reply for the weekly review, or a single reply a human asks you to check.

Steps:
1. Read the whole thread: the customer's message, every earlier reply and the reply under review. Before you read
   the reply, write what a complete answer to the question would contain.
2. Check the critical items first: a policy the reply got wrong, a promise the policy does not allow, another
   customer's details, or advice that could cause harm or loss. Any one of them fails the reply, whatever the scores
   below. Record it on its own line and send the ticket to the owner the same day (see AGENT.md, Escalating).
3. Find the written source for each factual claim: `hub doc ask` or the linked docs, with the date of the source. A
   claim with no source is "unverified", never "wrong" and never "right".
4. Score each criterion 1, 2 or 3 as `scorecard.md` defines it: accuracy, tone, completeness, policy, next step.
   Score only the listed criteria.
5. For each score, write one reason and one quoted sentence from the reply, with no personal details.
6. Record the entry in `knowledge/reviews/YYYY-MM-DD.md` with the ticket reference and the date the reply was sent.

Checks before you finish:
- The critical check is written, even when it found nothing.
- Each factual claim has its source and date, or is marked unverified.
- Every score has a reason and a quote.
- No customer's name, address or account detail is in the entry.

Example (invented, for illustration only): a reply to a refund question says the refund will arrive in five days. The
refund policy doc, read 2026-09-28, says "up to ten business days". The promise is more specific than the policy
allows, so the policy score is 1 and the critical check fails the reply, with the quote "the refund will arrive in five
days". The reply's tone scores 3, with the quote "Thanks for your patience while we checked".
