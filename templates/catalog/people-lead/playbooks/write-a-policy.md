# Write a policy

Triggered by a task asking for a new policy or a change to one (remote work, time off, expenses, conduct, equipment). Budget
40 minutes.

Inputs: the task and its request; the Librarian's pages on the topic (`hub doc ask`); the current practice the requester
describes; and any counsel questions already on file in `knowledge/policies.md`.

Outcome: a policy the team can use with one edit, the decisions it makes with the reasons for each, and a list of what counsel
should check. Nothing is published.

Done when: the policy, the decisions list and the counsel questions are on the task, each fact about current practice has a
source, and the change is recorded in `knowledge/policies.md`. Follow `skills/write-a-policy-from-evidence/SKILL.md` for the
drafting.

---

## 1. Read what exists

    hub task show <id>
    hub doc ask "What do our current policies say about <topic>?"

Open every page the Librarian cites (`hub doc read <path>`) and read it whole. Note contradictions between pages and how people
actually work today. Ask the requester if unclear, one question.

## 2. Settle the decisions first

List the three to six choices the policy makes (for example: who approves time off, how far ahead, carry-over, what happens on
leaving). For each, give the current practice and one or two options. Where a law or regulation may set a minimum (leave, pay,
working time, privacy), write "check with counsel: <question>" instead of stating the law. You are not a lawyer.

## 3. Write it

Purpose in one sentence, who it covers, the rules as short numbered statements, the stated terms, where to ask, the effective
date and the review date (default: one year). Plain words, second person, under two pages.

## 4. Check it

Read it as a new hire, as a manager applying it, and as someone it could disadvantage (a part-timer, a remote worker, someone
on leave). Fix any rule that treats them differently without a reason.

## 5. Hand over

Attach the policy, the decisions list and the counsel questions to the task. Ask only about unresolved facts, with
`hub task ask <id>`. Record the change in `knowledge/policies.md`. Once the owner asks for it to be published, send it to the
Librarian: `hub task create --owner librarian --title "Publish policy: <topic>" --body "<text, effective date, owner>"`.
Announce it to the intended teammates only when the task asks and a granted Tool allows it.
