"""Harness manifests and the runner's tool installer (runner/harness_tools.py).

No network: `npm` is a stub script first on PATH that "installs" a shell script per package and
answers `npm view` from a file the test controls, so the real subprocess flow runs end to end.
"""
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from runner import harness_tools as H

FAKE_NPM = """#!/bin/sh
# fake npm: `install --prefix DIR pkg@version` and `view pkg version`
case "$1" in
  view) cat "$FAKE_LATEST"; exit 0 ;;
  install)
    [ -f "$FAKE_FAIL" ] && { echo "npm ERR! network" >&2; exit 1; }
    prefix="$3"
    for last; do :; done
    pkg="${last%@*}"; version="${last##*@}"
    [ "$version" = latest ] && version="$(cat "$FAKE_LATEST")"
    name="$(printf '%s' "$pkg" | sed 's|.*/||; s|-cli$||; s|-coding-agent$||')"
    case "$pkg" in @anthropic-ai/claude-code) name=claude ;; @xai-official/grok) name=grok ;;
      @earendil-works/pi-coding-agent) name=pi ;; @google/gemini-cli) name=gemini ;; esac
    mkdir -p "$prefix/node_modules/.bin"
    printf '#!/bin/sh\\necho "%s %s"\\n' "$name" "$version" > "$prefix/node_modules/.bin/$name"
    chmod +x "$prefix/node_modules/.bin/$name"
    exit 0 ;;
esac
exit 2
"""


