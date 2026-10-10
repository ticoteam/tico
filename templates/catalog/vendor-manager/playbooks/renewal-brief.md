# Renewal brief

Triggered when a renewal's notice window opens, or by a task that names a vendor and asks "should we
keep it?". Budget 25 minutes. The outcome is a one-page brief with one call (keep, renegotiate or exit)
and, where useful, the message to the vendor ready to use.

---

## 1. Read the contract, not the register

    hub doc search "<vendor>"
    hub doc read <id>

Confirm end date, notice period, how notice must be given (email, letter, portal), price and any
uplift clause, minimum term, and what happens to the team's data on exit. If the terms need a
summary, ask the Contracts Manager: `hub task create --owner legal-review` within the requested work.

## 2. Gather the evidence

- **Use:** seats bought against seats used, or volume ordered against delivered, from the last export
  or the FP&A Analyst's latest spend report.
- **Performance:** issues logged in `knowledge/reviews/<vendor>.md` and tasks naming the vendor in the
  last 12 months.
- **Cost:** this term, next term with the uplift, and one or two alternatives from `procurement`'s past
  comparisons if any exist. Never research new vendors yourself: that is a purchase request.

## 3. Make the call

Keep when it performs and is used; renegotiate when use is under 70 percent, the uplift is above
inflation, or issues repeat; exit when a must-have has failed or an owned tool already covers it. Give
the two or three reasons that decide it.

## 4. Prepare the next step

For renegotiate: the questions to the vendor (seat count, uplift, term). For exit: the notice text in
the form the contract requires, and the offboarding checklist (data export, access removed, final
invoice, the vendor removed from the register). Put either on the task; send requested text to its recipient with your Tools when a person has turned mail sending on in Tico, otherwise keep the draft.

## 5. Hand over

Write `reports/renewal-<vendor>-<date>.md`, publish it, ask the vendor's owner once for the decision,
and record it in `knowledge/vendors.md` when it comes. Finish the task.
