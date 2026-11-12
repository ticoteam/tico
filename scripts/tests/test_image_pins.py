"""The image pins in Dockerfile and docker/versions.env, checked in seconds instead of in an image build.

A base image left on a moving tag, a Debian package name the base release no longer has, an SQLite option dropped
from the build, or a pinned package with no wheel for the image's Python fails only when Release validation builds
the images, half an hour in. These read the files directly. The slow tests build the pinned SQLite the way the
image does, and check the apt packages and the Python pins against Debian's and PyPI's indexes.
"""

import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / 'Dockerfile').read_text()
VERSIONS = dict(
    line.split('=', 1)
    for line in (ROOT / 'docker/versions.env').read_text().splitlines()
    if line and not line.startswith('#')
)
SQLITE_SH = (ROOT / 'docker/sqlite.sh').read_text()
# What Debian's SQLite does that Tico's data depends on, so the newer build behaves the same.
SQLITE_OPTIONS = (
    'ENABLE_FTS5',
    'ENABLE_FTS4',
    'ENABLE_DBSTAT_VTAB',
    'ENABLE_MATH_FUNCTIONS',
    'ENABLE_RTREE',
    'ENABLE_COLUMN_METADATA',
    'SECURE_DELETE',
    'DEFAULT_RECURSIVE_TRIGGERS',
    'MAX_VARIABLE_NUMBER=250000',
    'LIKE_DOESNT_MATCH_BLOBS',
)
# Debian 13 (trixie) renamed these for 64-bit time; the old names install nothing there.
TRIXIE_RENAMED = ('libasound2', 'libatk-bridge2.0-0', 'libatk1.0-0', 'libatspi2.0-0', 'libcups2', 'libglib2.0-0')
# Pins with no wheel for the image's Python that the venv stage builds (it has a compiler and their headers).
BUILT_IN_THE_VENV_STAGE = {'pyyaml': 'libyaml-dev'}
DIGEST = re.compile(r'@sha256:[0-9a-f]{64}$')
# The images ship for both (Release validation builds each, .github/workflows/ci.yml); a pin needs a wheel for each.
ARCHES = ('x86_64', 'aarch64')
# glibc in each Debian release (packages.debian.org/<release>/libc6); a manylinux wheel needing newer won't install.
DEBIAN_GLIBC = {'bookworm': (2, 36), 'trixie': (2, 41)}
# The legacy manylinux names and the glibc each one means (PEP 600).
LEGACY_MANYLINUX = {'1': (2, 5), '2010': (2, 12), '2014': (2, 17)}


def python_image():
    return re.search(r'^ARG PYTHON_IMAGE=(\S+)$', DOCKERFILE, re.M).group(1)


def stage(name):
    return next(s for s in re.split(r'^FROM ', DOCKERFILE, flags=re.M) if re.match(rf'\S+ AS {name}\n', s))


def apt_packages(text):
    packages = set()
    for command in re.findall(r'apt-get install([^&;]*)', text.replace('\\\n', ' ')):
        packages |= {word for word in command.split() if not word.startswith(('-', '$'))}
    return packages


def test_the_python_image_is_a_stable_release_pinned_by_digest():
    image = python_image()
    match = re.fullmatch(r'python:(\d+)\.(\d+)\.(\d+)-slim-(\w+)@sha256:[0-9a-f]{64}', image)
    assert match, f'{image}: pin python:X.Y.Z-slim-<debian>@sha256:<digest>, never a moving or pre-release tag'
    assert (int(match[1]), int(match[2])) >= (3, 12)


@pytest.mark.parametrize('dockerfile', ['Dockerfile', 'relay-rs/Dockerfile'])
def test_every_base_image_is_pinned_by_digest(dockerfile):
    text = (ROOT / dockerfile).read_text()
    stages = set(re.findall(r'^FROM \S+ AS (\S+)$', text, re.M))
    for image in re.findall(r'^FROM (\S+)', text, re.M):
        # A stage chosen by an argument (runner-bin-${NATIVE}) is one of the stages; scratch is empty, nothing to pin.
        stage = re.compile(re.sub(r'\\\$\\\{\w+\\\}', r'[\\w-]+', re.escape(image)))
        if not any(stage.fullmatch(name) for name in stages) and image not in ('${PYTHON_IMAGE}', 'scratch'):
            assert DIGEST.search(image), f'{dockerfile}: {image} is not pinned by digest'
    for image in re.findall(r'^ARG \w+_IMAGE=(\S+)$', text, re.M):
        assert DIGEST.search(image), f'{dockerfile}: {image} is not pinned by digest'


