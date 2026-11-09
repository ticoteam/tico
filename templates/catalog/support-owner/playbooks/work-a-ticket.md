# Work a ticket

Triggered by a task that hands you one ticket, and used for each ticket in
`playbooks/daily-support-queue.md`. Budget ten minutes for a single ticket. The outcome is a draft
reply if you can answer it, a task if you cannot, a line in `knowledge/follow-ups.md`, and a bucket
either way.

Done when the ticket has its bucket, its draft or route is on the task, its follow-up line is written, and
the task note names the bucket and where the ticket went.

---

## 1. Read the ticket

    hub task show <id>

Read the whole thread, not the subject. If you cannot reach the source at all, say so and stop: an
unread queue and an empty queue must never read the same.

## 2. Sort each one

Four buckets, and every ticket is in exactly one:

| Bucket | What it is | What you do |
|---|---|---|
| Answered by the docs | The Librarian's answer to the question is `covered` | Draft the reply from that answer, adjusted to what they asked |
| Known issue | `knowledge/known-issues.md` names it | Draft the agreed holding answer and add this ticket to the count |
| Needs a human | A refund, a credit, an exception, an angry customer, anything in `knowledge/escalation.md` | One task on the human who owns it, with the ticket and one line on what they are deciding |
| New | None of the above, or the docs say "Not in the docs" | Draft what you can, name what you do not know, ask, and report the gap |

A ticket that is two of these is the more serious one. Anything that mentions money, a deadline, a
legal matter, or a human's safety goes to a human immediately, before you finish the pass.

## 3. Research the answer

    hub doc ask "<what the customer is asking, in plain words, no personal details>"

Use the answer and its citations. `covered: false`, or an answer that contradicts what the ticket
shows, is a doc gap: one task to the Librarian (`hub task create --owner librarian --parent <id>`) with
the ticket reference, the question and what you found. Never fill the gap with a guess, and never
write the missing doc yourself.

## 4. Draft the reply

One draft per ticket, on the task, never in the support tool. Each draft:

- starts by naming what they asked, in one line, so the requester can check the match;
- answers from a doc the Librarian cited or a human's word, not from a policy you assembled;
- leaves a marked gap wherever it would need a refund, a credit, a discount, a fix, or a date, and
  says on the task what the gap needs (`skills/write-a-reply-with-a-marked-gap/SKILL.md`);
- carries no personal detail beyond what the intended reader already has;
- matches `knowledge/voice.md`, is short enough to read on a phone, and ends with what happens next
  or what you need from them.

## 5. Route what is left

    hub task create --owner <human> --parent <id>
    hub task create --owner <slug> --parent <id>

One task per thing, with the ticket reference, what the customer could not do, and what you need
decided. Never two tasks for the same thing, and never a second escalation of something an open task
already names. If the same problem has now arrived three times, that is one line in
`knowledge/known-issues.md` and one task for whoever owns the fix, not three escalations.

## 6. Finish

Commit, then `hub task update <id> --status done --note`: the bucket, whether a draft is on the task,
and who you routed it to. Add the ticket to `knowledge/follow-ups.md` with who it waits on (the customer, a colleague, a fix)
and the next nudge date. A draft that rests on a human's word rather than a doc says so on the task.

## When the source is unreadable

Record which source, the exact refusal, and the window it covers. Put one line on the task for the
owner if it has failed twice in a row. Never report zero tickets for a source you could not open.
