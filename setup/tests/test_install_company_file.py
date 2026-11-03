"""scripts/install.sh --company-file: the team a company file (tico.company.yaml) starts with, run for real against a local
release with docker, curl, chown and the wizard's python stubbed (the `box` of test_install_runner.py). The server image's
packer is a stub there; the last test runs the installer's own packer program (PACKER) against this checkout's backend."""
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from setup.tests.test_install_runner import box, install  # noqa: F401  (box is a fixture)

COMPANY = b"version: '0.2'\ncompany:\n  name: Acme Heating & Air\n"   # the packer is a stub: any bytes will do
PACKED = "H4sIAAAAAAAC+stub/packed/text=="   # standard base64, as backend.company_file.starter.pack prints it
PACK = """docker compose run --rm -T --no-deps -e TICO_PACKER server python -c import os; exec(os.environ["TICO_PACKER"])"""
DOCKER = """#!/bin/sh
echo "docker $*" >> "$STUB_LOG"
case "$*" in
  "compose version --short") echo 2.29.7 ;;
  "version --format"*) echo 27.0.0 ;;
  "compose run "*)
    echo "packer TICO_TAG=${TICO_TAG:-unset}" >> "$STUB_LOG"
    [ -e .env ] && echo "env-before-pack" >> "$STUB_LOG"
    cat > "$STUB_INPUT"
    if [ -n "${STUB_PACK_FAIL:-}" ]; then echo "error: the file isn't valid YAML" >&2; exit 2; fi
    cat "$STUB_PACKED" ;;
  "compose exec -T server cat /data/local-owner.token") echo owner-token-stub ;;
esac
exit 0
"""
# Answers the enrollment call like the server refusing it (a reason, no code); every other call is the real curl.
CURL = """#!/bin/sh
case "$*" in
  *enrollments*) echo "curl $*" >> "$STUB_LOG"; cat >/dev/null
    printf '{"error":{"code":"idempotency_key","detail":"stub"}}\\n' ;;
  *) exec /usr/bin/curl "$@" ;;
esac
"""
CHOWN = '#!/bin/sh\necho "chown $*" >> "$STUB_LOG"\n'
# The wizard is not run here: its arguments are logged, which is all the passthrough needs.
PYTHON3 = """#!/bin/sh
case "$1" in -c) exit 0 ;; esac
echo "python3 $*" >> "$STUB_LOG"
"""


@pytest.fixture
def company(box):  # noqa: F811
    for name, text in (("docker", DOCKER), ("curl", CURL), ("chown", CHOWN), ("python3", PYTHON3)):
        (box["stubs"] / name).write_text(text)
        (box["stubs"] / name).chmod(0o755)
    health = box["tmp"] / "health"
    health.write_text("")
    box["file"] = box["tmp"] / "tico.company.yaml"
    box["file"].write_bytes(COMPANY)
    box["packed"] = box["tmp"] / "packed.txt"
    box["packed"].write_text(PACKED + "\n")
    box["input"] = box["tmp"] / "packer-input.yaml"
    box["env"] = {"STUB_INPUT": str(box["input"]), "STUB_PACKED": str(box["packed"]),
                  "TICO_INSTALL_HEALTH_URL": "file://" + str(health), "TICO_INSTALL_HEALTH_TRIES": "1"}
    return box


def run(box, *args, **env):
    return install(box, *args, **box["env"], **env)


def run_from(box, cwd, *args, **env):
    """install() started in another folder, so a relative --company-file is taken from there."""
    variables = {"PATH": f"{box['stubs']}:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
                 "HOME": str(box["tmp"]), "STUB_LOG": str(box["log"]),
                 "TICO_INSTALL_RELEASES_URL": "file://" + str(box["rel"]),
                 "TICO_INSTALL_MEMINFO": str(box["tmp"] / "meminfo"), **box["env"], **env}
    script = box["rel"] / "download" / "v0.2.0" / "install.sh"
    return subprocess.run(["sh", str(script), "--dir", str(box["dir"]), *args], env=variables, cwd=cwd,
                          capture_output=True, text=True, timeout=120)


def env_of(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if line and not line.startswith("#"))


LOCAL = ("--local", "--owner-email", "ana@example.com")


