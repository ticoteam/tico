"""The image pins in Dockerfile and docker/versions.env, checked in seconds instead of in an image build.

A base image left on a moving tag, a Debian package name the base release no longer has, an SQLite option dropped
from the build, or a pinned package with no wheel for the image's Python fails only when Release validation builds
the images, half an hour in. These read the files directly. The slow tests build the pinned SQLite the way the
image does, and check the apt packages and the Python pins against Debian's and PyPI's indexes.
"""
import json
import lzma
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / 'Dockerfile').read_text()
VERSIONS = dict(line.split('=', 1) for line in (ROOT / 'docker/versions.env').read_text().splitlines()
                if line and not line.startswith('#'))
SQLITE_SH = (ROOT / 'docker/sqlite.sh').read_text()
# What Debian's SQLite does that Tico's data depends on, so the newer build behaves the same.
SQLITE_OPTIONS = ('ENABLE_FTS5', 'ENABLE_FTS4', 'ENABLE_DBSTAT_VTAB', 'ENABLE_MATH_FUNCTIONS', 'ENABLE_RTREE',
                  'ENABLE_COLUMN_METADATA', 'SECURE_DELETE', 'DEFAULT_RECURSIVE_TRIGGERS', 'MAX_VARIABLE_NUMBER=250000',
                  'LIKE_DOESNT_MATCH_BLOBS')
# Debian 13 (trixie) renamed these for 64-bit time; the old names install nothing there.
TRIXIE_RENAMED = ('libasound2', 'libatk-bridge2.0-0', 'libatk1.0-0', 'libatspi2.0-0', 'libcups2', 'libglib2.0-0')
# Pins with no wheel for the image's Python that the venv stage builds (it has a compiler and their headers).
BUILT_IN_THE_VENV_STAGE = {'pyyaml': 'libyaml-dev'}
DIGEST = re.compile(r'@sha256:[0-9a-f]{64}$')
# The images ship for both (.github/workflows/docker.yml); a pin needs a wheel for each.
ARCHES = ('x86_64', 'aarch64')


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
        if image not in stages and image != '${PYTHON_IMAGE}':
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
    assert '-DSQLITE_OMIT_LOOKASIDE' not in SQLITE_SH   # Debian's; it only makes SQLite slower


def test_every_python_image_checks_its_sqlite_after_its_own_apt_installs():
    assert 'sqlite.sh install' in stage('python')
    for name in ('runner', 'server'):
        text = stage(name)
        assert re.search(r'^FROM python AS ', f'FROM {text}', re.M), f'{name} must build on the python stage'
        if 'apt-get install' in text:   # an apt upgrade could bring Debian's SQLite back; the check catches it
            assert text.rindex('sqlite.sh check') > text.rindex('apt-get install'), name


def test_packages_built_in_the_venv_stage_get_their_headers_and_runtime_library():
    venv = stage('venv')
    for package, headers in BUILT_IN_THE_VENV_STAGE.items():
        assert headers in apt_packages(venv), f'{package} needs {headers} in the venv stage'
    assert 'libyaml-0-2' in apt_packages(stage('python')), "PyYAML's C loader needs libyaml at run time"
    assert 'yaml.CSafeLoader' in stage('source'), 'the build must fail if PyYAML lost its C loader'


def installs_on_the_image(tags, minor, arch):
    """Whether pip in the image (CPython 3.<minor>, glibc Debian, <arch>) can install a wheel with these tags."""
    python, abi, platform = tags
    pythons, platforms = python.split('.'), platform.split('.')
    cp = f'cp3{minor}'
    abi3 = abi == 'abi3' and any(p.startswith('cp3') and p[3:].isdigit() and int(p[3:]) <= minor for p in pythons)
    pure = abi == 'none' and any(p in ('py3', f'py3{minor}', cp) for p in pythons)
    native = abi == cp and cp in pythons
    return ((pure or abi3 or native)
            and ('any' in platforms or any(p.startswith('manylinux') and p.endswith(f'_{arch}') for p in platforms)))


@pytest.mark.parametrize('wheel, x86_64, aarch64', [
    ('py3-none-any', True, True),
    ('py2.py3-none-any', True, True),
    ('py3-none-win_amd64', False, False),
    ('py3-none-macosx_11_0_arm64', False, False),
    ('cp315-cp315-manylinux_2_17_x86_64.manylinux2014_x86_64', True, False),
    ('cp315-cp315-manylinux_2_17_aarch64.manylinux2014_aarch64', False, True),
    ('cp315-cp315-musllinux_1_2_x86_64', False, False),
    ('cp315-cp315-win_amd64', False, False),
    ('cp314-cp314-manylinux_2_17_x86_64', False, False),
    ('cp315-cp315t-manylinux_2_17_x86_64', False, False),
    ('cp39-abi3-manylinux_2_28_x86_64', True, False),
    ('cp316-abi3-manylinux_2_28_x86_64', False, False),
])
def test_wheel_tags_match_only_the_images_python_and_platforms(wheel, x86_64, aarch64):
    tags = tuple(wheel.split('-'))
    assert (installs_on_the_image(tags, 15, 'x86_64'), installs_on_the_image(tags, 15, 'aarch64')) == (x86_64, aarch64)


