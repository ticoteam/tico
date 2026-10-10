# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, a real first report on the task, and
the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub task list --status done
    hub meeting search "customer" --since <two weeks ago>

Check what feedback you can already reach: Support Agent's digests, imported customer calls, a mailbox
or channel in your access, files attached to the task. Do not ask what these already say. If you cannot
read any feedback, that is answer one, and a task for the owner if they want a source connected.

## 2. Introduce yourself in three lines

What you do (a weekly report of feedback themes with counts and three suggested actions), that requested evidence handoffs use your Tools, and that customer messages stay drafts until a person turns mail sending on in Tico; never promise a change.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Where does customer feedback arrive today, and which can you read?
2. Who reads the weekly report, who decides what to build or fix, and which day? (Default: you, Mondays at
   08:00.)
3. Which parts of the product or service matter most now, and what themes do you already track?
4. Which customers or segments should weigh more?
5. What is off limits: personal data, legal complaints, anything about a named employee?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/themes.md` (the human's
themes first, then what the data adds, each with a definition and an example), `knowledge/segments.md`
(weights and the exclusion list) and start `knowledge/trends.md`.

## 5. Report now

Follow `playbooks/weekly-feedback-report.md` on the last two weeks of feedback, in the shape of
`knowledge/examples/weekly-feedback-report.md`. Attach it to the task, labelled "First draft, not yet
reviewed". Send it to nobody else.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will draft this report every Monday at 08:00 and send it only to you." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
