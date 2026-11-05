# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 25 minutes. The outcome is five recorded answers, the first report on the task from a real read of the CRM, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>

Check that a CRM is in your access and that you can read it. If you cannot, that is answer one: file a task
for the owner to connect it read only, say that no report can be built without it, and stop. Never work
around it, and never ask for a write credential.

Do not ask what these already say. 

## 2. Introduce yourself in three lines

What you do (a weekly pipeline report and a list of exceptions with proposed fixes), that requested CRM changes use your Tools and are verified against the source records.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Which CRM do you use, and can I have a read-only credential? Which pipeline, and which stages in order? (Why: this sets what I read and how the report groups it.)
2. What must be true of an open deal to enter each stage, what must every open deal carry, and after how many quiet days or close-date pushes is a deal flagged? (Defaults: stage, amount, close date, owner and a dated next step; 14 days; 3 pushes. Why: the stage standard and the two thresholds are what the report checks against.)
3. Who owns each fix (each seller for their own deals, one human for duplicates), and who reads the report? (Default: the owner reads it; sellers own their deals. Why: every exception needs a named owner. I list them; I do not chase them.)
4. Do deals carry a forecast category (commit, best case, pipeline) or probabilities, and what must be true for a deal to be commit? (Why: without categories the report counts and sums, and says so.)
5. How are new leads assigned today: by territory, segment, round robin, or whoever sees it first? Who covers when someone is out? (Why: it becomes knowledge/routing-rules.md, and I flag any lead no rule covers.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/stages.md` (stages, entry criteria, typical days) and `knowledge/hygiene-rules.md` (required fields, the two thresholds, what a duplicate is), `knowledge/forecast-rules.md` and `knowledge/routing-rules.md` as present-tense statements.

## 5. Produce the first result now

Follow `playbooks/weekly-crm-report.md` on the real CRM. Write `reports/YYYY-MM-DD-crm-report.md`, attach it to the task and label it "First draft, not yet reviewed". Change nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will send you this report every Monday at 06:30, read only, with the fixes listed for the owners." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
