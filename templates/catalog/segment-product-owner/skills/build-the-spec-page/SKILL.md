---
name: build-the-spec-page
description: Build a spec as one visual Tico page, with mockups drawn in Tico's own styles, publish it on the Product task with --spec, and attach it to the Spec page requirement, which Jev checks and a person OKs. Read after the spec's words are written, before handing over.
---

# Build the spec page

A visual spec is one HTML page on the Product task. It carries the written spec and a mockup of every screen
the change touches. The Engineering task filed from it gets its link at the OK'd version. The Spec stage's
requirement holds it: Jev checks it, then a person OKs it; nothing else does.

## Page order

1. Title line: the feature name, "Spec" and the date.
2. Problem, Evidence, Appetite and Goal, in the order of `knowledge/spec-format.md`.
3. Scope and Non-goals.
4. Screens: one mockup per screen the change touches, each with one caption line on what changes.
5. Acceptance criteria: Given/When/Then, each with its check tag.
6. Open questions, each with who owes it, and Risks.

## Mockups in Tico's styles

Draw each screen in HTML and CSS, or in inline SVG, with Tico's classes and tokens. The page is styled with Tico's
own styles when it is published, so use them rather than new colours.

- Colour: only the tokens. `var(--ink)` for text, `var(--muted)` for secondary text, `var(--line)` for borders,
  `var(--surface)` and `var(--surface2)` for panels, `var(--bg)` for the page. Never a hex colour: the page follows
  the reader's light or dark theme.
- Accent: `var(--accent)` for at most one primary action per screen. Links, counts, selection and focus are ink.
  `var(--fail)` and `var(--wait)` only for a state that needs someone.
- Type: `var(--sans)` for text and `var(--mono)` for code. Body `var(--t-base)` (13px), panel titles `var(--t-md)`
  (14px), page titles `var(--t-lg)` (16px), figures `var(--t-xl)`.
- Classes: `.card` for a panel, `.pill` for a status chip, `.empty` for an empty state, `.ln` for a row line,
  `.scroll` around anything wider than the screen.
- Words: short labels, no fluff, no subtitle on the side of a panel. A mockup shows the real copy the screen would show.

## What the page may not do

Scripts are removed. The page cannot load anything from outside: no external images, fonts, frames or stylesheets.
Images are inline SVG or `data:` URIs. Keep it to one file.

At phone width the page keeps a 16px gutter on each side and has no horizontal scroll: wrap long rows, or put wide
content in `.scroll`.

## Steps

1. Read the written spec (`specs/<slug>.md`) and the screens it changes. If a screen is not known, make it an open
   question with its owner. Do not invent one.
2. Write `specs/<slug>.html` in the page order above.
3. Check it: every criterion can be tested, every mockup uses only the tokens and classes above, and there is no
   script and no outside address.
4. Publish it on the task: `tico page publish specs/<slug>.html --spec --task <id>`. It prints the page's file id.
5. Attach it to the requirement `tico task show` names:

       tico task attach <id> page:<file id> --req "<requirement>" --version N

   Then end the turn. Jev checks it, then the person on the requirement OKs it; do not answer it yourself. If they send
   it back, read the note, revise the page, publish it again and attach the new version.

## Done

The Spec page requirement holds the page, and a note on the task says the page's name, the open questions and who
owes each.
