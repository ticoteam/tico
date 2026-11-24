import pytest

from clients import harness_manifests as manifests


def test_manifests_load_and_reject_unknown_capabilities():
    rows = manifests.load()
    assert {"codex", "claude", "gemini", "antigravity", "grok", "cursor", "musecode", "pi"} <= set(rows)
    # Antigravity is a runtime of its own, from its own manifest.
    assert (
        rows["antigravity"]["runtime"] == "antigravity"
        and rows["antigravity"]["manifest"] == "antigravity"
        and rows["antigravity"]["capabilities"]["sign_in_status"] == "command"
        and rows["antigravity"]["capabilities"]["resume_command"] == "agy --conversation"
        and rows["antigravity"]["capabilities"]["persistent_session"]
    )
    with pytest.raises(manifests.ManifestError, match="unknown capability"):
        manifests.parse({"id": "x", "runtime": "x", "capabilities": {"telepathy": True}})
    with pytest.raises(manifests.ManifestError, match="weekly_usage"):
        manifests.parse({"id": "x", "runtime": "x", "capabilities": {"weekly_usage": "sometimes"}})