def test_runner_packages_exist_on_the_images_debian_release():
    if not python_image().split('@')[0].endswith('-trixie'):
        pytest.skip('only trixie renamed these packages')
    stale = sorted(apt_packages(DOCKERFILE) & set(TRIXIE_RENAMED))
    assert not stale, f'{stale}: trixie names these with a t64 suffix (e.g. libasound2t64)'


def test_the_sqlite_pin_is_complete_and_matches_the_download_name():
    assert re.fullmatch(r'3\.\d+\.\d+', VERSIONS['SQLITE_VERSION'])
    major, minor, patch = map(int, VERSIONS['SQLITE_VERSION'].split('.'))
    assert VERSIONS['SQLITE_ID'] == f'{major}{minor:02d}{patch:02d}00', 'sqlite.org names 3.54.1 as 3540100'
    assert re.fullmatch(r'20\d\d', VERSIONS['SQLITE_YEAR'])
    assert re.fullmatch(r'[0-9a-f]{64}', VERSIONS['SQLITE_SHA3_256'])


def test_the_sqlite_build_keeps_debians_behaviour():
    for option in SQLITE_OPTIONS:
        assert re.search(rf'-DSQLITE_{re.escape(option)}\b', SQLITE_SH), option
    assert '-DSQLITE_OMIT_LOOKASIDE' not in SQLITE_SH  # Debian's; it only makes SQLite slower


def test_every_python_image_checks_its_sqlite_after_its_own_apt_installs():
    assert 'sqlite.sh install' in stage('python')
    for name in ('runner', 'server'):
        text = stage(name)
        assert re.search(r'^FROM python AS ', f'FROM {text}', re.M), f'{name} must build on the python stage'
        if 'apt-get install' in text:  # an apt upgrade could bring Debian's SQLite back; the check catches it
            assert text.rindex('sqlite.sh check') > text.rindex('apt-get install'), name


def test_packages_built_in_the_venv_stage_get_their_headers_and_runtime_library():
    venv = stage('venv')
    for package, headers in BUILT_IN_THE_VENV_STAGE.items():
        assert headers in apt_packages(venv), f'{package} needs {headers} in the venv stage'
    assert 'libyaml-0-2' in apt_packages(stage('python')), "PyYAML's C loader needs libyaml at run time"
    assert 'yaml.CSafeLoader' in stage('source'), 'the build must fail if PyYAML lost its C loader'


def debian_release():
    return python_image().split('@')[0].rsplit('-', 1)[1]


def image_glibc():
    return DEBIAN_GLIBC[debian_release()]


def manylinux(platform):
    """(glibc, arch) a manylinux platform tag needs, or None for any other platform."""
    if match := re.fullmatch(r'manylinux_(\d+)_(\d+)_(\w+)', platform):
        return (int(match[1]), int(match[2])), match[3]
    if match := re.fullmatch(r'manylinux(1|2010|2014)_(\w+)', platform):
        return LEGACY_MANYLINUX[match[1]], match[2]
    return None


def installs_on_the_image(tags, minor, arch, glibc):
    """Whether pip in the image (CPython 3.<minor>, Debian with this glibc, <arch>) can install these wheel tags."""
    python, abi, platform = tags
    pythons, platforms = python.split('.'), platform.split('.')
    cp = f'cp3{minor}'
    abi3 = abi == 'abi3' and any(p.startswith('cp3') and p[3:].isdigit() and int(p[3:]) <= minor for p in pythons)
    pure = abi == 'none' and any(p in ('py3', f'py3{minor}', cp) for p in pythons)
    native = abi == cp and cp in pythons
    linux = [m for m in map(manylinux, platforms) if m]
    return (pure or abi3 or native) and (
        'any' in platforms or any(needs <= glibc and on == arch for needs, on in linux)
    )


FIXTURE = ROOT / 'scripts/tests/fixtures/pypi_wheels.json'
REQUIRES_PYTHON_FIXTURE = ROOT / 'scripts/tests/fixtures/pypi_requires_python.json'
PYPI_ATTEMPTS = 4
PYPI_RETRY_BACKOFF = 0.25
RECORD_WORKERS = 8


def pins(requirements):
    return re.findall(r'^([A-Za-z0-9_.-]+)==(\S+)', (ROOT / requirements).read_text(), re.M)


