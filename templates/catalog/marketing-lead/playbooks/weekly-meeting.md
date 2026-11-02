# Weekly growth meeting

When: Mondays at 08:00 (routine `weekly-growth-meeting`), or when the owner asks. One turn, about 30 minutes. The outcome
is the tests that have ended closed out, the picks moved to Testing, and one question to the owner with the picks ticked.
Nothing else waits for a person.

If there is nothing to decide (no test has ended and no idea is ready to pick), finish the task with "Nothing to decide
this week" and the numbers in one line. Do not ask.

---

## 1. Read the week

    hub growth numbers                 the growth numbers
    hub task list --type idea          the board: Idea, Testing, Scaling and Paused

Note anything the numbers show off track; it goes in the question in step 4.

A task someone else holds: take it first with `hub task update <task id> --owner marketing-lead`, or ask its assignee on the
task.

## 2. Close out what has ended

For each Testing or Scaling task whose test has ended or been stopped, apply `playbooks/close-out.md`. A test still running
is not closed. If its task has a due date within seven days, the question says so.

## 3. Pick what to test next

Candidates: the tasks at Idea. Apply the capacity rule before you offer any pick:

- at most six tests running at once across the team (Testing tasks);
- at most two new tests per channel owner at a time, counting tests already running for that owner;
- a test needs a channel with a stage of `testing` or `scaling`, and budget left for its spend (`hub growth channel list`).

A pick that breaks the rule waits for next Monday, and the question says so.

## 4. Ask one question

Put the picks to the owner as one multi-pick question, filed as its own task under this one (`--parent`). Options are "LABEL -> WHAT HAPPENS", two to six of them.
Recommend the picks you would start. Name the money on the label for any idea that spends. `--spend` takes one pick: ticking
that option approves its spend, and no other option does. Any other paid pick needs its own approval
(`hub approval request --kind spend`) and does not start until it is approved. Name any breached guardrail and every test
ending soon in `--why`.

    hub task question "Which of these should we test first?" --on <owner person ref> --multi \
      --title "Weekly growth, week <NN>" --parent <this task id> \
      --why "Closed: <task and step>. Ending soon: <task and date, or none>. Off track: <none or what>." \
      --option "Price calculator -> moves to Testing, no spend" \
      --option "Competitor bids -> two weeks, <amount> on Google Ads" \
      --option "Manager meetups -> sponsor one for <amount>, after its own spend approval" \
      --recommend "Price calculator" --recommend "Competitor bids" \
      --spend "Competitor bids=<amount>:Google Ads, two weeks" \
      --if-no-answer "Move to Testing the picks that cost nothing" --default-kind reversible

Use the owner's person ref from `hub team show`. Two rules for the `--if-no-answer` line: only reversible work goes ahead on
its own, and a pick with money waits for a yes.

## 5. On the answer

For each picked task, in this order:

1. Say what changes and what we will watch, then move it to Testing:

       hub task comment <task id> "<what changes, in one line, and what we will watch>"
       hub task update <task id> --step Testing

2. File a subtask for each channel owner, with the channel label. Order matters: link later steps with `blocked_by`.

       hub task child <idea task id> --owner <channel owner slug> --title "<one step>"
       hub task label <child task id> --add channel:<channel slug>
       hub task relate <later task id> <earlier task id> --kind blocked_by

3. Spend the question did not cover needs its own approval first: `hub approval request --kind spend --payload-file <file> --task <idea task id>`. Money on a ticked pick is already approved by `--spend`, as a spend approval you consume once, when the money is committed.

Picks the owner did not tick stay at Idea.

## 6. Finish

    hub task update <id> --status done --note "<closed: n; moved to Testing: n; waiting on the owner>"

Finish the task even when the question is open. The owner's answer arrives on the question task; run step 5 for it in a
turn of its own.
