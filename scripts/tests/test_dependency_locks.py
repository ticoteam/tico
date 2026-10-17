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

import pytest
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


def test_python_locks_satisfy_what_they_lock():
    check_satisfied(BACKEND / 'requirements.in', BACKEND / 'requirements.txt')
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


def cargo_requirement_allows(requirement, version):
    """Whether a Cargo version requirement ("1", "^0.2.3", "~1.2", "=1.2.3", ">=1, <2", "1.*") allows `version`,
    by Cargo's rules: a bare version is a caret requirement."""
    have = tuple(int(part) for part in re.match(r'(\d+)\.(\d+)\.(\d+)', version).groups())
    for comparator in requirement.split(','):
        comparator = comparator.strip()
        if comparator in ('*', ''):
            continue
        match = re.fullmatch(r'(\^|~|=|>=|<=|>|<)?\s*(\d+)(?:\.(\d+|\*|x|X))?(?:\.(\d+|\*|x|X))?(?:[-+]\S*)?', comparator)
        assert match, f'unrecognised Cargo version requirement {comparator!r}'
        op = match.group(1) or '^'
        given = []
        for part in match.groups()[1:]:
            if part is None or part in '*xX':
                op = '~' if part and op == '^' else op   # 1.* means >=1.0.0, <2.0.0, like ~1
                break
            given.append(int(part))
        low = tuple(given + [0] * (3 - len(given)))

        def bump(index):   # the first version past `given` at that position: (1, 2) at 1 -> (1, 3, 0)
            return tuple(given[:index] + [given[index] + 1] + [0] * (2 - index))

        if op == '^':
            index = next((i for i, part in enumerate(given) if part), len(given) - 1)
            ok = low <= have < bump(index)
        elif op == '~':
            ok = low <= have < bump(min(len(given), 2) - 1)
        elif op == '=':
            ok = low <= have < bump(len(given) - 1) if len(given) < 3 else have == low
        elif op == '>':
            ok = have >= bump(len(given) - 1) if len(given) < 3 else have > low
        elif op == '>=':
            ok = have >= low
        elif op == '<':
            ok = have < low
        else:   # <=
            ok = have < bump(len(given) - 1) if len(given) < 3 else have <= low
        if not ok:
            return False
    return True


def cargo_lock_problems(crate_dir):
    """Why `cargo build --locked` would refuse this crate's Cargo.lock, read from the files only."""
    manifest = tomllib.loads((crate_dir / 'Cargo.toml').read_text())
    lock = tomllib.loads((crate_dir / 'Cargo.lock').read_text())
    versions = {}
    for package in lock['package']:
        versions.setdefault(package['name'], []).append(package['version'])
    name, version = manifest['package']['name'], manifest['package']['version']
    # `cargo build --locked` refuses to start when the crate's own version moved without its lock.
    root = [package for package in lock['package'] if package['name'] == name and package['version'] == version]
    if not root:
        return [f'Cargo.lock has {name} {versions.get(name)}, Cargo.toml says {version}']
    # The crate's own entry names what each dependency resolved to ("base64 0.22.1" when the lock has several).
    resolved = {}
    for entry in root[0].get('dependencies', []):
        dependency, *rest = entry.split()
        resolved[dependency] = rest[0] if rest else versions[dependency][0]
    sections = [manifest] + list(manifest.get('target', {}).values())
    problems = []
    for table in sections:
        for section in ('dependencies', 'build-dependencies', 'dev-dependencies'):
            for dependency, spec in table.get(section, {}).items():
                spec = spec if isinstance(spec, dict) else {'version': spec}
                package = spec.get('package', dependency)
                if package not in resolved:
                    problems.append(f'{section} {dependency} is not in Cargo.lock')
                elif 'version' in spec and not cargo_requirement_allows(spec['version'], resolved[package]):
                    problems.append(f'{section} {dependency} = "{spec["version"]}", Cargo.lock has {resolved[package]}')
    return problems


def test_cargo_locks_match_their_manifests():
    for crate in ('runner-rs', 'relay-rs'):
        problems = cargo_lock_problems(ROOT / crate)
        assert not problems, f'{crate}: {problems}: run cargo update -p <crate> and commit Cargo.lock'


@pytest.mark.parametrize('requirement, version, allowed', [
    ('1', '1.53.3', True), ('1', '2.0.0', False), ('0.2', '0.2.190', True), ('0.2', '0.3.0', False),
    ('0.2.191', '0.2.190', False), ('0.2.191', '0.2.191', True), ('^0.0.3', '0.0.4', False), ('0.22', '0.23.1', False),
    ('~1.2', '1.2.9', True), ('~1.2', '1.3.0', False), ('=1.2.3', '1.2.4', False), ('>=1, <2', '1.9.0', True),
    ('>=1, <2', '2.0.0', False), ('1.*', '1.7.0', True), ('1.*', '2.0.0', False), ('*', '9.9.9', True),
    ('>1.2', '1.2.9', False), ('<=1.2', '1.2.9', True),
])
def test_cargo_requirements_follow_cargos_rules(requirement, version, allowed):
    assert cargo_requirement_allows(requirement, version) is allowed


def test_a_dependency_raised_past_its_locked_version_is_caught(tmp_path):
    # runner-rs with libc raised to 0.2.191 while Cargo.lock still has 0.2.190: `cargo metadata --locked` exits 101.
    crate = tmp_path / 'runner-rs'
    crate.mkdir()
    (crate / 'Cargo.lock').write_text((ROOT / 'runner-rs' / 'Cargo.lock').read_text())
    manifest = (ROOT / 'runner-rs' / 'Cargo.toml').read_text()
    locked_libc = re.search(r'name = "libc"\nversion = "([^"]+)"', (crate / 'Cargo.lock').read_text()).group(1)
    major, minor, patch = map(int, locked_libc.split('.'))
    raised = f'{major}.{minor}.{patch + 1}'
    (crate / 'Cargo.toml').write_text(re.sub(r'^libc = "[^"]+"', f'libc = "{raised}"', manifest, flags=re.M))
    assert cargo_lock_problems(crate) == [f'dependencies libc = "{raised}", Cargo.lock has {locked_libc}']
    (crate / 'Cargo.toml').write_text(manifest)
    assert cargo_lock_problems(crate) == []
