---
name: build-the-spec-page
description: Build a spec as one visual Tico page, with mockups drawn in Tico's own styles, publish it on the roadmap task with --spec, and ask a person for its OK on the task. Read after the spec's words are written, before handing over.
---

# Build the spec page

A visual spec is one HTML page on the roadmap task. It carries the written spec and a mockup of every screen
the change touches. Tasks filed under it, such as the builder's, get its link and text. A person approves
it with an OK asked on the task; nothing else does.

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
- Type: `var(--sans)` for text and `var(--mono)` for code. Body `var(--t-base)` (13px), card titles `var(--t-md)`
  (14px), page titles `var(--t-lg)` (16px), figures `var(--t-xl)`.
- Classes: `.card` for a panel, `.pill` for a status chip, `.empty` for an empty state, `.ln` for a row line,
  `.scroll` around anything wider than the screen.
- Words: short labels, no fluff, no subtitle on the side of a card. A mockup shows the real copy the screen would show.

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
4. Publish it on the task: `hub page publish specs/<slug>.html --spec --task <id>`.
5. Hand it in for an OK, showing the page:

       hub task ask <id> --to <the person who OKs specs> "OK the spec for <title>?" --kind ok --show page:<page id> --version N

   Then end the turn. Do not answer it yourself. If they send it back, read the note, revise the page, publish it
   again and ask again.

## Done

The task holds the spec page, the OK is asked on it, and a comment says the page's name, the open questions and who
owes each.
