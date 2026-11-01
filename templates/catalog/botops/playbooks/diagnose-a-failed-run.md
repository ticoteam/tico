# Diagnose a failed run

Triggered by a task that says a bot did not run, ran and produced nothing, or produced the wrong
thing. Budget 30 minutes. The outcome is one sentence naming the cause, the evidence for it, and
either the smallest fix or a clear statement of what a human has to do.

Inputs: the task that reports the failed run, and the slug of the bot it names.

Done when: the one-sentence cause and its evidence are on the task, the smallest fix is made or the human's step is named, and what is still unverified is said.

---

## 1. Establish what actually happened

    hub task show <id>
    hub task list --owner <slug>
    hub bot status list
    hub automation list --owner bot:<slug>     # the routine, if the task came from one
    hub automation runs <id>                   # its runs: fired, failed or held

Read the failed run's own task and its conversation before anything else. Most reports of "it did
not run" are one of four things, and they look nothing alike once you have the record:

| What you see | What it usually is |
|---|---|
| No task exists for the occurrence | The routine is off or was never created, its trigger is invalid, or the last one was left unfinished and absorbed it |
| A task exists and is still `open` | Nothing claimed it: the computer, the runtime, or the readiness check |
| A task ran and ended early | The run hit its limit, or an instruction told it to stop |
| A task ran and did the wrong thing | The instructions, not the machinery |

Never write that a run happened if it is not in the record. Absence of a record is the finding.

## 2. Check the bot itself

    hub bot check <slug>

This tells you whether the repository is in a state Tico will accept: instructions present
and not the untouched template, a non-empty `## Owns`, `state.md` present, `bot.yaml` parsing
with the right name, schedules Tico would accept, declared credentials resolving, and the
runtime available. Read every failure line literally. A failing check explains most "it did not
run" reports on its own.

## 3. Read the instructions the run actually had

Open the bot's `AGENT.md` and the playbook the task named, and read them as the bot did: in full,
with no other context. Look for the line that produced the behaviour. Three patterns cover most of
it:

- a rule that says two things and the bot chose the other one;
- a dated exception stacked at the top that buries the current rule;
- an instruction to use something the bot has no access to, so the run stops politely and says
  nothing useful.

## 4. Fix the smallest thing

In this order, and stop at the first one that works:

1. **A sentence in the bot's instructions or its playbook.** Most failures are the bot not knowing
   something.
2. **A check in that bot's `software/`.** Only when knowing is not enough and doing is the fix.
3. **A change the owner has to make**: a credential on the computer, a runtime sign in, a setting on
   the server, a decision about what the bot should do instead. That is a task for a human, with
   the exact line of evidence.

Never rewrite a bot's instructions wholesale to fix one failure. Never change a bot the task did not
name. Never turn a bot off, change its status, or edit the product checkout.

## 5. Record it and finish

    hub bot check <slug>
    hub task update <id> --status done --note "..."

Commit the repository you changed with a one line message. Add the check that would have caught this
to `knowledge/checks.md`, and add the failure and its cause to `knowledge/fleet.md` under that bot,
so the next run starts from what you already learned. The note names the cause, quotes the one or
two lines of evidence, says what you changed, and says what is still unverified: you cannot prove a
routine works until the next occurrence fires.

## When you cannot tell

Say so. A guessed cause with a confident fix costs the owner more than an honest "the record shows
the task was never claimed, and the computer reports nothing for that hour; this needs someone at the
computer". Put that on the task and finish it.

## Evidence for the task
Publish the write-up a human should read instead of pasting it into the note:
`hub file publish reports/<date>-<bot>-diagnosis.md`, and put the file's title in the task note. A
Google Doc you made for it goes on the page with `hub file link <url> --title "..."`.
