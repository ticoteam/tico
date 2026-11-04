"""Cleanup wave 5: goal_checkins.source_actor becomes sender_id (wave 5 of scripts/cleanup/renames.yaml). A check-in's
source_actor is whose words it is, the same role `sender_id` names on a message, and the check-in rows reach the
response unchanged, so the column takes the response's name. The stored values do not change: the column still holds
`bot:<id>` or `human:<id>`.

The rename is backend/migrations/renames_data.py's entry for this wave, applied the way 0031 applies its waves
(backend/migrations/renames.py).
"""
from backend.migrations import renames as schema_renames

WAVES = ("5",)


def up(conn):
    schema_renames.apply(conn, waves=WAVES)
