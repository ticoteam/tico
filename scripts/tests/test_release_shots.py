"""Release shots: which pages a diff picks, the markdown section, unchanged pairs, the evidence branch and the workflow."""

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import zlib

import pytest
import yaml

from scripts import release_shots as shots

ROOT = shots.ROOT
HARNESS_FILES = ('index.cts', 'select.cts', 'compare.cts')
EVIDENCE = 'https://github.com/ticoteam/tico/blob/release-evidence'


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    """Write files, commit them, and return the new commit's SHA."""
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, 'add', '-A')
    git(
        repo,
        '-c',
        'user.name=Test',
        '-c',
        'user.email=test@example.com',
        'commit',
        '-q',
        '--allow-empty',
        '-m',
        message,
    )
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository holding the harness, a few ui/ files and a base commit; the tests commit a head on top."""
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    for name in HARNESS_FILES:
        target = root / 'scripts' / 'screenshots' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / 'scripts' / 'screenshots' / name, target)
    commit(
        root,
        {
            'ui/app/updates.ts': 'updates\n',
            'ui/app/tasks.ts': 'tasks\n',
            'ui/styles/base.css': 'base\n',
            'backend/x.py': 'x = 1\n',
        },
        'base',
    )
    return root


def picked(repo: Path, head_files: dict[str, str]) -> shots.Selection:
    base = git(repo, 'rev-parse', 'HEAD')
    head = commit(repo, head_files, 'head')
    return shots.select_pages(base, head, repo)


def all_page_names(repo: Path) -> set[str]:
    text = (repo / 'scripts' / 'screenshots' / 'index.cts').read_text()
    return {match.group(1) for match in shots.PAGE_NAME.finditer(text)}


def test_a_mapped_file_picks_only_the_pages_that_draw_it(repo: Path) -> None:
    selection = picked(repo, {'ui/app/updates.ts': 'updates changed\n'})
    # The support popover draws the updates file too, so both pages are shot, and no others.
    assert selection.pages == ['updates', 'support']
    assert not selection.every
    assert 'pages updates, support' in selection.reason


def test_a_shared_file_picks_every_page(repo: Path) -> None:
    selection = picked(repo, {'ui/styles/base.css': 'changed\n'})
    assert selection.every
    assert set(selection.pages) == all_page_names(repo)


def test_no_ui_change_picks_no_pages(repo: Path) -> None:
    selection = picked(repo, {'backend/x.py': 'x = 2\n'})
    assert selection.pages == []
    assert not selection.every
    assert selection.reason == 'no ui/ change: no pages'


def test_ui_tests_and_baselines_pick_no_pages(repo: Path) -> None:
    selection = picked(repo, {'ui/tests/updates.cjs': 'changed\n'})
    assert selection.pages == []
    assert selection.reason == 'only ui/ tests or baselines: no pages'


def test_a_new_ui_file_that_no_table_names_picks_every_page(repo: Path) -> None:
    selection = picked(repo, {'ui/app/brand-new.js': 'new\n'})
    assert selection.every
    assert 'unmapped ui/app/brand-new.js' in selection.reason


def test_an_unshown_file_picks_no_page_and_is_named(repo: Path) -> None:
    selection = picked(repo, {'ui/app/vault.ts': 'vault\n'})
    assert selection.pages == []
    assert selection.unshown == ['ui/app/vault.ts']
    assert 'not shown: ui/app/vault.ts' in selection.reason


def test_a_dark_line_turns_the_dark_pictures_on(repo: Path) -> None:
    assert picked(repo, {'ui/app/updates.ts': 'updates in dark mode\n'}).dark


def test_a_dark_theme_file_turns_the_dark_pictures_on(repo: Path) -> None:
    assert picked(repo, {'ui/styles/tokens.css': 'tokens\n'}).dark


def test_every_page_can_be_limited_to_named_pages() -> None:
    assert sorted(shots.every_page_selection(('market', 'bot-chat')).pages) == ['bot-chat', 'market']
    with pytest.raises(RuntimeError, match='no-such-page'):
        shots.every_page_selection(('no-such-page',))


def test_every_page_names_real_files_and_every_ui_file_is_named() -> None:
    listing = json.loads(
        subprocess.run(
            ['node', 'scripts/screenshots/index.cts', '--list-pages'],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    stale = [
        f'{page["name"]}: {glob}' for page in listing['pages'] for glob, files in page['matched'].items() if not files
    ]
    assert not stale, 'globs that match no tracked file: ' + ', '.join(stale)
    assert listing['unmapped'] == [], (
        'ui/ files no table names. Add each to a PAGES entry ui list in scripts/screenshots/index.cts, or to SHARED, '
        'UNSHOWN or NON_RENDERING in scripts/screenshots/select.cts: ' + ', '.join(listing['unmapped'])
    )


def sample_manifest() -> shots.Manifest:
    return shots.Manifest(
        version='v1.2.3',
        base_ref='v1.2.2',
        base_sha='a' * 40,
        head_ref='origin/main',
        head_sha='b' * 40,
        selection=shots.Selection(
            pages=['updates', 'bot-chat', 'tasks'],
            every=False,
            dark=True,
            reason='pages updates, bot-chat, tasks',
            unshown=['ui/app/vault.js'],
        ),
        shots=[
            shots.Shot(
                'updates',
                'desktop-1440',
                'light',
                'updates-desktop-1440-before.png',
                'updates-desktop-1440-after.png',
                12.5,
            ),
            shots.Shot('updates', 'phone', 'light', 'updates-phone-before.png', 'updates-phone-after.png', 0.02),
            shots.Shot(
                'updates',
                'desktop-1440',
                'dark',
                'updates-desktop-1440-dark-before.png',
                'updates-desktop-1440-dark-after.png',
                3.0,
            ),
            shots.Shot(
                'bot-chat',
                'desktop-1440',
                'light',
                'bot-chat-desktop-1440-before.png',
                'bot-chat-desktop-1440-after.png',
                40.0,
            ),
            shots.Shot('bot-chat', 'phone', 'light', 'bot-chat-phone-before.png', 'bot-chat-phone-after.png', 33.0),
            shots.Shot('tasks', 'desktop-1440', 'light', None, 'tasks-desktop-1440-after.png', None),
        ],
        problems=shots.Problems(before=[], after=['tasks-desktop-1440-light: console error: boom | again']),
    )


def test_markdown_shows_each_page_before_and_after_with_phone_underneath() -> None:
    text = shots.render_markdown(sample_manifest(), 'ticoteam/tico')
    assert text.startswith('## What it looks like\n')
    updates_desktop = (
        f'| ![Updates before, Desktop, 1440 px]({EVIDENCE}/v1.2.3/updates-desktop-1440-before.png?raw=true) '
        f'| ![Updates after, Desktop, 1440 px]({EVIDENCE}/v1.2.3/updates-desktop-1440-after.png?raw=true) |'
    )
    assert updates_desktop in text
    bot_desktop = text.index('![Bot chat before, Desktop, 1440 px]')
    bot_phone = text.index('![Bot chat before, Phone, 390 px]')
    assert text.index('### Bot chat') < bot_desktop < bot_phone


def test_markdown_puts_a_dark_table_after_the_light_ones() -> None:
    text = shots.render_markdown(sample_manifest(), 'ticoteam/tico')
    assert 'Dark theme, desktop, 1440 px' in text
    assert f'{EVIDENCE}/v1.2.3/updates-desktop-1440-dark-after.png?raw=true' in text
    assert text.index('Dark theme, desktop, 1440 px') > text.index('Desktop, 1440 px')


def test_markdown_lists_unchanged_pairs_on_one_line_and_leaves_their_pictures_out() -> None:
    text = shots.render_markdown(sample_manifest(), 'ticoteam/tico')
    assert 'Unchanged (under 0.1% of pixels changed): `updates` phone light 0.02%.' in text
    assert 'updates-phone-after.png' not in text


def test_markdown_marks_a_new_page_and_names_what_no_picture_shows() -> None:
    text = shots.render_markdown(sample_manifest(), 'ticoteam/tico')
    assert (
        f'| new page | ![Tasks after, Desktop, 1440 px]({EVIDENCE}/v1.2.3/tasks-desktop-1440-after.png?raw=true) |'
    ) in text
    assert 'Changed, but no picture shows them: `ui/app/vault.js`.' in text


def test_markdown_escapes_pipes_in_capture_problems() -> None:
    text = shots.render_markdown(sample_manifest(), 'ticoteam/tico')
    assert '- after: tasks-desktop-1440-light: console error: boom \\| again' in text


def test_markdown_for_a_release_with_no_page_change_says_so() -> None:
    manifest = shots.Manifest(
        version='v1.2.3',
        base_ref='v1.2.2',
        base_sha='a' * 40,
        head_ref='origin/main',
        head_sha='b' * 40,
        selection=shots.Selection(pages=[], every=False, dark=False, reason='no ui/ change: no pages', unshown=[]),
        shots=[],
        problems=shots.Problems(before=[], after=[]),
    )
    text = shots.render_markdown(manifest, 'ticoteam/tico')
    assert 'No page draws a file this release changed (no ui/ change: no pages)' in text
    assert '|' not in text


@pytest.mark.parametrize(('pct', 'unchanged'), [(0.0999, True), (0.1, False), (None, False)])
def test_a_pair_under_a_tenth_of_a_percent_is_unchanged(pct: float | None, unchanged: bool) -> None:
    assert shots.Shot('updates', 'phone', 'light', 'b.png', 'a.png', pct).unchanged is unchanged


def test_a_base_from_before_the_rename_reads_the_old_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / 'legacy'
    (legacy / 'scripts' / 'screenshots').mkdir(parents=True)
    (legacy / 'scripts' / 'screenshots' / 'index.js').write_text(
        "const PAGES = [\n  {name: 'updates', route: '#/updates'},\n];\n"
    )
    git(legacy, 'init', '-q')
    git(legacy, 'add', '.')
    git(legacy, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-q', '-m', 'old harness')
    monkeypatch.setattr(shots, 'ROOT', legacy)
    assert shots.page_names(git(legacy, 'rev-parse', 'HEAD')) == {'updates'}


def test_only_changed_pictures_and_new_pages_are_uploaded() -> None:
    names = set(shots.evidence_names(sample_manifest().shots))
    assert names == {
        'updates-desktop-1440-before.png',
        'updates-desktop-1440-after.png',
        'updates-desktop-1440-dark-before.png',
        'updates-desktop-1440-dark-after.png',
        'bot-chat-desktop-1440-before.png',
        'bot-chat-desktop-1440-after.png',
        'bot-chat-phone-before.png',
        'bot-chat-phone-after.png',
        'tasks-desktop-1440-after.png',
    }


def png(width: int, height: int, pixels: list[tuple[int, int, int, int]], color_type: int = 6) -> bytes:
    """A PNG of RGBA pixels, row by row, with no filtering: RGBA (color type 6), or RGB (2) without the alpha."""
    channels = 4 if color_type == 6 else 3
    raw = b''.join(
        b'\x00' + bytes(channel for pixel in pixels[row * width : (row + 1) * width] for channel in pixel[:channels])
        for row in range(height)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack('>IIBBBBB', width, height, 8, color_type, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def test_changed_pixels_counts_past_the_tolerance_and_flags_a_size_change(tmp_path: Path) -> None:
    white = (255, 255, 255, 255)
    files = {
        'before.png': png(2, 2, [white] * 4),
        'same.png': png(2, 2, [white] * 4),
        'nudged.png': png(2, 2, [(254, 255, 255, 255)] + [white] * 3),
        'changed.png': png(2, 2, [(0, 0, 0, 255)] + [white] * 3),
        'wide.png': png(4, 1, [white] * 4),
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    pairs = [(key, tmp_path / 'before.png', tmp_path / f'{key}.png') for key in ('same', 'nudged', 'changed', 'wide')]
    percent = shots.changed_pixels(pairs, tmp_path)
    assert percent == {'same': 0.0, 'nudged': 0.0, 'changed': 25.0, 'wide': 100.0}


def test_an_rgb_picture_matches_the_same_pixels_in_rgba(tmp_path: Path) -> None:
    """RGB (color type 2) decodes as opaque, so it matches the same pixels saved as RGBA (color type 6)."""
    pixels = [(10, 20, 30, 255), (255, 255, 255, 255), (0, 128, 255, 255), (200, 100, 50, 255)]
    (tmp_path / 'rgb.png').write_bytes(png(2, 2, pixels, color_type=2))
    (tmp_path / 'rgba.png').write_bytes(png(2, 2, pixels))
    percent = shots.changed_pixels([('same', tmp_path / 'rgb.png', tmp_path / 'rgba.png')], tmp_path)
    assert percent == {'same': 0.0}


def test_upload_evidence_adds_versions_to_an_orphan_branch_and_leaves_other_branches(tmp_path: Path) -> None:
    bare = tmp_path / 'remote.git'
    git(tmp_path, 'init', '-q', '--bare', str(bare))
    work = tmp_path / 'work'
    work.mkdir()
    git(work, 'init', '-q')
    main = commit(work, {'README': 'main\n'}, 'main')
    git(work, 'push', '-q', str(bare), f'{main}:refs/heads/main')

    pictures = tmp_path / 'pictures'
    pictures.mkdir()
    first = pictures / 'updates-desktop-1440-after.png'
    first.write_bytes(b'first')
    commit_one = shots.upload_evidence([first], 'v9.9.9', repo_dir=work, remote=str(bare))
    second = pictures / 'tasks-phone-after.png'
    second.write_bytes(b'second')
    commit_two = shots.upload_evidence([second], 'v9.9.10', repo_dir=work, remote=str(bare))

    listed = git(bare, 'ls-tree', '-r', '--name-only', 'release-evidence').split()
    assert sorted(listed) == ['v9.9.10/tasks-phone-after.png', 'v9.9.9/updates-desktop-1440-after.png']
    assert git(bare, 'rev-parse', 'release-evidence') == commit_two
    assert git(bare, 'rev-parse', 'release-evidence^') == commit_one
    assert git(bare, 'rev-list', '--parents', '-n', '1', commit_one).split() == [commit_one]
    assert git(bare, 'rev-parse', 'refs/heads/main') == main
    assert git(bare, 'log', '-1', '--format=%an <%ae>', 'release-evidence') == (
        'Tico release shots <release-shots@users.noreply.github.com>'
    )


def test_the_workflow_takes_base_head_and_version_and_asks_for_contents_write_only() -> None:
    workflow = yaml.safe_load((ROOT / '.github' / 'workflows' / 'release-shots.yml').read_text())
    triggers = workflow.get('on', workflow.get(True))
    call = triggers['workflow_call']
    assert set(call['inputs']) == {'base', 'head', 'version'}
    # The caller downloads the pictures and the markdown by the artifact's name, not by a path on the runner.
    assert set(call['outputs']) == {'artifact'}
    assert call['outputs']['artifact']['value'] == '${{ jobs.shots.outputs.artifact }}'
    assert workflow['permissions'] == {}
    job = workflow['jobs']['shots']
    assert '4cpu-linux-x64' in job['runs-on']
    assert 'spot=false' not in job['runs-on']
    assert job['permissions'] == {'contents': 'write'}
    steps = job['steps']
    for step in steps:
        if 'uses' in step:
            assert re.fullmatch(r'[\w.-]+/[\w.-]+@[0-9a-f]{40}', step['uses']), step['uses']
    runs = '\n'.join(step.get('run', '') for step in steps)
    assert 'scripts/release_shots.py' in runs and '--upload' in runs
    assert 'playwright install' in runs and 'chromium' in runs
    assert any(step.get('with', {}).get('python-version') == '3.15' for step in steps if 'with' in step)


def test_the_markdown_is_written_only_once_its_pictures_are_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Everything but the markdown and the push is stubbed: no browser runs and no branch is touched.
    @contextmanager
    def fake_worktree(sha: str, where: Path) -> Iterator[Path]:
        where.mkdir()
        yield where

    picture = shots.Shot(
        page='tasks',
        width='phone',
        theme='light',
        before='tasks-phone-before.png',
        after='tasks-phone-after.png',
        changed_pixels_pct=12.0,
    )

    def fake_shoot(
        base_sha: str,
        base_tree: Path,
        head_tree: Path,
        selection: shots.Selection,
        widths: list[str],
        python: str,
        work: Path,
        out: Path,
    ) -> tuple[list[shots.Shot], shots.Problems]:
        (out / 'tasks-phone-before.png').write_bytes(b'before')
        (out / 'tasks-phone-after.png').write_bytes(b'after')
        return [picture], shots.Problems(before=[], after=[])

    selection = shots.Selection(pages=['tasks'], every=False, dark=False, reason='pages tasks', unshown=[])
    monkeypatch.setattr(shots, 'resolve', lambda ref: ref)
    monkeypatch.setattr(shots, 'worktree', fake_worktree)
    monkeypatch.setattr(shots, 'select_pages', lambda base_sha, head_sha: selection)
    monkeypatch.setattr(shots, 'shoot', fake_shoot)
    monkeypatch.setattr(shots, 'render_markdown', lambda manifest, repo: '## What it looks like\n\nthe pictures\n')
    out = tmp_path / 'out'
    pushed: list[tuple[list[str], str]] = []

    def refused(files: list[Path], version: str) -> str:
        pushed.append((sorted(file.name for file in files), version))
        raise RuntimeError('push refused')

    monkeypatch.setattr(shots, 'upload_evidence', refused)
    with pytest.raises(RuntimeError, match='push refused'):
        shots.run('v0.5.204', 'main', 'v9.9.9', out, ['phone'], 'python3', True, 'ticoteam/tico')
    assert pushed == [(['tasks-phone-after.png', 'tasks-phone-before.png'], 'v9.9.9')]
    # A failed push leaves the manifest and the pictures, but no markdown whose links would be dead.
    assert (out / 'manifest.json').exists() and not (out / 'release-shots.md').exists()

    monkeypatch.setattr(shots, 'upload_evidence', lambda files, version: 'pushed')
    assert shots.run('v0.5.204', 'main', 'v9.9.9', out, ['phone'], 'python3', True, 'ticoteam/tico') == 0
    assert (out / 'release-shots.md').read_text() == '## What it looks like\n\nthe pictures\n'
