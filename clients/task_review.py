"""Shared CLI attachment payloads (stdlib only)."""
import base64
from pathlib import Path


def attachment(path, name=None):
    path = Path(path)
    data = path.read_bytes()
    body = {"name": name or path.name}
    try:
        body["text"] = data.decode("utf-8")
    except UnicodeDecodeError:
        body["content_base64"] = base64.b64encode(data).decode("ascii")
    return body
