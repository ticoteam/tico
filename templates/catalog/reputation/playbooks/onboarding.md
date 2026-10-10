# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 40 minutes. The outcome is five recorded answers, a first read of every surface on
the task, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub market show          # only if the team has a market page

Search for the team's listings under its name and old names. Do not ask what a search already
answers. Note which surfaces you cannot read; that is part of answer one.

## 2. Introduce yourself in three lines

What you do (read the review listings, keep the ledger, draft one batch of honest replies and
rule-based flags per surface), that requested batches use your Tools when a person has turned mail sending on in Tico; never write, buy or steer a review.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which review sites matter to your buyers, and where are your listings?
2. Have you claimed each listing, and who holds the login?
3. What is your current rating on each, and which review worries you most?
4. Who owns replies and flags, and what tone? Can you paste one reply you liked?
5. Which customers should be invited to review on the software sites, and at what milestone?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Fill `knowledge/surfaces.md` with the
listings, and check each rule against the platform's current policy page, dated.

## 5. Read now

Follow `playbooks/weekly-review-sweep.md` steps 1 to 3 and 5 once: read each surface, fill the ledger, write
the digest in the shape of `knowledge/examples/review-sweep.md` and attach it to the task, labelled
"First draft, not yet reviewed". Draft the first batch per `playbooks/work-queue.md` steps 1 to 3 as a
list for review only; keep this first result a draft and report the checks.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will read your review listings every Monday at 09:00 and bring you one batch per surface ready to act on." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
