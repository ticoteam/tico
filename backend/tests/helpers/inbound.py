"""Helpers that other tests share, moved out of test_inbound.py so they import no test file."""

import time
import pytest
from backend import inbound


@pytest.fixture(autouse=True)
def no_live_inbound(monkeypatch):
    monkeypatch.delenv('TICO_INBOUND_KEY', raising=False)
    monkeypatch.setenv('TICO_INBOUND_URL', 'https://hq.example.test')

    # Isolate before the API fixture starts: replacing tick later cannot stop an already-submitted callback.
    # These contract tests exercise sync/tick explicitly, with no concurrent timer competing for their state.
    async def idle_loop(self, stop):
        await stop.wait()

    monkeypatch.setattr(inbound.Inbound, 'loop', idle_loop)

    # These tests invoke sync/tick explicitly; the app's real timer must not race their fake HQ.
    async def no_background_sync(self, stop):
        pass

    monkeypatch.setattr(inbound.Inbound, 'loop', no_background_sync)


def seed(api):
    svc = api.app.state.inbound
    with svc.store.transaction() as c:
        for ident in ('ibx_a', 'ibx_b', 'ibx_c'):
            inbound.save_inbox(
                c,
                {
                    'inbox_id': ident,
                    'kind': 'email',
                    'name': ident,
                    'address': ident + '@example.test',
                    'state': 'active',
                    'created_by': 'human:ana',
                    'period_end': None,
                    'cancel_pending': 0,
                },
            )
    return svc


def page(seq=1, inbox='ibx_b', ident='ses:msg:ibx_b', body='Your verification code is 123456.'):
    return {
        'deliveries': [
            {
                'hq_seq': seq,
                'delivery_id': ident,
                'inbox_id': inbox,
                'kind': 'email',
                'received_at': time.time(),
                'from': 'Login <login@example.test>',
                'subject': 'Sign in',
                'text': body,
            }
        ],
        'gaps': [],
        'cursor': seq,
    }
