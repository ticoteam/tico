"""`hub docs fetch` (clients/doc_fetch.py): what it refuses and what it never sends. No network, no DNS:
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

    def __init__(self, names, *replies):
        self.names, self.replies, self.sent, self.connected = names, list(replies), [], []

    def resolver(self, host, port):
        return self.names[host]

    def opener(self, ip, port, tls, host, timeout):
        self.connected.append((ip, port, tls, host))
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


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "fd00:ec2::254", "::ffff:127.0.0.1"])
def test_no_internal_address_is_public(address):
    assert not F.public_address(address)


def test_a_public_address_is_public():
    assert F.public_address(PUBLIC) and F.public_address("2606:4700:4700::1111")


@pytest.mark.parametrize("url,code", [
    ("file:///etc/passwd", "scheme"), ("https://user:pw@example.com/", "userinfo"),
    ("http://169.254.169.254/latest/meta-data/", "private_address")])
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


def test_a_redirect_to_an_internal_address_is_refused_and_never_connected_to():
    for target, addresses in (("https://evil.example.com/steal", ["10.0.0.9"]),
                              ("http://169.254.169.254/latest/meta-data/", None)):
        net = Net({"docs.example.com": [PUBLIC], "evil.example.com": addresses}, reply(302, location=target))
        with pytest.raises(F.FetchError) as refused:
            net.fetch("https://docs.example.com/a")
        assert refused.value.code == "private_address"
        assert net.connected == [(PUBLIC, 443, True, "docs.example.com")]      # only the first hop was ever made


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
    net = Net({"docs.example.com": [PUBLIC], "api.github.com": [PUBLIC], "docs.google.com": [PUBLIC]},
              reply(302, location="https://api.github.com/repos/o/r"),   # a redirect from elsewhere carries nothing
              reply(ctype="application/json", body=b"{}"),
              reply(ctype="text/plain", body=b"hello"))
    net.fetch("https://docs.example.com/a", env=env)
    net.fetch("https://docs.google.com/document/d/abc123/edit", env=env)
    sent = [text for _, text in net.sent]
    assert "Authorization" not in sent[0] and "ghs_secret" not in sent[0]
    assert "Authorization: Bearer ghs_secret" in sent[1]      # the hop to api.github.com gets GitHub's token
    assert "ya29.secret" not in sent[0] + sent[1] and "Authorization: Bearer ya29.secret" in sent[2]
    assert F.credential_headers("api.github.com", env)["Authorization"] == "Bearer ghs_secret"
    for host in ("github.com", "raw.githubusercontent.com", "api.github.com.evil.example.com", "example.com",
                 "docs.google.com.evil.example.com", "notgoogleapis.com", "drive.google.com"):
        assert F.credential_headers(host, env) == {}
    assert F.credential_headers("sheets.googleapis.com", env) == {"Authorization": "Bearer ya29.secret"}
