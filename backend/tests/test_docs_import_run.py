"""Document import conversion in a worker: golden output, time, memory, busy, output cap, cancel and cleanup."""

import io
import subprocess
import sys
import tempfile
import threading
import time
import types
import zipfile

import pytest

from backend import docs_import, docs_import_run, docs_import_worker
from backend.tests.test_api import api, headers  # noqa: F401  (the api fixture)

ANA = "ana-test"
BIG = 1_000_000


def docx(*paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def text_pdf(line):
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    """Every temporary file the conversion makes lands here, so a leftover shows up as a file."""
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def fake_worker(tmp_path, body):
    script = tmp_path / "fake_worker.py"
    script.write_text(body)
    return script


def test_normal_files_convert_in_the_worker_exactly_as_in_process(temp_root):
    samples = {
        "notes.md": b"# Refund policy\n\nRefund within 30 days.\n\n\n\nThanks.\n",
        "notes.txt": b"Plain text\r\nsecond line   \r\n",
        "page.html": b"<html><head><title>Hello page</title></head><body><h1>Hi</h1><p>Body &amp; more</p></body></html>",
        "report.docx": docx("Quarterly report", "Revenue rose."),
        "scan.pdf": text_pdf("Hello PDF"),
    }
    for name, data in samples.items():
        expected = docs_import.to_markdown(name, data)
        got = docs_import_run.to_markdown(name, data, max_chars=BIG)
        assert got == expected, name
    assert list(temp_root.iterdir()) == []


def test_time_budget_stops_the_worker_and_writes_nothing(tmp_path, temp_root, monkeypatch):
    monkeypatch.setattr(docs_import_run, "TIME_LIMIT", 1.0)
    monkeypatch.setattr(docs_import_run, "WORKER", fake_worker(tmp_path, "import time\ntime.sleep(60)\n"))
    started = time.monotonic()
    with pytest.raises(ValueError) as caught:
        docs_import_run.to_markdown("slow.pdf", b"%PDF", max_chars=BIG)
    assert str(caught.value) == docs_import_run.TIME_MESSAGE
    assert time.monotonic() - started < 1.0 + 5.0
    assert list(temp_root.iterdir()) == []


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS checks resident memory from the parent, not the limit")
def test_memory_budget_refusal_uses_the_memory_message(temp_root, monkeypatch):
    # A 16 MB Word XML (under the 30 MB cap) cannot be parsed within a 64 MB address space: the worker refuses.
    monkeypatch.setattr(docs_import_run, "MEMORY_LIMIT", 64 * 1024 ** 2)
    big = docx(*["word " * 400] * 8000)
    with pytest.raises(ValueError) as caught:
        docs_import_run.to_markdown("big.docx", big, max_chars=10 * BIG)
    assert str(caught.value) == docs_import_run.MEMORY_MESSAGE
    assert list(temp_root.iterdir()) == []


def test_a_memory_error_in_the_worker_is_reported_as_memory(monkeypatch, tmp_path):
    def out_of_memory(filename, data):
        raise MemoryError()

    monkeypatch.setattr(docs_import_worker, "_limit_memory", lambda limit: None)
    monkeypatch.setitem(sys.modules, "docs_import", types.SimpleNamespace(to_markdown=out_of_memory))
    (tmp_path / "request.json").write_text('{"filename": "a.md", "max_chars": 10, "memory_bytes": 1}')
    (tmp_path / "input").write_bytes(b"x")
    assert docs_import_worker.convert(tmp_path) == {"status": "memory"}


def test_a_pdf_memory_error_is_not_read_as_a_bad_pdf(monkeypatch):
    import pypdf

    def out_of_memory(*args, **kwargs):
        raise MemoryError()

    monkeypatch.setattr(pypdf, "PdfReader", out_of_memory)
    with pytest.raises(MemoryError):
        docs_import.from_pdf(text_pdf("x"))


def test_a_busy_server_refuses_a_third_conversion_at_once(temp_root, monkeypatch):
    held = [docs_import_run._slots.acquire(blocking=False) for _ in range(docs_import_run.MAX_CONCURRENT)]
    try:
        assert all(held)
        with monkeypatch.context() as m:
            def no_worker(*args, **kwargs):
                raise AssertionError("no conversion may start while the server is busy")

            m.setattr(subprocess, "Popen", no_worker)
            with pytest.raises(docs_import_run.Busy):
                docs_import_run.to_markdown("a.md", b"# A\n", max_chars=BIG)
    finally:
        for _ in held:
            docs_import_run._slots.release()
    assert list(temp_root.iterdir()) == []
    # Once the slots are back, a normal import works again.
    assert docs_import_run.to_markdown("a.md", b"# A\n", max_chars=BIG) == docs_import.to_markdown("a.md", b"# A\n")


def test_output_over_the_cap_is_refused_before_any_write(temp_root):
    with pytest.raises(ValueError) as caught:
        docs_import_run.to_markdown("a.md", b"# A longer heading than the cap\n", max_chars=10)
    assert str(caught.value) == docs_import_run.TOO_LONG_MESSAGE
    assert list(temp_root.iterdir()) == []


def test_a_failed_worker_gives_the_plain_failure_and_removes_its_copy(tmp_path, temp_root, monkeypatch):
    monkeypatch.setattr(docs_import_run, "WORKER", fake_worker(tmp_path, "import sys\nsys.exit(3)\n"))
    with pytest.raises(ValueError) as caught:
        docs_import_run.to_markdown("a.md", b"# A\n", max_chars=BIG)
    assert str(caught.value) == docs_import_run.FAILED_MESSAGE
    assert list(temp_root.iterdir()) == []


def test_a_crashed_worker_logs_its_last_stderr_line_capped_and_shows_the_plain_failure(tmp_path, temp_root,
                                                                                        monkeypatch, caplog):
    noisy = "boom " + "x" * 400
    monkeypatch.setattr(docs_import_run, "WORKER", fake_worker(
        tmp_path, f"import sys\nprint('earlier', file=sys.stderr)\nprint({noisy!r}, file=sys.stderr)\nsys.exit(3)\n"))
    with caplog.at_level("WARNING", logger="tico.docs_import"), pytest.raises(ValueError) as caught:
        docs_import_run.to_markdown("a.md", b"# A\n", max_chars=BIG)
    assert str(caught.value) == docs_import_run.FAILED_MESSAGE
    assert noisy not in str(caught.value)
    logged = [r.getMessage() for r in caplog.records if r.name == "tico.docs_import"]
    assert len(logged) == 1
    assert noisy[:docs_import_run.CRASH_LINE_LIMIT] in logged[0]
    assert noisy[:docs_import_run.CRASH_LINE_LIMIT + 1] not in logged[0]
    assert "earlier" not in logged[0]
    assert list(temp_root.iterdir()) == []


def test_a_crash_after_a_large_stderr_logs_only_the_tail(tmp_path, temp_root, monkeypatch, caplog):
    monkeypatch.setattr(docs_import_run, "WORKER", fake_worker(
        tmp_path, "import sys\nsys.stderr.write('x' * 200000)\nsys.stderr.write('\\nthe last line\\n')\nsys.exit(3)\n"))
    with caplog.at_level("WARNING", logger="tico.docs_import"), pytest.raises(ValueError) as caught:
        docs_import_run.to_markdown("a.md", b"# A\n", max_chars=BIG)
    assert str(caught.value) == docs_import_run.FAILED_MESSAGE
    logged = [r.getMessage() for r in caplog.records if r.name == "tico.docs_import"]
    assert len(logged) == 1 and logged[0].endswith("the last line")
    assert list(temp_root.iterdir()) == []


def test_a_failed_memory_probe_is_logged_once(monkeypatch, caplog):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("/bin/ps")

    monkeypatch.setattr(subprocess, "run", unavailable)
    monkeypatch.setattr(docs_import_run, "_probe_warned", False)
    with caplog.at_level("WARNING", logger="tico.docs_import"):
        assert docs_import_run._resident_kb(1) == 0
        assert docs_import_run._resident_kb(1) == 0
    assert len([r for r in caplog.records if r.name == "tico.docs_import"]) == 1


def test_an_unmeasurable_resident_size_counts_as_zero_and_does_not_raise(monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("/bin/ps")

    monkeypatch.setattr(subprocess, "run", unavailable)
    assert docs_import_run._resident_kb(12345) == 0

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ps", timeout=1)

    monkeypatch.setattr(subprocess, "run", slow)
    assert docs_import_run._resident_kb(12345) == 0


def test_a_cancelled_conversion_stops_the_worker_and_writes_nothing(tmp_path, temp_root, monkeypatch):
    monkeypatch.setattr(docs_import_run, "WORKER", fake_worker(tmp_path, "import time\ntime.sleep(60)\n"))
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(docs_import_run.Cancelled):
        docs_import_run.to_markdown("slow.pdf", b"%PDF", max_chars=BIG, cancel=cancel)
    assert time.monotonic() - started < 10
    assert list(temp_root.iterdir()) == []


def test_a_busy_import_route_answers_503_and_saves_no_doc(api):  # noqa: F811
    held = [docs_import_run._slots.acquire(blocking=False) for _ in range(docs_import_run.MAX_CONCURRENT)]
    try:
        response = api.request("POST", "/api/v2/docs/import", headers=headers(ANA),
                               files={"file": ("busy.md", b"# Busy title\n\nBody.\n", "text/markdown")})
    finally:
        for _ in held:
            docs_import_run._slots.release()
    assert response.status_code == 503, response.text
    assert "imports_busy" in response.text
    assert docs_import_run.BUSY_MESSAGE in response.text
    assert "Busy title" not in api.request("GET", "/api/v2/docs", headers=headers(ANA)).text


def test_a_normal_import_route_saves_the_converted_doc(api):  # noqa: F811
    response = api.request("POST", "/api/v2/docs/import", headers=headers(ANA),
                           files={"file": ("handbook.md", b"# Handbook title\n\nBody text.\n", "text/markdown")})
    assert response.status_code == 200, response.text
    assert "Handbook title" in api.request("GET", "/api/v2/docs", headers=headers(ANA)).text
