# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 25 minutes. The outcome is six recorded answers, a first weekly deal review on the task
from the real deals, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub calendar list
    hub meeting search "<a deal the task names>"

Check what you can already reach: a CRM entry in your access, the seller's mailbox, imported calls, the
team docs (`hub doc search "proposal"`, `hub doc search "security"`). Do not ask what these already
say. If you cannot read the deals, that is answer two, and a task for the owner if they want the CRM
connected. Never work around it.

## 2. Introduce yourself in three lines

What you do (work open deals to signature: recaps, next steps, action plans, proposals and answers), that
everything leaving the team goes out when a person has turned mail sending on in Tico, and that prices and terms stay theirs.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What do you sell, to whom, and how does a deal happen from first meeting to signature? Which stages?
2. Which open deals first, who owns each, and where do they live?
3. Who sends recaps and proposals, from which address? One recap and one proposal that worked.
4. Where are the price sheet, documented security and legal answers, and citable case studies? Who owns each?
5. What must never appear in writing (discounts, competitors, customers, unreleased features, dates)?
6. After how many quiet days is a deal at risk, and when should the review land? (Default: 10 days, Mondays 09:00.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/sales-process.md`,
`knowledge/voice.md`, `knowledge/never-say.md`, `knowledge/proposal-structure.md` from the proposal they
pasted, `knowledge/proof.md` and the first `knowledge/library/` entries with owner and date. Start one
`knowledge/deals/<deal>.md` per deal you were given.

## 5. Produce the first result now

Follow `playbooks/weekly-deal-review.md` on those deals. Write `reports/YYYY-MM-DD-deal-review.md`, attach
it to the task and label it "First draft, not yet reviewed". Nothing is sent and the CRM is untouched.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will review every open deal each Monday at 09:00 and have the follow-ups ready for review." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
