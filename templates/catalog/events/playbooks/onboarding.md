# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 20 minutes. The outcome is five recorded answers, a first events review on the task,
and the first routine checked.

Inputs: the setup task, the calendar and team read in step 1, and the five questions in step 3.
Done when: `state.md` holds five dated answers; `knowledge/calendar.md` and `knowledge/rules.md` are written, and `knowledge/do-not-email.md` is written or the task says no suppression source is connected; the first review is on the task labelled as a draft; the routine is checked and the decision is logged.

---

## 1. Read before you ask

    hub task show <id>
    hub calendar list
    hub team show

Note events already on the calendar and who in sales takes leads. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (event briefs, the events calendar, invitations and follow-ups prepared, results per
event), that a booking or spend happens only when the task asks for it, within the stated budget, and a granted Tool allows it; messages to outsiders stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which events are booked or being considered in the next six months?
2. What is an event for here, and what would make one worth repeating?
3. Who owns event spending, and what is the budget?
4. Who follows up event leads today, and how fast? (Default: sales within two business days.)
5. Which tools hold registrations, leads and opt-outs?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/calendar.md` (one line per
event) and `knowledge/rules.md` (budget owner and ceiling, follow-up owner and deadline, lead sources). Write
`knowledge/do-not-email.md`, the suppression source for leads: copy the opt-out and objection list from the tool named in
answer 5, with its date. If no tool holds one, say so on the task and ask the owner to name the source before any lead is routed.

## 5. Produce the first result now

Follow `playbooks/weekly-events-review.md`. Write `reports/YYYY-MM-DD-events.md`, attach it to the
task and label it "First draft, not yet reviewed". For the nearest event with no brief, list what the
brief still needs.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will review the events calendar every Thursday at 10:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
