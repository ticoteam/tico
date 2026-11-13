# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 25 minutes. The outcome is five recorded answers, a first weekly partner review on the
task, and the first routine checked.

---

## 1. Read before you ask

    tico task show <id>
    tico doc search "partner agreement"
    tico meeting search "partner"

Check which agreements are in the team docs, whether a CRM is in your access, and which partners
appear in recent calls. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (the partner register, registration checks, partner-sourced deals, new partners, fees owed), that requested decisions and payouts use your Tools, with one rule applied to every partner; messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which partners do you have, of which kind, and where are their agreements?
2. Your rules of engagement: what makes a registration valid, how long it protects, who wins a conflict? (Default: first complete registration wins, 90 days' protection, our seller wins if already engaged.)
3. How are referral fees or margins calculated and paid, and who owns payouts?
4. What makes a good new partner? One you would clone.
5. How do registrations arrive, and who works approved partner deals?

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Write one `knowledge/partners/<partner>.md` per
partner, `knowledge/rules-of-engagement.md`, `knowledge/partner-fit.md`, and start
`knowledge/registrations.md` with any registration still open.

## 5. Produce the first result now

Follow `playbooks/weekly-partner-review.md`. Write `reports/YYYY-MM-DD-partner-review.md`, attach it to the
task and label it "First draft, not yet reviewed". Nothing is decided, sent or paid.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list` (if it shows off,
`tico automation update <id> --enable`) and tell the human in one line what it does: "I will review the partner channel every Thursday at 09:00, with a proposed decision on each registration." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
