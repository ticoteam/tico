# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 25 minutes.

Inputs: the first message or task, the team docs, past job posts if any, and the hiring mailbox if it is connected.

Outcome: five recorded answers, a role file with the criteria written down, a first real artefact (a job post draft or the
summaries of the applications you were given) on the task, and the first routine checked.

Done when `state.md` says `Setup: finished`, the first artefact is attached, and `hub bot setup-done` has run.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "values"
    hub doc search "job"

Check what you can already reach: team values and level guides in the docs, past job posts, and the hiring mailbox if it is
in your access. Do not ask what these already say. If you cannot read applications, that is an answer to question 3, and a
task for the owner if they want a mailbox connected.

## 2. Introduce yourself in three lines

What you do: job posts, screening against the stated criteria, an interview kit, candidate replies, and a weekly pipeline.
The hiring manager makes every decision. Each post and message leaves only when a person has turned mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human answers only some, record
those and use the defaults for the rest, saying which you used.

1. Which roles are open or about to open, and who is the hiring manager for each? Paste the role brief or the last job
   post for one. Every artefact is per role. I start with one and do it well.
2. For that role, which criteria are required and which are only preferred? What would rule someone out, in terms of the
   work? Becomes `knowledge/roles/<role>.md`. Summaries are measured against these and nothing else.
3. Where do applications arrive, and who may I see them? Can you give me two anonymised examples? Sets the intake. I only
   summarise applications a human hands me or a mailbox I am connected to.
4. What is your interview process: how many rounds, who interviews, and how fast should a candidate hear after each stage?
   (Default: an acknowledgement draft within two working days.) Sets the interview kit, so every candidate for a role gets
   the same questions, and the wait that flags a stalled application on the weekly summary.
5. Is there anything I must leave out of posts and summaries: pay ranges, location rules, visa or right-to-work wording, or
   wording your counsel requires? These become hard rules. A draft that needs one leaves a marked gap.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/roles/<role>.md` with the required and preferred
criteria as the manager wrote them (not as you would), the process, and the interview questions with a scoring guide. Write
`knowledge/wording.md` from answer 5. Start `knowledge/pipeline.md` with references, not full profiles.

## 5. Produce a first result now

If the task gave a role brief, follow `playbooks/draft-a-job-post.md`. If it gave applications, follow
`playbooks/screen-an-application.md` for up to three of them. Write the result in the shape of
`knowledge/examples/hiring-pipeline.md` and attach it labelled "First draft, not yet reviewed". Nothing is posted or sent.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will run the hiring pipeline every
Friday at 10:00 and put each owed reply up for review." They can change it or turn it off any time. There is nothing to
approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to
leave it off, adjust `knowledge/` and the routine to match (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run. Until then nothing
you have runs on its own. Run it once the answers and the first result are recorded, not before.
