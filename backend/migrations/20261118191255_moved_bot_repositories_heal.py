"""Read the repository list from GitHub again soon after this update, so moved bots get their repositories back.

A bot recreated here from another server is younger than its repository, so Homes (backend/repositories.py) read it
as another company's and the bot committed on its computer only (TIDY, after its Nov 13 move). The repository sync now
heals that (repositories.heal): it needs GitHub's last push to each repository, which only the listing carries, so a
migration cannot decide it. Forgetting the day of the last sync makes the heartbeat's daily check
(repositories.daily) run the sync within seconds of start instead of tomorrow. Nothing else changes.

Running it again changes nothing more.
"""


def up(conn):
    conn.execute("DELETE FROM registry_metadata WHERE key='repositories-synced'")
