import pytest

from clients import harness_manifests as manifests


def test_manifests_load_and_reject_unknown_capabilities():
    rows = manifests.load()
    assert {"codex", "claude", "gemini", "antigravity", "grok", "cursor", "musecode", "pi"} <= set(rows)
    assert rows["antigravity"]["runtime"] == "gemini" and rows["antigravity"]["capabilities"]["readiness"] is False
    with pytest.raises(manifests.ManifestError, match="unknown capability"):
        manifests.parse({"id": "x", "runtime": "x", "capabilities": {"telepathy": True}})
    with pytest.raises(manifests.ManifestError, match="weekly_usage"):
        manifests.parse({"id": "x", "runtime": "x", "capabilities": {"weekly_usage": "sometimes"}})
