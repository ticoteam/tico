# Open an escalation

Triggered by a task that escalates a ticket, or by the digest finding one that meets a trigger. Budget 15 minutes.

Inputs: the escalation task, the ticket thread, `knowledge/register.md`, `knowledge/escalation-rules.md`, and the customer's
VIP terms if they are listed.

Outcome: a case file, one register line, a severity and an owner proposed and recorded, and for a defect an engineering-ready
bug report.

Done when: the case is in the register once and no second case exists; the severity and owner are on the task with their
reasons; the timeline has every customer touch with a time; and the first update is drafted and marked for review.

---

## 1. Check it is not already open

Search `knowledge/register.md` and `hub task list` for the customer and the problem. If a case exists, add to it; never open
a second one.

## 2. Set severity and owner

Follow `skills/set-severity-and-owner/SKILL.md`. When the rules do not decide the owner, ask the head of support in the task.

## 3. Build the timeline

From the first customer message to now: each touch with its time, channel and one line. Mark the gaps where the customer
waited without hearing from us.

## 4. Write the bug report, if it is a defect

Follow `skills/write-an-engineering-bug-report/SKILL.md`. If you cannot reproduce it, ask the Technical Support Engineer with
`hub task create --owner technical-support` first.

## 5. Prepare the first update

Follow `skills/write-a-customer-update/SKILL.md`. Put the draft on the task, marked for review. Save the case file, add the
register line, and commit.
