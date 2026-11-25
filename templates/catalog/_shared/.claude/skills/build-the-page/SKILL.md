---
name: build-the-page
description: Build any page a person reads - a spec, a Review page, a QA page, a release report, a Checked in production page, a plan, a report or a sidebar widget - as one clear HTML page from a ready template, with the page kit's stats, charts, tables and lists in Tico's look. Title, one line saying what the page is, clickable contents, then short sections of bullets, charts and real screenshots. page.py embeds the screenshots, checks the page against the eight rules and renders the published copy. Read before you write any page a person reads.
metadata:
  function: tico
---

# Build the page

A person reads a page in a minute, on a phone or a desktop, without opening the app or the code. A good page is short,
plain and visual. `examples/change-spec.html` is a real one: read it before you write your first page.

## The eight rules

1. **Less is more.** Few words, plain English, no jargon. If two lines can be one, merge them.
2. **The line under the title says what the page is.** `<p class="desc">This is a spec to ...</p>`, one plain
   sentence, at most 40 words. It is the summary: no Summary section, no boxed opening, no date line.
3. **Clickable contents.** `<nav class="toc">` right after that line, linking every H2. Sections stay open.
4. **Semantic headings.** One H1 (the title), then H2 for each section and H3 inside it. Headings say what is inside.
5. **Bullets, one line each.** One style per item. Never stack a heading, a bullet and a grey caption that say one
   thing; no eyebrow labels over headings. No paragraph over 50 words.
6. **Pictures first.** Numbers as a chart, screens as real screenshots. A chart reads without explanation: every bar
   labelled, its value on it.
7. **No picks or decision forms.** What to do is plain from what the page is: a spec is OK'd, a QA page approved. No
   "Decide" line, no options to choose from, no form.
8. **Facts, not filler.** Numbers, screens, files and real cases. One word for one thing, the same on every line. Cut
   any sentence that would fit another page.

The page brings the proof. It never asks its reader to test, look at the app, or send a screenshot.

## Which template

| Page | Template, kind | Who |
|---|---|---|
| A spec for a bug | `bug-spec` | Feature Owner, Spec |
| A spec for a change on a screen | `screen-spec` | Feature Owner, Spec |
| A spec for a change with no screen (API, command, job) | `change-spec` | Feature Owner, Spec |
| A Review page | `review` | Reviewer |
| The QA page | `qa` | Feature Owner or Builder, QA |
| The release report for a batch | `release` | Release Owner |
| Checked in production | `validation` | Feature Owner, Validate |
| Anything else: a plan, a research report, an update | the closest template's shape | Anyone |

Each template is `templates/<kind>.html`. Keep its sections in order. Delete an optional section that would say
nothing, and its contents link; `page.py check` names the sections a kind must keep. A section it requires that has
nothing in it says "Nothing." or "None."

## Pictures

**Charts.** For a number, a comparison or a share, use the template's bars, one row per number:

    <figure class="chart" aria-label="Where the 6.5 h went">
      <div class="bars"><span>Working</span><i style="width:46%">3 h</i><span>Stalled</span><i style="width:31%">2 h</i></div>
      <figcaption>Source and date.</figcaption>
    </figure>

**Screenshots** are real, never drawn: no mock-up in HTML, CSS or SVG. Before is the app today; after is the app
running with the change. Name each `shots/<screen>-<before|after>-<390|1280|1440>.png` (validation:
`shots/<screen>-<390|1280>.png`); the caption says today or after, and phone or desktop.

