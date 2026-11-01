# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 20 minutes. The outcome is five recorded answers, a first weekly summary on the task from the real pipeline, and the first routine checked.

---

## 1. Read before you ask

    hub team show
    hub task list --status open --status doing --status waiting
    hub update list --kind weekly --limit 6

Check which sales bots exist (`hub team show`) and whether a CRM is in your access. If you cannot read the
pipeline, that is answer two: say so and build the summary from the bots' reports.

Do not ask what these already say. 

## 2. Introduce yourself in three lines

What you do (the weekly pipeline and forecast review, routing, coaching notes, and which sales role to add), that requested routing uses your Tools, and that you never rank individuals or invent deal facts.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Who is on the sales team, humans and bots, and what does each do? Who leads it? Becomes knowledge/team.md and the routing rules. Routing to the wrong owner wastes a week.
2. What are your pipeline stages, in order, and what has to be true to enter each? Where do deals live: a CRM or a spreadsheet? Sets what 'moved' and 'stalled' mean, and where I read the numbers.
3. After how many days without activity is a deal stalled? (Default: 14 days.) Which deals count as big enough to always show? Sets the stalled list and the three to five deals the summary puts first.
4. Which day and hour should the summary land, and who reads it? (Default: Mondays 08:00, you.) Sets the routine's schedule and recipient. Share only with the named recipients.
5. Which new-lead sources should I route (web form, inbound email, referrals, events), and who takes each today? Routing starts from how leads arrive now, so the first proposals are ones you would have made.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/team.md` (humans, bots, what each owns), `knowledge/pipeline-rules.md` (stages, stalled threshold, always-show size) and `knowledge/routing.md` (lead source to owner) as present-tense statements.

## 5. Produce the first result now

Follow `playbooks/weekly-sales-summary.md` on the real record. Write `reports/YYYY-MM-DD-sales-summary.md`, attach it to the task and label it "First draft, not yet reviewed". Assign nothing and change nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will write this every Monday at 08:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
