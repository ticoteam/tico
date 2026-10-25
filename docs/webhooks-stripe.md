# Recipe: Stripe events start a routine

You will have Tico start a routine, and so a task for a bot, when Stripe sends the events you choose, for example a failed
invoice payment. The background is on [Webhooks](webhooks.md).

You need: a Stripe account where you can add webhook endpoints, a bot for the routine (this page uses `billing`), and a server
Stripe can reach. If yours is a laptop or sits behind a firewall, use an
[address through Tico Gateway](webhooks.md#through-tico-gateway) in step 1.

Stripe has separate test and live modes, each with its own endpoints and signing secrets. Do this once in test mode first, then
again in live mode with a second Tico webhook.

## 1. Make the incoming webhook in Tico

In Settings → Webhooks → Incoming, add a webhook named *Stripe* with the id `stripe`. Or:

```
hub trigger create Stripe --id stripe
```

Copy the address, `https://tico.example.com/api/v2/hooks/stripe/<secret>`. It is shown once.

Stripe sends names, emails and amounts. Keep the calls for less time than the 7-day default:

```
hub trigger update stripe --keep-days 2
```

## 2. Add the endpoint in Stripe

In the Stripe Dashboard: Developers → Webhooks → Add endpoint.

- **Endpoint URL**: the address from step 1.
- **Events to send**: pick only the events you want to act on, such as `invoice.payment_failed` and
  `checkout.session.completed`. Tico's routine takes every call it gets, so this list is your filter.

When you save, Stripe shows a **Signing secret** that starts `whsec_`. Reveal it and copy it.

## 3. Turn on signature checking

```
hub trigger sign stripe --preset stripe --secret whsec_your_stripe_signing_secret
```

Paste Stripe's secret as it is. Tico now checks the `Stripe-Signature` header on every call and refuses one that is wrong, or
whose time is more than 5 minutes off, with 401. The type of each call is read from the body's `type`, for example
`invoice.payment_failed`.

## 4. Make the routine

A routine can be set to run when a webhook is called. In the bot's Routines card, or Settings → Rules → New routine, choose
*Runs: On an event* and pick *Stripe*. Or:

```
hub routine set stripe-events --bot billing --title "Stripe event" --on webhook.stripe \
  --text "Stripe sent an event. Read the type and the data. For a failed payment, find the customer and draft a note to them. Do nothing for anything else."
```

Each call opens one task for `billing`: the routine's text, the webhook's name and the call's `type:`, then the body, fenced as
data the bot did not write. A routine has no filter on type. If a type needs different handling, choose which events Stripe
sends (step 2), or use a rule below.

## 5. Try it

In Stripe, open the endpoint and use **Send test webhook**, or make a test payment. In Tico, **Recent calls** under *Stripe*
shows the call with its type, and a task opens for `billing`.

If **Recent calls** shows nothing, Stripe could not reach Tico: look at the endpoint's attempts in Stripe. If it shows a refused
call, the signing secret is wrong; run step 3 again. Stripe retries a failed delivery for days, so a call that was refused can
arrive again once the secret is fixed. Use **Run again** in Tico for any kept call you want to process once more.

## Duplicates

Stripe does not name its deliveries in a header, so Tico uses the event id in the body (`id`, like `evt_...`). When Stripe sends
the same event again, even days later, nothing new starts.

## Act on one type without a task

For a type that needs a quick nudge, not a task, use a rule on the same webhook. This wakes `billing` for a failed payment and
for nothing else:

```
hub rule set --scope-kind team --event webhook.received \
  --when '{"trigger":"stripe","type":"invoice.payment_failed"}' \
  --then action \
  --params '{"action":"wake_bot","bot":"billing","text":"Payment failed: {{data.data.object.customer_email}} ({{data.data.object.amount_due}})"}'
```

A routine and a rule on the same webhook both run. Use one or the other for a given type.
