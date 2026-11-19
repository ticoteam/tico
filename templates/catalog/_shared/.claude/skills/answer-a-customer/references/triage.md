# Sorting a message

Two labels per message: a **category** (why it happened) and a **priority** (how fast). Both go on the ticket or the
thread label, and both feed the weekly page.

## Category: name the cause, not the symptom

"Can't log in" is a symptom. The cause might be a bug, an expired trial, a wrong email or a locked account. Pick the
category after you have looked at the customer's actual state, and change it if step 6 shows a different cause.

| Category | It is this when | Usually goes to |
|---|---|---|
| How-to | The product works as designed and they need to know how | You answer |
| Bug | The product does not do what the docs say | `write-an-engineering-bug-report`, then engineering |
| Outage or incident | Many customers, one cause | `set-severity-and-owner`; recover-a-bad-experience |
| Billing | A charge, invoice, payment method or tax question | You, with the billing Tool; disputes to the Finance Owner |
| Order or delivery | Where it is, wrong or damaged item, change an order | You, with the order Tool |
| Return or refund | Money back or goods back | `check-a-return-against-the-order`, `work-out-a-refund-amount` |
| Account and access | Login, ownership, permissions, data export or deletion | You, after the identity check |
| Cancellation | They want to stop | You; `code-a-cancellation-reason`, `match-one-save-offer` |
| Product request | They want something that does not exist | You answer honestly; `tag-one-feedback-item` |
| Complaint | They are unhappy with how they were treated | recover-a-bad-experience |
| Security | A vulnerability, a breach worry, a takeover | Engineering, now |
| Legal | A threat, a regulator, a lawyer | answer-a-demand-or-complaint |
| Dispute | A chargeback notice, a small-claims notice, court papers | defend-a-consumer-claim |
| Not support | Sales lead, partner, press, job seeker, spam | Route to the right skill or owner; reply with where it went |

One message can hold two categories. Label the one that decides the priority and answer both.

## Priority

Default targets. The team's own promise in `knowledge/support.md` replaces these. "First reply" means a real answer
or an honest holding reply, never an auto-acknowledgement.

| Priority | It is this when | First reply | Updates until closed |
|---|---|---|---|
| P1 | The customer cannot use what they paid for; money is wrong and growing; security or safety; many customers at once | 15 minutes | Every hour, or as promised |
| P2 | A main function is broken or a deadline of theirs is at risk, and there is a workaround or it affects one customer | 1 hour | Every 4 hours in their working day |
| P3 | Something is wrong or unclear but they can carry on | 1 hour | Daily |
| P4 | A question, a request, feedback | 1 hour | When there is news |

Why the first-reply target is the same for most rows: a bot does not have a queue the way a person does, so there is
no reason to make a P4 wait. The priority mostly decides who else is pulled in and how often you update.

Rules:
- **Unsure between two? Take the higher.** Downgrading later costs nothing; a missed P1 costs a customer.
- **Their deadline raises the priority.** "I need this for a 3pm meeting" moves a P3 to P2.
- **The second contact about the same thing raises it one level** and sends it to recover-a-bad-experience.
- **Tone does not set priority.** A polite customer who is locked out is P1. An angry one with a how-to question is P4
  with extra care.
- **Count before you call it an incident.** Use `check-a-repeat-before-you-escalate`: same cause, different
  customers, inside a short window. Then `set-severity-and-owner` names one owner.

## Duplicates and threads

- Same customer, same issue, new thread: merge or link them, answer once, in the thread they last wrote in.
- Same issue, many customers: one entry in `knowledge/known-issues.md` with approved wording, one task for the cause,
  and each customer still gets their own reply.
- An auto-reply, bounce or out-of-office: no reply. Label and archive.
- A message to a mailbox you can read but not send from: file a task for the mailbox's owner with sender, subject,
  text, message id and thread link. Nothing else from the mailbox goes in the task.

## Handing off

When another teammate must act, the handoff carries everything they need to never ask the customer again:

1. Who the customer is and their plan or order.
2. What they asked, in their words, and what they are trying to get done.
3. What you checked and what you found, with sources.
4. What you already told the customer, and the date you promised.
5. The one decision or action you need from the teammate.

Create it as a sub-task (`tico task create --owner <slug> --parent <id>`). You stay the customer's point of contact
and you send the reply when the answer comes back.
