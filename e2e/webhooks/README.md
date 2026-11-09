# Webhooks end-to-end

`backend/tests/test_e2e_webhooks.py`, over real HTTP:

- **Inbound**: a Zapier-shaped JSON call (`type`, `id`, fields; an `Idempotency-Key` header) posted to the address a
  trigger gives. The recipe is the one in `docs/webhooks-zapier-make-n8n.md`: a `webhook.type` filter and a `wake_bot`
  action. A matching call wakes the bot with the payload as quoted data; a call of another type does not start a run.
- **Outbound**: a `task.done` event goes to a receiver on `127.0.0.1` through the real delivery worker. The receiver checks
  the Standard Webhooks signature with an independent verifier (`verify` in `test_webhooks.py`, written from the spec).

Plain-http delivery is allowed only to this machine (`allow_http`), so the receiver is on a loopback address.

## Run

    python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_webhooks.py

No data files: the payloads and the recipe are in the test.
