"""Link previews (backend/link_preview.py, backend/link_preview_child.py): the server reads an outside page for a link
chip, and never one it must not reach (private, loopback, link-local and metadata addresses, by name, by number and
behind a redirect), within its size caps and a deadline it kills at; a picture that would decode huge never does; only a
person in the app, from the app, makes it read; a rehearsal copy reads nothing; what it keeps stays within its budget
and is never served past its day."""

import io
import os
import sys
import time

import pytest

from backend import link_preview as LP
from backend import link_preview_child as C
from backend.tests.helpers.api import headers, setup_attempt
from backend.tests.helpers.assistant import assistant_turn
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
    monkeypatch.setattr(C, "resolver", n.resolver)
    monkeypatch.setattr(C, "opener", n.opener)
    # The route's reading process, in this one: the fake network reaches no other process. Its picture is still made
    # small in a real one.
    monkeypatch.setattr(LP, "fetch_in_child", lambda url, ends: C.read(url, LP.DEADLINE))
    return n


def test_a_good_page_gives_its_title_site_description_and_a_small_picture(net):
    net.routes[("shop.acme.example", "/beds/cedar")] = html(PAGE)
    net.routes[("shop.acme.example", "/img/bed.png")] = picture("image/png", png())

    found = C.read("https://shop.acme.example/beds/cedar", 5)
    assert (found["title"], found["site_name"], found["description"]) == (
        "Cedar raised bed & cover",
        "Acme Garden",
        "A four-foot bed, untreated cedar.",
    )
    assert found["image_url"] == "https://shop.acme.example/img/bed.png" and found["image"][:4] == b"\x89PNG"
    host, _path, connected, sent = net.sent[0]
    assert (host, connected, sent["host"]) == ("shop.acme.example", PUBLIC, "shop.acme.example")  # the checked address
    assert "cookie" not in sent and "authorization" not in sent

    p = LP.read_page("https://shop.acme.example/beds/cedar", LP.Limits())
    assert p.image[:3] == b"\xff\xd8\xff" and (p.image_width, p.image_height) == (480, 320)  # re-encoded, made small


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
        C.fetch(url, C.PAGE_TYPES, 5)
    assert C.read(url, 5) is None
    assert net.sent == []


def test_every_redirect_hop_is_checked_and_at_most_three_are_followed(net):
    routes, sent = net.routes, net.sent
    routes[("shop.acme.example", "/to-metadata")] = to("http://169.254.169.254/latest/meta-data/iam")
    routes[("shop.acme.example", "/to-inside")] = to("https://inside.acme.example/admin")
    assert C.read("https://shop.acme.example/to-metadata", 5) is None
    assert C.read("https://shop.acme.example/to-inside", 5) is None
    assert [s[1] for s in sent] == ["/to-metadata", "/to-inside"]  # the private hop itself was never requested

    sent.clear()
    for n in range(4):
        routes[("shop.acme.example", f"/r{n}")] = to(f"/r{n + 1}")
    routes[("shop.acme.example", "/r4")] = html(PAGE)
    routes[("shop.acme.example", "/r1-ok")] = to("/r2")
    assert C.read("https://shop.acme.example/r0", 5) is None  # four redirects: one too many
    assert [s[1] for s in sent] == ["/r0", "/r1", "/r2", "/r3"]
    assert C.read("https://shop.acme.example/r1-ok", 5)["title"] == "Cedar raised bed & cover"  # three are followed


def test_a_page_is_read_only_as_far_as_its_cap_and_a_large_picture_is_not_kept(net):
    net.routes[("shop.acme.example", "/long")] = html(b"<p>" + b"x" * (4 * C.PAGE_BYTES))
    _final, body = C.fetch("https://shop.acme.example/long", C.PAGE_TYPES, 5)
    assert len(body) == C.PAGE_BYTES
    assert C.PAGE_BYTES < net.streams[-1].far < C.PAGE_BYTES + 64 * 1024 + 200  # stopped at the cap, not the end

    net.routes[("cdn.acme.example", "/big.png")] = picture("image/png", b"\x89PNG" + b"\0" * (2 * C.IMAGE_BYTES))
    with pytest.raises(F.FetchError, match="larger"):
        C.fetch("https://cdn.acme.example/big.png", C.IMAGE_TYPES, 5)
    assert C.IMAGE_BYTES < net.streams[-1].far < C.IMAGE_BYTES + 64 * 1024 + 200


