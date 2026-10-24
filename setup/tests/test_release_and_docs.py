"""The release workflow and the docs that describe the install: lint the workflow and check the API guide."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


@pytest.mark.skipif(not shutil.which("actionlint"), reason="actionlint not installed")
def test_workflows_pass_actionlint():
    # Every workflow's syntax and expressions; the shell inside run: blocks only for the release and docker workflows.
    r = subprocess.run(["actionlint", "-shellcheck=", *map(str, WORKFLOWS)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    strict = [str(ROOT / ".github/workflows" / n) for n in ("release.yml", "docker.yml")]
    r = subprocess.run(["actionlint", *strict], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_release_publishes_the_installer_its_bundle_and_checksums():
    text = (ROOT / ".github/workflows/release.yml").read_text()
    doc = yaml.safe_load(text)
    assert doc[True]["push"]["tags"]  # YAML reads the key `on` as a boolean
    for asset in ("dist/install/install.sh", "dist/install/install-wsl.ps1", "tico-bundle-$VERSION.tar.gz", "dist/install/SHA256SUMS"):
        assert asset in text
    assert "build_install_bundle.py --version \"$VERSION\"" in text
    # Never a release whose images are missing: the version tags point at the validated images first.
    assert text.index("Point the version tags at the validated images") < text.index("gh release create")


def test_api_guide_operation_groups_match_the_spec():
    r = subprocess.run([sys.executable, "scripts/build_api_docs.py", "--check"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
