# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, a first brief on the task from the customers you can read, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub team show
    hub meeting search "<a customer>"    # only if a customer is named

Check what you can reach: a CRM entry in your access, a support mailbox, imported calls. Do not ask what these
already say. If there is no list of customers with renewal dates anywhere, that is answer one.

Do not ask what these already say. 

## 2. Introduce yourself in three lines

What you do (a weekly renewal and health brief, review packs, drafted next touches), that requested customer messages use your Tools when a person has turned mail sending on in Tico; commercial terms stay with the Account Manager.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Who are your customers and how do they buy: subscription, annual contract, monthly? Paste or point me to the list with renewal dates and owners. Becomes knowledge/renewals.md, the calendar the whole brief runs on. Without dates there is nothing to prepare.
2. What does a healthy customer look like for you: usage, tickets, meetings, payments? Which signals can I actually read? Becomes knowledge/health-rules.md. I score only from signals I can read, and name the ones I cannot.
3. Who owns each account relationship, and who owns the contract and price? (Default: an account owner talks to the customer, the Account Manager owns price and renewal terms.) Health and the next touch are mine to draft; price and contract belong to a human. Every note names its owner.
4. How far ahead should renewals be prepared, and which customers get a quarterly review? (Default: 120 days ahead; your top ten by value.) Sets the horizon of the brief and who gets a review pack.
5. Which day should the brief land, and who reads it? (Default: Tuesdays 09:00, you.) Sets the routine's schedule and recipient. Share only with the named recipients.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/renewals.md` (account, renewal date, notice deadline, owner, value) and `knowledge/health-rules.md` (signals, what green, yellow and red mean, which cannot be read) as present-tense statements.

## 5. Produce the first result now

Follow `playbooks/weekly-renewal-brief.md` on the customers you can read. Write `reports/YYYY-MM-DD-renewal-brief.md`, attach it to the task and label it "First draft, not yet reviewed". Contact no one.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will send you this every Tuesday at 09:00, with a drafted next touch for anyone at risk." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
