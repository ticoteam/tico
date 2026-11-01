"""Offline Hosting enrollment. Only a SHA-256 digest crosses the SSM control plane.

HQ keeps the random join secret privately. An IAM-scoped command on the selected
server registers its digest; this grants no owner session or general API access.
"""
import re

from .store import H, Problem


def enrollment(store, auth, request_id, code_hash, label, owner, status=False):
    if not re.fullmatch(r"pv-[0-9a-f]{16}", request_id) or not re.fullmatch(r"[0-9a-f]{64}", code_hash):
        raise ValueError("invalid hosting enrollment")
    if not 1 <= len(label) <= 100:
        raise ValueError("invalid computer name")
    if getattr(store.settings, 'rehearsal', False):
        raise Problem('rehearsal', 'Enrolments are off in a rehearsal', 409)
    with store.transaction() as c:
        auth.sync_access(c)
        who = auth.owner_identity(c)
        person = c.execute("SELECT id FROM humans WHERE lower(email)=?", (owner.strip().lower(),)).fetchone()
        if not person or person['id'] != H.actor_id(who.actor):
            raise Problem("owner", "Hosting enrollment requires this server's owner", 403)
        row = c.execute("SELECT * FROM hosting_enrollments WHERE request_id=?", (request_id,)).fetchone()
        if row and (row['code_hash'] != code_hash or row['label'] != label):
            raise ValueError("hosting enrollment changed")
        if not row:
            if status:
                return {"online": False}
            c.execute("INSERT INTO hosting_enrollments VALUES(?,?,?)", (request_id, code_hash, label))
            c.execute("INSERT INTO enrollments(code_hash,operator,expires_at) VALUES(?,?,?)",
                      (code_hash, person['id'], H.shift(H.now(), minutes=15)))
            H.event(c, who.actor, 'runner.hosting_enrollment', request_id)
        e = c.execute("SELECT * FROM enrollments WHERE code_hash=?", (code_hash,)).fetchone()
        if not e:
            raise ValueError("hosting enrollment missing")
        # A resumed install refreshes only an unused code. A consumed enrollment
        # never becomes a second computer, even after its original expiry.
        if not status and not e['consumed_at']:
            c.execute("UPDATE enrollments SET expires_at=? WHERE code_hash=?", (H.shift(H.now(), minutes=15), code_hash))
        runner = c.execute("SELECT last_seen_at,revoked_at FROM runners WHERE id=?", (e['runner_id'],)).fetchone()
        return {"online": bool(runner and not runner['revoked_at'] and runner['last_seen_at']
                               and runner['last_seen_at'] > H.shift(H.now(), minutes=-2))}
