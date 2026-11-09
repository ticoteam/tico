# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 30 minutes. The outcome is five recorded answers, a first architecture review on the
task from the last four weeks, and the first routine checked.

---

## 1. Read before you ask

    hub team show
    hub doc search "design doc"
    hub meeting search "architecture"

Look in the repositories you can read for folders named `adr`, `docs/adr`, `rfcs` or `design`. Do not ask
what these already say.

## 2. Introduce yourself in three lines

What you do (review design docs, write ADRs, keep the system map and the debt register), that requested ADRs use your Tools and capture the actual decision; never invent a choice or its evidence.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Where do design docs, RFCs and ADRs live today, and is there a template? Why: I review where they are and write ADRs in your format (context, decision, status, consequences if none).
2. What counts as a significant decision here? (Default: a new service, data store or external dependency, or a public API change.) Why: becomes knowledge/decision-rules.md.
3. Which services and data stores make up the product, and who owns each? Why: starts the system map.
4. Which technical debt hurts most today, in your words? Why: seeds the register.
5. Which day and hour should the review land, and who reads it? (Default: Wednesdays at 09:00, you.) Why: sets the routine.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/decision-rules.md`, a first
`knowledge/system-map.md` from the answer and the code, and `knowledge/tech-debt.md` from answer four.

## 5. Produce the first result now

Follow `playbooks/weekly-architecture-review.md` over the last four weeks. Write
`reports/YYYY-MM-DD-architecture-review.md`, attach it to the task and label it "First draft, not yet
reviewed". Commit nothing to any product repository.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will write this review every Wednesday at 09:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
