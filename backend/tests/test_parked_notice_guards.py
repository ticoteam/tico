"""Nothing marks a notice to run without its task any more (the task it waited behind went with blocked_by), so the
mark is a capability no caller may supply: the message writer takes it off whatever it is given (backend/rules.py,
PARKED_NOTICE)."""

from backend import hubdb as H, rules as R


def test_untrusted_notice_marker_is_removed_for_a_human_recipient(api):
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, 'bot:ops', 'Restore the service', 'Synthetic held work', 'bot:cpo')
        msg = H._write_message(
            c,
            H.KEEPER,
            'human:ana',
            'Synthetic news',
            H.conversation(c, task['conversation_id']),
            'notice',
            {'task': task['id'], R.PARKED_NOTICE: True},
            None,
            None,
        )
        assert R.PARKED_NOTICE not in msg['refs']


def test_untrusted_notice_marker_is_removed_for_a_bot_and_its_run_is_about_its_task(api):
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, 'bot:ops', 'Restore the service', 'Synthetic held work', 'bot:cpo')
        msg = H._wake(c, H.task(c, task['id']), 'bot:cpo', 'Incident needs attention', {R.PARKED_NOTICE: True})
        assert R.PARKED_NOTICE not in msg['refs']
        assert not R.notice_only(H.message(c, msg['id']))
