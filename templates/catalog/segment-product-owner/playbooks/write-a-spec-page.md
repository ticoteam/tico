# Write a spec page

Triggered when your manager approves a brief. The Product pipeline (`hub pipeline --type "Product"`) then holds the
approved idea in its Ideas stage. Budget 60 minutes. The outcome is one Spec page on the card, in the Review stage, that
a person can approve from the page alone. You write it yourself: the Product pipeline's Specifying stage needs a Spec
page, and nobody else on this team writes one.

---

## 1. Take the card

    hub task show <id>
    hub task update <id> --step Specifying

Read the brief in its task and its evidence in `knowledge/briefs.md`. Work only from the approved brief: a comment after
the approval that changes its scope goes back to your manager first.

## 2. Write the page

One HTML file, saved in `reports/`, plain words, with these sections in this order:

    What we are building: <the change, as the customer will see it>
    Problem: <from the brief, in the customer's words>
    Who has it: <accounts in the window, out of the segment's accounts in the cohort, with the window>
    What done looks like: <what a customer can do, or sees, when it is done; something a stranger could check on an account>
    How we will know: <measure, baseline and its date, target and its source, read date, guard measure>
    How it gets validated: <the export or report that will carry each measure, and who reads it on the read date>
    Not in this spec: <what is out of scope>
    Open questions: <question, who owes the answer, by when>

The page has no script and loads nothing from outside. Charts are inline SVG. Put the headline and the key numbers first.
The spec says what the team builds and how it is judged; it does not pick the design. Where the team has not decided,
write "open question" and name who decides.

## 3. Check the page against the Review rubric

- It says what we are building.
- It says how we will know it worked, with its numbers, sources and read date.
- It says how it gets validated.
- No customer's name, email address or other personal detail appears. Name an account by its id.

## 4. Attach it and move it to Review

    hub page publish reports/<name>.html --title "Spec: <problem, under ten words>" --cadence none --task <id>
    hub task update <id> --step Review
    hub task comment <id> "<one line: what the spec decides, and what the approver must check>"

`--task` files the page on the card. Publishing the same path again adds a version, so fix a page by publishing it again
before the card moves. A person approves the spec at Review (its "Spec approved" step). Nothing starts in Engineering
until that approval; the card reaches In development only after it.

## 5. At Validate

When the card reaches Validate, read the measures on the read date with the export named in the spec. Publish a page
with each measure's number, its baseline, its window and the change, and attach it the same way (`--task <id>`). Say
whether each measure held. Do not call a measure held from a cohort younger than its window.
