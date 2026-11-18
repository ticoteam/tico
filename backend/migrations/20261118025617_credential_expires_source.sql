-- Where a key's end date (`credentials.expires_on`) came from (backend/api_tools.py `vendor_end_date`): `vendor` when
-- the tool's check read it from the vendor's reply (GitHub's token expiration header, Cloudflare's token verify), which
-- the next check keeps current; `person` when a person typed it on the key. NULL for no end date, and for dates typed
-- before this column existed (they count as typed).
ALTER TABLE credentials ADD COLUMN expires_source TEXT;