- **Production today** (every validation picture; a spec's before only where no demo runs), signed in as yourself:
  `tico bot app-shot /#/<screen> --width 390 --out shots/<screen>-before-390.png` (and `--width 1280`; `--full` for
  the whole page). Refused: the bot's **Can view the app (read only)** setting is off; an owner or admin turns it on
  (`tico bot app-view <bot> on`). Say so in the task note; a spec uses the demo, a validation marks those checks
  "Not checked" (a demo never proves production).
- **The app with the change** (a spec's after, a review's before and after): the repository's demo, run from a branch
  with the change. In Tico: build web/ (`npm ci && npm run build`), run `python3 -m backend.demo --port <port>` on each
  side, and take each page in Playwright at 1440 and 390 px. A pair comes from one source: for a spec, both from the
  demo (before on `origin/main`, after on your branch), named `-1440`.
- **A spec, before anything is built:** make a local branch with the smallest change that shows the result, run the
  demo, take the after picture. Never push it. Too big for an hour: show the before with arrows and say what changes.
- **Another repository:** its demo or dev server as `knowledge/standards.md` says, at the same widths.
- **A release report** takes no new pictures: each changelog entry reuses the after picture from its QA page.
- **No screen changes:** show the real call and its real output, today and after, in two `<pre>` blocks.

Crop to what matters. Never show one person's real data: use your own view, the demo or a test account.

**Arrows.** Each `<figure data-shot="...">` takes red numbered arrows in `data-arrows`, in the picture's own pixels:
`data-arrows="1: 600,120 -> 420,1500; 2: 80,300 -> 160,420"` (arrow 1 from its numbered tail to its head). A phone
picture from `app-shot` is 780 px wide for 390. At most five a picture; the same number for the same thing in the
before, the after and the criterion that checks it. One line per number in the caption.

## The page kit

Tico draws these tags in its own look, dark and light. Use them for figures, charts, tables and lists on any page;
plain HTML still works, and no page needs them. Tico adds the kit when the page uses a tag, so add no script for it.
Without the kit a tag shows what is inside it, so put the figure inside too.

| Tag | Shows |
|---|---|
| `<tico-stat label value sub trend>` | One figure. `trend="+12%"` draws ▲ 12% |
| `<tico-chart kind="bars" label>` | Bars, or `kind="line"`. Rows in `data="Mon 10, Tue 20"` or as `<li>Mon 10</li>` inside it |
| `<tico-table>` | Wraps a normal `<table>`; figures align right |
| `<tico-list>` | Wraps a `<ul>`: a title per row, marks (`<tico-badge>`, `<small>`, `<time>`) on the right |
| `<tico-badge tone>` | A small mark; `tone="warn"` for late, `tone="strong"` for the one that matters |

    <tico-stat label="Signups" value="1,204" trend="+12%" sub="this week">Signups: 1,204</tico-stat>
    <tico-chart kind="bars" label="Signups by source"><ul><li>Search 640</li><li>Ads 410</li></ul></tico-chart>

**Widgets.** A widget is a page pinned to your sidebar; it shows the page's card view, as a chat card does. In the card
view (`html[data-tico-view=card]`) show one stat or one chart and hide the rest (`[data-tico-view=card] .full
{display:none}`); the card's Expand opens the full page. `examples/widget.html` is one. Pin it at the top with
`tico_pin_add`: place `bot:<your slug>`, item `page:<file id>`, position 0 (arrange-your-sidebar has the rest).

`page.py kit <page>.html` names a tag or attribute the kit does not know; `check` shows the same hints. Neither
fails on them. A widget is not a report: check it with `kit` alone, then publish and pin it (Steps 5 and 6 below).

## Steps

1. Copy the template to `specs/<slug>.html`, `reports/<task>-<page>.html`, `reports/releases/YYYY-MM-DD.N.html` or
   `reports/YYYY-MM-DD-checked-<slug>.html`. Replace every `{{...}}`. Keep its `<style>`: colour only through its
   tokens (`var(--ink)`, `var(--muted)`, `var(--surface)`, `var(--line)`, `var(--accent)`), which follow light and dark.
   A colour of your own needs dark values under `@media (prefers-color-scheme: dark)`.
2. Take the screenshots, set the arrows, draw the charts.
3. Build and check:

       python3 .claude/skills/build-the-page/page.py build <draft>.html --out <page>.html
       python3 .claude/skills/build-the-page/page.py check <page>.html --kind <kind>

   `check` names each rule the page breaks. Fix each and build again. A spec over its usual length (1,200 words, 1,000
   for a change spec) and a bullet over 30 words are warnings: cut what does not help, keep what clarity needs.
4. Cut. Read it once more and remove every word the reader does not need. Most first drafts are twice too long.
5. Publish it on the task: `tico page publish <page>.html --task <id> --title "<title>"`, with `--as Spec` for a spec
   (or the document's name). It prints the file id and anything it removed; anything of yours removed is a fault to
   fix (with `--as Spec`, Tico's own fonts are always listed and are not).
6. Render the published page and look at it:

       python3 .claude/skills/build-the-page/page.py render <file id> --kind <kind>

   Open both pictures it names: the title and its line at the top, the contents, every screenshot drawn, every arrow
   on its target, nothing cut off at 390 px. With the read-only view on, also
   `tico bot app-shot /#/page/<file id> --width 390 --full --out render-390.png`. Fix, publish again and render again
   until it is right.
7. Attach it where `tico task show` names the requirement: `tico task attach <id> page:<file id> --req "<requirement>" --version N`.

## Done

The page has a title, one line saying what it is, contents linking every H2, short sections of one-line bullets,
charts for its numbers and real screenshots for its screens. `check` passes, and you saw it rendered at both widths.
