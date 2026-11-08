"""Helpers that other tests share, moved out of test_demo_finance.py so they import no test file."""

from fastapi.testclient import TestClient
from backend.app import create_app


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client
