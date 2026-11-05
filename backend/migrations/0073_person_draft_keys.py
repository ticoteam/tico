"""A person's own support requests keep the key their chat lists them under.

Until this change every approved support report was filed under `approval:<id>`, whoever asked for it. The person's chat
lists their own tickets (backend/support.py OWN), and that leaves out `approval:` tickets: those are the bots' reports and
the Health Monitor's. So a request a person asked for and sent before this change had vanished from their chat. This
moves each such ticket to `draft:<id>`, the key a person's draft is filed under now.

Only a ticket whose approval a person asked for changes: approvals.requested_by_id names a human (`human:`, not a bot and
not the keeper), and the Health Monitor did not file that approval (health_reports). The ticket's source stays `person`
and no other column changes. Running it again changes nothing.
"""


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"support_tickets", "approvals"} <= tables:
        return
    if "requested_by_id" not in {row[1] for row in conn.execute("PRAGMA table_info(approvals)")}:
        return
    asked = "SELECT id FROM approvals WHERE requested_by_id LIKE 'human:%'"
    if "health_reports" in tables:
        asked += " AND id NOT IN (SELECT approval_id FROM health_reports WHERE approval_id IS NOT NULL)"
    # 'approval:' is nine characters, so the approval's id starts at character ten.
    conn.execute(
        "UPDATE support_tickets SET request_key='draft:' || SUBSTR(request_key, 10) "
        "WHERE request_key LIKE 'approval:%' AND COALESCE(json_extract(sent,'$.source'),'person')='person' "
        f"AND SUBSTR(request_key, 10) IN ({asked})")
