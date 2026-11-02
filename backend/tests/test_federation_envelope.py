"""The signed text and the namespace of federation envelopes (backend/federation_envelope.py). Pure: no database, no HQ."""
import re

import pytest

from backend import federation_envelope as E


def test_key_ids_are_stable_and_shaped():
    private = E.generate()
    raw = E.raw_public(private)
    assert len(raw) == E.PUBLIC_KEY_BYTES
    assert re.fullmatch(r"fk_[0-9a-f]{24}", E.key_id_of(raw))
    assert E.key_id_of(raw) == E.key_id_of(E.raw_public(E.private_from(E.private_bytes(private))))


def test_signature_checks_the_whole_envelope():
    private = E.generate()
    raw = E.raw_public(private)
    body = b'{"type":"catch_up","thread":"t","after":4}'
    sig = E.sign(private, "gi_" + "a" * 24, "gi_" + "b" * 24, "env-abcdefgh12345678", 1_800_000_000, body)
    text = E.signed_text("gi_" + "a" * 24, "gi_" + "b" * 24, "env-abcdefgh12345678", 1_800_000_000, body)
    assert E.verify(raw, sig, text)
    # Any change to the sender, recipient, time or body breaks it.
    assert not E.verify(raw, sig, E.signed_text("gi_" + "c" * 24, "gi_" + "b" * 24, "env-abcdefgh12345678", 1_800_000_000, body))
    assert not E.verify(raw, sig, E.signed_text("gi_" + "a" * 24, "gi_" + "b" * 24, "env-abcdefgh12345678", 1_800_000_001, body))
    assert not E.verify(raw, sig, E.signed_text("gi_" + "a" * 24, "gi_" + "b" * 24, "env-abcdefgh12345678", 1_800_000_000,
                                                body + b" "))


@pytest.mark.parametrize("actor, install, expected", [
    ("human:ana@gi_" + "a" * 24, "gi_" + "a" * 24, True),
    ("human:ana@gi_" + "a" * 24, "gi_" + "b" * 24, False),   # another install's namespace
    ("human:ana", "gi_" + "a" * 24, False),                  # not qualified: a local name never crosses
    ("keeper", "gi_" + "a" * 24, False),
])
def test_an_actor_belongs_to_one_install(actor, install, expected):
    assert E.in_namespace(actor, install) is expected


def test_qualify_adds_the_install_once():
    install = "gi_" + "a" * 24
    assert E.qualify("human:ana", install) == "human:ana@" + install
    assert E.qualify("human:ana@" + install, install) == "human:ana@" + install
    assert E.install_of("human:ana@" + install) == install


def test_payloads_are_canonical_and_bounded():
    assert E.payload_bytes({"b": 1, "a": "x"}) == E.payload_bytes({"a": "x", "b": 1})
    assert E.parse_payload(E.payload_bytes({"type": "catch_up"})) == {"type": "catch_up"}
    assert E.parse_payload(b"[1, 2]") is None
    assert E.parse_payload(b"x" * (E.MAX_BODY + 1)) is None
