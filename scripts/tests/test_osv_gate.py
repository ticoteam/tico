"""The advisory gate (scripts/osv_gate.py), offline: a recorded OSV-Scanner result for the pre-#4077 snapshot, and the
drift check on a small tree. The real scanner runs on the same fixture, offline, in PR checks' advisories job
(`scripts/osv_gate.py selftest`)."""

import datetime
import json
from pathlib import Path

from scripts import osv_gate

FIXTURE = osv_gate.FIXTURE
TODAY = datetime.date(2026, 11, 18)


def recorded():
    return osv_gate.findings(json.loads((FIXTURE / "result.json").read_text()))


def allowlist():
    return osv_gate.load(osv_gate.ROOT, "allowlist.json")


def failing(found, entries, today=TODAY):
    return {(f.source, i) for f in osv_gate.blocking(found, entries, today) for i in f.ids}


def test_the_pre_4077_snapshot_fails_on_each_advisory_that_release_fixed_and_notices_never_fail():
    expected = json.loads((FIXTURE / "expected.json").read_text())
    found = recorded()
    fails = failing(found, allowlist())
    # uv's CA store (every pinning file), rustls-webpki, SQLite, rustix and js-yaml (both vendored copies).
    assert {(source, i) for source, ids in expected["fail"].items() for i in ids} <= fails
    # A RustSec "unmaintained" record stays visible and never fails; the patched GLib is accepted for its own source.
    notices = {(f.source, i) for f in found if f.notice for i in f.ids}
    assert ("app/Cargo.lock", "RUSTSEC-2024-0370") in notices
    assert not {(s, i) for s, i in fails if i == "RUSTSEC-2024-0370"}
    assert ("app/Cargo.lock", "RUSTSEC-2024-0429") not in fails
    failures, printed, accepted, _ = osv_gate.evaluate(found, allowlist(), TODAY)
    assert osv_gate.report(failures, printed, accepted, []) == 1


def test_a_disposition_covers_only_its_advisory_package_and_source_and_only_until_it_expires():
    glib = next(f for f in recorded() if f.package == "glib")
    entry = {
        "id": "RUSTSEC-2024-0429",
        "package": "glib",
        "sources": ["app/Cargo.lock"],
        "reason": "patched in-tree",
        "expires": "2027-01-01",
    }
    assert not failing([glib], [entry])
    # The same advisory on the same package from another lockfile is a new finding.
    assert failing([glib._replace(source="recorder/Cargo.lock")], [entry])
    assert failing([glib._replace(package="glib-sys")], [entry])
    # An alias names the same advisory group.
    assert not failing([glib], [{**entry, "id": "GHSA-wrw7-89jp-8q8g"}])
    # Expired: the finding fails again until someone reviews it.
    assert failing([glib], [{**entry, "expires": "2026-11-17"}])
    # Every entry needs a reason and an expiry no more than a year out, or the gate fails.
    for broken in ({**entry, "reason": ""}, {**entry, "expires": "2028-01-01"}, {**entry, "expires": "soon"}):
        failures, *_ = osv_gate.evaluate([glib], [broken], TODAY)
        assert failures, broken


def tree(tmp_path, monkeypatch, files):
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    monkeypatch.setattr(
        osv_gate,
        "tracked",
        lambda root, *specs: sorted(
            str(p.relative_to(root))
            for p in Path(root).rglob("*")
            if p.is_file() and (not specs or any(str(p.relative_to(root)).startswith(s) for s in specs))
        ),
    )
    return tmp_path


def test_drift_catches_a_swapped_vendored_file_unlisted_vendored_code_and_an_unlisted_uv_pin(tmp_path, monkeypatch):
    root = tree(
        tmp_path,
        monkeypatch,
        {
            "ui/vendor/js-yaml.min.js": "4.3.2",
            "ui/vendor/LICENSE-js-yaml": "MIT",
            "Dockerfile": "FROM ghcr.io/astral-sh/uv:0.13.9@sha256:abc AS uv\n",
            "docs/setup.md": "pip install uv==0.1.0\n",
        },
    )
    vendored = {
        "ui/vendor/js-yaml.min.js": {
            "package": "js-yaml",
            "ecosystem": "npm",
            "version": "4.3.2",
            "sha256": osv_gate.sha256_entry(root, "ui/vendor/js-yaml.min.js"),
        }
    }
    tools = {"uv": {"ecosystem": "PyPI", "pins": {"Dockerfile": ["0.13.9"]}}}
    data = root / osv_gate.DATA
    data.mkdir(parents=True)
    (data / "vendored.json").write_text(json.dumps(vendored))
    (data / "tools.json").write_text(json.dumps(tools))
    assert osv_gate.drift(root) == []
    # An older copy dropped in under the same name, a new library, and a script pinning another uv.
    (root / "ui/vendor/js-yaml.min.js").write_text("4.1.0")
    (root / "ui/vendor/marked.min.js").write_text("marked")
    (root / "scripts/install.sh").write_text("python3 -m pip install uv==0.13.0\n")
    problems = "\n".join(osv_gate.drift(root))
    assert "ui/vendor/js-yaml.min.js changed" in problems
    assert "ui/vendor/marked.min.js is vendored code" in problems
    assert "scripts/install.sh pins uv 0.13.0" in problems
    assert "docs/setup.md" not in problems and "LICENSE" not in problems


def test_each_vendored_file_and_pinning_script_reaches_the_scanner_as_its_own_source(tmp_path):
    sources = osv_gate.custom_inventories(FIXTURE, tmp_path)
    packages = {}
    for path, source in sources.items():
        for item in json.loads(Path(path).read_text())["results"][0]["packages"]:
            packages.setdefault(source, set()).add(tuple(item["package"][k] for k in ("ecosystem", "name", "version")))
    assert packages["ui/vendor/js-yaml.min.js"] == {("npm", "js-yaml", "4.1.0")}
    assert packages["templates/employee-repo/ui/vendor/js-yaml.min.js"] == {("npm", "js-yaml", "4.1.0")}
    assert packages["Dockerfile"] == {("PyPI", "uv", "0.13.1")}
    assert packages[".github/workflows/pr-checks.yml"] == {("PyPI", "uv", "0.13.0")}