def test_only_html_and_raster_pictures_are_read(net):
    routes = net.routes
    routes[("shop.acme.example", "/sheet.pdf")] = picture("application/pdf", b"%PDF-1.7")
    routes[("shop.acme.example", "/svg")] = html(PAGE.replace(b"/img/bed.png", b"/img/bed.svg"))
    routes[("shop.acme.example", "/img/bed.svg")] = picture("image/svg+xml", b"<svg onload='alert(1)'/>")
    assert C.read("https://shop.acme.example/sheet.pdf", 5) is None
    found = C.read("https://shop.acme.example/svg", 5)
    assert found["title"] == "Cedar raised bed & cover" and found["image"] is None


def test_a_slow_body_ends_at_the_reading_deadline(net, monkeypatch):
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(C, "clock", lambda: float(next(ticks)))  # each look at the clock is a second later
    net.routes[("shop.acme.example", "/slow")] = html(b"<p>" + b"x" * C.PAGE_BYTES)
    with pytest.raises(F.FetchError, match="longer than 5 seconds"):
        C.fetch("https://shop.acme.example/slow", C.PAGE_TYPES, 5)
    assert 0 < net.streams[-1].far < 5 * 64 * 1024  # a few reads, then it stopped
    assert C.read("https://shop.acme.example/slow", 5) is None


def test_a_reader_that_never_answers_is_killed_at_the_deadline(monkeypatch, tmp_path):
    # Anything the reading process can be stuck in (a name lookup, headers sent a byte at a time, chunk framing) ends
    # with the process: the request is answered at the deadline whatever the site does.
    pid_file = tmp_path / "pid"
    stuck = "import os, select; open(os.environ['STUCK_PID'], 'w').write(str(os.getpid())); select.select([], [], [])"
    monkeypatch.setenv("STUCK_PID", str(pid_file))
    monkeypatch.setattr(LP, "CHILD", [sys.executable, "-c", stuck])
    monkeypatch.setattr(LP, "DEADLINE", 0.5)
    assert LP.read_page("https://shop.acme.example/", LP.Limits()) is None
    with pytest.raises(ProcessLookupError):  # killed and reaped, not left running
        os.kill(int(pid_file.read_text()), 0)


def test_the_reading_process_answers_and_refuses_what_it_must():
    # The real process, end to end, with an address it refuses before any lookup: the protocol works, nothing is read.
    assert LP._run(["page", "http://10.0.0.1/", "1"], time.monotonic() + 5) == b"null"
    assert LP.fetch_in_child("http://10.0.0.1/", time.monotonic() + 5) is None


def test_a_picture_with_too_many_pixels_is_refused_from_its_header_and_two_decode_at_once(tmp_path):
    from PIL import Image

    bomb = tmp_path / "bomb.png"
    Image.new("1", (5000, 4000)).save(bomb, "PNG")  # 20 MP in a few kilobytes
    assert bomb.stat().st_size < 64 * 1024
    with pytest.raises(ValueError, match="too many pixels"):
        C.small(bomb, tmp_path / "out.jpg")
    assert not (tmp_path / "out.jpg").exists()  # refused before decoding
    limits = LP.Limits()
    assert LP.small_picture(bomb.read_bytes(), time.monotonic() + 5, limits) is None
    assert LP.small_picture(png(), time.monotonic() + 5, limits)[1:] == (480, 320)

    assert limits.decoding.acquire(blocking=False) and limits.decoding.acquire(blocking=False)
    assert not limits.decoding.acquire(blocking=False)  # two at once, no more
    assert LP.small_picture(png(), time.monotonic() + 0.2, limits) is None  # no slot before its deadline: no picture


def test_one_page_is_one_key_and_the_kept_pictures_stay_in_their_budget(api, monkeypatch):
    assert LP.normal("HTTPS://Shop.Acme.example/beds?b=2&utm_source=x&a=1&fbclid=y#top") == (
        "https://shop.acme.example/beds?a=1&b=2"
    )
    assert LP.normal("https://shop.acme.example") == "https://shop.acme.example/"

    monkeypatch.setattr(LP, "IMAGES_BYTES", 250)
    with api.app.state.store.transaction() as c:
        for n in range(4):
            LP.keep(c, f"{n:064x}", f"https://shop.acme.example/{n}", LP.Preview(f"Bed {n}", "Acme", image=b"j" * 100))
        left = [r[0] for r in c.execute("SELECT url FROM link_previews ORDER BY created_at")]
    assert left == ["https://shop.acme.example/2", "https://shop.acme.example/3"]  # the oldest went first


def ask(api, url, token="ana-test", **more):
    return api.get("/api/v2/link-preview", params={"url": url}, headers={**headers(token), **more})


