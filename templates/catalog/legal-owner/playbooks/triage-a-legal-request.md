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
Log it in `knowledge/requests.md` and record its stage there: Intake until it is sorted, then Review.

## 3. Decide the path

1. **Must go to a lawyer** under `knowledge/escalation.md` (a threatened claim, a regulator, a dismissal, a
   breach, a value over the threshold): write the lawyer's brief (facts with dates, documents, the question,
   the deadline) and put it on the task for the owner. Engage counsel when requested and your Tools allow it; record the cost and scope.
2. **Belongs to a legal bot** by the team lines: propose the route in one line with the reason.
3. **Contract with flags needing a judgement**: review it yourself (read liability, indemnity, term and renewal, termination, IP and data first), and write
   `reports/reviews/<party>-<kind>.md`: the five-line summary, the flags against the playbook, and the
   questions for counsel.
4. **Answered by a documented position**: answer in three lines and cite the source and its date.
5. **A rule check.** A question about what a rule allows (a contract term against the team's rules, or a "can we...?"
   that the documented positions do not settle) goes to the Rules Owner (`tico-counsel`). Set the request to Rules check,
   ask once with `hub task create --owner tico-counsel --parent <id> --title "Rule check: <request>" --body "<the rule,
   the facts with dates, the question and the deadline>"`, and wait. You do not decide the rule check yourself.

## 4. Hand over

Put the outcome on the task. Ask once with `hub task question "<question>" --on <person> --parent <id>` if the scope or lawyer is missing. Route requested work: `hub task create --owner <slug> --title "<what>" --body "<request and source>" --parent <id>`.
End with the not-legal-advice line, update `knowledge/requests.md`, and `hub task update <id> --status done --note`.

## Done when
- The request has one of the three outcomes, on the task and in `knowledge/requests.md`.
- Its stage in `knowledge/requests.md` matches its state, and no rule check was decided by you.
- Any deadline in it is sourced, or marked "not confirmed" with what you checked (`skills/check-a-legal-deadline`).
- Anything that meets `knowledge/escalation.md` has a counsel brief (`skills/write-a-counsel-brief`) on the task.
