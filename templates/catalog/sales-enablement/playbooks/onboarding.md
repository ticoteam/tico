# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, first win/loss notes on the task from
real closed deals, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub team show --team sales
    hub meeting search "pricing"

Check how many sales calls are imported and from when, whether a CRM is in your access, and which sales
roles already report (`hub update list --kind weekly`). Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (win/loss notes, talk tracks and objection answers, ramp plans), that you coach the work and never grade a human; buyer messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Who is on the sales team, who joined in the last six months, who starts next?
2. Which deals closed in the last 90 days, and where are their calls and notes?
3. Which objections come up most, and who handles each best?
4. Do you, or will you, talk to buyers after a decision? Who would?
5. What must a new seller know by day 30, 60 and 90? (Default: product and ICP by 30, running discovery by 60, a first deal by 90.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Seed `knowledge/objections.md` and
`knowledge/talk-tracks.md` with what the team named, marked "not yet confirmed by a call", write
`knowledge/interview-guide.md`, and one `knowledge/ramp/<seller>.md` per new seller.

## 5. Produce the first result now

Follow `playbooks/weekly-win-loss.md` over the last 90 days instead of one week. Write
`reports/YYYY-MM-DD-win-loss.md`, attach it and label it "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will write win/loss notes every Friday at 10:00 from the week's closed deals." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
