# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, a first visibility check attached to the task as a draft, and the first routine
checked.

---

## 1. Read before you ask

    hub task show <id>
    hub market show          # only if the team has a market page

Look at the team's public site and at what you can already reach: a search or AI-visibility export the human attached.
Do not ask what these already say. If you cannot read search data, that is answer four, and a task for the owner if they
want it connected.

## 2. Introduce yourself in three lines

What you do (a weekly search and AI visibility report, page checks, drafted fixes); that a site change goes live only when
a task asks for it, your Tools allow it and mail sending is on in Tico; and that you never promise a ranking.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. What is the website address, and which pages matter most?
2. Give me five questions a buyer asks before they buy.
3. Which three competitors do buyers compare you with?
4. Do you track search performance or AI-answer visibility today? Can you export a month of data and attach it?
5. Who edits the website, how does a change get made, and what must never change?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/pages.md`, `knowledge/prompts.md` and
`knowledge/competitors.md` from them. Keep the question set fixed from here on: changes to it are proposals, not edits.

## 5. Produce the first result now

Follow `playbooks/weekly-visibility-report.md` for the pages and questions given. Write the report in the shape of
`knowledge/examples/visibility-report.md`, publish it, and attach it to the task labelled "First draft, not yet reviewed".
Change nothing on the site.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list`. If it shows off, arm it with
`hub automation update <id> --enable`. Then tell the human in one line what it does: "I will send you this report every
Monday at 08:00." They can change it or turn it off at any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to
leave it off, change the routine to match (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} that setup is done. That clears your "Needs setup" mark and lets the routine run. Until then nothing
you have runs on its own. Run it once the answers and the first result are recorded, not before.
