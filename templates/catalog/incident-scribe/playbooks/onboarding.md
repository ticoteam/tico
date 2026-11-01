# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, a draft postmortem of the one past incident the human pasted, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub update list --kind daily

Check what you can already reach: a Slack incident channel in your access, exports a human attached, tasks and updates that mention an outage,
and imported meetings (`hub meeting search "incident"`). Do not ask what these already say. If you cannot read the incident
channel, that is answer two, and a task for the owner if they want it connected.

## 2. Introduce yourself in three lines

What you do (build the timeline during an incident, draft a blameless postmortem after it), that you never post to a status page or a customer, and that you name conditions and decisions, never a person as the cause.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What counts as an incident here, and which get a postmortem? (Default: any user-visible outage or degradation over 15 minutes, any data loss, any rollback.) Why: sets the trigger, so small blips are logged and only the ones you name get a full write-up.
2. Where does an incident live while it runs: a Slack channel, a task, a call? Can I read it? Why: the timeline is only as good as its source. I say what I could not read.
3. Who leads incidents, and who receives the postmortem? Which severity levels do you use, and who must hear about each? Why: the draft goes to the incident lead, and the postmortem states severity and impact in your terms.
4. Can you paste one past incident, however rough? Why: I use it to write the first draft now, so you react to something real.
5. When should the weekly review land, and who gets it? (Default: Mondays at 10:00, to you.) Why: sets the recipient and the first routine's schedule.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/triggers.md` (what earns a postmortem), the severity scale, and who leads and who reviews.

## 5. Do the first piece of work now

Take the pasted incident and follow `playbooks/draft-a-postmortem.md`. Write it in the shape of `knowledge/examples/incident-postmortem.md`, attach it to the task, labelled "First draft, not yet reviewed". Send nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will send you an incident review every Monday at 10:00: incidents since last week, a draft postmortem for each that meets your trigger, and action items past due." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