def pypi_json(name, version):
    """One pinned version's PyPI metadata, retrying a brief network failure (a reset or a refused connection)."""
    url = f'https://pypi.org/pypi/{name}/{version}/json'
    for attempt in range(PYPI_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.load(response)
        except (urllib.error.URLError, ConnectionError):
            if attempt + 1 == PYPI_ATTEMPTS:
                raise
            time.sleep(PYPI_RETRY_BACKOFF * 2**attempt)


def wheels_on_pypi(pin):
    """The wheel tags (python-abi-platform) PyPI lists for one pinned version."""
    files = pypi_json(*pin)['urls']
    return sorted({'-'.join(f['filename'][:-4].split('-')[-3:]) for f in files if f['filename'].endswith('.whl')})


# Only production pins are copied into the image's venv; CI can build its own test-only C extensions.
@pytest.mark.parametrize('requirements', ['backend/requirements.txt'])
def test_every_pin_has_a_wheel_for_the_images_python(requirements):
    """The images' Python has no compiler outside the venv stage, so a pin with no wheel fails the build there.

    The wheels come from scripts/tests/fixtures/pypi_wheels.json, recorded from PyPI, not a live call: a published
    version's files do not change, and a live call failed Release validation with a connection reset. A new or bumped
    pin needs its wheels recorded with `python3 scripts/tests/test_image_pins.py --record`.
    """
    major, minor = re.match(r'python:(\d+)\.(\d+)', python_image()).groups()
    tag = f'cp{major}{minor}'
    recorded = json.loads(FIXTURE.read_text())
    assert set(recorded) == {f'{name}=={version}' for name, version in pins(requirements)}, (
        'the recorded wheels do not match the pins: python3 scripts/tests/test_image_pins.py --record'
    )
    missing = sorted(
        f'{key} ({arch})'
        for key, wheels in recorded.items()
        for arch in ARCHES
        if not any(installs_on_the_image(tuple(w.split('-')), int(minor), arch, image_glibc()) for w in wheels)
        and key.partition('==')[0].lower() not in BUILT_IN_THE_VENV_STAGE
    )
    assert not missing, (
        f'no {tag} Linux wheel for glibc {image_glibc()}: {missing}; add it to BUILT_IN_THE_VENV_STAGE with its headers'
    )


def requires_python_on_pypi(pin):
    """The Requires-Python PyPI lists for one pinned version ('' when it names none)."""
    return pypi_json(*pin)['info'].get('requires_python') or ''


@pytest.mark.parametrize('requirements', ['backend/requirements.txt', 'backend/requirements-dev.txt'])
def test_every_pin_still_installs_on_the_oldest_supported_python(requirements):
    """Locks resolve for the images' Python, but runner Macs may still have 3.12 (scripts/setup-runner.sh).

    The Requires-Python values come from scripts/tests/fixtures/pypi_requires_python.json, recorded from PyPI, not a
    live call: a live call failed Release validation with a connection reset, and a published version's metadata does
    not change. A new or bumped pin needs `python3 scripts/tests/test_image_pins.py --record`.
    """
    from packaging.specifiers import SpecifierSet

    oldest = re.search(r'Python (\d+\.\d+) or newer', (ROOT / 'scripts/setup-runner.sh').read_text()).group(1)
    recorded = json.loads(REQUIRES_PYTHON_FIXTURE.read_text())
    keys = [f'{name}=={version}' for name, version in pins(requirements)]
    missing = [key for key in keys if key not in recorded]
    assert not missing, f'no recorded Requires-Python for {missing}: python3 scripts/tests/test_image_pins.py --record'
    refused = sorted(f'{key} ({recorded[key]})' for key in keys if oldest not in SpecifierSet(recorded[key]))
    assert not refused, f'these pins refuse Python {oldest}: {refused}'


if __name__ == '__main__':
    import sys

    if sys.argv[1:] != ['--record']:
        sys.exit(
            'usage: python3 scripts/tests/test_image_pins.py --record   '
            '(asks PyPI for the wheels and Requires-Python of every pin)'
        )
    found = pins('backend/requirements.txt')
    with ThreadPoolExecutor(RECORD_WORKERS) as pool:
        recorded = dict(zip((f'{name}=={version}' for name, version in found), pool.map(wheels_on_pypi, found)))
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(dict(sorted(recorded.items())), indent=1) + '\n')
    print(f'recorded {len(recorded)} pins to {FIXTURE.relative_to(ROOT)}')
    found = sorted({pin for name in ('backend/requirements.txt', 'backend/requirements-dev.txt') for pin in pins(name)})
    with ThreadPoolExecutor(RECORD_WORKERS) as pool:
        versions = dict(
            zip((f'{name}=={version}' for name, version in found), pool.map(requires_python_on_pypi, found))
        )
    REQUIRES_PYTHON_FIXTURE.write_text(json.dumps(dict(sorted(versions.items())), indent=1) + '\n')
    print(f'recorded {len(versions)} Requires-Python values to {REQUIRES_PYTHON_FIXTURE.relative_to(ROOT)}')
