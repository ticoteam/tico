# Renewal pack

Triggered by a renewal reaching 60 days, or by a task that names an account and asks for renewal terms or an expansion
quote. Budget 30 minutes.

Inputs: the account plan in `knowledge/accounts/<account>.md`; the current contract (term, seats or units, price, uplift clause,
notice period, auto-renew); usage against the contract; the Customer Success Manager's latest note; and what was promised at
the last renewal, from the owner's thread or the call.

Outcome: one pack, `reports/YYYY-MM-DD-<account>-renewal.md`, with the current position, two or three options, every price
either drawn from the stated terms or marked as a gap, the notice deadline, and a cover note draft. A person can finish it in
ten minutes once the gaps are filled. Nothing is sent.

---

## 1. Read the account

    hub task show <id>

Open the account plan and the contract. Check the notice clause and the uplift clause against `knowledge/renewal-rules.md`
(`skills/read-a-contract-for-renewal-terms/SKILL.md`).

## 2. Write the current position

Five lines: what they have; what it costs today, from the contract; what they use; their health and its date; anything
promised or disputed.

## 3. Shape two or three options

For example: renew as is; renew with the seats they use; a longer term. Each option says what changes, why it fits what the
customer said, and its price. A price, discount or uplift comes from `knowledge/renewal-rules.md` or the requested terms.
Anything else is a marked gap, written `[price: <owner>]`, with the name of the person who sets it.

## 4. Prepare the paperwork

The order form or renewal quote in the team's template, with the gaps marked, and a cover note written with
`skills/write-a-renewal-cover-note/SKILL.md`. Both go into the pack.

## 5. Hand over

Save the pack in `reports/`, publish it with `hub file publish reports/<file>`, and attach it to the task with
`hub task attach <id> <file>`. Send the cover note only when the task asks, a person has turned mail sending on for this bot in
Tico, and a granted Tool allows it. Otherwise it stays a draft on the task. Then:

    hub task update <id> --status done --note "..."

The note holds the options, the gaps, and the notice deadline.