def test_local_packs_the_file_in_the_server_image_and_puts_the_packed_plan_in_env_before_anything_starts(company):
    r = run(company, *LOCAL, "--company-file", str(company["file"]))
    assert r.returncode == 0, r.stdout + r.stderr
    env = env_of(company["dir"] / ".env")
    assert env["TICO_COMPANY_FILE_B64"] == PACKED
    assert "TICO_SAMPLE_COMPANY" not in env and "TICO_COMPANY_FILE" not in env
    assert stat.S_IMODE((company["dir"] / ".env").stat().st_mode) == 0o600
    assert company["input"].read_bytes() == COMPANY                       # the file itself went to the packer's stdin
    calls = company["log"].read_text().splitlines()
    assert "docker compose pull --quiet server" in calls                  # the image is here before it packs
    assert f"packer TICO_TAG=v0.2.0" in calls                             # the pinned release packs it, not latest
    assert calls.index("docker compose pull --quiet server") < calls.index(PACK) < calls.index("docker compose up -d")
    assert "env-before-pack" not in calls                                 # nothing installed before the file packs
    assert f"Your team from {company['file']} is loaded." in r.stdout
    assert "Finish setup" not in r.stdout
    assert "/api/v2/local-signin?token=owner-token-stub" in r.stdout
    assert "If the team does not load, Health in the app says why." in r.stdout


def test_a_plan_too_big_for_the_env_is_a_private_file_the_server_container_reads(company):
    big = "A" * 8000 + "=\n"                 # longer than the 7168 that fit in .env
    company["packed"].write_text(big)
    r = run(company, *LOCAL, "--company-file", str(company["file"]))
    assert r.returncode == 0, r.stdout + r.stderr
    env = env_of(company["dir"] / ".env")
    assert "TICO_COMPANY_FILE_B64" not in env
    plan_dir = company["dir"] / "company-file"
    assert env["TICO_COMPANY_FILE_DIR"] == str(plan_dir)
    assert env["TICO_COMPANY_FILE"] == "/run/tico/company-file/plan.b64"
    assert (plan_dir / "plan.b64").read_text() == big
    assert stat.S_IMODE((plan_dir / "plan.b64").stat().st_mode) == 0o600
    assert f"chown 10001:10001 {plan_dir / 'plan.b64'}" in company["log"].read_text()   # the container's user reads it
    assert "TICO_SAMPLE_COMPANY" not in env


def test_a_file_the_server_cannot_pack_installs_nothing_and_says_why(company):
    r = run(company, *LOCAL, "--company-file", str(company["file"]), STUB_PACK_FAIL="1")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "not one Tico can import" in r.stderr and "isn't valid YAML" in r.stderr
    assert not (company["dir"] / ".env").exists()
    assert "docker compose up -d" not in company["log"].read_text()


def test_a_path_that_cannot_be_read_dies_before_anything_is_downloaded_or_installed(company):
    missing = company["tmp"] / "nope.yaml"
    r = run(company, *LOCAL, "--company-file", str(missing))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Cannot read --company-file; use a readable UTF-8 company YAML file" in r.stderr
    assert not company["dir"].exists()


@pytest.mark.parametrize("flag", [["--company-file", "~/tico.company.yaml"], ["--company-file=~/tico.company.yaml"]],
                         ids=["separate-value", "equals"])
def test_both_spellings_take_a_path_and_a_leading_tilde_is_the_home_folder(company, flag):
    # install() runs with HOME set to the test folder, where the fixture wrote the file.
    r = run(company, *LOCAL, *flag)
    assert r.returncode == 0, r.stdout + r.stderr
    home_file = (company["tmp"] / "tico.company.yaml").resolve()
    assert f"Your team from {home_file} is loaded." in r.stdout
    assert company["input"].read_bytes() == COMPANY


def test_a_relative_path_is_taken_from_the_folder_the_installer_was_started_in(company):
    r = run_from(company, company["tmp"], *LOCAL, "--company-file", "tico.company.yaml")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"Your team from {(company['tmp'] / 'tico.company.yaml').resolve()} is loaded." in r.stdout
    assert company["input"].read_bytes() == COMPANY


@pytest.mark.parametrize("args", [["--local", "--owner-email", "a@b.example", "--company-file"],
                                  ["--local", "--owner-email", "a@b.example", "--company-file="]],
                         ids=["no-value", "empty-equals"])
def test_a_company_file_flag_without_a_path_is_a_usage_error(box, args):  # noqa: F811
    r = install(box, *args)
    assert r.returncode == 2 and "--company-file needs a path" in r.stderr


