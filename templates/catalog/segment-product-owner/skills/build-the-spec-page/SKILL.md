---
name: build-the-spec-page
description: Build a spec as one visual Tico page that shows rather than tells - the change in one line first, then before and after screenshots at phone (390 px) and desktop (1280 px) width with red numbered arrows - publish it on the Product task with --spec, and attach it to the Spec page requirement, which Jev checks and a person OKs. Read after the spec's words are written, before handing over.
---

# Build the spec page

A spec is one HTML page on the Product task. A person reads it and OKs it from the page alone, so it shows the change
instead of describing it: what the screen looks like today, what it will look like, and a red numbered arrow on each
thing that changes. The Engineering task filed from it gets its link at the OK'd version. The Spec stage's requirement
holds it: Jev checks it, then a person OKs it; nothing else does.

## The four rules

1. **One page.** Everything is on it: the words, the pictures and the criteria. No "see the attached", no second page,
   no link out for something the reader needs to decide.
2. **Result first.** The first line under the title says what changes for whom, in one plain sentence ("On a phone,
   the account row in the drawer becomes 40 px tall and is no longer clipped"). The reader knows the change before
   any section starts.
3. **Show, don't tell.** Every screen the change touches is shown before and after, at phone width (390 px) and desktop
   width (1280 px). Before is a screenshot of production today. After is a mockup at the same width, drawn as the
   screen will be, with its real copy. A change with no screen (an API, a command, a calculation) shows its before
   and after as the real call and the real output, side by side.
4. **Red numbered arrows.** On each before and after picture, a red arrow points at each thing that changes, numbered
   1, 2, 3. Under the picture, one line per number says what it points at and what changes. Use the same number for
   the same thing on the before and the after picture, and in the acceptance criterion that checks it.

## Page order

1. Title line: the feature name, "Spec" and the date.
2. The change in one sentence (rule 2).
3. Before and after, at both widths (rules 3 and 4).
4. Problem, Evidence, Appetite and Goal, in the order of `knowledge/spec-format.md`.
5. Scope and Non-goals.
6. Acceptance criteria: Given/When/Then, each with its check tag and the arrow number it checks, where it has one.
7. Open questions, each with who owes it, and Risks.

## Before: screenshots of production

Take each before picture of production as it is today, signed in as yourself in the read-only view:

    tico bot app-shot /#/<screen> --width 390 --out shots/<screen>-before-390.png
    tico bot app-shot /#/<screen> --width 1280 --out shots/<screen>-before-1280.png

The address is the app's own (`/#/tasks`, `/#/task/<id>`); `--full` takes the whole page, not only the first screen. The view is read-only: it sees only what you can already read, and nothing you do in it
changes anything.

If the command is refused (the bot's "Can view the app (read only)" setting is off) or is not there yet, do not ask a
person for a screenshot. Draw the before as a mockup from the code that ships today, label it "Drawn from the code,
not a screenshot", and put "Turn on Can view the app (read only) for this bot (`tico bot app-view <bot> on`, an owner or admin)" in the note on the task for the bot's
owner. A screen only another person can see is drawn from the bot's own view or a test account; never one person's
real data.

Crop each picture to the part that matters, keeping enough around it to place it on the screen. A phone picture is
shown 390 px wide and a desktop picture at the page's width.

## After: mockups in Tico's styles

Draw each after screen in HTML and CSS, or in inline SVG, with Tico's classes and tokens, at the width it stands for.
The page is styled with Tico's own styles when it is published, so use them rather than new colours.

- Colour: only the tokens. `var(--ink)` for text, `var(--muted)` for secondary text, `var(--line)` for borders,
  `var(--surface)` and `var(--surface2)` for panels, `var(--bg)` for the page. Never a hex colour: the page follows
  the reader's light or dark theme.
- Accent: `var(--accent)` for at most one primary action per screen. Links, counts, selection and focus are ink.
  `var(--fail)` and `var(--wait)` only for a state that needs someone, and `var(--fail)` for the arrows.
- Type: `var(--sans)` for text and `var(--mono)` for code. Body `var(--t-base)` (13px), panel titles `var(--t-md)`
  (14px), page titles `var(--t-lg)` (16px), figures `var(--t-xl)`.
- Classes: `.card` for a panel, `.pill` for a status chip, `.empty` for an empty state, `.ln` for a row line,
  `.scroll` around anything wider than the screen.
- Words: short labels, no fluff, no subtitle on the side of a panel. A mockup shows the real copy the screen would show.

## Drawing the arrows

Put the picture and its arrows in one inline SVG whose `viewBox` is the picture's own size in pixels, so the arrows
stay on their target at any width. The arrow is `var(--fail)` (Tico's red, in both themes); the number sits in a red
circle at its tail.

    <figure class="shot">
      <svg viewBox="0 0 390 300" width="390" style="max-width:100%;height:auto" role="img"
           aria-label="Phone, 390 px, before: 1 the account row">
        <defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0 0L10 5L0 10z" style="fill:var(--fail)"/></marker></defs>
        <image href="data:image/png;base64,..." width="390" height="300"/>
        <line x1="300" y1="60" x2="220" y2="240" marker-end="url(#a)" style="stroke:var(--fail);stroke-width:3"/>
        <circle cx="300" cy="60" r="12" style="fill:var(--fail)"/>
        <text x="300" y="65" text-anchor="middle" style="fill:#fff;font:700 14px var(--sans)">1</text>
      </svg>
      <figcaption>Phone, 390 px, before. 1: the account row, 32 px tall, clipped at the bottom.</figcaption>
    </figure>

Give each SVG's marker its own id (`a1`, `a2`, ...): ids are shared across the page. The number's white is the one
colour that is not a token, since it sits on red in both themes. Keep arrows off the thing they point at, and no more
than five on one picture: split a busy screen into two pictures.

## What the page may not do

Scripts are removed. The page cannot load anything from outside: no external images, fonts, frames or stylesheets.
Images are inline SVG or `data:` URIs. Keep it to one file under 8 MB: crop pictures, and save a large one as JPEG.

At phone width the page keeps a 16px gutter on each side and has no horizontal scroll: wrap long rows, put the before
and after one above the other, or put wide content in `.scroll`.

## Steps

1. Read the written spec (`specs/<slug>.md`) and the screens it changes. If a screen is not known, make it an open
   question with its owner. Do not invent one.
2. Take the before screenshots at 390 and 1280 px, and draw the after mockups at the same widths.
3. Write `specs/<slug>.html` in the page order above, with the arrows.
4. Check it against the four rules: one page; the change in the first line; every touched screen before and after at
   both widths; every change has a numbered red arrow, its caption line and, where it can be checked, its criterion.
   Then: every criterion can be tested, the mockups use only the tokens and classes above, and there is no script and
   no outside address.
5. Publish it on the task: `tico page publish specs/<slug>.html --spec --task <id>`. It prints the page's file id.
6. Attach it to the requirement `tico task show` names:

       tico task attach <id> page:<file id> --req "<requirement>" --version N

   Then end the turn. Jev checks it, then the stage asks the person on the requirement to OK it; do not answer it
   yourself or ask them again. If they send it back, read the note, revise the page, publish it again and attach the
   new version.

## Done

The Spec page requirement holds the page, and a note on the task says the page's name, the open questions and who
owes each.
