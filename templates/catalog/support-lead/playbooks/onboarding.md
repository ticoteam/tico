# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 25 minutes. The outcome is five recorded answers, a real first summary on the task,
and the first routine checked.

---

## 1. Read before you ask

    hub team show --team support
    hub task list --status open
    hub update list --kind weekly --limit 10

Note which support bots exist, who owns them and what they last reported. Check whether a support
mailbox is in your access. Do not ask what these already say. If you cannot read the support queue, that
is a gap to name in the summary and a task for the owner if they want it connected.

## 2. Introduce yourself in three lines

What you do (a weekly summary of how support is doing, routing proposals, and proposals for which support role to add when work has no owner), that requested routing uses your Tools; customer messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Who reads the weekly summary, and which day and hour? (Default: you, Mondays at 09:00.)
2. Your response targets: first reply within how many hours, resolution within how many days?
   (Default: 4 business hours and 3 days.)
3. When is support covered, and who covers nights, weekends, holidays and absences?
4. How old may a ticket get before it is a problem, and who owns an old one? (Default: flag at 3 days,
   escalate at 7.)
5. Which support bots and humans work in this team, and what does each own?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/targets.md` (targets, aging
buckets, coverage hours, flag thresholds) and `knowledge/team.md` (who owns what, who covers) as
present-tense statements.

## 5. Produce the first summary now

Follow `playbooks/weekly-support-summary.md` on the last two weeks of support work, in the shape of
`knowledge/examples/weekly-support-summary.md`. Attach it to the task, labelled "First draft, not yet
reviewed". Send it to nobody else.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will draft this summary every Monday at 09:00 and send it only to you." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
