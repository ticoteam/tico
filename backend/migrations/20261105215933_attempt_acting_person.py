"""Retain a Make-page turn's person after the execution lease ends."""


def up(conn):
    if "acting_person" not in {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}:
        conn.execute("ALTER TABLE attempts ADD COLUMN acting_person TEXT")
    # Existing receipts retain only a person proven by the server-authored request in their own Assistant room.
    conn.execute("UPDATE attempts SET acting_person=(SELECT v.owner_id FROM jobs j "
                 "JOIN messages m ON m.id=j.message_id JOIN conversations v ON v.id=m.conversation_id "
                 "WHERE j.id=attempts.job_id AND m.recipient_id='bot:'||attempts.bot_id "
                 "AND v.scope='personal' AND v.room_key='assistant' AND m.sender_id=v.owner_id "
                 "AND v.owner_id LIKE 'human:%' AND json_extract(m.refs,'$.assistant')=1 "
                 "AND json_type(m.refs,'$.make_page')='object' "
                 "AND NOT coalesce(json_extract(m.refs,'$.via'),'') AND m.deleted_at IS NULL) "
                 "WHERE acting_person IS NULL")
