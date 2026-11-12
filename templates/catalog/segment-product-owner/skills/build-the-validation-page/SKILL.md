---
name: build-the-validation-page
description: Build the Checked in production page as one visual Tico page - the verdict line first, the version checked, each acceptance criterion with its screenshot or measurement and its result, and what could not be checked - with screenshots of production at phone (390 px) and desktop (1280 px) width and red numbered arrows. Read in Validate, before you write the page.
---

# Build the validation page

The Checked in production page is the proof that the change works where customers use it. You check production
yourself and bring the proof on one page; a person never does the checking. Jev reads the page against the spec, and
where the spec asked for a person's OK, the person reads this page and only says OK or Not validated. So the page has
to stand alone: a reader who never opens the app knows from it what was checked, on which version, and what it showed.

## The four rules

1. **One page.** The verdict, the version, every criterion, every picture and what could not be checked.
2. **Verdict first.** The first line under the title is the result, in plain words, with the count:
   - "Checked on v0.6.0-rc.13: the account row is 40 px tall and not clipped, on phone and desktop. 3 of 3 met."
   - "Not validated on v0.6.0-rc.13: on a phone the account row is still clipped. 2 of 3 met."
   - "Not yet: production runs v0.6.0-rc.8; the change ships in rc.9."
3. **Show, don't tell.** Each criterion carries its proof: a screenshot of production, or a measurement (a size, a
   count, a command and its output, a check's result). A line that only says "works" or "met" is not proof.
   Screens are shown at phone width (390 px) and desktop width (1280 px).
4. **Red numbered arrows.** On each screenshot, a red arrow points at the thing each criterion measures, numbered with
   the criterion's number. Draw them as `skills/build-the-spec-page/SKILL.md` shows ("Drawing the arrows").

## Page order

1. Title: "Checked in production: <the change>", and the date.
2. The verdict line (rule 2).
3. The version line: the version production runs and when you read it (date, time, UTC), the release the change
   shipped in, and how you checked ("read-only view as Product Owner", "the release's code, run with git archive",
   "the usage export of <date>").
4. The criteria, numbered as in the spec. Each on one line: the criterion in short, its result (**Met**, **Not met**
   or **Not checked**) and its proof ("Met: 349 × 40 px", "Met: see 2", "Not met: clipped by 6 px, see 1").
5. The screenshots: phone (390 px) and desktop (1280 px) for each screen a criterion touches, each with its arrows and
   a caption line per number. Where the spec page has a before picture, put this after picture beside it at the same
   width, so the change is visible.
6. Could not check: each criterion or part you could not check, why, and what would check it. If you checked
   everything, write "Could not check: nothing."
7. After launch: each criterion tagged after launch, with who watches it, the measure and the read date from the
   spec's "How we will know".

Nothing else. No instructions to anyone, no question, no "please check". Plain words, no release jargon unless the
version is the point.

## Taking the screenshots

Open production in the read-only view, signed in as yourself:

    tico bot app-shot --help

It names the options: the screen to open, the width (390 for phone, 1280 for desktop) and the file to write. Take
each screen a criterion touches at both widths. The view is read-only: it sees only what you can already read, and
nothing in it writes. A screen only another person can see is checked on your own view or a test account; say which.

A size criterion ("at least 40 px tall") is measured, not guessed: read it from the screenshot at the width you set
(the picture's pixels are the screen's), and write the number.

If the command is refused (the bot's "Can view the app (read only)" setting is off) or is not there yet, check what
you can another way (the release's code, its tests, the pull request's checks, an export) and list the rest under
"Could not check" with "no read-only view of the app". Put "Turn on Can view the app (read only) for this bot" in
the note on the task for the bot's owner. Never ask a person to test it, look at it or send a screenshot.

## A change with no screen

An API, a command, a calculation or a job has no screenshot. Its proof is the call and its output on the version
production runs, quoted on the page ("`subtract(3, 5)` returned -2 on tag v6"), or the check's result with its link.
The page still leads with the verdict and still lists what could not be checked.

## What the page may not do

The same as the spec page: no script, nothing loaded from outside, pictures as `data:` URIs or inline SVG, only Tico's
colour tokens (`var(--fail)` for the arrows), one file under 8 MB, no horizontal scroll at phone width.

## Steps

1. Read the OK'd spec page and its acceptance criteria (`tico task show <id>` lists it under "From earlier stages").
2. Read the version production runs. If it is older than the release that carries the change, stop: the playbook's
   step 2 says how to wait.
3. Check each criterion tagged before release, with a screenshot or a measurement, and write its result.
4. Write `reports/YYYY-MM-DD-checked-<slug>.html` in the page order above.
5. Check it against the four rules: one page; the verdict line first with its count; every criterion has its proof
   and its result; the screenshots are at both widths with numbered red arrows that match the criteria; "Could not
   check" is there.
6. Publish and attach it:

       tico page publish reports/YYYY-MM-DD-checked-<slug>.html --title "Checked in production: <the change>" --task <id>
       tico task attach <id> page:<file id> --req "<requirement>" --version N

## Done

The stage's requirement holds the page, and its verdict line is the first thing a reader sees. Not met is a result,
not a failure of the page: the playbook's step 5 sends the work back.
