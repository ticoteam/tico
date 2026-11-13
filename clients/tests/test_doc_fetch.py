"""`tico docs fetch` (clients/doc_fetch.py): what it refuses and what it never sends. No network, no DNS:
names resolve through a table and connections are scripted byte streams."""

import io

import pytest

from clients import doc_fetch as F

PUBLIC = "93.184.216.34"


def reply(status=200, ctype="text/html; charset=utf-8", body=b"", location=None):
    head = f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\n"
    head += f"Location: {location}\r\n" if location else ""
    return head.encode() + f"Content-Length: {len(body)}\r\n\r\n".encode() + body


class Net:
    """DNS and sockets for one test: `names` maps a host to its addresses and `replies` are the raw answers,
    in order. Everything the code sent is kept in `sent` with the address it went to."""

    def __init__(self, names, *replies, down=None):
        self.names, self.replies, self.sent, self.connected = names, list(replies), [], []
        self.down = down or {}  # address -> the OSError connecting to it raises

    def resolver(self, host, port):
        return self.names[host]

    def opener(self, ip, port, tls, host, timeout):
        self.connected.append((ip, port, tls, host))
        if ip in self.down:
            raise self.down[ip]
        outer, raw = self, self.replies.pop(0)

        class Sock:
            def sendall(self, data):
                outer.sent.append((ip, data.decode()))

            def makefile(self, *a, **k):
                return io.BytesIO(raw)

            def close(self):
                pass

        return Sock()

    def fetch(self, url, **kw):
        return F.fetch(url, resolver=self.resolver, opener=self.opener, **kw)


@pytest.mark.parametrize("address", ["::ffff:127.0.0.1"])
def test_no_internal_address_is_public(address):
    assert not F.public_address(address)


def test_a_public_address_is_public():
    assert F.public_address(PUBLIC) and F.public_address("2606:4700:4700::1111")


@pytest.mark.parametrize(
    "url,code",
    [
        ("file:///etc/passwd", "scheme"),
        ("https://user:pw@example.com/", "userinfo"),
        ("http://169.254.169.254/latest/meta-data/", "private_address"),
    ],
)
def test_an_unsafe_address_is_refused_before_any_connection(url, code):
    net = Net({})
    with pytest.raises(F.FetchError) as refused:
        net.fetch(url)
    assert refused.value.code == code and not net.connected


def test_a_name_that_resolves_to_an_internal_address_is_refused_even_beside_a_public_one():
    net = Net({"docs.example.com": [PUBLIC, "10.1.2.3"], "internal.example.com": ["169.254.169.254"]})
    for host in net.names:
        with pytest.raises(F.FetchError) as refused:
            net.fetch("https://%s/x" % host)
        assert refused.value.code == "private_address"
    assert not net.connected


def test_the_connection_goes_to_the_address_that_was_checked():
    net = Net({"docs.example.com": [PUBLIC]}, reply(body=b"<title>Hi</title><p>Hello</p>"))
    result = net.fetch("https://docs.example.com/a")
    assert net.connected == [(PUBLIC, 443, True, "docs.example.com")]
    assert net.sent[0][1].startswith("GET /a HTTP/1.1\r\nHost: docs.example.com\r\n")
    assert result["title"] == "Hi" and result["text"] == "Hello" and not result["truncated"]


GOOGLE_V6, GOOGLE_V4 = "2001:4860:4802:38::15", "216.239.36.21"  # syften.com's, 2026-11-13
UNREACHABLE = OSError(101, "Network is unreachable")  # a computer with no IPv6 route


def test_an_address_that_cannot_be_reached_falls_back_to_the_next_checked_one():
    net = Net({"syften.com": [GOOGLE_V6, GOOGLE_V4]}, reply(body=b"<p>Hello</p>"), down={GOOGLE_V6: UNREACHABLE})
    assert net.fetch("https://syften.com/")["text"] == "Hello"
    assert [ip for ip, *_ in net.connected] == [GOOGLE_V6, GOOGLE_V4]
    assert [ip for ip, _ in net.sent] == [GOOGLE_V4]


def test_a_failure_after_a_fallback_is_reported_beside_the_skipped_address():
    net = Net({"syften.com": [GOOGLE_V6, GOOGLE_V4]}, reply(), down={GOOGLE_V6: UNREACHABLE})
    real = net.opener

    def opener(*a):
        sock = real(*a)
        sock.sendall = lambda data: (_ for _ in ()).throw(ConnectionResetError(104, "Connection reset by peer"))
        return sock

    with pytest.raises(F.FetchError) as failed:
        F.fetch("https://syften.com/", resolver=net.resolver, opener=opener)
    msg = failed.value.message
    assert "[Errno 101] Network is unreachable" in msg
    assert f"{GOOGLE_V4}: " in msg and "[Errno 104] Connection reset by peer" in msg


def test_a_tls_failure_on_a_later_address_is_reported():
    import ssl

    net = Net(
        {"syften.com": [GOOGLE_V6, GOOGLE_V4]}, down={GOOGLE_V6: UNREACHABLE, GOOGLE_V4: ssl.SSLError(1, "bad cert")}
    )
    with pytest.raises(F.FetchError) as failed:
        net.fetch("https://syften.com/")
    assert "Network is unreachable" in failed.value.message and "bad cert" in failed.value.message


