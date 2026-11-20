"""Link previews (backend/link_preview.py): the server reads an outside page for a link chip, and never one it must not
reach: private, loopback, link-local and metadata addresses, by name, by number and behind a redirect, within its size
and time caps."""

import io

import pytest

from backend import link_preview as LP
from backend.tests.helpers.api import headers, setup_attempt
from backend.tests.helpers.openapi_v2 import conforms
from clients import doc_fetch as F

PUBLIC = "93.184.216.34"
NAMES = {
    "shop.acme.example": [PUBLIC],
    "cdn.acme.example": ["93.184.216.35"],
    "inside.acme.example": ["10.0.0.7"],
    "meta.acme.example": ["169.254.169.254"],
    "mixed.acme.example": [PUBLIC, "127.0.0.1"],
    "2130706433": ["127.0.0.1"],  # what the system's resolver makes of 127.0.0.1 written as one number
}
PAGE = (
    b"<!doctype html><html><head><title>Ignored when og:title is there</title>"
    b'<meta property="og:title" content="Cedar raised bed &amp; cover">'
    b'<meta property="og:site_name" content="Acme Garden">'
    b'<meta name="description" content="  A four-foot bed,\n  untreated cedar. ">'
    b'<meta property="og:image" content="/img/bed.png"></head><body><svg><title>not this</title></svg></body></html>'
)


def png(width=900, height=600):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), (90, 120, 70)).save(out, "PNG")
    return out.getvalue()


def reply(status=200, ctype="text/html; charset=utf-8", body=b"", location=None):
    head = f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\n" + (f"Location: {location}\r\n" if location else "")
    return head.encode() + f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def html(body):
    return reply(body=body)


def picture(ctype, body):
    return reply(ctype=ctype, body=body)


def to(where):
    return reply(302, location=where)


class Answer(io.BytesIO):
    """A raw answer that remembers how far it was read once its connection closed it."""

    far = 0

    def close(self):
        self.far = self.tell()
        super().close()


class Net:
    """Names and sockets as tables: `routes` maps (host, path) to the raw answer. Every connection is kept in `sent`
    as (host, path, the address connected to, the request's headers), and every answer's stream in `streams`, so a
    test sees how far it was read."""

    def __init__(self):
        self.routes, self.sent, self.streams = {}, [], []

    def resolver(self, host, port):
        if host not in NAMES:
            raise F.FetchError("dns", f"{host} does not resolve")
        return NAMES[host]

    def opener(self, ip, port, tls, host, timeout):
        net = self

        class Sock:
            def settimeout(self, value):
                pass

            def sendall(self, data):
                self.request = data.decode()

            def makefile(self, *a, **k):
                line, *rest = self.request.split("\r\n")
                path = line.split()[1].split("?")[0]
                sent = {k.lower(): v for k, _, v in (h.partition(": ") for h in rest if h)}
                net.sent.append((host, path, ip, sent))
                stream = Answer(net.routes.get((host, path), reply(404)))
                net.streams.append(stream)
                return stream

            def close(self):
                pass

        return Sock()


@pytest.fixture
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(LP, "resolver", n.resolver)
    monkeypatch.setattr(LP, "opener", n.opener)
    return n


def test_a_good_page_gives_its_title_site_description_and_a_small_picture(net):
    net.routes[("shop.acme.example", "/beds/cedar")] = html(PAGE)
    net.routes[("shop.acme.example", "/img/bed.png")] = picture("image/png", png())

    p = LP.read_page("https://shop.acme.example/beds/cedar")

    assert (p.title, p.site_name, p.description) == (
        "Cedar raised bed & cover",
        "Acme Garden",
        "A four-foot bed, untreated cedar.",
    )
    assert p.image_url == "https://shop.acme.example/img/bed.png"
    assert p.image[:3] == b"\xff\xd8\xff" and (p.image_width, p.image_height) == (480, 320)  # re-encoded, made small
    host, _path, connected, sent = net.sent[0]
    assert (host, connected, sent["host"]) == ("shop.acme.example", PUBLIC, "shop.acme.example")  # the checked address
    assert "cookie" not in sent and "authorization" not in sent


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://2130706433/",  # 127.0.0.1 as one number
        "http://[::ffff:169.254.169.254]/",  # the metadata address inside IPv6
        "http://printer.local/",
        "http://inside.acme.example/",
        "http://meta.acme.example/",
        "http://mixed.acme.example/",  # one of its addresses is this machine
        "https://shop.acme.example:8443/",
        "https://ana:secret@shop.acme.example/",
        "file:///etc/passwd",
        "ftp://shop.acme.example/",
    ],
)
def test_an_address_it_must_not_reach_is_never_requested(net, url):
    with pytest.raises(F.FetchError):
        LP.fetch(url, LP.PAGE_TYPES, cut=True)
    assert LP.read_page(url) is None
    assert net.sent == []


def test_every_redirect_hop_is_checked_and_at_most_three_are_followed(net):
    routes, sent = net.routes, net.sent
    routes[("shop.acme.example", "/to-metadata")] = to("http://169.254.169.254/latest/meta-data/iam")
    routes[("shop.acme.example", "/to-inside")] = to("https://inside.acme.example/admin")
    assert LP.read_page("https://shop.acme.example/to-metadata") is None
    assert LP.read_page("https://shop.acme.example/to-inside") is None
    assert [s[1] for s in sent] == ["/to-metadata", "/to-inside"]  # the private hop itself was never requested

    sent.clear()
    for n in range(4):
        routes[("shop.acme.example", f"/r{n}")] = to(f"/r{n + 1}")
    routes[("shop.acme.example", "/r4")] = html(PAGE)
    routes[("shop.acme.example", "/r1-ok")] = to("/r2")
    assert LP.read_page("https://shop.acme.example/r0") is None  # four redirects: one too many
    assert [s[1] for s in sent] == ["/r0", "/r1", "/r2", "/r3"]
    assert LP.read_page("https://shop.acme.example/r1-ok").title == "Cedar raised bed & cover"  # three are followed


