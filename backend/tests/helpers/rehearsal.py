"""Helpers that other tests share, moved out of test_rehearsal.py so they import no test file."""

import stat
import shlex
import sys
from backend.tests.helpers.replication import entrypoint


def stub_server(tmp_path):
    """Record only the server entry point; initialization helpers still use the real interpreter."""
    stub = tmp_path / "bin" / "python"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "-m backend.server" ]; then\n'
        "  env | grep -E '^(TICO_[A-Z_]*|AWS_[A-Z_]*REGION)=' | sort > \"$STUB_ENV\"\n"
        '  echo "python $*" >> "$STUB_LOG"\n'
        '  exit 0\n'
        'fi\n' + "exec " + shlex.quote(sys.executable) + ' "$@"\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return tmp_path / "server.env"


def serve(tmp_path, **extra):
    """`docker/entrypoint.sh server` with fake server and litestream. Returns (result, env the server got, litestream log)."""
    env_file = stub_server(tmp_path)
    base = {
        "TICO_COMPANY_NAME": "Acme",
        "TICO_OWNER_EMAIL": "owner@example.com",
        "TICO_AUTH_PROXY": "none",
        "TICO_LOCAL_OWNER_TOKEN_FILE": str(tmp_path / "token"),
        "STUB_ENV": str(env_file),
        "STUB_DB": str(tmp_path / "no-replica"),
        "STUB_FAIL": "",
        **extra,
    }
    result = entrypoint(tmp_path, command="server", env=base)
    seen = dict(line.split("=", 1) for line in env_file.read_text().splitlines()) if env_file.exists() else {}
    log = (tmp_path / "stub.log").read_text() if (tmp_path / "stub.log").exists() else ""
    return result, seen, log
