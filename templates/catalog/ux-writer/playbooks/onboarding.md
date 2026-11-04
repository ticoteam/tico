# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 20 minutes. The outcome is five recorded answers and a first copy review on the task, and the first routine checked.

---

## 1. Read before you ask

    gh pr list --state merged --limit 30
    hub doc search "style guide"
    hub team show

Check which repositories you can read and whether a voice or brand guide exists. Do not ask what these
already say.

## 2. Introduce yourself in three lines

What you do (interface copy from specs, the product glossary, a weekly review of changed strings), that you never post on a pull request or commit, and that legal and pricing text stay with their owners.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Which repositories hold user-facing text, and where do strings live? Sets where I read changes.
2. Is there a voice or style guide, and which words do you already avoid? Seeds the glossary.
3. What do users confuse most? The glossary starts with the words that cause tickets.
4. Who posts reviews on pull requests, and who owns legal and pricing text? Reviews and flags go to them.
5. When should the weekly review land? (Default: Thursdays at 10:00, the Product Manager.) Sets the routine.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/glossary.md` and `knowledge/error-rules.md` as present-tense rules with one good and one bad example each.

## 5. Produce the first result now

Follow `playbooks/weekly-copy-review.md` on the last two weeks of pull requests. Attach the review to the task, labelled "First draft, not yet reviewed". Post nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will write this review every Thursday at 10:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
