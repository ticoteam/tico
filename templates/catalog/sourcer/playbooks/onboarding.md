# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 25 minutes.

Inputs: `tico task show <id>`, the open roles on the recruiting board (`tico task list --owner recruiter`),
`tico team show`, and any role file the Recruiter already keeps with written criteria.

Outcome: five answers recorded in `knowledge/`, a search file and a do-not-contact list, a first slate of up to five profiles
for one role, and the first routine checked.

Done when: the answers, the first result and the routine check are recorded, and `tico bot setup-done` has run.

---

## 1. Read before you ask

    tico task show <id>
    tico task list --owner recruiter
    tico team show

Check which roles are open and whether the Recruiter already keeps a role file. Do not ask what those already say.

## 2. Introduce yourself in three lines

What you do (find people who have not applied, check them against the stated criteria, write personal outreach, hand yeses to
the Recruiter). That you never judge a person or collect private details. That every message leaves only when a person has
turned mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human answers only some, record
those and use the defaults for the rest, saying which you used.

1. Which roles should I source for first, and where are their criteria written? If the Recruiter has a role file, I read
   that. Why: every profile is measured against the manager's stated criteria and nothing else.
2. Where do the people you want tend to show up in public: communities, events, open source, portfolios, particular kinds
   of employer? Why: this becomes the search plan per role. Good sources beat broad searches.
3. Who must I never approach: clients' or partners' staff the team has agreed not to hire, people who said no, current
   candidates? Why: this becomes the do-not-contact list, checked before every profile goes on a slate.
4. Who is the sender of outreach, and what can the message honestly say about the role, the pay range and the process? Why:
   outreach that cites the person's own work and states the range gets answered; vague messages do not.
5. How many profiles a week per role do you want, and on which day? (Default: 15, Tuesdays at 09:00.) Why: this sets the
   routine and the volume the hiring manager can review.

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Write `knowledge/searches/<role>.md` for the first role (criteria
with their date and source, and where to look) and `knowledge/do-not-contact.md`. Start `knowledge/contact-log.md` empty.

## 5. Produce the first result now

Follow `playbooks/weekly-sourcing-slate.md` steps 3 to 5 for one role, up to five profiles, each with its first message, and
write `reports/YYYY-MM-DD-sourcing-slate.md`. Contact no one. Label it "First draft, not yet reviewed" and attach it to the task.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list` (if it shows off, run
`tico automation update <id> --enable`), and tell the human in one line what it does: "I will build a slate every Tuesday at 09:00
and put each message up for review." They can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a different schedule or to leave
it off, adjust `knowledge/` and the routine to match (`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then nothing you
have runs on its own. Run it once the answers and the first result are recorded, not before.
