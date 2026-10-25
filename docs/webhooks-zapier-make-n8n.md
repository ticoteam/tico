# Recipe: Zapier, Make and n8n, both ways

These three tools work the same way as far as Tico is concerned: each can post to an address, and each can give you an address to
post to. This page connects them in both directions. The background is on [Webhooks](webhooks.md).

## Part 1: send something from Zapier, Make or n8n into Tico

### In Tico

Add an incoming webhook (Settings → Webhooks → Incoming), or:

```
hub trigger create "Zapier orders" --id zapier-orders
hub trigger update zapier-orders --type-from /type
```

Copy the address it shows, `https://tico.example.com/api/v2/hooks/zapier-orders/<secret>`. It is shown once.

These tools do not sign calls, so the secret in the address is what keeps strangers out. Treat the address like a password. If it
leaks, use **New secret** and update the tool.

### In the tool

Post JSON to that address. Put two things in the body:

- a `type`, a short word like `order.paid`, so rules can tell calls apart;
- an id of your own, such as the order number.

```json
{"type": "order.paid", "id": "ord_1001", "total": 42, "customer": "ada@example.com"}
```

- **Zapier**: add a *Webhooks by Zapier* action, event **POST**. Set **URL** to the Tico address, **Payload Type** to `json`, and
  fill **Data** with the fields above.
- **Make**: add an **HTTP → Make a request** module. Set **URL** to the Tico address, **Method** `POST`, **Body type** *Raw*,
  **Content type** *JSON (application/json)*, and the JSON as the request content.
- **n8n**: add an **HTTP Request** node. Set **Method** `POST`, **URL** to the Tico address, turn on **Send Body**, **Body
  Content Type** *JSON*.

If the tool lets you set a header, add `Idempotency-Key` with the order id. Tico then ignores a second call with the same id for
30 days. Without it, Tico treats the same body in the same minute as one call, so two different events must differ in their
body.

Run the Zap, scenario or workflow once. In Tico, **Recent calls** under *Zapier orders* shows the call. The answer the tool gets
is `202` with a small JSON note.

### Start something

Either of these:

```
hub rule set --scope-kind team --event webhook.received \
  --when '{"trigger":"zapier-orders","type":"order.paid"}' \
  --then action \
  --params '{"action":"wake_bot","bot":"ops","text":"Order {{data.id}} paid: {{data.total}} from {{data.customer}}"}'
```

or a routine that opens a task for every call (`hub routine set paid --title "Order paid" --on webhook.zapier-orders --text ...`).

## Part 2: send Tico events to Zapier, Make or n8n

### In the tool

Make the trigger that waits for a call and copy its address.

- **Zapier**: a new Zap with the trigger *Webhooks by Zapier → Catch Hook*. Copy the **Custom Webhook URL**
  (`https://hooks.zapier.com/hooks/catch/...`).
- **Make**: a new scenario with the *Webhooks → Custom webhook* module. Add a webhook and copy its address
  (`https://hook.<region>.make.com/...`).
- **n8n**: a workflow starting with the **Webhook** node, **HTTP Method** `POST`. Copy the **Production URL**
  (`https://n8n.example.com/webhook/<path>`). The *Test URL* works only while you press **Listen for test event**, so use it for
  step 2 below and the Production URL once the workflow is active.

### In Tico

Add an outgoing endpoint (Settings → Webhooks → Outgoing), or:

```
hub webhook create --name "Zapier tasks" --url https://hooks.zapier.com/hooks/catch/000000/example/ --events task.done task.blocked
```

Tico shows a signing secret. These tools do not check it, so you can ignore it. The address still has to be `https` and public.

### Try it

Send a sample before a real event happens, so the tool can learn the fields:

```
hub webhook example <endpoint id> --type task.done
```

The tool gets this body, and shows the nested fields under `data` and `links`:

```json
{"type": "task.done", "version": 1, "timestamp": "2026-10-25T10:00:00Z",
 "data": {"id": "t_1", "title": "Example"},
 "links": {"app": "https://tico.example.com/#/task/t_1", "api": "https://tico.example.com/api/v2/tasks/t_1"}}
```

Build the rest of the Zap, scenario or workflow from that. Then turn it on. When a task finishes, Tico posts the event; the
**Outgoing** tab shows each send and its state, and `hub webhook sends <endpoint id>` lists them.

A real event can have a different shape than the sample. Check one real send, with `hub webhook sends <endpoint id> --send <id>`,
before you rely on a field.

If the tool is down or answers with an error, Tico tries again after 5 seconds, 5 minutes, 30 minutes, and on up to about 28
hours. See [Webhooks](webhooks.md#answering-and-retries). Delivery is at least once, so make the Zap safe to run twice: the
`webhook-id` header is the same on every try.

### Send an event of your own

To post something that is not one of Tico's events, use `custom.`:

```
hub webhook send <endpoint id> --type custom.deploy-done --data '{"app":"web"}'
```

The endpoint must list `custom.*`. A rule can do the same with `send_webhook`.
