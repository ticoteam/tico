# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 30 minutes. The outcome is five recorded answers, a first real visibility check on
the task and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub market show          # only if the team has a market page

Look at the team's public site and what you can reach: search or AI-visibility
exports the human attached. Do not ask what these already say. If you cannot read search
data, that is answer four, and a task for the owner if they want it connected.

## 2. Introduce yourself in three lines

What you do (a weekly search and AI visibility report, page audits, drafted fixes), that requested site changes use your Tools when a person has turned mail sending on in Tico, and that you never promise a ranking.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What is the website address, and which pages matter most?
2. Give me five questions a buyer asks before they buy.
3. Which three competitors do buyers compare you with?
4. Do you track search performance or AI-answer visibility today? Can you export a month of data and attach it?
5. Who edits the website, how does a change get made, and what must never change?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/pages.md`,
`knowledge/prompts.md` and `knowledge/competitors.md`.

## 5. Check now

Follow `playbooks/weekly-visibility-report.md` for the pages and questions given. Write the report in
the shape of `knowledge/examples/visibility-report.md`, attach it to the task, labelled "First draft,
not yet reviewed". Change nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send you this report every Monday at 08:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
