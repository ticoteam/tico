# Weekly deal review

Schedule: Mondays at 09:00 team time (routine `weekly-deal-review`), after setup. Also run by hand. Budget 40 minutes. The outcome is one page per week: every open deal
with a dated next step, the follow-ups ready to use, and what is due. Messages to outsiders stay drafts until a person turns mail sending on in Tico.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/sales-process.md`, every file in `knowledge/deals/`, and last week's review. Check each
action last week's review proposed: done, slipped or dropped. Say which.

## 2. Refresh each deal

For each open deal: the stage and amount as the CRM shows them (a read), the last touch (the seller's
thread, `hub meeting search "<customer>"`), the next step and its date, and the mutual action plan's next
milestone. Days quiet = today minus the last two-way contact, not the last email we sent.

## 3. Sort

1. **Needs a human now**: the buyer asked about price, terms or a discount; a competitor offer; a large
   deal past the at-risk threshold.
2. **At risk**: no dated next step, quiet past the threshold, or a plan milestone slipped twice.
3. **Moving**: one line each in a count.

## 4. Prepare the follow-ups

For each at-risk deal, prepare one follow-up that adds a new reason (an answer to their open question, a
relevant fact, a smaller ask), under 120 words, in `knowledge/voice.md`. Never "just checking in". Put the
recipient, subject and body on the task and send requested follow-ups with your Tools when a person has turned mail sending on in Tico; otherwise leave drafts in the connected seller's mailbox.

## 5. List what is due

Proposals and questionnaires due this week, with the gaps a human must fill and by when. CRM changes you
think are due (a stage, a close date) go in as proposals for the owner; you change nothing.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-deal-review.md` in the shape of `knowledge/examples/deal-review.md`, then
`hub file publish reports/YYYY-MM-DD-deal-review.md`. Commit and `hub task update <id> --status done
--note`: deals needing a human, follow-ups drafted or sent, sources not read. Always finish the task.
