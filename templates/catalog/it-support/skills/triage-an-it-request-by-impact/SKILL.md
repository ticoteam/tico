---
name: triage-an-it-request-by-impact
description: Sort an IT request into a category and a priority (P1, P2 or P3) by how many people it stops and how badly, write the reason on the task, and give the first action for anything that looks like a compromise.
---
# Triage an IT request by impact

When to use: on every new IT request, before you write a fix or prepare an access change.

Steps:
1. Read the request as the requester wrote it, then `hub task show <id>`. Note the date it was filed and anything they
   already tried.
2. Pick one category: account and access, device, network, software, or security. If two fit, pick the one that stops
   the person more.
3. Count who is stopped: one person, a small group, or the team. Ask one question with `hub task ask <id>` only when the
   answer would change the priority.
4. Set the priority:
   - P1: many people stopped, a suspected compromise (a login they did not make, a link that asked for a password, a lost
     laptop), or a leaver whose access is still live past their last day.
   - P2: one person cannot work and no fix is known yet.
   - P3: an inconvenience, a workaround exists, or the person can still work.
5. For P1, put the first action in the first sentence of the reply and tell the Operations Manager at once. For a
   suspected compromise, the first action is to change that password through the tool's own reset page and to report the
   device. Never ask for the password itself.
6. Write the category, the priority and one line on why on the task note. A priority with no reason is not set.
7. Search `knowledge/requests.md` for the same problem in the last 30 days. Three or more is a repeat, and it goes on the
   weekly page under "Repeats".

Checks before you finish:
- Every request has a category, a priority and a reason on the task.
- Every P1 has its first action in the first sentence, and the Operations Manager was told.
- No step asks the requester to send a password, recovery code or MFA code.

Example (invented, for illustration): "Three people on the sales team (Lena, Omar and Sofia) cannot open the CRM since
08:40, read at 09:10. Category: account and access. Priority P1: a team tool is blocked for three people. Operations
Manager told at 09:12. First action for each: do not retry the sign-in; the CRM admin is checking the sign-in service and
will reply on this task."
