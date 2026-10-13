# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 20 minutes. The outcome is five recorded answers, a first digest on the task from
the real community, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub team show

Check whether chat access to a community workspace is in your `bot.yaml`, and whether the
team's public site links to a forum. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (a weekly community digest, sourced replies to waiting questions, champions and feedback
routed), that requested replies and moderation use your Tools, with public messages kept as drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Where does your community live, and roughly how many active members does it have?
2. What is it for, and which purpose matters most?
3. How fast should a question get a first answer? (Default: one business day.) Who may answer publicly?
4. What are the rules, and who handles a post that breaks them?
5. Who should hear product feedback and bug reports from the community?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/community.md` (where,
purpose, rules, response target, who answers, who moderates, where feedback goes). Start
`knowledge/champions.md` empty with its column headings.

## 5. Produce the first result now

Follow `playbooks/weekly-community-digest.md` over the last 14 days. Write
`reports/YYYY-MM-DD-community.md`, attach it to the task and label it "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will write this digest every Friday at 11:00 and prepare replies for the waiting questions." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
