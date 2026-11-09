# Renewal brief

Triggered when a renewal's notice window opens, or by a task that names a vendor and asks "should we keep it?". Budget
25 minutes.

Inputs: the contract and its order form (`hub doc search "<vendor>"`, then `hub doc read <id>`),
`knowledge/vendors.md`, `knowledge/reviews/<vendor>.md`, the latest spend report or seat export, and procurement's past
comparisons where they exist.

Outcome: `reports/renewal-<vendor>-<date>.md`: a one-page brief with one call (keep, renegotiate or exit), the two or
three reasons that decide it, the renewal price from `skills/price-a-renewal-increase/SKILL.md`, and, where useful, the
message to the vendor.

Done when: the dates and the price come from the contract, with their pages; the call and its reasons are on the page;
the next step is on the task; the brief is published; and the owner has been asked once for the decision.

---

## 1. Read the contract, not the register

    hub doc search "<vendor>"
    hub doc read <id>

Confirm the end date, the notice period and its unit, how notice must be given (email, letter, portal) and whether it must
be received or only sent, the price and any uplift clause, the minimum term, and what happens to the team's data on exit.
If the terms need a summary, ask the Contracts Manager within the requested work:
`hub task create --owner legal-review`.

## 2. Gather the evidence

- **Use:** seats bought against seats used, or volume ordered against delivered, from the last export or the FP&A
  Analyst's latest spend report.
- **Performance:** issues logged in `knowledge/reviews/<vendor>.md` and tasks naming the vendor in the last 12 months.
- **Cost:** this term, next term with the uplift (`skills/price-a-renewal-increase/SKILL.md`), and one or two
  alternatives from procurement's past comparisons if any exist. Never research new vendors here: that is a purchase
  request.

## 3. Make the call

Keep when the vendor performs and is used. Renegotiate when use is under 70 percent, the uplift is above inflation, or
issues repeat. Exit when a must-have has failed or a tool the team already owns covers it. Give the two or three reasons
that decide it.

## 4. Prepare the next step

For renegotiate: the questions to the vendor (seat count, uplift, term). For exit: the notice text in the form the
contract requires, and the offboarding checklist (data export, access removed, final invoice, vendor removed from the
register). Put either on the task. Sending any of it is a Sending item: it goes only when the task asks for it, a granted
Tool allows it, and a person has turned mail sending on in Tico. Otherwise it stays a draft for the owner.

## 5. Hand over

Write `reports/renewal-<vendor>-<date>.md`, publish it with `hub file publish`, ask the vendor's owner once for the
decision, and record the decision in `knowledge/vendors.md` when it comes. Finish the task.
