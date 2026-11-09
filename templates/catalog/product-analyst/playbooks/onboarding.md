# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers and a first readout (or a tracking gap list) on the task, and the first routine checked.

---

## 1. Read before you ask

    hub db list
    hub goal list --all
    hub task list --status open --status doing

Run `hub db doctor <name>` on each database you can see, and look for a tracking plan with
`hub doc search "tracking"`. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (a weekly usage readout, answers to product questions, experiment readouts), that sharing stays within the intended audience and your Tools, and every number has a source.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Where does product usage data live, and can I read it? Sets the source, or makes the first result a gap list.
2. What counts as active and as activated here? Every funnel and cohort is built on these.
3. Which recent launches should I track, and when did each ship? The first readout covers what people ask about.
4. Are experiments running now, and what metric was each meant to move? Results are judged against the plan.
5. When should the readout land, and who reads it? (Default: Wednesdays at 09:00, the Head of Product.) Sets the routine.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/definitions.md` (each definition as a rule and its query) and `knowledge/launches.md` as present-tense statements. Write `knowledge/company.md` from the answers: what the product is, who uses it, and the scope of this work.

## 5. Produce the first result now

Follow `playbooks/weekly-usage-readout.md` on the real data. Attach the readout to the task, labelled "First draft, not yet reviewed". With no readable data, attach `knowledge/tracking-gaps.md` instead.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will write this readout every Wednesday at 09:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
