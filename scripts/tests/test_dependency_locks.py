"""The dependency locks must match what they lock, so installs and `--locked` builds succeed.

The images install backend/requirements.txt, CI installs backend/requirements-dev.txt and runs `npm ci`, and both
Rust crates build with `cargo build --locked`. An edit to requirements.in, package.json or Cargo.toml without
regenerating its lock passes a PR's tests and fails the image build or Release validation instead. These checks read
the files only (no resolver, no network), so they run on every PR.
"""
import json
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
PIN = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]+\])?)==([^\s;]+)\s*(?:;.*)?$')


def locked(path):
    """{canonical name: version} from a uv-compiled lock; every requirement line must be an exact pin."""
    pins = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = PIN.match(line)
        assert match, f'{Path(path).name}: {line!r} is not an exact pin (name==version)'
        name = canonicalize_name(re.sub(r'\[.*\]', '', match.group(1)))
        assert name not in pins, f'{Path(path).name}: {name} is pinned twice'
        pins[name] = match.group(2)
    assert pins, f'{path} pins nothing'
    return pins


def requested(path):
    """The requirements an .in file asks for, without its -r/-c include lines."""
    found = []
    for line in Path(path).read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if line and not line.startswith('-'):
            found.append(Requirement(line))
    return found


def check_satisfied(source, lock):
    pins = locked(lock)
    for requirement in requested(source):
        name = canonicalize_name(requirement.name)
        assert name in pins, f'{source.name} asks for {requirement}, but {lock.name} does not lock it: recompile the lock'
        assert requirement.specifier.contains(pins[name], prereleases=True), (
            f'{source.name} asks for {requirement}, {lock.name} locks {pins[name]}: recompile the lock')


def test_runtime_lock_satisfies_requirements_in():
    check_satisfied(BACKEND / 'requirements.in', BACKEND / 'requirements.txt')


def test_dev_lock_satisfies_requirements_dev_in():
    check_satisfied(BACKEND / 'requirements-dev.in', BACKEND / 'requirements-dev.txt')


def test_dev_lock_tests_the_versions_the_image_ships():
    # requirements-dev.in is constrained by requirements.txt, so CI tests exactly what the images install.
    runtime, dev = locked(BACKEND / 'requirements.txt'), locked(BACKEND / 'requirements-dev.txt')
    drift = {name: (version, dev.get(name)) for name, version in runtime.items() if dev.get(name) != version}
    assert not drift, f'requirements-dev.txt differs from requirements.txt (image, dev): {drift}: recompile it'


def test_package_lock_matches_package_json():
    manifest = json.loads((ROOT / 'package.json').read_text())
    lock = json.loads((ROOT / 'package-lock.json').read_text())
    root = lock['packages']['']
    for section in ('dependencies', 'devDependencies'):
        assert root.get(section, {}) == manifest.get(section, {}), f'package-lock.json {section} is stale: run npm install'
        for name, spec in manifest.get(section, {}).items():
            assert re.fullmatch(r'\d+\.\d+\.\d+', spec), f'package.json {name} {spec!r} is not an exact version'
            installed = lock['packages'].get(f'node_modules/{name}', {}).get('version')
            assert installed == spec, f'package-lock.json installs {name} {installed}, package.json pins {spec}'


def test_cargo_locks_match_their_manifests():
    for crate in ('runner-rs', 'relay-rs'):
        manifest = tomllib.loads((ROOT / crate / 'Cargo.toml').read_text())
        lock = tomllib.loads((ROOT / crate / 'Cargo.lock').read_text())
        packages = {}
        for package in lock['package']:
            packages.setdefault(package['name'], []).append(package['version'])
        name, version = manifest['package']['name'], manifest['package']['version']
        # `cargo build --locked` refuses to start when the crate's own version moved without its lock.
        assert version in packages.get(name, []), f'{crate}/Cargo.lock has {name} {packages.get(name)}, Cargo.toml says {version}'
        for section in ('dependencies', 'build-dependencies', 'dev-dependencies'):
            for dependency, spec in manifest.get(section, {}).items():
                package = spec.get('package', dependency) if isinstance(spec, dict) else dependency
                assert package in packages, f'{crate}/Cargo.toml {section} {dependency} is not in Cargo.lock: run cargo update -p'
