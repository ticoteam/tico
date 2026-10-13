# Daily support queue

Schedule: weekdays at 09:00 team time (routine `daily-support-queue`), after setup. Also run by hand on request. Budget 20 minutes. The outcome is one digest on the
task: every ticket in a bucket with a draft where it can be answered, escalations sent, follow-ups due
listed, and doc gaps reported to the Librarian. Nothing goes to a customer.

---

## 1. Read what came in since the last pass

    hub task show <id>

Then the source setup named: tasks routed to you (`hub task list --owner me --status open`),
the support mailbox (`$HUB_DIR/scripts/mail.sh inbox --untriaged --format brief`), a Slack channel. Read
what arrived since the watermark in `state.md` and nothing older. If a source refuses you, name it
and go on. An unread queue and an empty queue must never read the same.

## 2. Work each ticket with `playbooks/work-a-ticket.md`

Every ticket is in exactly one bucket: answered by the docs, known issue, needs a human, or new. Ask
the Librarian once per distinct question, not once per ticket: tickets that ask the same thing share
the answer. `covered: false` means the docs do not say, and the ticket is "new".

## 3. Draft, route, cluster

- **Draft** a reply for each ticket you can answer, following `playbooks/work-a-ticket.md`
  step 3. Put every draft on the task. Never in the support tool.
- **Route** what needs a human: `hub task create --owner <human> --parent <id>` with the ticket and
  one line on what they decide. Money, a deadline, a legal matter, a security concern or an
  outage goes first, before you finish the pass.
- **Cluster** the repeats. Three tickets that ask a question the docs never answer are one task to the
  Librarian (`hub task create --owner librarian`) with the question, the ticket references and the
  count; you do not write the answer. Three that report the same failure become one line in
  `knowledge/known-issues.md` and one task for the owner of the fix. Add a repeat to an existing
  entry's count, not a new entry.
- **Follow up.** Read `knowledge/follow-ups.md`. For each ticket waiting on a customer longer than the
  nudge rule, draft the nudge on the task; after the last nudge, mark it "closed quiet" and say so.
  For each ticket waiting on a colleague or a fix, name who and since when.

### Tico HQ tickets and GitHub threads

If this bot works them (`playbooks/tico-hq-tickets.md`, `playbooks/tico-github.md`), the watchers already opened a task for
each; you do not fetch them here. Add one heading to the digest: opened since the last pass, replies posted, drafts waiting
when a person has turned mail sending on in Tico or for a maintainer to post (and for how long), bugs handed to engineering, and threads that closed. Counts
and one line each, no ticket or thread text.

## 4. Write the digest

In the shape of `knowledge/examples/support-queue.md`: counts first, what needs a human today, then
tickets by bucket with the draft and the doc it rests on, follow-ups due, then patterns and doc gaps. Save it to `reports/YYYY-MM-DD-triage.md` only when the
pass is worth keeping. Paraphrase; no personal details, no credentials.

## 5. Finish

Update the watermark in `state.md` and `knowledge/follow-ups.md`, commit, then `hub task update <id> --status done --note`: how many
arrived, how many you drafted, how many went to a human and to whom, and anything you could not read.
Always finish it: an open routine task absorbs the next occurrence.

## When the source is unreadable

Record which source, the exact refusal and the window it covers. One line on the owner's task if it
failed twice in a row. Never report zero tickets for a source you could not open.
