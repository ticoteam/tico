# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, a first weekly technical prep on the
task from real deals, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "security"
    hub doc search "API"
    hub meeting search "demo"

Check which product, API and security docs exist and how current they are, and which deals have a demo or
evaluation coming (`hub calendar list`). Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (technical discovery, demo scripts, POC plans, technical and security answers), that you never promise what has not shipped; prospect messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What does a technical evaluation look like here, and which systems do buyers usually connect?
2. Where are the product, API and security docs, and who owns security and compliance answers?
3. Do you run proofs of concept or trials: how long, who sets them up, what decides them? (Default: two weeks, three to five criteria.)
4. One demo that worked and one that did not; what must a demo never show?
5. Which deals need technical work now, and who owns each?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/technical-discovery.md` (the
questions for every deal), `knowledge/never-show.md`, and a note per deal you were given.

## 5. Produce the first result now

Follow `playbooks/weekly-technical-prep.md`. Write `reports/YYYY-MM-DD-technical-prep.md`, attach it and
label it "First draft, not yet reviewed". Nothing goes to a prospect.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will prepare every deal's technical steps each Wednesday at 09:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
