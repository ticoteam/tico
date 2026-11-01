# Answer a thread

Triggered by a waiting question in the digest, or a task linking one thread ("can someone answer this?"). Budget 10 minutes per thread.

Inputs: the thread link and its whole conversation, `knowledge/community.md` (response target, who may answer, routing), and the team's docs through `hub doc ask`.

Outcome: one sourced reply ready for review, or, when the thread is not a how-to, a short public line and a routed task.

Done when: the thread has one type and its next step, the reply or the routed task exists with the thread link, and the digest line names it.

---

## 1. Read the whole thread

Read every message, not just the first. Note what the member tried, their plan or setup if they said it, and whether another member already answered (then your reply confirms or corrects, and thanks them).

## 2. Sort it

Follow `skills/sort-community-threads/SKILL.md`. The type sets the next step:
- **A how-to question:** answer it here, in steps 3 and 4.
- **An account-specific problem** (billing, their data, a login): no public answer. Prepare a short public line ("We've opened a ticket and will follow up directly") and a task for the Support Agent with the link.
- **A bug or outage:** route to the human named for bugs. The public line acknowledges it without a date.
- **A feature request:** thank them, say it has been passed on, and promise nothing. Route it as feedback.
- **A policy question** (refund, price, terms, an exception): follow `skills/answer-a-policy-question/SKILL.md`.

## 3. Find the answer

    hub doc ask "<the member's question, in their words>"

Use the answer and its citations. If the Librarian says it is not covered, do not guess: report the gap to it (`hub task create --owner librarian`) and tell the owner the thread needs a human.

## 4. Write the reply

The answer in the first sentence, then the steps, then the doc link. Use the member's words. Under 120 words. Thank a member who helped, by their public name, for the specific answer.

## 5. Put it up for review

Add the reply to the week's batch, or, for one thread, put it to the human named in setup as a review question: `hub task question "Post this reply?" --on <that person> --parent <id> --review <reply file>`, with the thread link in the file. A person approves it. It is posted with your Tools only when the task asks for it and mail sending is on in Tico; otherwise it stays a draft. After it is posted, note it in the digest, then `hub task update <id> --status done --note "<type>; <reply or task link>"`.
