"""One refresh of an OAuth sign-in at a time, across processes: the server's workers, the scheduler and the proxy.

A vendor that rotates refresh tokens accepts each one once. Two refreshes that send the same token cannot both
succeed: the second is refused, and some vendors then revoke the token the first one got. So each sign-in has a claim
in the database, held while its refresh request is in flight, and what a refresh stores must still belong to the
refresh token it sent:

1. `take` runs inside a write transaction (store.transaction, BEGIN IMMEDIATE) that has just read the sign-in's stored
   tokens. It takes the claim unless another holder's has not lapsed. A caller without the claim waits, outside any
   transaction, and reads the tokens again: it never sends a refresh token while another holder may be sending it.
2. The refresh request goes out with no transaction open, so the database's write lock is never held across the
   network. A claim lapses after CLAIM_SECONDS, longer than one request to a vendor, so a live refresh keeps it; a
   holder that died frees the sign-in when it lapses.
3. The holder stores its new tokens only while the stored refresh token is still the one it sent (a compare-and-swap),
   then drops the claim with `release`. When it is not, the claim lapsed under a slow refresh and another refresh has
   stored tokens since: this refresh's tokens are dropped and the stored ones are used.

Holding BEGIN IMMEDIATE across the request would block every writer in the server for as long as the vendor takes.
A compare-and-swap after the request alone would let two refreshes send the same token before either stores it.
"""

from typing import Any

from . import store as _store

H = _store.H

CLAIM_SECONDS = 60  # a claim held this long without its holder dropping it lapses: longer than one vendor request
WAIT_SECONDS = CLAIM_SECONDS + 10  # a caller waits this long for another refresh before it gives up
POLL_SECONDS = 0.2


def take(c: Any, sign_in: str) -> str | None:
    """Inside a write transaction: the holder's id when this call takes the sign-in's claim, None while another
    holder's claim has not lapsed."""
    now = H.now()
    row = c.execute("SELECT claimed_until FROM oauth_refresh_claims WHERE sign_in=?", (sign_in,)).fetchone()
    if row is not None and row["claimed_until"] > now:
        return None
    holder = H.new_id()
    c.execute(
        "INSERT INTO oauth_refresh_claims(sign_in,holder,claimed_until) VALUES(?,?,?) "
        "ON CONFLICT(sign_in) DO UPDATE SET holder=excluded.holder, claimed_until=excluded.claimed_until",
        (sign_in, holder, H.shift(now, seconds=CLAIM_SECONDS)),
    )
    return holder


def release(store: Any, sign_in: str, holder: str) -> None:
    """Drops the claim if it is still `holder`'s: a claim that lapsed and was taken by another stays theirs."""
    with store.transaction() as c:
        c.execute("DELETE FROM oauth_refresh_claims WHERE sign_in=? AND holder=?", (sign_in, holder))
