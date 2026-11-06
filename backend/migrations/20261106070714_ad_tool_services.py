"""Google Ads and Meta Ads became known services (backend/tool_services.py) after the migration that made a tool for every
stored key (20261105220550). A tool it made for one of their keys has no service, so neither its page nor Finance's ad
spend import knows it. Each such tool whose key's variable or name now matches one of the two takes the service, and its
empty base URL and check path take the service's. Its name, delivery and keys stay as they are. Idempotent: a tool with
a service is never touched.
"""

from backend import tool_services

SERVICES = ("google-ads", "meta-ads")


def up(conn):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"credentials", "tools"} <= have:
        return
    if "tool_id" not in {r[1] for r in conn.execute("PRAGMA table_info(credentials)")}:
        return
    rows = conn.execute("SELECT t.id, k.env, k.name FROM tools t JOIN credentials k ON k.tool_id=t.id "
                        "WHERE t.service='' ORDER BY t.id, k.id").fetchall()
    for tid, env, name in rows:
        service = tool_services.match(env=env or "", name=name or "")
        if service not in SERVICES:
            continue
        fields = tool_services.defaults(service)
        conn.execute("UPDATE tools SET service=?, base_url=CASE WHEN base_url='' THEN ? ELSE base_url END, "
                     "check_path=CASE WHEN check_path='' THEN ? ELSE check_path END WHERE id=? AND service=''",
                     (service, fields["base_url"], fields["check_path"], tid))