@pytest.mark.slow
def test_the_pinned_sqlite_builds_with_every_option_python_sees(tmp_path):
    if sys.platform != 'linux' or not shutil.which('gcc'):
        pytest.skip('the image build is Linux with gcc')
    (tmp_path / 'versions.env').write_text((ROOT / 'docker/versions.env').read_text())
    script = tmp_path / 'sqlite.sh'
    script.write_text(SQLITE_SH.replace('/tmp/versions.env', str(tmp_path / 'versions.env')))
    subprocess.run(['sh', str(script), 'build', str(tmp_path / 'out')], check=True, cwd=tmp_path)
    code = ('import sqlite3, json; c = sqlite3.connect(":memory:"); '
            'print(json.dumps([sqlite3.sqlite_version, [r[0] for r in c.execute("pragma compile_options")]]))')
    env = {**os.environ, 'LD_LIBRARY_PATH': str(tmp_path / 'out')}
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, check=True)
    version, options = json.loads(result.stdout)
    if version == sqlite3.sqlite_version and version != VERSIONS['SQLITE_VERSION']:
        pytest.skip(f'this Python links SQLite statically ({version}); the image build checks the version itself')
    assert version == VERSIONS['SQLITE_VERSION']
    for option in SQLITE_OPTIONS:
        assert option in options, option


@pytest.mark.slow
def test_every_apt_package_exists_in_the_images_debian_release():
    release = python_image().split('@')[0].rsplit('-', 1)[1]
    index = f'https://deb.debian.org/debian/dists/{release}/main/binary-amd64/Packages.xz'
    with urllib.request.urlopen(index, timeout=120) as response:
        names = set(re.findall(r'^Package: (\S+)$', lzma.decompress(response.read()).decode(), re.M))
    wanted = apt_packages(DOCKERFILE)
    assert wanted and not wanted - names, f'not in Debian {release}: {sorted(wanted - names)}'


@pytest.mark.slow
@pytest.mark.parametrize('requirements', ['backend/requirements.txt', 'backend/requirements-dev.txt'])
def test_every_pin_has_a_wheel_for_the_images_python(requirements):
    """The images' Python has no compiler outside the venv stage, so a pin with no wheel fails the build there."""
    major, minor = re.match(r'python:(\d+)\.(\d+)', python_image()).groups()
    tag = f'cp{major}{minor}'
    pins = re.findall(r'^([A-Za-z0-9_.-]+)==(\S+)', (ROOT / requirements).read_text(), re.M)

    def wheel_tags(pin):
        name, version = pin
        with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as response:
            files = json.load(response)['urls']
        return name, {tuple(f['filename'][:-4].split('-')[-3:]) for f in files if f['filename'].endswith('.whl')}

    with ThreadPoolExecutor(16) as pool:
        missing = sorted(f'{name} ({arch})' for name, wheels in pool.map(wheel_tags, pins) for arch in ARCHES
                         if not any(installs_on_the_image(w, int(minor), arch) for w in wheels)
                         and name.lower() not in BUILT_IN_THE_VENV_STAGE)
    assert not missing, f'no {tag} Linux wheel: {missing}; add it to BUILT_IN_THE_VENV_STAGE with its headers'


@pytest.mark.slow
@pytest.mark.parametrize('requirements', ['backend/requirements.txt', 'backend/requirements-dev.txt'])
def test_every_pin_still_installs_on_the_oldest_supported_python(requirements):
    """Locks resolve for the images' Python, but runner Macs may still have 3.12 (scripts/setup-runner.sh)."""
    from packaging.specifiers import SpecifierSet
    oldest = re.search(r'Python (\d+\.\d+) or newer', (ROOT / 'scripts/setup-runner.sh').read_text()).group(1)
    pins = re.findall(r'^([A-Za-z0-9_.-]+)==(\S+)', (ROOT / requirements).read_text(), re.M)

    def requires_python(pin):
        name, version = pin
        with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as response:
            return name, json.load(response)['info'].get('requires_python') or ''

    with ThreadPoolExecutor(16) as pool:
        refused = sorted(f'{name} ({spec})' for name, spec in pool.map(requires_python, pins)
                         if oldest not in SpecifierSet(spec))
    assert not refused, f'these pins refuse Python {oldest}: {refused}'
