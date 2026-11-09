---
name: build-the-spec-page
description: Build a spec as one visual Tico page, with mockups drawn in Tico's own styles, publish it on the Product roadmap card with --spec, and attach it to the card's Spec approved requirement. Read after the spec's words are written, before handing over.
---

# Build the spec page

A visual spec is one HTML page on the Product roadmap card. It carries the written spec and a mockup of every screen
the change touches. Tickets filed under the card, such as the builder ticket, get its link and text. A person approves
it through the pipeline's Spec approved requirement; nothing else does.

## Page order

1. Title line: the feature name, the card's status (Spec) and the date.
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
4. Publish it on the card: `hub page publish specs/<slug>.html --spec --task <card>`. The publish is refused unless
   the card is on the Product roadmap.
5. Hand it in for approval. Move the card to Review, find the Review stage's Spec approved requirement
   (`hub requirements <stage>`), and attach the page to it:

       hub requirement attach <card> <requirement> --source file --file-id <page file id>

   Then end the turn. A person approves it from their review queue. When the card leaves Review, the pipeline files
   the builder ticket under it with the spec attached. Do not approve it or move the card past Review yourself. If
   the review sends it back, read the note, revise the page and publish it again.

## Done

The card holds the spec page, the page is attached to Spec approved, and the task note says the page's name, the open
questions and who owes each.
