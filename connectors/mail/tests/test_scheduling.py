import contextlib
import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from connectors.mail import scheduling as s, calendar as cal, Refused, Failure
from connectors.mail import __main__ as cli

NOW = datetime(2026,9,8,15,tzinfo=s.TZ)


class Workflow(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.ctx=SimpleNamespace(slug='inbox',mailbox='ana@acme.example',conn=self.conn,
                                 dry=False,calendar=MagicMock(),gmail=MagicMock(),audit=MagicMock())
        self.ctx.calendar.create_scheduling_event.return_value={'id':'event'}
        self.ctx.gmail.create_draft.return_value={'id':'draft'}
        self.ctx.gmail.send_raw.return_value={'id':'sent'}
        self.msg={'id':'accepted','from':'creator@example.org','to':['ana@acme.example'],'cc':[],
                  'date':'2030-01-01','subject':'Call','body':'Tuesday at 10 works', 'labels':[]}
        self.args=SimpleNamespace(thread='thread',category='influencer',mode='book',bd_confirmation=None,
                                  slot='2030-01-08T10:00:00-08:00',acceptance_message='accepted')
        self.stack=contextlib.ExitStack()
        self.stack.enter_context(patch.object(cli,'thread_context',return_value=([self.msg],'','','Call',[])))
        self.stack.enter_context(patch.object(s,'permission'))
        self.review=self.stack.enter_context(patch.object(s,'review_thread',return_value={'eligible':True,'accepts_slot':True}))
        self.free=self.stack.enter_context(patch.object(s,'check_free'))
        self.stack.enter_context(patch.object(s,'mailbox_lock',return_value=contextlib.nullcontext()))
        self.stack.enter_context(patch.object(cli,'modify'))
    def tearDown(self):self.stack.close();self.conn.close()

    def test_refusal_and_dry_run_never_invite(self):
        self.ctx.dry=True
        self.assertTrue(s.run(self.ctx,self.args)['would_send'])
        self.ctx.calendar.create_scheduling_event.assert_not_called()
        self.ctx.dry=False;self.review.side_effect=Refused('not accepted')
        with self.assertRaises(Refused):s.run(self.ctx,self.args)
        self.ctx.calendar.create_scheduling_event.assert_not_called()

    def test_booking_queues_the_invitation_instead_of_notifying_from_calendar(self):
        with patch.object(s.pl, 'queue_send', return_value={'id':'send', 'state':'waiting_approval'}) as queued:
            answer = s.run(self.ctx, self.args)
        assert answer['queued'] and not answer['sent'] and answer['state'] == 'waiting_approval'
        from backend.email_content import snapshot
        proof = snapshot(queued.call_args.args[1], self.ctx.mailbox)
        assert proof['attachments'][0]['type'] == 'text/calendar'
        self.ctx.gmail.send_raw.assert_not_called()

    def test_a_pending_conference_is_retryable_and_does_not_freeze_a_linkless_invitation(self):
        self.ctx.calendar.create_scheduling_event.side_effect = cal.ConferencePending('Meeting link pending', status=503)
        with patch.object(s.pl, 'queue_send') as queued:
            result = s.run(self.ctx, self.args)
        assert result['retry'] and not result['queued'] and not result['sent']
        queued.assert_not_called()
        assert self.conn.execute('SELECT status FROM scheduling_actions').fetchone()[0] == 'canceled'

    def test_uncertain_or_active_sends_keep_the_booking_fence(self):
        with patch.object(s.pl, 'queue_send', return_value={'id': 'send-1', 'state': 'pending'}) as queued:
            first = s.run(self.ctx, self.args)
            for state in ('pending', 'waiting_approval', 'dispatching', 'sent', 'unknown'):
                self.conn.execute("UPDATE scheduling_actions SET status='queued'")
                self.conn.commit()
                with patch.object(s.pl, 'HUB_GET', return_value={'send': {'id': 'send-1', 'state': state}}):
                    answer = s.run(self.ctx, self.args)
                assert answer['already_attempted'] and answer['send_id'] == first['send_id']
            assert queued.call_count == 1


def test_invitation_freezes_join_location_description_and_conference_entry_points():
    from connectors.mail import compose
    from backend.email_content import parsed, snapshot, attachment
    start = datetime(2030, 1, 8, 10, tzinfo=s.TZ)
    join = 'https://meet.google.com/abc-defg-hij'
    event = {'id': 'event', 'hangoutLink': join, 'location': 'Room A; upstairs', 'description': 'Agenda: budget, review\nSecond line',
             'conferenceData': {'entryPoints': [{'entryPointType': 'video', 'uri': join}, {'entryPointType': 'phone', 'uri': 'tel:+15555550123'}]}}
    raw = compose.with_invitation(compose.build(['guest@vendor.example'], 'Meeting', 'Meeting tomorrow', from_addr='ana@acme.example'),
                                  event, 'ana@acme.example', ['guest@vendor.example'], start, start + s.DURATION, 'Meeting')
    message = parsed(raw)
    assert join in message.get_body(preferencelist=('plain',)).get_content()
    assert 'Room A; upstairs' in message.get_body(preferencelist=('plain',)).get_content()
    ics = next(part.get_content() for part in message.walk() if part.get_content_type() == 'text/calendar')
    unfolded = ics.replace('\r\n ', '').replace('\n ', '')
    assert 'URL:' + join in unfolded and 'CONFERENCE;VALUE=URI:' + join in unfolded
    assert 'CONFERENCE;VALUE=URI:tel:+15555550123' in unfolded
    assert 'LOCATION:Room A\\; upstairs' in unfolded and 'DESCRIPTION:Agenda: budget\\, review\\nSecond line' in unfolded
    frozen = snapshot(raw, 'ana@acme.example')
    assert join.encode() in attachment(raw, frozen['attachments'][0]['part'])


@pytest.mark.parametrize('mode', ['standing', 'hub'])
def test_calendar_waits_for_the_conference_before_returning_an_invitable_event(mode):
    from connectors.mail.tests.fake import FakeCalendarService
    service = FakeCalendarService()
    calendar = cal.Calendar(service, 'ana@acme.example')
    pending = {'id': 'event', 'conferenceData': {'createRequest': {'status': {'statusCode': 'pending'}}}}
    ready = {'id': 'event', 'hangoutLink': 'https://meet.google.com/abc-defg-hij',
             'conferenceData': {'createRequest': {'status': {'statusCode': 'success'}}}}
    start = datetime(2030, 1, 8, 10, tzinfo=s.TZ)
    with patch.object(calendar, '_exec', side_effect=[pending, pending, ready]) as execute, patch.object(cal.time, 'sleep') as sleep:
        if mode == 'standing':
            event = calendar.create_scheduling_event(start, start+s.DURATION, 'guest@vendor.example', 'event', 'Agenda')
        else:
            event = calendar.create_hub_event(start, start+s.DURATION, 'Meeting', ['guest@vendor.example'], 'event')
    assert event['hangoutLink'] == ready['hangoutLink'] and execute.call_count == 3 and sleep.call_count == 2


def test_conference_polling_stops_with_a_retryable_error_and_no_linkless_event():
    from connectors.mail.tests.fake import FakeCalendarService
    calendar = cal.Calendar(FakeCalendarService(), 'ana@acme.example')
    pending = {'id': 'event', 'conferenceData': {'createRequest': {'status': {'statusCode': 'pending'}}}}
    with patch.object(calendar, '_exec', return_value=pending) as execute, patch.object(cal.time, 'sleep') as sleep:
        with pytest.raises(cal.ConferencePending) as refused:
            calendar.ready_conference(pending)
    assert refused.value.status == 503 and execute.call_count == 15 and sleep.call_count == 15


def test_a_failed_conference_read_is_retryable_after_the_silent_event_was_created():
    from connectors.mail.tests.fake import FakeCalendarService
    calendar = cal.Calendar(FakeCalendarService(), 'ana@acme.example')
    pending = {'id': 'event', 'conferenceData': {'createRequest': {'status': {'statusCode': 'pending'}}}}
    with patch.object(calendar, '_exec', side_effect=Failure('Calendar unavailable', status=503)), patch.object(cal.time, 'sleep'):
        with pytest.raises(cal.ConferencePending) as refused:
            calendar.ready_conference(pending)
    assert refused.value.status == 503 and 'retry' in str(refused.value)


def test_rebooking_excludes_only_the_unchanged_canceled_hold_and_keeps_other_busy_events():
    from connectors.mail.tests.fake import FakeCalendarService
    service = FakeCalendarService(calendars=['primary'])
    calendar = cal.Calendar(service, 'ana@acme.example')
    start = datetime(2030, 1, 8, 10, tzinfo=s.TZ)
    def event(ident, begin):
        return {'id': ident, 'start': {'dateTime': begin.isoformat()}, 'end': {'dateTime': (begin+s.DURATION).isoformat()}}
    service.events_by_calendar['primary'] = [event('canceled-hold', start), event('unrelated', start)]
    hold = {'id': 'canceled-hold', 'bounds': (start, start+s.DURATION), 'event': copy.deepcopy(service.events_by_calendar['primary'][0])}
    assert calendar.busy(start, start+s.DURATION, exclude_hold=hold) == [(start, start+s.DURATION)]
    service.events_by_calendar['primary'].pop()
    assert calendar.busy(start, start+s.DURATION, exclude_hold=hold) == []
    changed = start + timedelta(minutes=15)
    service.events_by_calendar['primary'] = [event('canceled-hold', changed)]
    assert calendar.busy(start, start+s.DURATION, exclude_hold=hold) == [(changed, changed+s.DURATION)]


@pytest.mark.parametrize('mutation', ['sequence', 'organizer', 'conference_notes'])
def test_hold_material_cannot_be_redefined_by_a_later_read(mutation):
    from connectors.mail.tests.fake import FakeCalendarService
    service = FakeCalendarService(calendars=['primary'])
    calendar = cal.Calendar(service, 'ana@acme.example')
    start = datetime(2030, 1, 8, 10, tzinfo=s.TZ)
    event = calendar.create_scheduling_event(start, start+s.DURATION, 'guest@vendor.example', 'hold', 'Agenda')
    verified = copy.deepcopy(event)
    if mutation == 'sequence':
        event['sequence'] = 42
    elif mutation == 'organizer':
        event['organizer'] = {'email': 'other@vendor.example'}
    else:
        event['conferenceData']['notes'] = 'User added notes'
    assert not cal.unchanged_hold(event, verified)
    assert calendar.busy(start, start+s.DURATION, exclude_hold=[{'id': 'hold', 'bounds': (start, start+s.DURATION),
                                                               'event': verified}]) == [(start, start+s.DURATION)]
    with pytest.raises(cal.ConferencePending, match='Review the calendar hold') as refused:
        calendar.reuse_scheduling_event(verified)
    assert refused.value.event == verified and not service.deleted


def test_conference_patch_refuses_an_event_edited_after_verification():
    from connectors.mail.tests.fake import FakeCalendarService
    service = FakeCalendarService(calendars=['primary'])
    calendar = cal.Calendar(service, 'ana@acme.example')
    start = datetime(2030, 1, 8, 10, tzinfo=s.TZ)
    event = calendar.create_scheduling_event(start, start+s.DURATION, 'guest@vendor.example', 'hold', 'Agenda')
    event.pop('hangoutLink')
    event['conferenceData']['createRequest']['status'] = {'statusCode': 'failure'}
    verified, patcher, execute = copy.deepcopy(event), service.patch, calendar._exec
    def race(**kwargs):
        event['etag'], event['summary'] = '"changed"', 'Edited meeting'
        return patcher(**kwargs)
    with patch.object(calendar, '_exec', side_effect=lambda req, what: copy.deepcopy(execute(req, what))), \
            patch.object(service, 'patch', side_effect=race):
        with pytest.raises(Failure) as refused:
            calendar.reuse_scheduling_event(verified)
    assert refused.value.status == 412 and not service.deleted and event['summary'] == 'Edited meeting'


class PermissionGrant(unittest.TestCase):
    """Standing scheduling permission lives in bot.yaml (`scheduling_send: true`); Tico still decides the send."""
    def setUp(self):
        self.manifest={'scheduling_send':True,'access':[
            {'service':'gmail','identity':'ana@acme.example','can':['read','draft','schedule']},
            {'service':'google-calendar','identity':'ana@acme.example','can':['read','schedule']}]}
        self.ctx=SimpleNamespace(slug='inbox',mailbox='ana@acme.example')

    def test_settings_need_scheduling_send_and_a_matching_mailbox(self):
        self.assertEqual(s.settings(self.ctx,self.manifest)['mailbox'],'ana@acme.example')
        self.assertEqual(s.settings(self.ctx,dict(self.manifest,scheduling_send='yes')),{})
        self.assertEqual(s.settings(self.ctx,dict(self.manifest,scheduling={'mailbox':'other@acme.example'})),{})
        named=s.settings(self.ctx,dict(self.manifest,scheduling={'mailbox':'Ana@Acme.example','meeting_title':'Intro'}))
        self.assertEqual((named['mailbox'],named['meeting_title']),('ana@acme.example','Intro'))

    def test_no_standing_permission_or_an_unknown_category_refuses(self):
        with patch.object(s.pl, "sending_preflight"), patch.object(s.access,'load',return_value=dict(self.manifest,scheduling_send=False)):
            with self.assertRaises(Refused):s.permission(self.ctx,'influencer','x@example.org','t',None)
        with patch.object(s.pl, "sending_preflight"), patch.object(s.access,'load',return_value=self.manifest), \
                patch.object(s.access,'resolve'):
            s.permission(self.ctx,'influencer','x@example.org','t',None)
            with self.assertRaises(Refused):s.permission(self.ctx,'sales','x@example.org','t',None)


class ReviewThread(unittest.TestCase):
    """The decision model reads the thread before any booking; no model, or a doubtful one, books nothing."""
    MSGS=[{'id':'m','from':'creator@example.org','date':'2030-01-01','subject':'Call','body':'Tuesday at 10 works'}]
    START=[datetime(2030,1,8,10,tzinfo=s.TZ)]

    def engine(self, eligible, accepts):
        values={'eligible':eligible,'accepts_slot':accepts}
        return lambda state, questions, label: {'answers':{q:{'noul':values[q]} for q in questions}}

    def test_no_decision_model_refuses(self):
        with patch.object(s.jd,'engine',return_value=None):
            with self.assertRaises(Refused):s.review_thread(self.MSGS,'creator@example.org','influencer','book',self.START)

    def test_a_doubtful_answer_refuses_and_a_clear_one_passes(self):
        with patch.object(s.jd,'engine',return_value=self.engine(0.3,0.95)):
            with self.assertRaises(Refused):s.review_thread(self.MSGS,'creator@example.org','influencer','book',self.START)
        with patch.object(s.jd,'engine',return_value=self.engine(0.95,0.3)):
            with self.assertRaises(Refused):s.review_thread(self.MSGS,'creator@example.org','influencer','book',self.START)
        with patch.object(s.jd,'engine',return_value=self.engine(0.95,0.9)):
            self.assertEqual(s.review_thread(self.MSGS,'creator@example.org','influencer','book',self.START),
                             {'eligible':0.95,'accepts_slot':0.9})

    def test_a_broken_model_answer_refuses(self):
        with patch.object(s.jd,'engine',return_value=lambda *a:{'answers':{}}):
            with self.assertRaises(Refused):s.review_thread(self.MSGS,'creator@example.org','influencer','book',self.START)


def test_calendar_provider_never_notifies_before_the_frozen_invitation_dispatch():
    from connectors.mail.tests.fake import FakeCalendarService
    service = FakeCalendarService()
    calendar = cal.Calendar(service, 'ana@acme.example')
    start = datetime(2030, 1, 8, 10, tzinfo=s.TZ)
    calendar.create_event(start, start+timedelta(minutes=30), 'Meeting', attendees=['guest@vendor.example'], send_updates='all')
    calendar.create_scheduling_event(start, start+timedelta(minutes=30), 'guest@vendor.example', 'booking', 'Meeting')
    calendar.create_hub_event(start, start+timedelta(minutes=30), 'Meeting', ['guest@vendor.example'], 'hub-event')
    assert all(event['sendUpdates'] == 'none' for event in service.created_events.values())


def test_scheduling_preflight_refuses_inbox_never_before_creating_an_event():
    from connectors.mail import policy
    with patch.object(policy, 'HUB_GET', return_value={'effective': {'mode': 'never'}}):
        with unittest.TestCase().assertRaises(Refused):
            policy.sending_preflight('ana@acme.example')
