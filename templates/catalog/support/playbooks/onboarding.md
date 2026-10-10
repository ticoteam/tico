# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 20 minutes. The outcome is seven recorded answers, a draft digest of the queue as it is
now, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub task list
    hub team show

Note what is already here: tickets as tasks, who the owner could be, who owns product. Do not ask
what this already says. Do not test mail or any other connection yet: where support arrives is
question one, and only what the human says it is gets checked, after they answer. Missing access is
a question for the human, not a task.

## 2. Introduce yourself in three lines

What you do (work each ticket to a draft reply, research answers through the Librarian, chase what is
open), that requested replies and ticket actions use your Tools; customer replies stay drafts until a person turns mail sending on in Tico, and docs stay with the Librarian.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Where does support arrive: a support mailbox, mail forwarded to you, a Slack channel, a ticket
   tool? It sets the intake, and you can only triage what you can read.
2. Who owns the support queue, and who covers when they are away?
3. What may a customer be told from the documented policy (refund windows, plan limits, response times),
   and where is it written? You ask the Librarian, which answers from the docs; what the docs do not say
   becomes a marked gap and a task to the Librarian.
4. What must reach a human immediately (refund, legal threat, outage, security, an angry customer),
   and who?
5. How should replies sound? Ask for two replies they were happy with.
6. Who owns fixing product problems?
7. How long should a ticket wait on a customer before you draft a nudge, and how many nudges before you
   stop? (Default 3 days, then 7, then stop.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write the immediate list to
`knowledge/escalation.md`, the voice to `knowledge/voice.md` and the nudge rule to
`knowledge/follow-ups.md`. Do not write standing answers: the Librarian and the docs hold them. Check the
Librarian is reachable with one `hub doc ask` about a refund window; if the answer is "Not in the docs",
that is the first task to the Librarian.

## 5. Work the queue now

Follow `playbooks/daily-support-queue.md` on what is in the queue, in the shape of
`knowledge/examples/support-queue.md`. If the queue is empty or unreadable, say which, and use the
three most recent resolved tickets the human points you to as practice. Attach the digest to the
task, labelled "First draft, not yet reviewed". Reply to nobody.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send you a queue digest with a draft for every ticket every weekday at 09:00 and never reply to anyone myself." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
