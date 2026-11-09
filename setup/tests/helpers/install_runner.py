"""Helpers that other tests share, moved out of test_install_runner.py so they import no test file."""

import stat
import subprocess
import pytest
from setup.tests.helpers.install_sh import SCRIPTS, build


ROOT = SCRIPTS.parent


DOCKER = """#!/bin/sh
echo "docker $*" >> "$STUB_LOG"
case "$*" in
  "compose version --short") echo 2.29.7 ;;
  "version --format"*) echo 27.0.0 ;;
  "volume inspect tico-runner") [ -n "${STUB_VOLUME:-}" ] || exit 1 ;;
  "container inspect"*) [ -n "${STUB_BARE:-}" ] || exit 1 ;;
  *" up -d") echo "memory ${TICO_RUNNER_MEMORY-unset}" >> "$STUB_LOG" ;;
esac
exit 0
"""


@pytest.fixture
def box(tmp_path):
    """A fake Linux server: stub tools first on PATH, a local release to download, a directory to install into."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "docker").write_text(DOCKER)
    (stubs / "uname").write_text(
        '#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; *) exec /usr/bin/uname "$@";; esac\n'
    )
    (stubs / "id").write_text('#!/bin/sh\nif [ "$1" = -u ]; then echo 0; else exec /usr/bin/id "$@"; fi\n')
    (stubs / "df").write_text(
        "#!/bin/sh\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\nfake 1 1 50000000 1%% /\\n'\n"
    )
    (stubs / "ss").write_text("#!/bin/sh\nexit 0\n")
    (stubs / "hostname").write_text("#!/bin/sh\necho build-host\n")
    for tool in stubs.iterdir():
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "meminfo").write_text("MemTotal:        2000000 kB\n")
    rel = tmp_path / "rel"
    version = "v0.2.0"
    assert build.main(["--version", version, "--output", str(rel / "download" / version), "--source", str(ROOT)]) == 0
    return {"stubs": stubs, "rel": rel, "dir": tmp_path / "runner", "log": tmp_path / "docker.log", "tmp": tmp_path}


def install(box, *args, **env):
    variables = {
        "PATH": f"{box['stubs']}:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(box["tmp"]),
        "STUB_LOG": str(box["log"]),
        "TICO_INSTALL_RELEASES_URL": "file://" + str(box["rel"]),
        "TICO_INSTALL_MEMINFO": str(box["tmp"] / "meminfo"),
        **env,
    }
    script = box["rel"] / "download" / "v0.2.0" / "install.sh"
    return subprocess.run(
        ["sh", str(script), "--dir", str(box["dir"]), *args], env=variables, capture_output=True, text=True, timeout=120
    )
