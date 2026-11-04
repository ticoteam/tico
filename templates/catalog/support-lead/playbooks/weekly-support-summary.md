# Weekly support summary

Schedule: Mondays at 09:00 team time (routine `weekly-support-summary`), after setup. Also run by hand on request. Budget 35 minutes.

Inputs: each support bot's open and waiting tasks, its last two weekly updates and latest reports, the support mailbox count if it is connected, and `knowledge/targets.md`, `knowledge/team.md` and `knowledge/decisions-needed.md`.

Outcome: one page for the support owner, drafted on the task and sent to nobody else.

Done when: the report is written and published, the decisions asked are in `knowledge/decisions-needed.md`, every source you could not read is named in the report, and the task is marked done with the headline.

---

## 1. Fix the window and read the targets

The window is the last seven full days, and the week before it for comparison. Read `knowledge/targets.md`,
`knowledge/team.md` and `knowledge/decisions-needed.md`. Set `hub bot status set` to one line naming the summary.

## 2. Gather, from the record

    hub task list --owner <support bot> --status open
    hub task list --owner <support bot> --status waiting
    hub update list --kind weekly --bot <support bot> --limit 2
    hub file list

Read each support bot's latest published report: queue digests, QA review, escalations, onboarding board, retention and returns reports, feedback
report. Where the support mailbox is connected, count arrivals and first replies with
`$HUB_DIR/scripts/mail.sh`. Write down which source each number came from, and its date range.

## 3. Compute, against target

Use `skills/measure-response-times-against-target/SKILL.md` for the two times and
`skills/read-the-backlog-by-age-and-owner/SKILL.md` for the backlog.

- **Volume**: new requests this week and last week.
- **First response time** and **time to resolution**: median and slowest tenth. If you can only measure
  from task creation, say so.
- **Backlog**: open count and by age bucket from `knowledge/targets.md`. Name the oldest five with their
  age, owner and what each is waiting on.
- **Repeats**: the three questions or problems that came back most, from the queue digests.
- **Quality and voice of the customer**: one line each from the latest QA review and feedback report.
- **Coverage**: any gap in the next two weeks against `knowledge/team.md`.

## 4. Decide what needs a human

At most three decisions, each with the evidence and a proposed answer. Anything red two weeks running,
an ownerless old ticket, or a coverage gap goes first. Write each one as in `skills/write-a-decision-ask/SKILL.md`
and add it to `knowledge/decisions-needed.md`.
Routing proposals follow `playbooks/route-a-request.md` and are listed, not created.
If the same work has arrived three weeks running with no owner (a repeat, a growing backlog bucket, return
or cancellation requests handled by whoever is free), add one hiring proposal: the support template that
covers it, the counts, and its first routine, as in `AGENT.md` under Hiring. It is listed, never requested.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-support-summary.md` in the shape of `knowledge/examples/weekly-support-summary.md`:
headline, decisions needed, the numbers against target, the oldest waiting, repeats, coverage, routing
proposals, what you could not read, sources. Then:

    hub file publish reports/YYYY-MM-DD-support-summary.md

## 6. Finish

Commit, then `hub task update <id> --status done --note`: the headline, the report path, the decisions
asked for and any source you could not read. Always finish it: an open routine task absorbs the next.

## When a source fails

Name it and what is therefore unknown, use the rest, and say it in the report. A summary built from two
of four sources that reads like the full picture is worse than none.
