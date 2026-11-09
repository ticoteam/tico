"""The demo's clock: TICO_DEMO_NOW freezes datetime.now() in a demo process, and nothing otherwise."""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend import demo_clock

ROOT = Path(__file__).resolve().parents[2]
FROZEN = "2026-11-06T18:00:00+00:00"
PRINT_NOW = "import datetime as dt; from backend import demo_clock; print(dt.datetime.now(dt.timezone.utc).isoformat())"


def run_python(env: dict[str, str]) -> str:
    done = subprocess.run(
        [sys.executable, "-c", PRINT_NOW], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def test_the_demo_time_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TICO_DEMO_NOW", raising=False)
    assert demo_clock.demo_now() is None
    monkeypatch.setenv("TICO_DEMO_NOW", "2026-11-06T18:00:00Z")
    assert demo_clock.demo_now() == datetime(2026, 11, 6, 18, tzinfo=timezone.utc)


def test_a_demo_time_without_a_zone_is_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TICO_DEMO_NOW", "2026-11-06T18:00:00")
    assert demo_clock.demo_now() == datetime(2026, 11, 6, 18, tzinfo=timezone.utc)


def test_a_demo_time_that_is_not_a_time_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TICO_DEMO_NOW", "yesterday")
    with pytest.raises(SystemExit):
        demo_clock.demo_now()


def test_datetime_now_is_the_demo_time_only_when_the_demo_sets_it() -> None:
    env = {key: value for key, value in os.environ.items() if key != "TICO_DEMO_NOW"}
    assert run_python({**env, "TICO_DEMO_NOW": "2026-11-06T18:00:00Z"}) == FROZEN
    assert run_python(env) != FROZEN
