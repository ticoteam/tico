# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 25 minutes. The outcome is five recorded answers, a first weekly summary on the
task, and the first routine checked.

Inputs: the setup task, `hub team show`, `hub task list --all`, and the five questions in step 3.
Done when: `state.md` holds five dated answers; `knowledge/workstreams.md`, `knowledge/calendar.md` and `knowledge/routing.md` are written; the first draft summary is on the task, labelled as a draft; the routine is checked and logged in `memory/decisions.md`; `hub bot setup-done` has run.

---

## 1. Read before you ask

    hub task show <id>
    hub team show
    hub task list --all

See which marketing bots and humans exist and what they have reported lately. Do not ask what
these already answer. If there are no marketing bots yet, the first draft is a short plan for which
workstream to start with and why, not a summary of nothing.

## 2. Introduce yourself in three lines

What you do (a weekly marketing summary, a six week calendar, routing proposals), that routing happens only when the task asks for it and a granted Tool allows it, and that public messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Who reads the weekly summary, which day and hour? (Default: you, Fridays at 14:00.)
2. Which workstreams should it report on and who owns each?
3. What are the two or three marketing numbers you look at weekly, and where do they live?
4. What is on the calendar for the next six weeks?
5. When a new marketing request arrives, who decides where it goes? Which routing rules should I use?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/workstreams.md` (workstream,
owner, where its status shows), `knowledge/calendar.md` and `knowledge/routing.md` as present-tense
statements.

## 5. Draft the first summary now

Follow `playbooks/weekly-marketing-summary.md`. Write it in the shape of
`knowledge/examples/marketing-week.md`, attach it to the task, labelled "First draft, not yet
reviewed". Nothing is shared and no task is created for anyone.

## 6. Check the routines

Setting you up switched your first routine on. List your automations with
`hub automation list --owner bot:marketing-lead`. If `weekly-marketing-summary` shows off, turn it on with
`hub automation update <id> --enable`. Then turn on the two growth routines the same way: `weekly-growth-meeting` and
`monthly-growth-report`, each by its id from that list.

Tell the human in one line what they do: "I will draft this summary every Friday at 14:00 for you to review, run the
growth meeting every Monday at 08:00, and file the growth report on the 3rd of each month." They can change or turn off
any of them any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to
leave one off, adjust `knowledge/` and the routine to match (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
