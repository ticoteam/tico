"""runner/version.py names the Rust runner's protocol version, which the shim matches against the binary's `--version`.
The release is separate: build.rs stamps it from TICO_VERSION and `tico-runner --release` prints it (runner-rs/src/runner.rs
RUNNER_RELEASE), so releases need no source bump."""
import re
from pathlib import Path

from runner.version import RUNNER_VERSION

ROOT = Path(__file__).resolve().parents[2]


def test_the_version_is_the_rust_runners():
    rust = (ROOT / "runner-rs/src/runner.rs").read_text()
    assert re.search(r'pub const RUNNER_VERSION: &str = "([^"]+)";', rust).group(1) == RUNNER_VERSION


def test_the_release_is_stamped_from_the_build_and_defaults_to_dev():
    build = (ROOT / "runner-rs/build.rs").read_text()
    rust = (ROOT / "runner-rs/src/runner.rs").read_text()
    assert 'env!("TICO_RUNNER_RELEASE")' in rust
    assert 'std::env::var("TICO_VERSION")' in build and '"dev"' in build
    assert 'cargo:rustc-env=TICO_RUNNER_RELEASE=' in build


def test_the_release_checks_read_the_release_not_the_source_version():
    # Release validation and the image build compare `tico-runner --release` with the tag; nothing greps source versions.
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "tico-runner --release" in ci and "tico-runner --release" in dockerfile
    assert "runner/version.py" not in ci and "Cargo.toml must say" not in ci
