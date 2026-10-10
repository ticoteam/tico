# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, a first developer pulse on the task
from the last two weeks of public questions, and the first routine checked.

---

## 1. Read before you ask

    hub team show
    hub doc search "API quickstart"

Find the product's public developer docs and any repository already in your GitHub list. Do not ask what
these already say.

## 2. Introduce yourself in three lines

What you do (answer developers' public questions, write samples and tutorials, keep the friction log),
that public messages stay drafts until a person turns mail sending on in Tico, and that you never promise a
feature, a date or a price.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Who are the developers you serve, and what is the first thing they try to do with the product? Why: sets the reader and the path the friction log follows.
2. Where do developers ask questions today? Links, please. Why: becomes the channel map I sweep each week.
3. Which repositories hold the SDKs and examples, and which checks apply to merges? Why: requested changes use those repositories, checks and Tools.
4. What may never be said in public? Why: becomes the do-not-say list.
5. Which day and hour should the pulse land, and who reads it? (Default: Thursdays at 10:00, you.) Why: sets the routine.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/channels.md` and
`knowledge/do-not-say.md` as present-tense statements.

## 5. Produce the first result now

Follow `playbooks/weekly-developer-pulse.md` over the last two weeks. Write
`reports/YYYY-MM-DD-developer-pulse.md`, attach it to the task and label it "First draft, not yet
reviewed". Post nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will write the pulse every Thursday at 10:00 with an answer ready for each question, with public posts kept as drafts until a person turns mail sending on in Tico." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
