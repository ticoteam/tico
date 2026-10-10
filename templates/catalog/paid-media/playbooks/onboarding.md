# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 20 minutes. The outcome is five recorded answers, a first review on the task from a
real export, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub goal list --all

Check whether an export is attached and whether any ads access is in your `bot.yaml`. Note any
marketing goal that names leads, trials or sales: it tells you which conversion matters. Do not ask
what these already say.

## 2. Introduce yourself in three lines

What you do (a weekly review of every paid campaign and three changes prepared for review), that requested changes stay within the stated budget and your Tools; public copy stays a draft until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which ad platforms do you run, roughly what do you spend a month on each, and who can change the accounts today?
2. What is each campaign for, and what may one result cost? (Default: last 90 days' average.)
3. Where is a conversion tracked, and which conversions count?
4. How should the weekly exports reach me? (Default: a campaign report and a search-terms report for the last 7 days, attached to the task.)
5. What brand terms and budget ceilings apply to campaigns?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/targets.md`: one block per
campaign (purpose, conversion, target cost per result, ceiling, owner), then the never-change rules.

## 5. Produce the first result now

Follow `playbooks/weekly-paid-media-review.md` on the attached export. Write
`reports/YYYY-MM-DD-paid-media.md`, attach it to the task and label it "First draft, not yet
reviewed". Keep this first result a draft and report the checks.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will review every campaign each Monday at 09:00 from the exports you attach." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
