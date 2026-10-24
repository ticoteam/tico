"""Harness manifests are the one place that says what a model CLI can do (docs/harnesses.md). Code asks a capability;
it never compares a CLI's name. The scan below keeps it that way, and the manifests themselves must load."""

import re
from pathlib import Path

import pytest

from clients import harness_manifests as manifests

ROOT = Path(__file__).resolve().parents[2]
NAMES = r"(?:codex|claude|gemini|antigravity|grok|pi|cursor|musecode|claude-code|gemini-cli|cursor-agent|muse-code)"
Q = rf"""["']{NAMES}["']"""
# A comparison, a membership test, a match arm, or a table keyed by CLI names.
CHECKS = [re.compile(p) for p in (
    rf"[=!]==?\s*{Q}", rf"{Q}\s*[=!]=", rf"\bin\s*[\(\[\{{]\s*{Q}", rf"^\s*{Q}(\s*\|\s*{Q})*\s*(if\b.*)?=>",
    rf"matches!\(.*{Q}", rf"[=&]\s*&?\s*[\(\[]\s*{Q}\s*,", rf"^\s*[\(\[]\s*{Q}\s*,", rf"^\s*{Q}\s*:\s*[\{{\(\[]",
    rf"\[\s*{Q}[^\]]*\]\.includes\(", rf"\bcase\s+{Q}\s*:", rf"=\s*\{{\s*{NAMES}\s*:")]
# Where naming a CLI is the point: the adapter factories that pick the code that drives each one, and the display
# names, logos and demo data that are about the vendor, not about what the CLI can do.
ALLOWED = {
    "runner-rs/src/hosts",            # one adapter per CLI, and the factory that picks it
    "ui/app/avatars.js",              # vendor logos
    "backend/demo_seed.py",           # sample bots
    "backend/openapi_v2.py",          # API schema; its `cursor` is a page cursor
}
ALLOWED_LINES = (re.compile(r"=> Host::"), re.compile(r"\bconst HOSTS\b"), re.compile(r"limitId"),
                 re.compile(r"\bCLI_SETTINGS\s*="), re.compile(r"\bHARNESS_WORDS\s*="))


def sources():
    for base, glob in (("backend", "*.py"), ("clients", "*.py"), ("ui/app", "*.js"), ("runner-rs/src", "**/*.rs")):
        for path in sorted((ROOT / base).glob(glob)):
            rel = path.relative_to(ROOT).as_posix()
            if "/tests/" not in rel and not any(rel == a or rel.startswith(a + "/") for a in ALLOWED):
                yield rel, path


def test_no_code_compares_cli_names():
    found = []
    for rel, path in sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if path.suffix == ".rs" and line.strip() == "#[cfg(test)]":
                break                           # Rust unit tests sit at the end of their file
            if line.lstrip().startswith(("#", "//", "*", "/*")) or any(p.search(line) for p in ALLOWED_LINES):
                continue
            if any(p.search(line) for p in CHECKS):
                found.append(f"{rel}:{number}: {line.strip()}")
    assert not found, "Ask the harness manifest's [capabilities] instead (docs/harnesses.md):\n" + "\n".join(found)


def test_manifests_load_and_reject_unknown_capabilities():
    rows = manifests.load()
    assert {"codex", "claude", "gemini", "antigravity", "grok", "cursor", "musecode", "pi"} <= set(rows)
    assert rows["antigravity"]["runtime"] == "gemini" and rows["antigravity"]["capabilities"]["readiness"] is False
    with pytest.raises(manifests.ManifestError, match="unknown capability"):
        manifests.parse({"id": "x", "runtime": "x", "capabilities": {"telepathy": True}})
    with pytest.raises(manifests.ManifestError, match="weekly_usage"):
        manifests.parse({"id": "x", "runtime": "x", "capabilities": {"weekly_usage": "sometimes"}})


def test_server_tables_come_from_the_manifests():
    from backend import model_login, providers
    assert set(model_login.RUNTIMES) == {"codex", "claude", "musecode", "grok", "cursor"}
    assert model_login.PASTE_RUNTIMES == ("claude",)
    assert manifests.caps("musecode")["sign_out"] is True
    assert "musecode" in model_login.SIGN_OUT_RUNTIMES
    assert providers.MODEL_KEYS["codex"] == ("OPENAI_API_KEY",) and "gemini" not in providers.MODEL_KEYS


def test_grok_and_cursor_relay_computer_sign_in_and_sign_out():
    for runtime in ("grok", "cursor"):
        caps = manifests.caps(runtime)
        assert caps["browser_sign_in"] and caps["sign_out"]
        assert caps["profile_home"] == "HOME"
        assert caps["paste_code"] is False
        assert caps["sign_in_status"] != "none"

    assert manifests.caps("cursor")["subscription_profiles"] is False
