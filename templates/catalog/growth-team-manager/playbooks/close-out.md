# Close out a growth experiment

When: a test has ended or been stopped, and the card and its numbers show what happened. The card is in Measuring, or in
Running when its test was stopped. The weekly meeting finds these; the owner may also ask. The outcome is the card in Kept
or Dropped, with a comment saying what we learned and, for Dropped, whether it failed, was inconclusive or was stopped.

---

## 1. Read the task

    hub task show <id>                the task, its steps and its comments
    hub growth numbers                the channel's numbers over the test
    hub kpi show <kpi id>             a KPI the test was meant to move

A reading is a fact. Use the reading that covers the test's period; a partial period is not a value to judge. Every number
you write must be one you read, with its source.

## 2. Choose the stage

- **Kept**: the change moved the numbers the way it was meant to, by enough to keep. The comment says by how much and over
  which weeks.
- **Dropped**, and the comment starts with which:
  - **Failed**: it did not move them, or it moved them the wrong way.
  - **Inconclusive**: the numbers do not settle it, for example too few customers or a mixed signal. The comment says what
    would settle it.
  - **Stopped**: it was stopped for a reason outside the test, such as the owner's decision or a channel closing. It says
    nothing about the customers.

## 3. Comment what we learned

The learning is a claim about customers, not about the test. "Price shoppers churn in two weeks" is a learning;
"the price test lost" is not. Every finished task gets this one line, a Failed one too. Put the learning in `memory/learnings.md` as well, dated.

    hub task comment <id> "<the numbers, their source, the window, and the learning>"

## 4. Move the card

    hub task update <id> --step Kept

Use Dropped in place of Kept as the evidence gives it.

## 5. Follow up

If the learning suggests a next test, file it as a new idea (`playbooks/propose-ideas.md`) and link it to this
one: `hub task relate <new id> <this id> --kind follow_up`. Do not start it here; the weekly meeting picks it.

## Quality

- The owner may change a stage. Say in the comment what would change your mind when the evidence is close.
- If the numbers and the test disagree, it is Dropped as Inconclusive and the comment says why.
- Never choose a stage the evidence does not support, even when the owner would like one.
