# Write a product brief

Triggered by the weekly review when a problem meets the brief threshold in `knowledge/product-practice.md`, or when your
manager asks for a brief on a named problem. Budget 30 minutes. The outcome is one page, filed as an idea in the Product
pipeline's Ideas stage, that can be chosen, changed or declined from the page alone.

---

## 1. Check that it needs a new brief

Open `knowledge/briefs.md`. If a brief for this problem is open, add the new evidence to that task as a comment:

    tico task comment <id> "<evidence, its source and date>"

Do not file a second brief. If an earlier brief was declined, read its reason first. File again only with evidence the
reason did not cover.

## 2. Write the page

One page, plain words, in this shape:

    Title: <the problem, in the customer's words, under ten words>
    Segment: <name> (segment:<slug>), or "all customers"
    Problem: <one or two sentences: what the customer tries to do, and where it fails>
    Who has it: <distinct accounts in the window, out of the segment's accounts in the cohort, with the window>
    Evidence: <three to five items, each with its source, its date and one line>
    Cost: <what it does to activation or retention, with both counts; or "unknown: no usage export">
    What done looks like: <what a customer can do, or sees, when the problem is gone; something a stranger could check on an account>
    How we will know: <measure, baseline and its date, target and its source, read date, guard measure; from skills/write-how-we-will-know/SKILL.md>
    Not in this brief: <what is out of scope, so nobody builds it by accident>
    Open questions: <question, who owes the answer, by when>
    Not a solution: this brief says what done looks like. The team chooses how.

## 3. Check the page

- Every number has its source, its date and its window.
- Nothing in it promises a customer a fix, a feature or a date.
- The problem is written without its solution in it.
- No customer's name, email address or other personal detail appears. Name an account by its id in the export.
- It fits on one page.

## 4. File it in Ideas

Save the page to a file in your checkout, then file it in the Product pipeline. It starts in Ideas, which you own:

    tico task create --owner <your slug> --pipeline product --title "<problem>" --body-file <file> --label segment:<slug>

Leave out the label when you own the whole product. If the problem is already a Product task, comment the brief on that
task instead of filing a new one. Record the brief in `knowledge/briefs.md`: the date, the problem, the task id, and
"in Ideas". The weekly review lists it for your manager.

## 5. Stop there

Do not tell a customer, a salesperson or a marketer that the problem will be fixed. Your manager's decision comes back
on the task, and the next weekly review records it. Your brief waits in Ideas until a person picks it; then it moves
to Spec by itself when Spec has room. Nobody is told when Spec has room.
