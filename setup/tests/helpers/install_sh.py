"""Helpers that other tests share, moved out of test_install_sh.py so they import no test file."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


SCRIPTS = ROOT / "scripts"


def _builder():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_install_bundle", SCRIPTS / "build_install_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _builder()
