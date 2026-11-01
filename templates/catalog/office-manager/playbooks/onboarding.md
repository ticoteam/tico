# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 20 minutes. The outcome is five recorded answers, the office, supplies and fixer
files written, a first weekly office page, and the first routine checked.

Done when: all five answers are in `state.md` under `## Answers`, the four `knowledge/` files exist with their par levels
marked, the first page is attached and labelled, the routine is checked and logged in `memory/decisions.md`, and
`hub bot setup-done` has run.

---

## 1. Read before you ask

    hub task show <id>
    hub task list --status open
    hub calendar list
    hub doc search "office"

Open tasks that mention the office, a printer, a key or supplies are your first requests; add them to
`knowledge/requests.md` with their original dates.

## 2. Introduce yourself in three lines

What you do (office requests to a fix, supplies above par, visitors expected), that you place an order only when asked, within the stated budget and a granted Tool, and that you never hand out access.

## 3. Ask, in one message

Numbered, each with its one-line why and a default.

1. Where is the office and what does it have? Who is the landlord or building manager? Becomes the office file.
2. Which supplies run out most often, where do you buy them, how much a month? Sets par levels.
3. Who owns office spending, and up to what per order? (Default: the Operations Manager, 300.)
4. Which contractors fix what, and how are they contacted? Becomes the fixer list.
5. How should visitors be handled, and when should the weekly page land? (Default: Mondays 08:00, to you.)

## 4. Record

Answers to `state.md` under `## Answers`, dated. Write `knowledge/office.md`, `knowledge/supplies.md`,
`knowledge/fixers.md` and `knowledge/visitors.md`. A supply with no usage figure gets a par level
marked "estimate, check after a month".

## 5. Produce the first result now

Follow `playbooks/weekly-office-page.md`. Attach the page labelled "First draft, not yet reviewed".
If a supply is already below par, the order is on the page, not placed.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will put this page in front of you every Monday at 08:00 and keep requests moving in between." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
