"""Computer revocation and retirement of unused self-hosted computers."""

from .store import H

UNASSIGNED_OFFLINE_RETIRE_SECONDS = 24 * 60 * 60


def revoke(c, actor, runner_id, *, reason=None, at=None):
    """Keep the audit record and remove the registration and its delivery settings."""
    from . import app_delivery
    c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (at or H.now(), runner_id))
    c.execute("DELETE FROM registry_metadata WHERE key IN (?,?)",
              ("computer-repositories:" + runner_id, "computer-enrolled-as:" + runner_id))
    app_delivery.runner_revoked(c, actor, runner_id)
    H.event(c, actor, "runner.revoked", runner_id, {"reason": reason} if reason else None)


def disconnect(store, runner_id):
    """Close screen and browser access after revocation commits."""
    sessions = getattr(store, "screen_sessions", None)
    if sessions is None:
        return
    sessions.set_mode(runner_id, "off", "computer removed")
    sessions.push_exit_policy(runner_id)
    sessions.exit_close(exit=runner_id, reason="computer removed")
    with store.read() as c:
        bots = [r[0] for r in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (runner_id,))]
    for bot in bots:
        sessions.push_bot_generation(bot, "computer removed")


def sweep(store, at):
    """Retire only unassigned, offline self-hosted computers, under the heartbeat/assignment write lock."""
    cutoff = H.shift(at, seconds=-UNASSIGNED_OFFLINE_RETIRE_SECONDS)
    with store.transaction() as c:
        retired = [r["id"] for r in c.execute(
            "SELECT r.id FROM runners_with_state r WHERE r.revoked_at IS NULL "
            "AND coalesce(r.last_seen_at,r.created_at)<? "
            "AND NOT EXISTS (SELECT 1 FROM assignments a WHERE a.runner_id=r.id) "
            "AND NOT EXISTS (SELECT 1 FROM cloud_machines m WHERE m.runner_id=r.id) "
            "AND NOT EXISTS (SELECT 1 FROM bot_settings b WHERE json_extract(b.app_delivery,'$.computer')=r.id)",
            (cutoff,))]
        for runner_id in retired:
            revoke(c, H.KEEPER, runner_id, at=at, reason="No bots assigned and offline for over 24 hours")
    for runner_id in retired:
        disconnect(store, runner_id)
    return retired
