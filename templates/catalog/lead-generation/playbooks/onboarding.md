# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, a first review attached to the task as a draft, and the first routine checked.

---

## 1. Read before you ask

    tico task show <id>
    tico market show          # only if the team has a market page

Look at the team's public site and at any lead export a person has attached. Do not ask what these already say. If you
cannot read lead data, that is answer one, and a task for the owner if they want it connected.

## 2. Introduce yourself in three lines

What you do (a weekly count of qualified leads by source, drafts for forms and lead magnets, sourced target lists); that you send no outreach and propose no send to a list, and nothing is published or bought unless a task asks for it and a person approves it; and that you count a lead as
qualified only on the definition Sales has agreed.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Which sources bring leads today, and roughly how many arrive each week from each one?
2. What makes a lead qualified here, and what does Sales do with one: accept, reject or send back?
3. Which buyers do you want more of: role, company size and place, and two customers you would clone?
4. Where do forms and lead magnets live, who loads a change, and what must never be said on them?
5. What did lead sources cost last quarter, and what budget can I plan against this quarter? (Default: none.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/sources.md` from answer 1 and
`knowledge/qualified.md` from answer 2. Ask Sales to confirm `knowledge/qualified.md` in the task before the first count
is called final, and record the confirmation with its date. Write the first `knowledge/lists/` file from answer 3 only when
a public source is named; otherwise leave the list for a later task.

## 5. Produce the first result now

Follow `playbooks/weekly-lead-generation-review.md` for the data attached. Write the review in the shape of
`knowledge/examples/lead-review.md`, publish it, and attach it to the task labelled "First draft, not yet reviewed".
Send nothing, load nothing and buy nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list`. If it shows off, arm it with
`tico automation update <id> --enable`. Then tell the human in one line what it does: "I will send you this review every
Monday at 08:30." They can change it or turn it off at any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to
leave it off, change the routine to match (`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} that setup is done. That clears your "Needs setup" mark and lets the routine run. Until then nothing
you have runs on its own. Run it once the answers and the first result are recorded, not before.
