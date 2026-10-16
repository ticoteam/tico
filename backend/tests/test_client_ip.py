"""Only configured proxies can choose a visitor's daily action bucket."""
from types import SimpleNamespace
from starlette.requests import Request
from backend.client_ip import client_ip, ip_bucket


def test_proxy_chain_cloudflare_spoofing_and_ipv6_bucket():
    settings = SimpleNamespace(trusted_proxies=('127.0.0.1/32', '172.20.0.2/32'), client_ip_header='x-forwarded-for')
    def address(peer, headers):
        request = Request({'type': 'http', 'client': (peer, 1234),
                           'headers': [(k.encode(), v.encode()) for k, v in headers.items()]})
        return client_ip(request, settings)
    headers = {'x-forwarded-for': '192.0.2.99, 198.51.100.9, 172.20.0.2', 'cf-connecting-ip': '192.0.2.1'}
    assert address('127.0.0.1', headers) == '198.51.100.9'
    assert address('203.0.113.8', headers) == '203.0.113.8'
    settings.client_ip_header = 'cf-connecting-ip'
    assert address('127.0.0.1', headers) == '192.0.2.1'
    assert address('203.0.113.8', headers) == '203.0.113.8'
    assert ip_bucket('2001:db8:1234:5678::1') == ip_bucket('2001:db8:1234:5678::abcd')
    assert ip_bucket('2001:db8:1234:5679::1') != ip_bucket('2001:db8:1234:5678::1')


def test_ipv4_mapped_addresses_are_their_ipv4_addresses():
    from backend.client_ip import trusted_proxies
    assert ip_bucket('::ffff:1.2.3.4') == '1.2.3.4'
    assert ip_bucket('::ffff:1.2.3.4') != ip_bucket('::ffff:5.6.7.8')
    assert trusted_proxies('::ffff:172.30.254.2, ::ffff:10.0.0.0/104') == ('172.30.254.2/32', '10.0.0.0/8')
    settings = SimpleNamespace(trusted_proxies=trusted_proxies('127.0.0.1/32,172.30.254.2/32'),
                               client_ip_header='x-forwarded-for')
    def address(peer, headers):
        request = Request({'type': 'http', 'client': (peer, 1234),
                           'headers': [(k.encode(), v.encode()) for k, v in headers.items()]})
        return client_ip(request, settings)
    # A dual-stack listener reports IPv4 peers as mapped addresses: the proxy is still trusted, the visitor unwrapped.
    assert address('::ffff:172.30.254.2', {'x-forwarded-for': '::ffff:198.51.100.9'}) == '198.51.100.9'
    assert address('::ffff:127.0.0.1', {'x-forwarded-for': '198.51.100.7, ::ffff:172.30.254.2'}) == '198.51.100.7'
    assert address('::ffff:203.0.113.8', {'x-forwarded-for': '198.51.100.9'}) == '203.0.113.8'
