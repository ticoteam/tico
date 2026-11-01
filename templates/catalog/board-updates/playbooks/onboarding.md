# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 20 minutes. The outcome is five recorded answers, a real
draft update on the task with any missing figure marked, and the first routine checked.

---

## 1. Read before you ask

    hub goal list --all
    hub update list --kind weekly --limit 6
    hub meeting search --since <first of last month>

Do not ask what these already say. If last month's update is in Docs or the mailbox, read it and copy its metrics and order.

## 2. Introduce yourself in three lines

Say what you do: a monthly investor update draft, and answers to investor questions. Say what you will not do: share anything outside the intended audience and
your Tools, or write a finance figure nobody supplied.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human answers only some, record those and use the defaults for the rest,
saying which you used.

1. Who receives the update (investors, board, both), how many people, and who signs it? (Default: the owner signs; nothing goes out without them.) The draft goes to the owner only.
2. Which five to eight numbers do investors expect every month, and where does each one come from? Paste the last update if you have one. These become `knowledge/metrics.md`, and their definitions stay fixed.
3. Who gives me cash, burn and runway each month, and by which day? (Default: the owner, by the 2nd.) I never estimate a finance figure.
4. What may never be in an update (customer names, unannounced deals, people matters, legal topics), and what is the investors' update cadence in the signed agreement?
5. Which day should the draft land each month, and are you fundraising now? (Default: the 3rd; not fundraising.) During a raise the update changes shape, and I ask before drafting it.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/metrics.md` (each metric, its definition, source and order), `knowledge/exclusions.md` and the recipient list as
present-tense statements. Note the finance figures' owner and due day in `knowledge/finance-inputs.md`.

## 5. Produce the first result now

Follow `playbooks/monthly-investor-update.md` on the real record for the month just ended. Leave any missing finance figure as "not supplied". Write
`reports/YYYY-MM-DD-investor-update.md`, attach it to the task and label it "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off, `hub automation update <id> --enable`) and tell the human in one line:
"I will have a draft ready on the 3rd of every month at 09:00 for you to review and send." They can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to leave it off, adjust `knowledge/` and the routine
to match (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then nothing you have runs on its own. Run it once the
answers and the first result are recorded, not before.
