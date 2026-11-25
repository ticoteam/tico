---
name: Stripe
category: finance
summary: "Payments, customers, subscriptions, invoices and payouts"
commands: [stripe]
---
## What it is for
Looking up customers, charges, subscriptions, invoices, refunds and payouts, and the numbers built on them.

## How to use it
Call the API through Tools: with a proxied key, `tico tool call <tool id> GET /v1/charges --query limit=10`; with a key
on your computer, the `stripe` CLI or `curl` with the variable Tools gives you.

    tico tool call stripe GET /v1/customers --query email=jane@acme.example
    stripe charges list --limit 20
    stripe invoices list --status open --limit 50

## Good to know
- Amounts are in the currency's smallest unit (cents).
- Lists page with `starting_after`; a script that sums them must follow every page.
- Refunds and anything that moves money wait for a person's OK.
