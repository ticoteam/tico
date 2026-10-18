"""The images, the dependency locks and the GitHub workflows must agree on tool versions.

Tests run on the workflows' toolchains; the release images build with the Dockerfile's. When the two drift, every PR
passes and the failure first shows in the image build or the release smoke, after an hour of Release validation:
locks compiled for one Python installed into an image on another, or Rust code using a feature newer than the image's
compiler. These checks read the files only, so they run on every PR.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / '.github' / 'workflows').glob('*.yml'))
LOCKS = ('backend/requirements.txt', 'backend/requirements-dev.txt')


def minor(text):
    major, minor_ = re.match(r'(\d+)\.(\d+)', text).groups()
    return int(major), int(minor_)


def dockerfile_python():
    image = re.search(r'^ARG PYTHON_IMAGE=(\S+)$', (ROOT / 'Dockerfile').read_text(), re.M).group(1)
    return image, minor(re.match(r'python:(\S+)', image).group(1))   # digest pins: test_image_pins.py


def test_locks_and_release_validation_use_the_image_python():
    _, python = dockerfile_python()
    for lock in LOCKS:
        header = re.search(r'--python-version (\S+)', (ROOT / lock).read_text())
        assert header, f'{lock} does not record which Python it was compiled for'
        assert minor(header.group(1)) == python, f'{lock} is compiled for {header.group(1)}, the image runs {python}'
    # Other workflows may add older Pythons (runner Macs); Release validation must test the one the images ship.
    for workflow in WORKFLOWS:
        versions = [minor(v) for v in re.findall(r"python-version:\s*['\"]?([\d.]+)", workflow.read_text())]
        if workflow.name == 'ci.yml':
            assert versions and set(versions) == {python}, f'Release validation tests on {versions}, the image runs {python}'
        elif versions:
            assert python in versions, f'{workflow.name} tests on {versions}, never on the image Python {python}'


def crate_msrv(crate):
    return minor(re.search(r'^rust-version\s*=\s*"([\d.]+)"', (ROOT / crate / 'Cargo.toml').read_text(), re.M).group(1))


def rust_stages():
    """(file, stage, compiler version, crate) for each Docker stage that builds a Rust crate."""
    stages = []
    for path in (ROOT / 'Dockerfile', ROOT / 'relay-rs' / 'Dockerfile'):
        for block in re.split(r'^(?=FROM )', path.read_text(), flags=re.M):
            image = re.match(r'FROM rust:([\d.]+)\S*', block)
            if not image:
                continue
            crate = re.search(r'^COPY ([\w-]+)/Cargo\.toml ', block, re.M)
            assert crate, f'{path.name}: {block.splitlines()[0]} copies no crate'
            stages.append((path.relative_to(ROOT), block.splitlines()[0], minor(image.group(1)), crate.group(1)))
    return stages


def test_every_pinned_rust_can_compile_its_crates():
    stages = rust_stages()
    assert {crate for *_, crate in stages} == {'runner-rs', 'relay-rs'}, stages
    for path, line, compiler, crate in stages:
        assert compiler >= crate_msrv(crate), (f'{path}: {line} is older than {crate} rust-version '
                                               f'{".".join(map(str, crate_msrv(crate)))}')
    msrv = max(crate_msrv('runner-rs'), crate_msrv('relay-rs'))
    pins = [(workflow.name, version) for workflow in WORKFLOWS
            for version in re.findall(r'rustup toolchain install ([\d.]+)', workflow.read_text())]
    assert pins, 'no workflow pins a Rust toolchain'
    for name, version in pins:
        assert minor(version) >= msrv, f'{name} installs Rust {version}, older than rust-version {msrv}'


def test_workflows_test_on_the_image_node():
    versions = dict(line.split('=', 1) for line in (ROOT / 'docker' / 'versions.env').read_text().splitlines()
                    if line and not line.startswith('#'))
    image_major = int(versions['NODE_VERSION'].split('.')[0])
    found = 0
    for workflow in WORKFLOWS:
        for version in re.findall(r"node-version:\s*['\"]?(\d+)", workflow.read_text()):
            found += 1
            assert int(version) == image_major, f'{workflow.name} tests on Node {version}, the images ship {image_major}'
    assert found, 'no workflow pins a Node version'
