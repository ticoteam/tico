---
name: set-severity-and-owner
description: Give an escalated ticket one severity from impact and scope, one named human owner and a one-line reason, using the team's rules, before any customer update goes out.
---
# Set severity and owner

When to use: when a ticket first matches an escalation trigger, or when a case's impact changes (more customers affected, a deadline now at risk, a workaround stops working).

Steps:
1. Write the facts: who is affected (one customer, a segment, or everyone), what they can no longer do, whether a workaround exists, and whether money, data or a contractual time is at risk.
2. Match them to `knowledge/escalation-rules.md`. Use the severity whose example fits best. If two fit, take the higher and say why.
3. If the case touches every customer, or data is lost or exposed, it is a major incident. Stop here, hand it to `incident-scribe` and a human, and keep the case file open.
4. Propose one owner from the rules: a named human, never a team or a queue. If the rules do not decide it, ask the head of support in the task.
5. Write the reason in one line on the case file, for example: "Sev 2: one customer, workaround exists, contract response time in 2 days."
6. Set the next customer update from the cadence in the rules, counted from the first customer message if none has gone out yet.
7. If the severity changes later, record the old and new severity, the time, and the fact that moved it.

Checks before you finish:
- One owner, a human, named on the task and in the register.
- The reason is one line and cites facts, not the customer's tone.
- The next update date is set and visible in the register.
- A major incident is not kept as an ordinary escalation.

Example (invented, for illustration): "E-14. Sev 1: every member of one VIP customer is charged twice; no workaround; contract response time 4 business hours. Owner: the support lead, named on the task. Next update: 4 business hours from the first customer message, 05:30 Tuesday."
