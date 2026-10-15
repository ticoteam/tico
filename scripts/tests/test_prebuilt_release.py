"""Publication requires complete source-bound artifacts and leaves a failed upload unpublished."""
import copy
import gzip
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import prebuilt_release as P

ROOT = Path(__file__).resolve().parents[2]
COMMIT, VERSION, PREVIOUS, REPO = 'a' * 40, 'v1.2.3', 'v1.2.2', 'acme/tico'


@pytest.fixture
def release(tmp_path, monkeypatch):
    source, binaries, output = (tmp_path / name for name in ['source', 'binaries', 'release'])
    source.mkdir(); binaries.mkdir()
    files = {'scripts/install.sh': '#!/bin/sh\nVERSION=@TICO_VERSION@\n',
             'infra/windows/install-wsl.ps1': '$Baked = "@TICO_VERSION@"\n',
             'compose.yaml': 'services: {}\n', '.env.example': 'TICO_TAG=dev\n',
             'CHANGELOG.md': '# Changelog\n## [1.2.3]\n- A checked release.\n',
             'runner-rs/build.rs': 'fn main() {}', 'runner-rs/Cargo.toml': '[package]\nname="runner"',
             'runner-rs/Cargo.lock': '# locked', 'runner-rs/src/main.rs': 'fn main() {}',
             'docker/updater.py': 'print("updater")'}
    for name in ['scripts/changelog_notes.py', 'scripts/build_install_bundle.py', 'backend/code_hash.py']:
        files[name] = (ROOT / name).read_text()
    for name in P.BUILD_WORKFLOWS:
        files['.github/workflows/' + name] = 'on:\n  push:\n    tags: ["v*"]\n' if name != 'app.yml' else 'on:\n  push:\n    branches: [main]\n'
    for name, content in files.items():
        path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
    build_id = P.source_id(source)
    for name, (kind, machine) in P.RUNNERS.items():
        raw = bytearray(32)
        if kind == 'elf':
            raw[:6] = b'\x7fELF\x02\x01'; struct.pack_into('<H', raw, 18, machine)
        else:
            raw[:4] = b'\xcf\xfa\xed\xfe'; struct.pack_into('<I', raw, 4, machine)
        (binaries / f'tico-runner-{name}.gz').write_bytes(gzip.compress(bytes(raw) + build_id.encode(), mtime=0))
    (binaries / 'tico-runner-SHA256SUMS').write_text(''.join(f'{P.sha256(p.read_bytes())}  {p.name}\n' for p in sorted(binaries.glob('*.gz'))))
    gate = tmp_path / 'gate.log'
    gate.write_text(f'Release check: candidate v9.9.9 from {COMMIT[:7]}; logs in /tmp/check\n' +
                    ''.join(f'| {phase} | 0.0 | 1.0 | 0 |\n' for phase in sorted(P.PHASES)) + 'Release check passed, over budget: 900s / 480s\n')
    receipt = {'version': VERSION, 'commit': COMMIT, 'repository': REPO, 'images': {}}
    code = P.module(source, 'backend/code_hash.py')['digest'](source)
    for n, image in enumerate(P.IMAGES):
        receipt['images'][image] = {}
        for m, platform in enumerate(P.PLATFORMS):
            row = {'digest': 'sha256:' + str(n * 2 + m + 1) * 64, 'config_digest': 'sha256:' + str(n * 2 + m + 2) * 64,
                   'commit': COMMIT, 'repository': REPO}
            if image == 'tico-updater': row['updater_sha256'] = P.sha256((source / 'docker/updater.py').read_bytes())
            else: row['code_sha256'] = code
            if image == 'tico-runner': row['runner_build_id'] = build_id
            receipt['images'][image][platform] = row
    receipt['images']['tico']['linux/amd64']['private_build_host'] = 'private operator metadata'
    receipt_path = tmp_path / 'images.json'; receipt_path.write_text(json.dumps(receipt))
    bundle_names = ['Tico.dmg', 'Tico.app.tar.gz', 'Tico-setup.exe', 'Tico.AppImage', 'Tico.deb']
    data = {name: b'signed desktop bytes' for name in bundle_names}
    for name in ['Tico.app.tar.gz', 'Tico-setup.exe', 'Tico.AppImage']: data[name + '.sig'] = b'update-signature'
    base = f'https://github.com/{REPO}/releases/download/{PREVIOUS}/'
    previous_manifest = {'app_kind': 'generic', 'version': '1.2.2', 'notes': 'previous', 'pub_date': '2026-01-01T00:00:00Z',
                         'platforms': {target: {'url': base + name, 'signature': 'update-signature'} for target, name in
                                       [('darwin-aarch64', 'Tico.app.tar.gz'), ('darwin-x86_64', 'Tico.app.tar.gz'),
                                        ('windows-x86_64', 'Tico-setup.exe'), ('linux-x86_64', 'Tico.AppImage')]},
                         'installers': {target: {'url': base + name, 'file': name} for target, name in
                                        [('mac', 'Tico.dmg'), ('windows', 'Tico-setup.exe'), ('linux', 'Tico.AppImage'), ('linux_deb', 'Tico.deb')]}}
    data['latest.json'] = (json.dumps(previous_manifest, indent=2) + '\n').encode()
    previous_release = {'tag_name': PREVIOUS, 'draft': False, 'assets': [{'name': name, 'digest': 'sha256:' + P.sha256(raw)} for name, raw in data.items()]}
    state = {'mutations': [], 'disabled': True, 'bad_config': False, 'upload_bad': False, 'existing_tag': None, 'draft': False, 'tag_moved': False}
    original_command = P.command

    def command(args, **kw):
        if args[0] == 'git':
            return COMMIT + '\n' if 'rev-parse' in args else ''
        if args[0] == P.sys.executable: return original_command(args)
        if args[:3] == ['gh', 'release', 'download']:
            name = args[args.index('--pattern') + 1]
            if '--output' in args: return data[name].decode()
            (Path(args[args.index('--dir') + 1]) / name).write_bytes(data[name]); return ''
        if args[:4] == ['docker', 'buildx', 'imagetools', 'inspect']:
            ref = args[4]; image = ref.split('/')[2].split(':')[0].split('@')[0]
            rows = receipt['images'][image]
            if '@' not in ref:
                return json.dumps({'manifests': [{'digest': row['digest'], 'platform': dict(zip(['os', 'architecture'], platform.split('/')))} for platform, row in rows.items()]})
            platform, row = next((p, r) for p, r in rows.items() if r['digest'] == ref.split('@')[1])
            if '--raw' in args: return json.dumps({'config': {'digest': ('sha256:' + 'f' * 64) if state['bad_config'] else row['config_digest']}})
            return json.dumps({'os': 'linux', 'architecture': platform.split('/')[1], 'config': {
                'Labels': {'org.opencontainers.image.version': VERSION, 'org.opencontainers.image.source': 'https://github.com/' + REPO},
                'Env': ['TICO_VERSION=' + VERSION]}})
        state['mutations'].append(args)
        if args[:3] == ['gh', 'release', 'create']: state['draft'] = True
        if args[:4] == ['gh', 'api', '--method', 'POST']: state['existing_tag'] = COMMIT
        return ''

    def gh(repo, path, **kw):
        assert repo == REPO
        if path == 'releases/tags/' + PREVIOUS: return previous_release
        if path.startswith('actions/workflows'):
            return {'workflows': [{'path': '.github/workflows/' + name, 'state': 'disabled_manually' if state['disabled'] else 'active'} for name in P.BUILD_WORKFLOWS]}
        if path == 'git/ref/tags/' + VERSION:
            return {'object': {'type': 'commit', 'sha': ('b' * 40 if state['tag_moved'] and state['draft'] else state['existing_tag'])}} if state['existing_tag'] else None
        if path == 'releases/tags/' + VERSION:
            if not state['draft']: return None
            manifest = json.loads((output / 'prebuilt-release.json').read_text())
            assets = [{'name': n, 'digest': 'sha256:' + h} for n, h in manifest['assets'].items() if n != 'notes.md']
            assets.append({'name': 'prebuilt-release.json', 'digest': 'sha256:' + P.sha256((output / 'prebuilt-release.json').read_bytes())})
            if state['upload_bad']: assets.pop()
            return {'assets': assets, 'draft': True, 'tag_name': VERSION, 'body': (output / 'notes.md').read_text()}
        raise AssertionError(path)

    monkeypatch.setattr(P, 'command', command); monkeypatch.setattr(P, 'gh', gh)
    args = SimpleNamespace(source=source, artifacts=output, version=VERSION, commit=COMMIT, repository=REPO,
                           receipt=receipt_path, gate_log=gate, runner_dir=binaries, desktop_from=PREVIOUS)
    P.prepare(args)
    return SimpleNamespace(source=source, output=output, receipt=receipt, state=state, args=args)


