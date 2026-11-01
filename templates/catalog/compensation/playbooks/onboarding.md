# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, the compensation philosophy and the band grid, and first bands for one role family or a first offer check, and the first routine checked.

Done when the five answers are in `state.md`, the first result is attached to the task labelled as a first draft, the routine is checked and logged in `memory/decisions.md`, and `hub bot setup-done` has run.

---

## 1. Read before you ask

    hub task show <id>
    hub team show
    hub doc ask "What is our compensation philosophy and level guide?"

Check the roles and levels in the roster and whether a payroll or offer export is attached. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (salary bands with sources, offer and pay change checks against the band, the review pack, pay equity checks), that the owner applies every pay change and you check it, and that person-level pay goes only to the named owners.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. What is your compensation philosophy: pay at the market middle, above it for some roles, one band for all locations or adjusted by location? Sets where the midpoint of each band sits. Bands without a philosophy are arbitrary.
2. What roles and levels do you have? Paste the level guide if there is one. Becomes the band grid: role family by level.
3. Which market data do you trust (a salary survey you buy, published ranges, a benchmark from your payroll provider)? Every band names its source and date. Without one I mark the band provisional.
4. Who owns offers and pay changes, and who may see person-level pay? I share person-level results with those people only. Pay is confidential. Every attachment goes to the owners you name.
5. Which day should the weekly offer and pay change check land? (Default: Wednesdays 11:00.) Sets the routine.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/philosophy.md` and the grid in `knowledge/bands.md` (role families by level, empty where no band exists yet). Never write a named person's pay into the repository.

## 5. Produce the first result now

Follow `playbooks/build-a-salary-band.md` for the first role family, or `playbooks/weekly-pay-check.md` for the offers on the task. Publish and share nothing. Label it "First draft, not yet reviewed" and attach it to the task.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will check every offer and pay change each Wednesday at 11:00 and attach the detail for the owners only." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
