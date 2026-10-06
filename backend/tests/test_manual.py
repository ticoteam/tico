"""The Tico manual: this release's docs, searchable, read-only and apart from the company's docs (docs/docs.md)."""

from pathlib import Path

from backend import manual
from backend.tests.test_api import api  # noqa: F401  (the api fixture)
from backend.tests.test_docs import call, make

ROOT = Path(__file__).resolve().parents[2]


def test_the_manual_is_read_only_and_never_mixed_with_company_docs(api):
    mine = make(api, "Watchers at Acme", "Our watchers poll the warehouse every hour.")
    # The Docs page's search and the company's list know nothing of the manual.
    assert [r["path"] for r in call(api, "GET", "docs/search?q=watchers")["results"]] == [mine["path"]]
    assert not any(d["path"].startswith(("manual", "docs/")) for d in call(api, "GET", "docs")["docs"])
    # Together, the company's own doc comes first and each result says which collection it is from.
    both = call(api, "GET", "docs/search?q=watchers&collection=all")["results"]
    assert any(r["collection"] == "company" and r["id"] == mine["id"] for r in both)
    assert any(r["collection"] == "manual" for r in both)
    assert [r["score"] for r in both] == sorted((r["score"] for r in both), reverse=True)
    # Nothing writes to it.
    call(api, "PATCH", "docs/manual:watchers", {"version": 1, "body": "x"}, expected=405)
    call(api, "POST", "docs/manual:watchers/restore", {"version": 1}, expected=405)
    assert call(api, "GET", "docs/manual/watchers")["doc"]["body"] == (ROOT / "docs/watchers.md").read_text()


def test_the_manual_is_rebuilt_when_the_release_or_a_file_changes(monkeypatch, tmp_path):
    (tmp_path / "widgets.md").write_text("# Widgets\n\n## Polishing\n\nUse the polish command on a widget.\n")
    monkeypatch.setattr(manual, "DOCS_DIR", tmp_path)
    monkeypatch.setenv("TICO_VERSION", "1.0.0")
    first = manual.search("polish widget")[0]
    assert first["version"] == "1.0.0" and first["url"].endswith("/v1.0.0/docs/widgets.md#polishing") and first["path"] == "docs/widgets.md"
    monkeypatch.setenv("TICO_VERSION", "1.0.1")
    assert manual.search("polish widget")[0]["url"].startswith("https://github.com/ticoteam/tico/blob/v1.0.1/")
    (tmp_path / "widgets.md").write_text("# Widgets\n\n## Buffing\n\nBuff the widget with wax.\n")
    assert manual.search("polish") == [] and manual.search("buff wax")[0]["title"] == "Widgets > Buffing"

