"""Helpers that other tests share, moved out of test_demo.py so they import no test file."""

from datetime import datetime, timezone
from fastapi.testclient import TestClient
from backend.app import create_app


NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)  # a Friday, so a week in review exists


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client
