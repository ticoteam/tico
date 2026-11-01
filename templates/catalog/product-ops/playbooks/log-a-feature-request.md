# Log a feature request

Triggered by a task from sales, support, the Customer Insights Analyst or a human that passes on a
request, and used in the weekly review. Budget 10 minutes. The outcome is the request in the ledger once,
with its account, and an answer the sender can pass on without promising anything.

---

## 1. Read the request

    hub task show <id>

Find the need behind it: who, trying to do what, blocked how. The account and where it was asked
(ticket, call, email) with the date. If the account is missing, ask the sender once.

## 2. Search the ledger

Search `knowledge/requests.md` by the need, the product area and the words the customer used. Then follow
`skills/match-a-request-to-the-ledger/SKILL.md` to pick one of three outcomes:
- **Same need**: add the account and source to that row. If the account is already there, add the date only.
- **Close but different**: new row, and a "see also" link both ways, with one line on the difference.
- **New**: new row with the need, area, account, segment (from the CRM, read), source and date.

## 3. Check its status

Is it on the roadmap, specced, shipped or declined? Link the spec or the decision if there is one.

## 4. Answer the sender

On the task: the row it went to, how many accounts now ask, and its status, in words the sender can
repeat without a promise: "Logged with 12 other accounts; not on the current roadmap." Then
`hub task update <id> --status done --note`.
