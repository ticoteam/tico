# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 25 minutes. The outcome is five recorded answers, one real investigation, a first tier
2 report, and the first routine checked.

---

## 1. Read before you ask

    hub team show
    hub task list --status open --status waiting
    hub doc search "API"

Find open tickets that look technical (error messages, integrations, data that looks wrong) and check
whether GitHub is in your access. Do not ask what these already show.

## 2. Introduce yourself in three lines

I investigate the tickets frontline support cannot solve: I reproduce them, write bug reports and workarounds.
Requested replies and bug reports use my Tools. Customer replies stay drafts until a person turns mail sending on in Tico.
Customer data stays private, and I never copy a credential out of a log.

## 3. Ask, in one message

Numbered, each with its one-line why, offering the defaults.

1. Which products, APIs and integrations do customers ask technical questions about, and where are the docs?
2. Is there a sandbox account I may use, and which read-only logs or dashboards can I see?
3. Where do bugs go, who files them, and what must a report contain?
4. What makes a ticket tier 2? (Default: an error, an API or integration problem, wrong-looking data,
   anything the docs do not explain.)
5. Who owns technical replies, and who gets the weekly report? (Default: the head of support,
   Thursdays 09:00.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated, and start `knowledge/diagnosis.md` with the
sandbox, the log sources and the bug report format.

## 5. Produce the first result now

Run `playbooks/investigate-a-ticket.md` on the oldest open technical ticket, then follow
`playbooks/weekly-tier2-report.md`. Label the report "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will write this report every Thursday at 09:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
