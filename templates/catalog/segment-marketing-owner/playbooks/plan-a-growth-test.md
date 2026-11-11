# Plan a growth test

Run inside the weekly review, or alone when the Growth Manager (`growth-manager`) asks for ideas for a segment. Budget 30 minutes.
The outcome is up to three idea tasks labelled growth:ideas, each with its channel and segment, ready for the Monday growth
meeting. Nothing starts, spends or changes a channel here.

---

## 1. Find the gap

Use the review's numbers. Look for a channel whose share of this segment's new customers is falling, a cost per customer
above its ceiling, a message whose claims just changed, or a goal with no test behind it. Write each gap in one line,
with its reading and its date.

## 2. Check the channel and the ideas

    tico growth channel list
    tico task list --label growth:ideas --label segment:<slug>

`tico growth channel list` gives each channel's stage and budget. A test can start only on a channel at `testing` or
`scaling` with budget left for its spend. A channel at `idea` can take an idea, but the growth meeting cannot start it
until its stage is `testing`; say so in the idea's body. A `paused` channel takes no new idea. The board list shows what
is already On deck, In development, Live or Working, so nothing is filed twice.

## 3. Write each idea

Write five lines, under 120 words, in a file:

- the channel (`channel:<slug>`) and the segment (`segment:<slug>`);
- the change, in one line, in the segment's terms;
- the measure: the segment's new customers or cost per customer, with its baseline and reading date;
- the spend it would use, and whether it fits the budget left in `knowledge/segment.md` (the spend is not approved here);
- the message version it uses, from `knowledge/message.md`.

An idea carries no prediction, no hypothesis, no score and no bet. Write what changes and what is measured. The growth
meeting decides.

## 4. File each idea

    tico task create --owner growth-manager --label growth:ideas --title "<idea, one line>" --body-file <file> --label channel:<slug> --label segment:<slug>

## 5. Link a follow-up

When an idea follows a test that has closed, relate the new idea to the closed one:

    tico task relate <new task id> <closed task id> --kind follow_up

## 6. Finish

List each idea filed, with its task id, in the weekly review. If this playbook was the task, finish it with
`tico task done <id> -m "<ideas filed: n; ids>" --version N`.
