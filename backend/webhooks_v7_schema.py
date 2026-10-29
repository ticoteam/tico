"""One column for how an outgoing endpoint signs its sends (backend/webhook_delivery.py); hubdb.MIGRATIONS applies it.

`standard` (every endpoint so far) signs as Standard Webhooks. `hmac-sha256` is an HMAC-SHA256 of the body, hex, in
`x-signature: sha256=<hex>`, with the secret as written: the check a QM inbound webhook makes (backend/external_sync.py).
"""
SCHEMA = """
ALTER TABLE webhooks ADD COLUMN signing TEXT NOT NULL DEFAULT 'standard';
"""
