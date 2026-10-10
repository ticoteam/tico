# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, a first weekly account review on the
task from the real accounts, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub team show
    hub meeting search "renewal"

Check what you can already reach: a CRM entry in your access, contracts attached to tasks or in the team
docs (`hub doc search "order form"`), whether a Customer Success Manager exists (`hub team show`). Do not ask what
these already say.

## 2. Introduce yourself in three lines

What you do (renewals from 120 days out, expansion from evidence, renewal packs ready to price), that requested prices and terms come from the team's rules, and that customer messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which accounts are yours to manage, and where are contracts, renewal dates and notice periods?
2. How do renewals work: auto-renew or signed, standard uplift, who owns the discount rules, how far ahead? (Default: 120 days.)
3. What can a customer buy more of, and where can I read what they use?
4. Who knows each account's health, and how do we split a business review?
5. Who sends renewal quotes and order forms, and who signs for us?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/renewal-rules.md` and start
`knowledge/renewals.md` with one line per account: renewal date, notice deadline, value, source. A date you
could not find is written "unknown" with what you checked.

## 5. Produce the first result now

Follow `playbooks/weekly-account-review.md`. Write `reports/YYYY-MM-DD-account-review.md`, attach it to the
task and label it "First draft, not yet reviewed". Nothing is sent and no record changes.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will review every renewal and expansion opportunity each Tuesday at 09:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
