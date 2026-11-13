---
name: build-the-page
description: Build any page a person reads to decide - a spec, a Checked in production page, a Review page or a What was built page - as one HTML page from a ready template. It opens with what it is and what to decide in two or three lines, then real screenshots of the app at phone and desktop width with red numbered arrows, then the details. page.py embeds the screenshots, checks the page and renders the published copy before it is attached. Read before you write any of those pages.
---

# Build the page

A person reads a page to decide: OK a spec, accept a check, merge a change. They read it in a minute, on a phone or a
desktop, without opening the app or the code. So every page is built the same way, from one of four templates, and is
never sent until it was seen rendered.

## The five rules

1. **The opening says what it is and what to decide.** Two or three short lines in `<div id="lead">`: what the page is
   about and its result, then a line that starts **Decide:**. A bug or no-screen spec opens with **Cause:** and
   **Fix:**, one line each. At most 70 words.
2. **Then the pictures. Details last.** Before and after, at phone (390 px) and desktop (1280 px; 1440 px when both
   sides come from the demo) width, with a red numbered arrow on each thing that changes or is measured. Under each
   picture, one line per number. Then
   `<section id="details">`: goal, scope, criteria, version, findings, whatever the template names.
3. **Pictures are real screenshots of the app, never drawings.** No mock-up drawn in HTML, CSS or SVG: drawn screens
   look nothing like the app and are sent back. Before is the app today; after is the app running with the change
   applied (below). A change with no screen shows its real call and real output instead, and no picture.
4. **One page.** Everything the reader needs is on it; nothing is "attached separately". One HTML file, under 8 MB,
   no script, nothing loaded from outside (the pictures go in as `data:` URIs: `page.py build` does it).
5. **Seen before it is sent.** After publishing, render the published page and look at it at both widths. A page that
   does not draw, has a broken picture or a misplaced arrow is fixed and published again. It is never attached broken.

The page brings the proof. It never asks its reader to test, look at the app, or send a screenshot.

## Which template

| Page | Template | Who |
|---|---|---|
| A spec for a change on a screen | `templates/screen-spec.html` | Product Owner, Spec |
| A spec for a bug fix or a change with no screen (API, command, job) | `templates/change-spec.html` | Product Owner, Spec |
| Checked in production | `templates/validation.html` | Product Owner, Validate |
| A Review page, or the What was built page | `templates/review.html` | Reviewers, Review 1 and 2; Builder, Fixes 2 |

Each template has the sections its stage's checker reads (a spec: goal, scope, acceptance criteria; Checked in
production: the version and what was run there; a review: the head commit, what was read and run, the findings; What
was built: how it was checked with output, the checks at the head, what each reviewer found). Keep every section; write
"Nothing." in one that has nothing, and delete the pictures section only where no screen changed (say so in the
opening).

## Real screenshots

Name each file `shots/<screen>-<before|after>-<390|1280|1440>.png` (1440 when both sides come from the demo; for
validation, `shots/<screen>-<390|1280>.png`), and say the same width in the picture's caption.

- **Production, as it is today** (every validation picture; a spec's before only where no demo runs): the read-only view, signed in as
  yourself.

      tico bot app-shot /#/<screen> --width 390 --out shots/<screen>-before-390.png
      tico bot app-shot /#/<screen> --width 1280 --out shots/<screen>-before-1280.png

  `--full` takes the whole page. If it is refused, the bot's **Can view the app (read only)** setting is off: an owner or
  admin turns it on (`tico bot app-view <bot> on`). Say so in the task note, and use the demo below for before.
- **The app with the change applied** (a spec's after, a review's before and after): the repository's demo, run from a
  branch with the change on it. In Tico's own repository one command takes both sides, at 390 px and desktop width:

      python3 scripts/release_shots.py --base origin/main --head <branch> --version <next> --out shots/

  It writes `<page>-<width>-before.png` and `<page>-<width>-after.png` at desktop 1440 px and phone 390 px for the
  pages the branch's `ui/` files draw; use those names in `data-shot`.

  The demo's desktop pictures are 1440 px wide; production's are 1280. A before and after pair comes from one source,
  so both sides match: for a spec, take both from the demo (before on `origin/main`, after on your branch), and name
  them `-1440` instead of `-1280`. Never pair a 1280 production before with a 1440 demo after.

  For a spec, nothing is built yet: make a local branch, apply the smallest change that shows the result (the copy,
  the layout, the new row), run the demo and take the after picture. That branch is never pushed. Where the change is
  too big to apply in an hour, show the before with arrows and say in its caption what will change; still draw nothing.
- **Another repository**: run its demo or dev server the way `knowledge/standards.md` says, and take the pictures
  with the same widths.

Crop each picture to what matters, keeping enough around it to place it. A screen only another person sees is shot on
your own view, the demo or a test account; never one person's real data.

## Arrows

Each `<figure data-shot="...">` in a template takes its arrows in `data-arrows`, in the picture's own pixels:

    <figure data-shot="shots/drawer-before-390.png" data-arrows="1: 600,120 -> 420,1500; 2: 80,300 -> 160,420">

`1: 600,120 -> 420,1500` is arrow 1, from its numbered tail to its head. Open the picture to read the positions. A phone
picture from `tico bot app-shot` is twice its width in pixels (780 for 390). Point at the thing, not on it; at most
five arrows a picture, and the same number for the same thing in the before, the after and the criterion that checks
it. `page.py build` draws them in Tico's red (`var(--fail)`, with a fallback so it stays red outside the app), scaled to the picture.

## Steps

1. Copy the template to `specs/<slug>.html`, `reports/YYYY-MM-DD-checked-<slug>.html` or `reports/<task>-<page>.html`.
   Replace every `{{...}}`. Keep its styles: they use only Tico's colour tokens, so the page follows light and dark.
2. Take the screenshots and set the arrows.
3. Build and check it (`page.py` is in this skill's folder):

       python3 skills/build-the-page/page.py build <draft>.html --out <page>.html
       python3 skills/build-the-page/page.py check <page>.html --kind <screen-spec|change-spec|validation|review>

   `check` names each rule the page breaks: the opening, the sections, a picture that is not a screenshot, a picture
   in the details, a placeholder left, a request to test, filler, length. Fix each and build again.
4. Publish it on the task: `tico page publish <page>.html --task <id> --title "<title>"`, with `--spec` for a spec. It
   prints the file id and anything it removed. Anything of yours removed is a fault to fix; with `--spec`, Tico's own
   fonts (`/vendor/fonts/...woff2`, from the app styles it adds) are always listed and are not a fault.
5. Render the published page and look at it:

       python3 skills/build-the-page/page.py render <file id> --kind <kind>

   It checks the published copy again and draws it at 390 and 1280 px. Open both pictures it names and look: the
   opening at the top, every screenshot drawn, every arrow on its target, nothing cut off at 390 px. With the read-only
   view on, also look at it as people see it in the app:
   `tico bot app-shot /#/page/<file id> --width 390 --full --out render-390.png`. Fix, publish again (a new version) and
   render again until it is right.
6. Attach it to the requirement `tico task show` names:

       tico task attach <id> page:<file id> --req "<requirement>" --version N

## Done

The requirement holds a page that opens with what it is and what to decide, shows real screenshots with numbered
arrows, keeps its details last, and that you saw rendered at both widths.
