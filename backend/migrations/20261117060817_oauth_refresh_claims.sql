-- One refresh of an OAuth sign-in at a time, across processes (backend/oauth_refresh.py): a claim per sign-in, held
-- while its refresh request is in flight. A claim the holder did not drop lapses at claimed_until, so a refresh that
-- died frees its sign-in then.
CREATE TABLE IF NOT EXISTS oauth_refresh_claims(
 sign_in TEXT PRIMARY KEY, holder TEXT NOT NULL, claimed_until TEXT NOT NULL);
