"""Move email sends into the outbound queue while preserving send and approval references."""

from backend import hubdb as H


def up(conn):
    now = H.now()
    conn.execute(
        """
        INSERT INTO outbound_actions(
            id, channel, audience, operation, destination, occurrence_key, actor, payload, metadata,
            policy_decision, state, attempts, send_after, next_attempt_at, provider_attempted_at,
            claimed_at, approval_id, task_id, receipt, last_error, created_at, updated_at
        )
        SELECT s.id, 'email', 'external', 'send', s.mailbox,
               CASE WHEN EXISTS (SELECT 1 FROM outbound_actions WHERE occurrence_key='email:send:'||s.id)
                    THEN 'email:send:'||s.id||':'||lower(hex(randomblob(16))) ELSE 'email:send:'||s.id END,
               s.actor,
               CASE WHEN s.state IN ('waiting_approval','pending') AND s.raw<>'' THEN json_object('raw',s.raw)
                    ELSE '' END,
               json_object(
                   'bot',s.bot, 'via',s.via, 'automation_action_id',s.automation_action_id,
                   'calendar_action_id',s.calendar_action_id, 'content_sha256',s.content_sha256,
                   'idempotency_key',s.idempotency_key, 'operation_key',s.operation_key,
                   'recipients',json(s.recipients), 'attachments',json(s.attachments), 'subject',s.subject,
                   'body_sha256',s.body_sha256, 'draft_id',s.draft_id, 'thread_id',s.thread_id,
                   'reply_to_id',s.reply_to_id, 'approval_task_id',s.approval_task_id,
                   'classification',json(s.classification), 'runner_id',s.runner_id, 'checked_at',s.checked_at,
                   'finished_at',CASE WHEN s.state='dispatching' THEN ? ELSE s.finished_at END,
                   'cutover_attempted_at',CASE WHEN s.state='dispatching' THEN s.provider_attempted_at END,
                   'cutover_unconfirmed',CASE WHEN s.state='dispatching' THEN 1 END
               ),
               s.decision, CASE s.state WHEN 'pending' THEN 'queued' WHEN 'dispatching' THEN 'unknown'
                                        WHEN 'sent' THEN 'done' WHEN 'declined' THEN 'blocked' ELSE s.state END,
               CASE WHEN s.provider_attempted_at IS NOT NULL AND s.state<>'dispatching' THEN 1 ELSE 0 END,
               s.send_after, s.send_after, CASE WHEN s.state<>'dispatching' THEN s.provider_attempted_at END,
               s.claimed_at, s.approval_id, s.task_id,
               CASE WHEN s.provider_message_id IS NULL THEN '{}' ELSE json_object('message_id',s.provider_message_id) END,
               CASE WHEN s.state='dispatching' THEN 'The connector did not confirm delivery' ELSE s.detail END,
               s.created_at, CASE WHEN s.state='dispatching' THEN ? ELSE coalesce(s.finished_at,s.claimed_at,s.created_at) END
        FROM email_sends s ORDER BY s.rowid
        """,
        (now, now),
    )
    # The live queue owns the only copy of the frozen MIME, so its normal wipe also removes migrated content.
    conn.execute("UPDATE email_sends SET raw='' WHERE raw<>''")
    for name in ("dispatch", "mailbox", "actor", "day", "pacing", "operation", "waiting"):
        conn.execute(f"DROP INDEX email_sends_{name}")
    # Email accounting includes legacy attempts without marking them as attempts made by the shared queue.
    attempted_at = "coalesce(provider_attempted_at,json_extract(metadata,'$.cutover_attempted_at'))"
    conn.execute(
        f"""
        CREATE VIEW outbound_email AS
        SELECT id, destination AS mailbox, actor,
               json_extract(metadata,'$.bot') AS bot,
               json_extract(metadata,'$.via') AS via,
               json_extract(metadata,'$.automation_action_id') AS automation_action_id,
               json_extract(metadata,'$.calendar_action_id') AS calendar_action_id,
               json_extract(metadata,'$.content_sha256') AS content_sha256,
               json_extract(metadata,'$.idempotency_key') AS idempotency_key,
               json_extract(metadata,'$.operation_key') AS operation_key,
               json_extract(metadata,'$.recipients') AS recipients,
               json_extract(metadata,'$.attachments') AS attachments,
               json_extract(metadata,'$.subject') AS subject,
               json_extract(metadata,'$.body_sha256') AS body_sha256,
               coalesce(json_extract(nullif(payload,''),'$.raw'),'') AS raw,
               json_extract(metadata,'$.draft_id') AS draft_id,
               json_extract(metadata,'$.thread_id') AS thread_id,
               json_extract(metadata,'$.reply_to_id') AS reply_to_id,
               approval_id, task_id,
               json_extract(metadata,'$.approval_task_id') AS approval_task_id,
               json_extract(metadata,'$.classification') AS classification,
               policy_decision AS decision,
               CASE state WHEN 'queued' THEN 'pending' WHEN 'attempting' THEN 'dispatching'
                          WHEN 'done' THEN 'sent' ELSE state END AS state,
               state AS queue_state, created_at, send_after, claimed_at, {attempted_at} AS provider_attempted_at,
               json_extract(metadata,'$.runner_id') AS runner_id,
               json_extract(metadata,'$.finished_at') AS finished_at,
               json_extract(receipt,'$.message_id') AS provider_message_id,
               last_error AS detail, coalesce(claim_token,'') AS lease_id,
               json_extract(metadata,'$.checked_at') AS checked_at,
               json_extract(metadata,'$.cutover_unconfirmed') AS cutover_unconfirmed, rowid
        FROM outbound_actions WHERE channel='email'
        """
    )
    for name, cols in (
        ("key", "json_extract(metadata,'$.idempotency_key')"),
        ("mailbox", "destination, created_at DESC, id DESC"),
        ("dispatch", "state, send_after"),
        ("waiting", "destination, state, json_extract(metadata,'$.checked_at')"),
        ("day", f"substr(coalesce({attempted_at},claimed_at,created_at),1,10), state"),
        ("pacing", f"destination, coalesce({attempted_at},claimed_at) DESC"),
        ("automation", "json_extract(metadata,'$.automation_action_id')"),
    ):
        unique = "UNIQUE " if name == "key" else ""
        where = " AND state IN ('done','attempting','unknown')" if name == "pacing" else ""
        conn.execute(
            f"CREATE {unique}INDEX outbound_email_{name} ON outbound_actions({cols}) WHERE channel='email'{where}"
        )
    conn.execute(
        "CREATE INDEX outbound_email_cutover ON outbound_actions(id) "
        "WHERE channel='email' AND json_extract(metadata,'$.cutover_unconfirmed')=1"
    )
    conn.execute(
        "CREATE INDEX outbound_actions_non_email_created ON outbound_actions(created_at DESC, id DESC) "
        "WHERE channel<>'email'"
    )
    conn.execute(
        "CREATE INDEX outbound_actions_non_email_state ON outbound_actions(state, created_at DESC, id DESC) "
        "WHERE channel<>'email'"
    )
