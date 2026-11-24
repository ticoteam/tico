# The ideas page

One HTML file, `ideas/<slug>.html`, built and checked with `.claude/skills/build-the-page` (its eight rules, its CSS
and `page.py check`) and published on the task with `tico page publish`. Keep it under about 600 words: a reader
finishes it in two minutes.

## Order

**1. Title, its line, contents**

- The title: the problem in a few words ("Invoices sent twice").
- The line under it, one sentence: "This is the ideas page for <who is blocked, the number today>: we recommend <the
  candidate>." It is the summary; no other top block.
- Contents linking every H2 below.

**2. The screen today** (only when the problem shows on a screen)

One real screenshot of the app as it is, with a one-line caption saying what goes wrong there. Never a drawing or a mock-up.

**3. The candidates** (an H2 with a table, two or three rows, the recommended one first)

| Column | What goes in it |
|---|---|
| Candidate | What it is, in one line a customer would understand |
| Evidence | Pattern or thin; distinct accounts; kinds of source; finding or evidence ids |
| Size | Small or big, in the team's appetite units |
| Adds / removes | Screens, settings and new words it adds; anything it removes |
| Strongest argument against | One line |
| Biggest unknown | A sentence that could be false |
| Cheapest test | What, who runs it, by when |
| Changes the decision if | The result, written before the test runs |

**4. The map** (a nested list, or the same thing as inline SVG)

    Number: <the KPI and its value>
      Problem: <in the customer's words> (<accounts>, <sources>)
        Option: <candidate 1>
          Test: <cheapest test>
        Option: <candidate 2>
          Test: ...
      Problem: <a neighbouring problem found on the way>

**5. The premise table**

| Must be true | Evidence, source, date | Status |
|---|---|---|
| The problem is real | ... | known / unproven |

Then the three one-line answers: what happens if we do nothing, who is left out of the evidence, who is content with today.

**6. Set aside**

One line per option that did not make the short list: the option and the reason. The same lines go into `knowledge/ideas.md` with the date and what would bring each back.

**7. What was read**

Each source, its date range, and anything that could not be read.

## Rules for the page

- Every number has its source and date beside it. Every quote has its evidence id.
- A customer is an account id and a type ("account 4417, solo electrician"), never a name or an email address.
- A guess is labelled "guess" where it stands.
- No sentence that would fit any other ideas page. No "overview", "summary" or "next steps" section.
- Each part above is an H2 section, in this order, linked from the contents. Numbers as a chart, not a sentence.

## `knowledge/ideas.md` line format

    - 2026-11-18 | <option in one line> | set aside: <reason> | back if: <event, number or date> | task <id>
