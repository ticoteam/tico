# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, a snapshot of the first one or two conversations you can read, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub meeting search --since YYYY-MM-DD
    hub doc search "interview"

Check what you can already reach: imported calls, research notes in the team docs, and the market graph (`hub market show`).
Do not ask what these already say. If there is nothing to read, that is answer two, and the first result is a plan for what to collect.

## 2. Introduce yourself in three lines

What you do (turn interviews, calls and feedback into snapshots, opportunities and research briefs), that you never contact a user or decide the roadmap, and that you say how many sources support each finding.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What outcome is the product team working toward this quarter, in one sentence? Why: Research is grouped under an outcome. Without one, I collect stories that answer nothing.
2. Where do interviews and user conversations live, and can I read them? What may I never store or quote: names, organizations, anything from a private meeting? Why: Sets the sources and the privacy rules. I say what I could not read instead of guessing, and quotes stay anonymised until you say otherwise.
3. Who are the users you most need to understand, and which decision is open right now? Why: The first brief is written for that decision, for that human.
4. Which competitors matter, and which of their features do people compare you to? Why: Sets the first comparison. Competitor facts go to the market graph, not to a file here.
5. When should the weekly research digest land, and who gets it? (Default: Thursdays at 10:00, to you.) Why: Sets the recipient and the first routine's schedule.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write the outcome and the open decision at the top of `knowledge/opportunities.md` and the never-quote rules into `knowledge/privacy.md`.

## 5. Do the first piece of work now

Take the first one or two conversations you can read and follow `playbooks/write-an-interview-snapshot.md`. Write the digest in the shape of `knowledge/examples/research-digest.md` to `reports/`, attach it to the task, labelled "First draft, not yet reviewed". Contact no one.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will send you a research digest every Thursday at 10:00 with new snapshots and the opportunities they support, and I will contact no one." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
