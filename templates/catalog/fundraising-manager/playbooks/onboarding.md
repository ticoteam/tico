# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, the `knowledge/` files written, a first result built from a real export, and the
first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub team show

Read anything attached before asking for it again.

## 2. Introduce yourself in three lines

What you do, what you will not do, and that requested work uses your Tools. Say that every investor message is a draft
until a person approves it.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Are you raising now? If so, how much, what kind of round, and by when do you want it closed? Sets the raise tracker's
   target and the pace of the follow-ups.
2. Where is the investor list today, and can you export it with name, firm, stage, last contact and who introduced them?
   Becomes `knowledge/pipeline.md`.
3. What documents should the data room hold, where do they live, and who owns each? Starts the data room checklist so
   gaps show before an investor asks.
4. Who talks to investors, who approves what is sent to them, and who signs off a term sheet? Every investor message
   waits for that person's yes on it, or they send it themselves.
5. What day and time suits a weekly pipeline review? (Default: Monday 09:00.) Sets the routine's schedule.

## 4. Record

Answers to `state.md` under `## Answers`, dated. Write the `knowledge/` files listed in `AGENT.md`. Anything nobody
answered goes in `knowledge/open-questions.md`, not guessed.

## 5. Produce the first result now

Follow `playbooks/weekly-raise-review.md`. Attach it labelled "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list`. If it shows off, arm it with
`hub automation update <id> --enable`. Tell the human in one line what it does: "I will review the raise every Monday at
09:00 team time." They can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to
leave it off, adjust the routine (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run. Until then nothing
you have runs on its own. Run it once the answers and the first result are recorded, not before.
