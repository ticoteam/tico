# Weekly growth meeting

When: Mondays at 08:00 (routine `weekly-growth-meeting`), or when the owner asks. One turn, about 30 minutes. The outcome
is the tests that have ended closed out, and one ask to the owner with the picks.
Nothing else waits for a person.

If there is nothing to decide (no test has ended and no idea is ready to pick), finish the task with "Nothing to decide
this week" and the numbers in one line. Do not ask.

---

## 1. Read the week

    hub growth numbers                 the growth numbers
    hub task list --label growth:ideas      ideas not started
    hub task list --label growth:running    tests running
    hub task list --label growth:measuring  tests ended, numbers being read

Note anything the numbers show off track; it goes in the ask in step 4.

- A running test that has ended moves to measuring:
  `hub task label <task id> --remove growth:running --add growth:measuring`.
- A running test with no update for two weeks is named in the ask. Its owning bot is nudged with a task of its own; only
  bots are nudged, never a person:
  `hub task create --owner <owner slug> --title "Update: <task title>" --body "<task id> is running with no update for two weeks: post its latest numbers, or say it has ended."`

## 2. Close out what has ended

For each measuring test, and each running test that was stopped, apply `playbooks/close-out.md`. A test still running is
not closed. If its task has a due date within seven days, the ask says so.

## 3. Pick what to test next

Candidates: the ideas. Apply the capacity rule before you offer any pick:

- at most six tests running at once across the team;
- at most two new tests per channel owner at a time, counting tests already running for that owner;
- a test needs a channel with a stage of `testing` or `scaling`, and budget left for its spend (`hub growth channel list`).

A pick that breaks the rule waits for next Monday, and the ask says so.

## 4. Ask once

Put the picks to the owner as one ask on this task, two to six choices. Recommend the picks you would start, and name the
money on any pick that spends. Name any breached guardrail and every test ending soon in the line.

    hub task ask <this task id> --to <owner person ref> "Which ideas should we test this week? I'd start Price calculator and Competitor bids (<amount> on Google Ads). Ending soon: <task and date, or none>." --choices "Price calculator,Competitor bids,Manager meetups" --version N

Use the owner's person ref from `hub team show`. The owner may pick several or type an answer. The run ends here, with
the task asking the owner.

## 5. On the answer

You are woken on this task with the answer. For each picked idea, in this order:

1. Say what changes and what we will watch, then mark it running:

       hub task comment <task id> "<what changes, in one line, and what we will watch>"
       hub task label <task id> --remove growth:ideas --add growth:running

2. File a sub-task for each channel owner, with the channel label:

       hub task create --owner <channel owner slug> --parent <idea task id> --title "<one piece of the build>" --label channel:<channel slug>

3. A pick that spends: ask the owner for an OK on its idea task before any money is committed, and commit it only on that
   OK: `hub task ask <idea task id> --to <owner person ref> "Spend <amount> on Google Ads for two weeks?" --version N`.

Ideas the owner did not pick stay ideas.

## 6. Finish

    hub task done <id> -m "<closed: n; started: n; spend asked for: n>" --version N
