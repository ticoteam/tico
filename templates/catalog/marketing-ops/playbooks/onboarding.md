# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 20 minutes. The outcome is five recorded answers, the first convention and handoff
rules, a first check on the task, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub goal list --all
    hub team show

Check whether CRM read access is in your `bot.yaml` and whether an export is attached. Note the
funnel goals the CEO Bot keeps; you measure against them, not beside them.

## 2. Introduce yourself in three lines

What you do (the tracking convention, tagged links for each campaign, the lead handoff rules and a
weekly data check), that requested system fixes use your Tools and are checked against the source exports.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Where do leads land, and which field says where each came from?
2. Is there a naming convention for campaign links today? May I propose one?
3. What makes a lead ready for sales, who receives it, and how fast should they touch it?
4. Which campaigns are running now, and who owns each?
5. How will analytics and lead exports reach me until read access is connected?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/tracking.md` (propose the
default convention if there is none, marked "proposed"), `knowledge/handoff.md` and
`knowledge/campaigns.md`.

## 5. Produce the first result now

Follow `playbooks/weekly-marketing-data-check.md` on what you can read. Write
`reports/YYYY-MM-DD-marketing-data.md`, attach it to the task and label it "First draft, not yet
reviewed". Record the requested convention in the same message; ask only about missing rules or conflicting facts.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will run this check every Monday at 10:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
