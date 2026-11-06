-- Where a skills-helper device code was asked from (a hash of the caller's address bucket), so codes asked for
-- without sign-in are capped per address the way agent pairings are (backend/skill_devices.py).
ALTER TABLE skill_device_codes ADD COLUMN client_hash TEXT;
CREATE INDEX IF NOT EXISTS index_skill_device_codes_on_client_hash ON skill_device_codes(client_hash, created_at);
