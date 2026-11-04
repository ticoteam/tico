# Triage a legal request

Triggered by a task that asks a legal question or hands over a legal document. Budget 15 minutes, 45 when you
review a contract yourself. The outcome is one of three: an answer from a documented position, a routed owner,
or a prepared question for a lawyer. Nothing is sent, signed or decided.

---

## 1. Read the request whole

    hub task show <id>

Read every attachment. Find: what is being asked, by whom, by when, who the other party is, and what is at
stake (money, a deadline, a person, data, a regulator). `hub doc ask "<the question>"` to see whether an
documented answer or a policy already covers it, and `hub meeting search "<party>"` for context.

## 2. Sort it

- **Kind**: contract, NDA, privacy, employment, IP, corporate, regulatory, dispute, other.
- **Urgency**: the real date (a notice window, a filing, a signature someone promised), or "none".
- **Risk**: one line, from the record: amount, exposure, who is affected.
Log it in `knowledge/requests.md`.

## 3. Decide the path

1. **Must go to a lawyer** under `knowledge/escalation.md` (a threatened claim, a regulator, a dismissal, a
   breach, a value over the threshold): write the lawyer's brief (facts with dates, documents, the question,
   the deadline) and put it on the task for the owner. Engage counsel when requested and your Tools allow it; record the cost and scope.
2. **Belongs to a legal bot** by the team lines: propose the route in one line with the reason.
3. **Contract with flags needing a judgement**: review it yourself with the method in `legal-review`'s
   summary (read liability, indemnity, term and renewal, termination, IP and data first), and write
   `reports/reviews/<party>-<kind>.md`: the five-line summary, the flags against the playbook, and the
   questions for counsel.
4. **Answered by a documented position**: answer in three lines and cite the source and its date.

## 4. Hand over

Put the outcome on the task. Ask once with `hub task ask <id>` if the scope or lawyer is missing. Route requested work: `hub task create --owner <slug> --title "<what>" --body "<request and source>" --parent <id>`.
End with the not-legal-advice line, update `knowledge/requests.md`, and `hub task update <id> --status done --note`.

## Done when
- The request has one of the three outcomes, on the task and in `knowledge/requests.md`.
- Any deadline in it is sourced, or marked "not confirmed" with what you checked (`skills/check-a-legal-deadline`).
- Anything that meets `knowledge/escalation.md` has a counsel brief (`skills/write-a-counsel-brief`) on the task.
