# Close out a marketing task

When: a test has ended or been stopped, and the task and its numbers show what happened. The weekly meeting finds these;
the owner may also ask. The outcome is the task on one step, Won, Lost, Inconclusive or Dropped, with a comment saying
what we learned.

---

## 1. Read the task

    hub task show <id>                the task, its steps and its comments
    hub growth numbers                the channel's numbers over the test
    hub kpi show <kpi id>             a KPI the test was meant to move

A reading is a fact. Use the reading that covers the test's period; a partial period is not a value to judge. Every number
you write must be one you read, with its source.

## 2. Choose the step

- **Won**: the change moved the numbers the way it was meant to, by enough to keep. The comment says by how much and over
  which weeks.
- **Lost**: it did not move them, or it moved them the wrong way.
- **Inconclusive**: the numbers do not settle it, for example too few customers or a mixed signal. The comment says what
  would settle it.
- **Dropped**: it was stopped for a reason outside the test, such as the owner's decision or a channel closing. It says
  nothing about the customers.

## 3. Comment what we learned

The learning is a claim about customers, not about the test. "Price shoppers churn in two weeks" is a learning;
"the price test lost" is not. Put the learning in `memory/learnings.md` as well, dated.

    hub task comment <id> "<the numbers, their source, the window, and the learning>"

## 4. Move the task

    hub task update <id> --step Won

Use Lost, Inconclusive or Dropped in place of Won as the evidence gives it.

## 5. Follow up

If the learning suggests a next test, file it as a new Marketing task (`playbooks/propose-ideas.md`) and link it to this
one: `hub task relate <new id> <this id> --kind follow_up`. Do not start it here; the weekly meeting picks it.

## Quality

- The owner may change a step. Say in the comment what would change your mind when the evidence is close.
- If the numbers and the test disagree, the step is Inconclusive and the comment says why.
- Never choose a step the evidence does not support, even when the owner would like one.
