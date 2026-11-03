"""The document import worker: converts one uploaded file, writes the result and exits.

docs_import_run.py starts it as `python -I docs_import_worker.py <work dir>`, so it reads nothing from the current
directory or the environment. It sets its memory limit before it parses anything, and checks the output length
before it writes a result, so a file that expands past the limit never reaches the server's own process. The parent
kills it at the time limit and removes the work directory after it exits.
"""
import json
import resource
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))  # docs_import is standard library only


def _limit_memory(limit_bytes):
    # macOS rejects memory rlimits; the parent enforces a resident-memory budget there.
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))


def convert(work):
    root = Path(work)
    request = json.loads((root / "request.json").read_text())
    _limit_memory(request["memory_bytes"])
    import docs_import
    try:
        text, guess = docs_import.to_markdown(request["filename"], (root / "input").read_bytes())
    except ValueError as exc:
        return {"status": "refused", "message": str(exc)}
    except MemoryError:
        return {"status": "memory"}
    if len(text) > request["max_chars"]:
        return {"status": "too_long"}
    return {"status": "ok", "text": text, "guess": guess}


if __name__ == "__main__":
    work = Path(sys.argv[1])
    (work / "result.json").write_text(json.dumps(convert(work)))
