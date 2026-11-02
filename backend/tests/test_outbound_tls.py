"""The outbound pollers on recurring paths share one SSL context per trust mode instead of building one per tick."""
import httpx

from backend import hook_relay, inbound, outbound_tls


def test_context_is_built_once_per_trust_mode(monkeypatch):
    outbound_tls.context.cache_clear()
    built = []
    real = httpx.create_ssl_context
    monkeypatch.setattr(httpx, 'create_ssl_context', lambda **kwargs: built.append(kwargs) or real(**kwargs))
    try:
        first = outbound_tls.context()
        assert all(outbound_tls.context() is first for _ in range(5))
        without_env = outbound_tls.context(trust_env=False)
        assert all(outbound_tls.context(trust_env=False) is without_env for _ in range(5))
        assert without_env is not first
        assert len(built) == 2, built
    finally:
        outbound_tls.context.cache_clear()


class SpyClient(httpx.Client):
    """Records the verify setting each client is built with, and answers from a fake HQ instead of the network."""
    opened = []

    def __init__(self, *args, **kwargs):
        SpyClient.opened.append(kwargs.get('verify'))
        kwargs['transport'] = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
        super().__init__(*args, **kwargs)


def test_recurring_pollers_reuse_the_shared_context(monkeypatch):
    monkeypatch.setattr(httpx, 'Client', SpyClient)
    SpyClient.opened.clear()
    service = inbound.Inbound.__new__(inbound.Inbound)
    service.off, service.url, service.key = False, 'https://hq.example', 'key'
    service.remote('GET', 'inboxes')
    service.remote('GET', 'inboxes')
    assert len(SpyClient.opened) == 2
    assert all(verify is outbound_tls.context() for verify in SpyClient.opened), SpyClient.opened

    SpyClient.opened.clear()
    link = hook_relay.Link('key', 'https://hq.example', 'fingerprint')
    link.call('GET', 'pull')
    link.call('GET', 'pull')
    assert len(SpyClient.opened) == 2
    assert all(verify is outbound_tls.context(trust_env=False) for verify in SpyClient.opened), SpyClient.opened