def script(path, body):
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        self.mine = self.root / "person-bin"          # where a person's own installs (Homebrew) live
        self.mine.mkdir()
        (self.root / "latest").write_text("1.0.0")
        script(self.stubs / "npm", FAKE_NPM.split("\n", 1)[1])
        env = {"PATH": f"{self.mine}:{self.stubs}:/usr/bin:/bin", "FAKE_LATEST": str(self.root / "latest"),
               "FAKE_FAIL": str(self.root / "fail"), "HOME": str(self.root)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = 1_000_000.0
        self.switched = []
        self.tools = H.Harnesses(self.root / "tools", self.root / "state.json", clock=lambda: self.now,
                                 on_switch=lambda manifest: self.switched.append(manifest.id))
        self.addCleanup(self.tools.stop)
        self.tools.expose_path()

    def settle(self, busy=(), rounds=200):
        """Step until the worker is idle, as the runner's loop would."""
        finished = []
        for _ in range(rounds):
            finished += self.tools.step(busy)
            if self.tools.job is None:
                break
            time.sleep(0.02)
        finished += self.tools.step(busy)
        return finished

    def version(self, ident="codex"):
        path, _ = self.tools.locate(self.tools.manifests[ident])
        return self.tools.detect(self.tools.manifests[ident], path) if path else ""


class Manifests(unittest.TestCase):
    def test_the_shipped_manifests_are_valid_and_complete(self):
        found = H.load_all()
        self.assertEqual(set(found), {"codex", "claude-code", "gemini-cli", "grok", "pi", "cursor-agent"})
        self.assertEqual([m.id for m in found.values() if m.catch_all], ["pi"])
        for manifest in found.values():
            self.assertEqual(manifest.method, "script" if manifest.id == "cursor-agent" else "npm")
            self.assertTrue((H.HOSTS_DIR / f"{manifest.host}.py").is_file())
            self.assertEqual(manifest.policy, "latest")

    def valid(self, **change):
        data = {"id": "tool", "name": "Tool", "host": "codex", "executable": "tool", "providers": ["acme"],
                "install": {"method": "npm", "package": "tool-cli"}}
        data.update(change)
        return data

    def test_bad_manifests_are_refused_with_the_field_named(self):
        cases = {
            "executable": self.valid(executable="../evil"),
            "install.package": self.valid(install={"method": "npm", "package": "x; rm -rf /"}),
            "install.url": self.valid(install={"method": "script", "url": "http://x", "prefix_env": "P", "bin": "b"}),
            "sha256": self.valid(install={"method": "binary", "url": "https://x/{os}-{arch}"}),
        }
        for needle, data in cases.items():
            with self.subTest(needle):
                with self.assertRaises(H.ManifestError) as raised:
                    H.parse(data)
                self.assertIn(needle.split(".")[-1].strip("[]").split()[0], str(raised.exception))

class Planning(Base):

    def test_a_harness_already_on_path_is_used_and_never_installed_or_touched(self):
        script(self.mine / "codex", 'echo "codex-cli 0.99.0"')
        self.tools.want(providers=["openai"])
        self.assertEqual(self.tools.install_plan(), [])
        (self.root / "latest").write_text("2.0.0")
        self.now += H.CHECK_EVERY_S * 2
        self.settle()
        self.assertFalse((self.root / "tools" / "codex").exists(), "nothing is installed beside the person's copy")
        row = self.tools.report()["codex"]
        self.assertEqual((row["installed"], row["version"], row["managed"], row["source"], row["update_available"]),
                         (True, "0.99.0", False, "path", False))
        state, message = self.tools.request("update", "codex")
        self.assertEqual(state, "failed")
        self.assertIn("outside Tico", message)
        self.assertEqual(self.tools.request("pin", "codex")[0], "failed")


class Installing(Base):
    def test_a_wanted_harness_is_installed_into_the_tools_dir_and_found_on_path(self):
        self.tools.want(providers=["openai"])
        self.settle()
        self.assertEqual(self.version(), "1.0.0")
        path, source = self.tools.locate(self.tools.manifests["codex"])
        self.assertEqual(source, "tools")
        self.assertTrue(path.startswith(str(self.root / "tools")))
        self.assertEqual(self.switched, ["codex"])
        # Only the tools directory holds it, and the PATH entry is last so anything else wins.
        self.assertTrue(os.environ["PATH"].endswith(str(self.root / "tools" / "bin")))
        self.assertEqual(self.tools.install_plan(), [])

class Updating(Base):
    def installed(self, providers=("openai",)):
        self.tools.want(providers=list(providers))
        self.settle()
        self.switched.clear()

    def newest(self, version):
        (self.root / "latest").write_text(version)
        self.now += H.CHECK_EVERY_S + 1

    def test_an_update_is_never_switched_in_while_a_turn_uses_the_harness(self):
        self.installed()
        self.newest("1.1.0")
        self.settle(busy={"codex"})
        for _ in range(10):
            self.settle(busy={"codex"})
        self.assertEqual(self.version(), "1.0.0", "a running turn keeps the version it started on")
        self.assertEqual(self.switched, [])
        row = self.tools.report()["codex"]
        self.assertEqual((row["state"], row["version"]), ("updating", "1.0.0"))
        self.assertIn("running turn", row["detail"])
        # A turn on another harness does not hold it back, and neither does an idle moment.
        self.settle(busy={"claude"})
        self.assertEqual(self.version(), "1.1.0")
        self.assertEqual(self.switched, ["codex"])
        self.assertEqual(sorted(p.name for p in (self.root / "tools" / "codex").iterdir() if not p.is_symlink()),
                         sorted({(self.root / "tools" / "codex" / "current").resolve().name}),
                         "the old copy is removed after the switch")


if __name__ == "__main__":
    unittest.main()



class BinaryInstall(Base):
    """A binary download is refused unless it matches the checksum the manifest pins."""

    def archive(self, version="4.5.6"):
        import hashlib
        import io
        import tarfile
        buffer = io.BytesIO()
        body = f'#!/bin/sh\necho "acme {version}"\n'.encode()
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            info = tarfile.TarInfo("acme-1/acme")
            info.size, info.mode = len(body), 0o755
            bundle.addfile(info, io.BytesIO(body))
        data = buffer.getvalue()
        return data, hashlib.sha256(data).hexdigest()

    def manifest(self, digest):
        system, machine = H._platform()
        return H.parse({"id": "acme", "name": "Acme", "host": "codex", "executable": "acme", "providers": ["acme"],
                        "install": {"method": "binary", "url": "https://dl.example/{os}-{arch}/acme.tar.gz",
                                    "sha256": {f"{system}-{machine}": digest}}})

    def test_a_download_that_does_not_match_its_checksum_is_refused_and_leaves_nothing(self):
        data, _ = self.archive()
        self.tools.fetch = lambda url: data
        self.tools.manifests = {"acme": self.manifest("0" * 64)}
        self.tools.want(providers=["acme"])
        self.settle()
        row = self.tools.report()["acme"]
        self.assertEqual((row["installed"], row["state"]), (False, "failed"))
        self.assertIn("checksum", row["detail"])
        self.assertFalse(any((self.root / "tools" / "acme").glob("*")))
