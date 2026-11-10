# Write a spec page

Triggered when your manager approves a brief, with a task for its spec. Budget 60 minutes. The outcome is one Spec page
on the task, and a person's OK asked for it, that they can give from the page alone. You write it yourself: nobody else
on this team writes one.

---

## 1. Read the task

    hub task show <id>

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

## 4. Attach it and ask for the OK

    hub page publish reports/<name>.html --title "Spec: <problem, under ten words>" --cadence none --task <id>
    hub task ask <id> --to <your manager or the person who approves specs> "Approve the spec for <problem>? It decides <one line>." --show page:<page id> --version N

`--task` files the page on the task. Publishing the same path again adds a version, so fix a page by publishing it again
before you ask. Nothing starts in Engineering until that OK. When it comes, finish the task:
`hub task done <id> -m "Spec approved: <page>" --version N`.

## 5. After it ships

When the work has shipped, read the measures on the read date with the export named in the spec. Publish a page
with each measure's number, its baseline, its window and the change, and attach it the same way (`--task <id>`). Say
whether each measure held. Do not call a measure held from a cohort younger than its window.
