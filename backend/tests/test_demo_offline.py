"""A demo build never reaches the network, whoever calls it (tests and rehearsal scripts call demo.build directly, not
through `tico demo`). It used to ask the Tico Gateway during an org rule check, so two builds of the same date stored
"auth" on one and "unavailable" on the next whenever the network blipped (test_demo_company's same-date check)."""

import socket

from backend import demo
from backend.tests.test_demo_company import NOW


def test_a_demo_build_looks_up_no_remote_host(tmp_path, monkeypatch):
    looked_up = []
    real = socket.getaddrinfo

    def recording(host, *args, **kwargs):
        looked_up.append(host)
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", recording)
    demo.build(tmp_path, now=NOW)
    remote = sorted({str(h) for h in looked_up if h not in (None, "") and not demo.loopback_address(h)})
    assert not remote, f"the demo build looked up {remote}"
