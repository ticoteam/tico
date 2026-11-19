# The ideas page

One HTML file, `ideas/<slug>.html`, published on the task with `tico page publish`. It runs no script and loads nothing from outside. Charts are inline SVG; pictures are `data:` addresses. Use the app's colour tokens (`var(--ink)`, `var(--muted)`, `var(--surface)`, `var(--line)`) so it reads in light and dark. Put the headline first: a card in Tico shows only the top of the page.

Keep it under about 600 words. A reader should finish it in two minutes.

## Order

**1. The top (three lines, at most 70 words)**

- Line 1: the frame. Who is blocked, how, and the number today with its date.
- Line 2: the recommendation in one sentence.
- Line 3: what happens next ("Going to scope and spec" or "Pick one: ...").

**2. The screen today** (only when the problem shows on a screen)

One real screenshot of the app as it is, with a one-line caption saying what goes wrong there. Never a drawing or a mock-up.

**3. The candidates** (a table, two or three rows, the recommended one first)

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

## `knowledge/ideas.md` line format

    - 2026-11-18 | <option in one line> | set aside: <reason> | back if: <event, number or date> | task <id>