def test_a_page_is_read_only_as_far_as_its_cap_and_a_large_picture_is_not_kept(net):
    net.routes[("shop.acme.example", "/long")] = html(b"<p>" + b"x" * (4 * LP.PAGE_BYTES))
    page = LP.fetch("https://shop.acme.example/long", LP.PAGE_TYPES, cut=True)
    assert len(page.body) == LP.PAGE_BYTES
    assert LP.PAGE_BYTES < net.streams[-1].far < LP.PAGE_BYTES + 64 * 1024 + 200  # stopped at the cap, not the end

    net.routes[("cdn.acme.example", "/big.png")] = picture("image/png", b"\x89PNG" + b"\0" * (2 * LP.IMAGE_BYTES))
    with pytest.raises(F.FetchError, match="larger"):
        LP.fetch("https://cdn.acme.example/big.png", LP.IMAGE_TYPES, cut=False)
    assert LP.IMAGE_BYTES < net.streams[-1].far < LP.IMAGE_BYTES + 64 * 1024 + 200


def test_only_html_and_raster_pictures_are_read(net):
    routes = net.routes
    routes[("shop.acme.example", "/sheet.pdf")] = picture("application/pdf", b"%PDF-1.7")
    routes[("shop.acme.example", "/svg")] = html(PAGE.replace(b"/img/bed.png", b"/img/bed.svg"))
    routes[("shop.acme.example", "/img/bed.svg")] = picture("image/svg+xml", b"<svg onload='alert(1)'/>")
    assert LP.read_page("https://shop.acme.example/sheet.pdf") is None
    p = LP.read_page("https://shop.acme.example/svg")
    assert p.title == "Cedar raised bed & cover" and p.image is None


def test_a_slow_page_ends_at_the_time_cap(net, monkeypatch):
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(LP, "clock", lambda: float(next(ticks)))  # each look at the clock is a second later
    net.routes[("shop.acme.example", "/slow")] = html(b"<p>" + b"x" * LP.PAGE_BYTES)
    with pytest.raises(F.FetchError, match="longer than 5 seconds"):
        LP.fetch("https://shop.acme.example/slow", LP.PAGE_TYPES, cut=True)
    assert 0 < net.streams[-1].far < 5 * 64 * 1024  # a few reads, then it stopped
    assert LP.read_page("https://shop.acme.example/slow") is None


def ask(api, url, token="ana-test"):
    return api.get("/api/v2/link-preview", params={"url": url}, headers=headers(token))


def test_the_route_fetches_once_for_everyone_serves_the_picture_and_matches_its_contract(api, monkeypatch, net):
    routes, sent = net.routes, net.sent
    routes[("shop.acme.example", "/beds/cedar")] = html(PAGE)
    routes[("shop.acme.example", "/img/bed.png")] = picture("image/png", png())

    first = ask(api, "https://shop.acme.example/beds/cedar#reviews")
    assert first.status_code == 200, first.text
    again = ask(api, "https://shop.acme.example/beds/cedar", token="ben-test")
    assert again.json() == first.json()
    assert len(sent) == 2  # the page and its picture, once, for both people

    body = first.json()
    assert body["url"] == "https://shop.acme.example/beds/cedar"
    assert body["image"].startswith("/api/v2/link-preview/images/")
    served = api.get(body["image"], headers=headers())
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.content[:3] == b"\xff\xd8\xff"

    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    schema = document["paths"]["/api/v2/link-preview"]["get"]["responses"]["200"]["content"]["application/json"]
    assert conforms(body, schema["schema"], document) is None

    assert ask(api, "https://shop.acme.example/nothing-here").status_code == 404
    assert ask(api, "https://shop.acme.example/nothing-here").status_code == 404
    assert [s[1] for s in sent[2:]] == ["/nothing-here"]  # a page without a preview is kept too


def test_the_route_refuses_private_addresses_bots_and_too_many_fetches(api, monkeypatch, net):
    routes, sent = net.routes, net.sent
    for url in ("http://169.254.169.254/latest/meta-data/", "http://localhost:8766/api/v2/me", "file:///etc/passwd"):
        r = ask(api, url)
        assert (r.status_code, r.json()["error"]["code"]) == (422, "blocked"), url
    assert ask(api, "http://inside.acme.example/").status_code == 404  # its name resolves inside: nothing read
    assert sent == []

    assert api.get("/api/v2/link-preview/images/" + "0" * 64, headers=headers()).status_code == 404
    assert api.get("/api/v2/link-preview", params={"url": "https://shop.acme.example/"}).status_code == 401
    _, _, attempt = setup_attempt(api)  # a bot's own run: the app's people only
    assert ask(api, "https://shop.acme.example/", token=attempt["token"]).json()["error"]["code"] == "identity"

    routes[("shop.acme.example", "/p")] = html(PAGE.replace(b'<meta property="og:image" content="/img/bed.png">', b""))
    for n in range(LP.FETCHES_PER_MINUTE):
        assert ask(api, f"https://shop.acme.example/p?n={n}", token="cara-test").status_code == 200
    over = ask(api, "https://shop.acme.example/p?n=over", token="cara-test")
    assert (over.status_code, over.json()["error"]["code"]) == (429, "rate_limited")
    assert ask(api, "https://shop.acme.example/p?n=0", token="cara-test").status_code == 200  # kept: not counted
    assert ask(api, "https://shop.acme.example/p?n=over", token="ben-test").status_code == 200  # per person