def test_checked_prebuilt_release_publishes_only_after_all_assets_and_no_builds(release):
    result = P.publish(release.source, release.output)
    calls = release.state['mutations']
    assert result['commit'] == COMMIT and result['runner_build_id'] == P.source_id(release.source)
    assert 'private_build_host' not in (release.output / 'prebuilt-release.json').read_text()
    assert calls[0][:4] == ['gh', 'api', '--method', 'POST']
    assert calls[1][:3] == ['gh', 'release', 'create'] and '--draft' in calls[1]
    assert calls[2][-1] == str(release.output / 'latest.json')
    assert calls[-1][:3] == ['gh', 'release', 'edit'] and '--draft=false' in calls[-1]
    assert not any('build' in c or 'workflow' in c for c in calls)
    for name in P.BUILD_WORKFLOWS:
        workflow = P.yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
        events = workflow.get('on', workflow.get(True))
        assert 'push' not in events and 'workflow_dispatch' in events
    manifest = json.loads((release.output / 'latest.json').read_text())
    assert manifest['version'] == '1.2.2'  # unchanged app keeps its own version and signatures
    assert all('/' + VERSION + '/' in x['url'] for x in manifest['platforms'].values())


def test_wrong_source_platform_checksum_or_unsuppressed_builds_never_mutate_github(release):
    baseline = {p.name: p.read_bytes() for p in release.output.iterdir()}
    original = json.loads(baseline['prebuilt-release.json'])
    cases = ['platform', 'receipt-source', 'updater-file', 'native-platform', 'installer', 'desktop-url', 'gate', 'registry-config', 'workflow', 'existing-tag', 'private-top-field', 'private-image-field']
    for case in cases:
        for name, raw in baseline.items(): (release.output / name).write_bytes(raw)
        value = copy.deepcopy(original)
        if case == 'private-top-field': value['operator_note'] = 'private operator metadata'
        if case == 'private-image-field': value['images']['tico']['linux/amd64']['operator_note'] = 'private operator metadata'
        if case == 'platform': del value['images']['tico-runner']['linux/arm64']
        if case == 'receipt-source': value['images']['tico']['linux/amd64']['commit'] = 'b' * 40
        if case == 'updater-file': value['images']['tico-updater']['linux/amd64']['updater_sha256'] = 'b' * 64
        if case == 'native-platform':
            name = 'tico-runner-linux-arm64.gz'; raw = bytearray(gzip.decompress(baseline[name])); struct.pack_into('<H', raw, 18, 62)
            (release.output / name).write_bytes(gzip.compress(raw))
            # Update both checksums: the platform/fingerprint guard, not a stale checksum, must stop it.
            (release.output / 'tico-runner-SHA256SUMS').write_text(''.join(f'{P.sha256((release.output/n).read_bytes())}  {n}\n' for n in sorted(baseline) if n.startswith('tico-runner-') and n.endswith('.gz')))
            for n in [name, 'tico-runner-SHA256SUMS']: value['assets'][n] = P.sha256((release.output / n).read_bytes())
        if case == 'installer': (release.output / 'install.sh').write_text('wrong version')
        if case == 'desktop-url':
            latest = json.loads(baseline['latest.json']); latest['platforms']['linux-x86_64']['url'] = 'https://elsewhere.example/app'
            (release.output / 'latest.json').write_text(json.dumps(latest)); value['assets']['latest.json'] = P.sha256((release.output / 'latest.json').read_bytes())
        if case == 'gate': (release.output / 'gate.log').write_text('Release check passed: 1s')
        release.state.update(bad_config=case == 'registry-config', disabled=case != 'workflow', existing_tag='b'*40 if case == 'existing-tag' else None)
        (release.output / 'prebuilt-release.json').write_text(json.dumps(value))
        with pytest.raises(ValueError): P.publish(release.source, release.output)
        assert release.state['mutations'] == [], case


def test_incomplete_upload_or_moved_tag_keeps_the_release_draft(release):
    for failure in ['upload_bad', 'tag_moved']:
        release.state.update(draft=False, existing_tag=None, upload_bad=False, tag_moved=False)
        release.state[failure] = True
        with pytest.raises(ValueError): P.publish(release.source, release.output)
        assert release.state['draft']
        assert not any('--draft=false' in c for c in release.state['mutations'])


def test_source_requiring_screen_relay_assets_fails_before_prepare_or_publication(release):
    workflow = release.source / '.github/workflows/release.yml'
    workflow.write_text(workflow.read_text() + '\njobs:\n  release:\n    steps:\n      - run: gh release upload "$GITHUB_REF_NAME" dist/tico-screen-relay-*.gz\n')
    before = {p.name: p.read_bytes() for p in release.output.iterdir()}
    for action in [lambda: P.prepare(release.args),
                   lambda: P.validate(release.source, release.output),
                   lambda: P.publish(release.source, release.output)]:
        with pytest.raises(ValueError, match='Source requires screen-relay release assets'):
            action()
        assert release.state['mutations'] == []
        assert {p.name: p.read_bytes() for p in release.output.iterdir()} == before