def test_the_route_fetches_once_for_everyone_serves_the_picture_and_matches_its_contract(net, api):
    routes, sent = net.routes, net.sent
    routes[("shop.acme.example", "/beds/cedar")] = html(PAGE)
    routes[("shop.acme.example", "/img/bed.png")] = picture("image/png", png())

    first = ask(api, "https://shop.acme.example/beds/cedar#reviews", **{"Sec-Fetch-Site": "same-origin"})
    assert first.status_code == 200, first.text
    again = ask(api, "https://shop.acme.example/beds/cedar?utm_source=news", token="ben-test")
    assert again.json() == first.json()
    assert len(sent) == 2  # the page and its picture, once, for both people

    body = first.json()
    assert body["url"] == "https://shop.acme.example/beds/cedar"
    assert body["image"].startswith("/api/v2/link-preview/images/")
    served = api.get(body["image"], headers=headers())
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert 23 * 3600 < int(served.headers["cache-control"].split("max-age=")[1]) <= 24 * 3600
    assert served.content[:3] == b"\xff\xd8\xff"

    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    schema = document["paths"]["/api/v2/link-preview"]["get"]["responses"]["200"]["content"]["application/json"]
    assert conforms(body, schema["schema"], document) is None

    assert ask(api, "https://shop.acme.example/nothing-here").status_code == 404
    assert ask(api, "https://shop.acme.example/nothing-here").status_code == 404
    assert [s[1] for s in sent[2:]] == ["/nothing-here"]  # a page without a preview is kept too

    with api.app.state.store.transaction() as c:  # its day is over
        c.execute("UPDATE link_previews SET expires_at = '2000-01-01T00:00:00.000000Z'")
    assert api.get(body["image"], headers=headers()).status_code == 404


def test_expired_rows_are_deleted_at_the_first_request_not_only_at_a_write(api):
    with api.app.state.store.transaction() as c:
        LP.keep(c, "e" * 64, "https://shop.acme.example/old", LP.Preview("Old", "Acme", image=b"j"))
        c.execute("UPDATE link_previews SET expires_at = '2000-01-01T00:00:00.000000Z'")
    assert api.get("/api/v2/link-preview/images/" + "f" * 64, headers=headers()).status_code == 404
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM link_previews").fetchone()[0] == 0


def test_only_a_person_in_the_app_from_the_app_makes_it_read(net, api):
    sent = net.sent
    assert api.get("/api/v2/link-preview", params={"url": "https://shop.acme.example/"}).status_code == 401
    _, _, attempt = setup_attempt(api)  # a bot's own run
    _, turn = assistant_turn(api, "ana-test")  # the Assistant acting for Ana
    for token in (attempt["token"], turn["token"]):
        r = ask(api, "https://shop.acme.example/", token=token)
        assert (r.status_code, r.json()["error"]["code"]) == (403, "identity"), r.text
        image = api.get("/api/v2/link-preview/images/" + "0" * 64, headers=headers(token))
        assert image.status_code == 403

    for more in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Cookie": "tico_session=x"}):
        r = ask(api, "https://shop.acme.example/", **more)  # another site's page, or an older browser's cookie
        assert (r.status_code, r.json()["error"]["code"]) == (403, "cross_site"), more
    assert sent == []


def test_bad_and_private_addresses_are_refused_and_a_rehearsal_copy_reads_nothing(net, api):
    routes, sent = net.routes, net.sent
    for url in ("http://[::1", "https://shop.acme.example:99999/", "https://shop.acme.example/" + "x" * 3000):
        r = ask(api, url)
        assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_url"), url
    for url in ("http://169.254.169.254/latest/meta-data/", "http://localhost:8766/api/v2/me", "file:///etc/passwd"):
        r = ask(api, url)
        assert (r.status_code, r.json()["error"]["code"]) == (422, "blocked"), url
    assert ask(api, "http://inside.acme.example/").status_code == 404  # its name resolves inside: nothing read
    assert sent == []

    routes[("shop.acme.example", "/p")] = html(PAGE.replace(b'<meta property="og:image" content="/img/bed.png">', b""))
    api.app.state.store.settings.rehearsal = True
    assert ask(api, "https://shop.acme.example/p").status_code == 404
    assert sent == []


def test_each_person_starts_at_most_thirty_fetches_a_minute(net, api):
    net.routes[("shop.acme.example", "/p")] = html(
        PAGE.replace(b'<meta property="og:image" content="/img/bed.png">', b"")
    )
    for n in range(LP.FETCHES_PER_MINUTE):
        assert ask(api, f"https://shop.acme.example/p?n={n}", token="cara-test").status_code == 200
    over = ask(api, "https://shop.acme.example/p?n=over", token="cara-test")
    assert (over.status_code, over.json()["error"]["code"]) == (429, "rate_limited")
    assert ask(api, "https://shop.acme.example/p?n=0", token="cara-test").status_code == 200  # kept: not counted
    assert ask(api, "https://shop.acme.example/p?n=over", token="ben-test").status_code == 200  # per person
