# Renewal pack

Triggered by a renewal reaching 60 days, or by a task that names an account and asks for renewal terms
or an expansion quote. Budget 30 minutes. The outcome is one pack ready to use in ten minutes once the stated prices are filled. Every price is a gap; nothing is sent.

---

## 1. Read the account

    hub task show <id>

Open `knowledge/accounts/<account>.md`, the current contract (term, seats or units, price, uplift clause,
notice period, auto-renew), the usage against it, the Customer Success Manager's latest note, and what was
promised at the last renewal (the owner's thread or the call).

## 2. Write the current position

Five lines: what they have, what it costs today (from the contract), what they use, the health status and
its date, and anything promised or disputed.

## 3. Shape two or three options

For example: renew as is; renew with the seats they use; a longer term. Each with what changes and why it
fits what the customer said. Every price, discount and uplift comes from `knowledge/renewal-rules.md` or the request. Mark missing terms and apply only the requested, sourced values with your Tools.

## 4. Prepare the paperwork

The order form or renewal quote in the team's template with gaps marked, and a cover note under 120
words in the owner's voice. Put both on the task.

## 5. Hand over

Write `reports/YYYY-MM-DD-<account>-renewal.md`, `hub file publish` it, attach it. Fill gaps from the agreed terms, then send the final file to the named recipient with your Tools when a person has turned mail sending on in Tico; otherwise keep the draft.
`hub task update <id> --status done --note`: the options, the gaps, the notice deadline.
