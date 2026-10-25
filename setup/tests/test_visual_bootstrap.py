"""Fresh bootstrap delivers only a checksum/source-matched optional operator."""
import hashlib
import io
import os
import subprocess
import tarfile

import pytest

from setup import remote, visual_config, visual_contract as wire


def bundle(*, revision="a" * 40, manifest=True):
    names = ("setup/__init__.py", "setup/cli.py", "setup/settings.py", "setup/envfile.py", "setup/contract.py", "setup/route53.py",
             "setup/visual_config.py", "setup/visual_contract.py", "compose.yaml")
    files = {name: (b"name: tico\n" if name == "compose.yaml" else b"fictional source\n") for name in names}
    runtime = {"revision": revision, "runtime_files": {n: "b" * 64 for n in wire.RUNTIME_FILES}}
    if manifest:
        files["installer-manifest.json"] = wire.encoded({"protocol": wire.PROTOCOL, "revision": revision,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            "runtime_files": runtime["runtime_files"]})
    files["VERSION"] = b"1.2.3\n"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, data in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    return output.getvalue(), runtime


def installer():
    namespace = {}
    exec(remote.VISUAL_BOOTSTRAP, namespace)
    return namespace["install"]


def test_fresh_supported_bundle_is_verified_inert_until_apply_and_preserves_existing_files(tmp_path):
    blob, runtime = bundle()
    (tmp_path / "compose.yaml").write_bytes(b"name: tico\n")
    (tmp_path / ".env").write_bytes(b"PRIVATE=unchanged\n")
    (tmp_path / "hub.sqlite").write_bytes(b"existing data")
    calls = []
    def fetch(url, limit):
        calls.append(url)
        assert url.startswith("https://github.com/ticoteam/tico/releases/download/v1.2.3/")
        return (hashlib.sha256(blob).hexdigest() + "  tico-bundle-v1.2.3.tar.gz\n").encode() if url.endswith("SHA256SUMS") else blob
    assert installer()(tmp_path, "v1.2.3", runtime, fetch=fetch)
    assert len(calls) == 2
    assert visual_config.installer_capability(tmp_path)["revision"] == runtime["revision"]
    assert (tmp_path / ".env").read_bytes() == b"PRIVATE=unchanged\n"
    assert (tmp_path / "hub.sqlite").read_bytes() == b"existing data"
    assert not (tmp_path / visual_config.JOURNAL).exists()
    assert not (tmp_path / visual_config.LOCK).exists()
    assert (tmp_path / "setup/visual_config.py").stat().st_mode & 0o777 == 0o644


def test_old_missing_corrupt_or_mismatched_release_never_installs_source(tmp_path):
    (tmp_path / "compose.yaml").write_bytes(b"name: tico\n")
    good, runtime = bundle()
    old, _ = bundle(manifest=False)
    def source(blob, digest=None):
        return lambda url, limit: ((digest or hashlib.sha256(blob).hexdigest()) + "  tico-bundle-v1.2.3.tar.gz\n").encode() if url.endswith("SHA256SUMS") else blob
    for fetch, observed in ((source(old), runtime), (source(good, "0" * 64), runtime),
                             (source(good), runtime | {"revision": "c" * 40})):
        with pytest.raises((ValueError, KeyError)):
            installer()(tmp_path, "v1.2.3", observed, fetch=fetch)
        assert not (tmp_path / "setup").exists()
    def missing(*args):
        raise OSError("missing release")
    with pytest.raises(OSError):
        installer()(tmp_path, "v1.2.3", runtime, fetch=missing)
    assert not (tmp_path / "setup").exists()
    with pytest.raises(ValueError):
        installer()(tmp_path, "latest", runtime, fetch=lambda *args: pytest.fail("unselected release"))


def test_destination_conflicts_and_source_symlinks_refuse_before_any_write(tmp_path):
    (tmp_path / "compose.yaml").write_bytes(b"name: tico\n")
    (tmp_path / "setup").mkdir()
    (tmp_path / "setup/settings.py").write_bytes(b"unrelated existing source")
    blob, runtime = bundle()
    def fetch(url, limit):
        return (hashlib.sha256(blob).hexdigest() + "  tico-bundle-v1.2.3.tar.gz\n").encode() if url.endswith("SHA256SUMS") else blob
    with pytest.raises(ValueError):
        installer()(tmp_path, "v1.2.3", runtime, fetch=fetch)
    assert not (tmp_path / "setup/visual_config.py").exists()
    assert (tmp_path / "setup/settings.py").read_bytes() == b"unrelated existing source"
    (tmp_path / "setup/settings.py").unlink()
    (tmp_path / "setup").rmdir()
    (tmp_path / "setup").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        installer()(tmp_path, "v1.2.3", runtime, fetch=fetch)
    assert not (tmp_path / "installer-manifest.json").exists()


def test_generated_bootstrap_app_readiness_precedes_optional_source_failure(tmp_path, monkeypatch):
    root, commands = tmp_path / "install", tmp_path / "commands"
    commands.mkdir()
    monkeypatch.setattr(remote.contract, "REMOTE_DIR", str(root))
    monkeypatch.setattr(remote, "docker_install_lines", lambda *args, **kwargs: [])
    (commands / "docker").write_text("#!/bin/sh\nexit 0\n")
    (commands / "chown").write_text("#!/bin/sh\nexit 0\n")   # the script gives the company file to the server's user
    # The optional child fails, but records whether the real readiness marker
    # already exists. No Docker, network, host log or provider is accessible.
    (commands / "python3").write_text('#!/bin/sh\nif [ -f "' + str(root / '.bootstrapped') + '" ]; then '
        'touch "' + str(tmp_path / 'optional_observed_ready') + '"; fi\nexit 9\n')
    for path in commands.iterdir():
        path.chmod(0o755)
    script = remote.bootstrap_script(os_family="ubuntu", domain="acme.tico.dev", front_door="caddy",
                                     compose=b"name: tico\n", compose_url="", env_b64=remote.env_b64("PRIVATE=unchanged\n"))
    # Relocate only the fixed host log sink into this fixture's filesystem.
    script = script.replace("/var/log/tico-setup.log", str(tmp_path / "bootstrap.log"))
    result = subprocess.run(["bash"], input=script.encode(), env={**os.environ, "PATH": str(commands) + os.pathsep + os.defpath},
                            capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "optional_observed_ready").exists()
    assert (root / ".env").read_text() == "PRIVATE=unchanged\n"
