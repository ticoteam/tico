"""The Company page is retired, and with it the self-hosted install's reader origin (--reader-origin,
TICO_READER_ORIGIN). An install set up while it existed still loads its saved settings and writes none of it."""

from pathlib import Path

import pytest
import yaml

from setup import contract
from setup.cli import parser
from setup.settings import Settings, from_saved

ROOT = Path(__file__).resolve().parents[2]


def saved(**extra):
    return {
        **Settings(
            domain="tico.example.com",
            target="ssh",
            auth="google",
            client_id="fictional-client",
            owner_email="me@example.com",
            front_door="cloudflared",
        ).public(),
        **extra,
    }


def test_a_saved_reader_origin_loads_and_is_written_nowhere():
    """Before, a saved reader origin on the tunnel front door stopped every later run ("needs --front-door caddy")."""
    s = from_saved(saved(reader_origin="https://page.acme.example"), "TICO_READER_ORIGIN=https://page.acme.example\n")
    assert "TICO_READER_ORIGIN" not in s.to_env()
    assert not hasattr(s, "reader_origin")


def test_an_older_private_env_with_a_reader_origin_is_not_carried():
    s = from_saved(saved(), "TICO_READER_ORIGIN=https://page.acme.example\n")
    assert "TICO_READER_ORIGIN" not in s.to_env()


def test_the_wizard_no_longer_takes_a_reader_origin():
    with pytest.raises(SystemExit):
        parser().parse_args(["--reader-origin", "https://page.acme.example"])


def test_the_server_is_not_given_a_reader_origin():
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    assert "TICO_READER_ORIGIN" not in services["server"]["environment"]
    assert "TICO_READER_ORIGIN" not in contract.ENV_ORDER