@pytest.mark.parametrize("args", [["--docker-only"], ["--runner", "--url", "https://tico.example.com", "--code", "code_123"]],
                         ids=["docker-only", "runner"])
def test_a_company_file_does_not_go_with_a_docker_or_runner_install(company, args):
    r = run(company, *args, "--company-file", str(company["file"]))
    assert r.returncode == 2 and "--company-file goes with --local, --recommended or a team install." in r.stderr


def test_recommended_with_a_company_file_loads_that_team_instead_of_the_sample_company(company):
    r = run(company, "--recommended", "--owner-email", "ana@example.com", "--company-file", str(company["file"]))
    assert r.returncode == 0, r.stdout + r.stderr
    env = env_of(company["dir"] / ".env")
    assert env["TICO_INSTALL_PRESET"] == "recommended" and env["TICO_COMPANY_FILE_B64"] == PACKED
    assert "TICO_SAMPLE_COMPANY" not in env
    assert env["TICO_TEAM_NAME"] == '"My Team"'                           # the file names the team, not the sample
    assert f"Your team from {company['file']} is loaded, and Tico Support is on." in r.stdout
    assert "The sample company is loaded" not in r.stdout
    assert "One step is left" in r.stdout


def test_a_team_install_hands_the_file_to_the_setup_wizard(company):
    r = run(company, "--yes", "--company-file", str(company["file"]), "--", "--domain", "tico.example.com")
    assert r.returncode == 0, r.stdout + r.stderr
    wizard = [line for line in company["log"].read_text().splitlines() if line.startswith("python3 -m setup")]
    assert len(wizard) == 1 and f"--company-file {company['file']} --domain tico.example.com" in wizard[0]
    assert "packer" not in company["log"].read_text()                    # the wizard packs it; the installer does not


def test_a_team_install_without_the_flag_passes_the_wizard_the_same_arguments_as_before(company):
    r = run(company, "--yes", "--", "--domain", "tico.example.com")
    assert r.returncode == 0, r.stdout + r.stderr
    wizard = [line for line in company["log"].read_text().splitlines() if line.startswith("python3 -m setup")]
    assert len(wizard) == 1 and wizard[0].endswith("--domain tico.example.com") and "--company-file" not in wizard[0]
    assert "docker compose run" not in company["log"].read_text()


def test_an_install_that_already_has_its_env_keeps_its_team_and_says_the_file_was_not_used(company):
    company["dir"].mkdir(parents=True)
    (company["dir"] / ".env").write_text('TICO_TAG=v0.2.0\nTICO_TEAM_NAME="Kept"\n')
    r = run(company, *LOCAL, "--company-file", str(company["file"]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--company-file is used when Tico is first installed" in r.stderr
    assert (company["dir"] / ".env").read_text() == 'TICO_TAG=v0.2.0\nTICO_TEAM_NAME="Kept"\n'
    assert "docker compose run" not in company["log"].read_text()


def test_the_help_text_names_the_flag(company):
    r = run(company, "--help")
    assert r.returncode == 0 and "--company-file PATH" in r.stdout


def test_the_installers_packer_prints_the_plan_the_server_reads_and_refuses_a_file_it_cannot_import():
    # The program install.sh runs in the server image, run here as the image runs it: the same bytes the server unpacks.
    from backend.company_file import starter
    root = Path(__file__).resolve().parents[2]
    packer = re.search(r"^PACKER='(.*?)'$", (root / "scripts" / "install.sh").read_text(), re.S | re.M).group(1)

    def pack(data):
        return subprocess.run([sys.executable, "-c", 'import os; exec(os.environ["TICO_PACKER"])'], input=data,
                              capture_output=True, cwd=root, timeout=120, env={**os.environ, "TICO_PACKER": packer})

    text = (root / "backend" / "tests" / "fixtures" / "company_strategy" / "company.yaml").read_text()
    done = pack(text.encode())
    assert done.returncode == 0 and done.stderr == b""
    packed = done.stdout.decode().strip()
    assert packed == starter.pack(text, starter.MAX_PACKED) and starter.unpack(packed) == starter.load(text)
    bad = pack(b"version: 0.2\nthis: [is not yaml")
    assert bad.returncode == 2 and bad.stdout == b"" and b"isn't valid YAML" in bad.stderr
    assert pack(b"\xff\xfe not utf-8").returncode == 2
    assert pack(b"x" * (starter.MAX_BYTES + 1)).returncode == 2