def test_a_tls_failure_is_not_retried_on_the_next_address():
    import ssl

    net = Net({"syften.com": [GOOGLE_V4, GOOGLE_V6]}, reply(), down={GOOGLE_V4: ssl.SSLError(1, "bad cert")})
    with pytest.raises(F.FetchError) as failed:
        net.fetch("https://syften.com/")
    assert "bad cert" in failed.value.message
    assert [ip for ip, *_ in net.connected] == [GOOGLE_V4]  # the certificate is the name's, not the address's


def test_an_address_that_hangs_leaves_time_for_the_next_one():
    now = [0.0]
    net = Net({"syften.com": [GOOGLE_V6, GOOGLE_V4]}, reply(body=b"<p>Hello</p>"))
    real = net.opener

    def opener(ip, port, tls, host, timeout):
        if ip == GOOGLE_V6:  # a firewall drops the packets: the connect waits out its whole timeout
            now[0] += timeout
            raise TimeoutError("timed out")
        return real(ip, port, tls, host, timeout)

    result = F.fetch("https://syften.com/", resolver=net.resolver, opener=opener, clock=lambda: now[0])
    assert result["text"] == "Hello" and [ip for ip, _ in net.sent] == [GOOGLE_V4]


def test_the_name_is_tried_in_the_order_the_system_resolves_it(monkeypatch):
    infos = [(None, None, 6, "", (GOOGLE_V4, 443)), (None, None, 6, "", (GOOGLE_V6, 443, 0, 0))] * 2
    monkeypatch.setattr(F.socket, "getaddrinfo", lambda *a, **k: infos)
    assert F.resolve("syften.com", 443) == [GOOGLE_V4, GOOGLE_V6]  # sorted as text, IPv6 came first


def test_a_network_failure_says_its_errno_and_reason():
    net = Net(
        {"syften.com": [GOOGLE_V6, GOOGLE_V4]}, down={GOOGLE_V6: UNREACHABLE, GOOGLE_V4: TimeoutError("timed out")}
    )
    with pytest.raises(F.FetchError) as failed:
        net.fetch("https://syften.com/")
    assert failed.value.code == "network"
    assert "[Errno 101] Network is unreachable" in failed.value.message and GOOGLE_V6 in failed.value.message
    assert "TimeoutError: timed out" in failed.value.message and GOOGLE_V4 in failed.value.message


def test_a_redirect_to_an_internal_address_is_refused_and_never_connected_to():
    for target, addresses in (
        ("https://evil.example.com/steal", ["10.0.0.9"]),
        ("http://169.254.169.254/latest/meta-data/", None),
    ):
        net = Net({"docs.example.com": [PUBLIC], "evil.example.com": addresses}, reply(302, location=target))
        with pytest.raises(F.FetchError) as refused:
            net.fetch("https://docs.example.com/a")
        assert refused.value.code == "private_address"
        assert net.connected == [(PUBLIC, 443, True, "docs.example.com")]  # only the first hop was ever made


def test_at_most_five_redirects_are_followed():
    net = Net({"docs.example.com": [PUBLIC]}, *([reply(301, location="/next")] * 6))
    with pytest.raises(F.FetchError) as refused:
        net.fetch("https://docs.example.com/a")
    assert refused.value.code == "redirects" and len(net.connected) == 6
    ok = Net({"docs.example.com": [PUBLIC]}, *([reply(301, location="/next")] * 5), reply(body=b"<p>done</p>"))
    assert ok.fetch("https://docs.example.com/a")["text"] == "done"


def test_a_page_over_five_megabytes_is_cut_and_says_so():
    big = b"x" * (F.MAX_BYTES + 5000)
    net = Net({"docs.example.com": [PUBLIC]}, reply(ctype="text/plain", body=big))
    result = net.fetch("https://docs.example.com/big.txt", max_chars=1_000_000)
    assert result["truncated"] and len(result["text"]) <= F.MAX_BYTES
    net = Net({"docs.example.com": [PUBLIC]}, reply(ctype="application/pdf", body=big))
    with pytest.raises(F.FetchError) as refused:
        net.fetch("https://docs.example.com/big.pdf")
    assert refused.value.code == "too_large"


def test_a_credential_is_sent_only_to_the_host_it_belongs_to():
    env = {"GH_TOKEN": "ghs_secret", "GOOGLE_ACCESS_TOKEN": "ya29.secret"}
    net = Net(
        {"docs.example.com": [PUBLIC], "api.github.com": [PUBLIC], "docs.google.com": [PUBLIC]},
        reply(302, location="https://api.github.com/repos/o/r"),  # a redirect from elsewhere carries nothing
        reply(ctype="application/json", body=b"{}"),
        reply(ctype="text/plain", body=b"hello"),
    )
    net.fetch("https://docs.example.com/a", env=env)
    net.fetch("https://docs.google.com/document/d/abc123/edit", env=env)
    sent = [text for _, text in net.sent]
    assert "Authorization" not in sent[0] and "ghs_secret" not in sent[0]
    assert "Authorization: Bearer ghs_secret" in sent[1]  # the hop to api.github.com gets GitHub's token
    assert "ya29.secret" not in sent[0] + sent[1] and "Authorization: Bearer ya29.secret" in sent[2]
    assert F.credential_headers("api.github.com", env)["Authorization"] == "Bearer ghs_secret"
    for host in (
        "github.com",
        "raw.githubusercontent.com",
        "api.github.com.evil.example.com",
        "example.com",
        "docs.google.com.evil.example.com",
        "notgoogleapis.com",
        "drive.google.com",
    ):
        assert F.credential_headers(host, env) == {}
    assert F.credential_headers("sheets.googleapis.com", env) == {"Authorization": "Bearer ya29.secret"}
