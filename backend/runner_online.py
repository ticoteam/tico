"""Whether a computer is online: the one rule every reader uses (Health, readiness, the computers list, placement,
the claim, the cloud machines and the meeting importers).

A computer is online when the server heard from it within ONLINE_SECONDS. The runner beats at least every
HEARTBEAT_SECONDS while nothing about it changes (runner-rs events.rs HEARTBEAT_LIVE_S), and its event stream and its
claims keep `last_seen_at` current between beats. Two heartbeat periods let one late beat pass without the computer
flickering offline; one period would flag a healthy, quiet computer offline for part of every cycle.
"""

from .store import H

HEARTBEAT_SECONDS = 60  # mirrors runner-rs events.rs HEARTBEAT_LIVE_S
ONLINE_SECONDS = 2 * HEARTBEAT_SECONDS


def cutoff():
    """The oldest contact that still counts as online: `last_seen_at > ?` in SQL."""
    return H.shift(H.now(), seconds=-ONLINE_SECONDS)


def is_online(last_seen_at):
    """Whether a computer whose last contact is `last_seen_at` (a stored timestamp, or None) is online."""
    return bool(last_seen_at) and last_seen_at > cutoff()


def online_rows(c):
    """The computers online now and not revoked, newest contact first: id, label, readiness."""
    return c.execute(
        "SELECT id,label,readiness FROM runners_with_state WHERE revoked_at IS NULL AND last_seen_at>? "
        "ORDER BY last_seen_at DESC",
        (cutoff(),),
    ).fetchall()
