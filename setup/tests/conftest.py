import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TICO_SETUP_HOME", str(tmp_path / "home"))
    for k in ("TICO_OIDC_CLIENT_SECRET", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_TUNNEL_TOKEN", "OPENAI_API_KEY",
              "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "TICO_INBOUND_KEY", "TICO_INBOUND_URL", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(k, raising=False)
