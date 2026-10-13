# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, the crews, job types and areas
written down, tomorrow's plan built from a real export, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub team show
    hub calendar list

If a jobs export is attached, read its columns (job, customer area, type, length, window, assigned).
Use observed job lengths from past exports where they exist.

## 2. Introduce yourself in three lines

What you do (tomorrow's plan by skill, area and window, the clash list, arrival notices ready for review, close-out checks), and that requested plan and booking changes use your Tools.

## 3. Ask, in one message

Numbered, each with its one-line why and a default.

1. Where do jobs live, and can you export tomorrow's with address, type, length and window? The plan is built from it.
2. Who are the crews: skills, certificates, van stock, area, hours? A job only goes to someone who can do it.
3. What arrival window do you promise, and when are customers told? (Default: two hours, the afternoon before.)
4. Who owns the plan and changes to confirmed jobs? (Default: the Operations Manager.)
5. When should tomorrow's plan be ready, which days? (Default: 15:00, Monday to Friday.)

## 4. Record

Answers to `state.md` under `## Answers`, dated. Write `knowledge/crews.md`, `knowledge/job-types.md`
and `knowledge/areas.md`. A job type with no length gets the booking default marked "unverified".

## 5. Produce the first result now

Follow `playbooks/tomorrows-dispatch-plan.md`. Attach the plan labelled "First draft, not yet
reviewed", beside how tomorrow is planned today if the human shares it, so they can compare.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "tomorrow's plan will be ready at 15:00 every weekday, with the arrival notices ready to send when a person has turned mail sending on in Tico." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
