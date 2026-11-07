# pyright: strict
"""Before and after screenshots of the pages a release changed, so the release's owner can approve it by looking.

    python3 scripts/release_shots.py --base v0.5.204 --head origin/main --version v0.5.205 --out DIR [--upload]

The base and the head are each checked out as a git worktree, and each runs the demo (python -m backend.demo) on its
own port, one after the other. This checkout's harness, scripts/screenshots/index.js, shoots the pages the diff's ui/ files
draw (its PAGES table says which), at desktop 1440 px and phone 390 px, light, and dark too when a dark-theme file
changed. Pairs are compared in Chromium (scripts/screenshots/compare.js). A pair under 0.1% changed pixels is unchanged
and is listed on one line; a page the head adds has no before.

DIR gets <page>-<width>-before.png and <page>-<width>-after.png (dark: <page>-<width>-dark-before.png), manifest.json,
and release-shots.md, the "What it looks like" section, whose images are release-evidence URLs. --upload pushes the
changed pictures to the release-evidence branch under <version>/ (upload_evidence); nothing else is pushed.

Exit codes: 0 clean, 1 the after capture had problems (the files are still written, and the markdown says what
failed), 2 the run could not finish.
"""

import argparse
from collections.abc import Generator, Mapping, Sequence
import contextlib
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import cast
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path('scripts/screenshots/index.js')
COMPARE = Path('scripts/screenshots/compare.js')
REMOTE = 'origin'
EVIDENCE_BRANCH = 'release-evidence'
DEFAULT_REPO = 'ticoteam/tico'
WIDTHS = ('desktop-1440', 'phone')
WIDTH_LABELS = {'desktop-1440': 'Desktop, 1440 px', 'phone': 'Phone, 390 px'}
THEMES = ('light', 'dark')
UNCHANGED_BELOW_PCT = 0.1
# The demo's clock and the browser's: one fixed instant, so two runs of one ref draw the same pictures.
DEMO_NOW = '2026-11-06T18:00:00Z'
UPLOAD_ATTEMPTS = 5
DEMO_START_SECONDS = 240
PAGE_NAME = re.compile(r"\{name: '([^']+)'")
VERSION_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
BOT_IDENTITY = {
    'GIT_AUTHOR_NAME': 'Tico release shots',
    'GIT_AUTHOR_EMAIL': 'release-shots@users.noreply.github.com',
    'GIT_COMMITTER_NAME': 'Tico release shots',
    'GIT_COMMITTER_EMAIL': 'release-shots@users.noreply.github.com',
}


@dataclass(frozen=True)
class Selection:
    """The pages the diff's ui/ files draw, from this checkout's PAGES table (scripts/screenshots/select.js)."""

    pages: list[str]
    every: bool
    dark: bool
    reason: str
    unshown: list[str]


@dataclass(frozen=True)
class Shot:
    """One page at one width and theme. before is None for a page the head adds."""

    page: str
    width: str
    theme: str
    before: str | None
    after: str
    changed_pixels_pct: float | None

    @property
    def new_page(self) -> bool:
        return self.before is None

    @property
    def unchanged(self) -> bool:
        return self.changed_pixels_pct is not None and self.changed_pixels_pct < UNCHANGED_BELOW_PCT


@dataclass(frozen=True)
class Problems:
    before: list[str]
    after: list[str]


@dataclass(frozen=True)
class Manifest:
    version: str
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    selection: Selection
    shots: list[Shot]
    problems: Problems


def as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f'expected a JSON object, got {value!r}')
    return cast(dict[str, object], value)


def as_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in cast(list[object], value)]


def as_float(value: object) -> float:
    if not isinstance(value, int | float):
        raise RuntimeError(f'expected a number, got {value!r}')
    return float(value)


def tail(path: Path, lines: int = 40) -> str:
    try:
        return '\n'.join(path.read_text(errors='replace').splitlines()[-lines:])
    except OSError:
        return '(no log)'


def git(args: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> str:
    result = subprocess.run(['git', *args], cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f'git {args[0]} failed: {result.stderr.strip()}')
    return result.stdout


def demo_now() -> str:
    return os.environ.get('TICO_DEMO_NOW', DEMO_NOW)


def node_env() -> dict[str, str]:
    """Playwright's own Chromium unless TICO_BROWSER_CHANNEL says otherwise, as CI runs it."""
    return {
        **os.environ,
        'TICO_BROWSER_CHANNEL': os.environ.get('TICO_BROWSER_CHANNEL', ''),
        'TICO_DEMO_NOW': demo_now(),
    }


def resolve(ref: str) -> str:
    return git(['rev-parse', '--verify', f'{ref}^{{commit}}'], ROOT).strip()


@contextlib.contextmanager
def worktree(sha: str, where: Path) -> Generator[Path]:
    """sha checked out detached at where. The checkout's node_modules is linked in: the demo compiles the UI with it."""
    git(['worktree', 'add', '--detach', str(where), sha], ROOT)
    try:
        modules = ROOT / 'node_modules'
        if modules.is_dir():
            (where / 'node_modules').symlink_to(modules, target_is_directory=True)
        yield where
    finally:
        link = where / 'node_modules'
        if link.is_symlink():
            link.unlink()
        with contextlib.suppress(RuntimeError):
            git(['worktree', 'remove', '--force', str(where)], ROOT)


def select_pages(base_sha: str, head_sha: str, repo: Path = ROOT) -> Selection:
    """The harness in repo (this checkout's, by default) names the pages the diff's ui/ files draw. The trees only run
    their demos, so their own harness is not needed."""
    result = subprocess.run(
        ['node', str(repo / HARNESS), '--pages-for-diff', f'{base_sha}..{head_sha}'],
        cwd=repo,
        env=node_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f'the page selection failed:\n{result.stderr.strip()}')
    data = as_dict(json.loads(result.stdout))
    return Selection(
        pages=as_strings(data.get('pages')),
        every=bool(data.get('all')),
        dark=bool(data.get('dark')),
        reason=str(data.get('reason', '')),
        unshown=as_strings(data.get('unshown')),
    )


def page_names(sha: str) -> set[str]:
    """The PAGES names at sha. A page the base does not have is new in the head and has no before."""
    try:
        text = git(['show', f'{sha}:{HARNESS}'], ROOT)
    except RuntimeError:
        return set()
    return {match.group(1) for match in PAGE_NAME.finditer(text)}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return int(probe.getsockname()[1])


def wait_for_demo(port: int, child: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + DEMO_START_SECONDS
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f'the demo exited with {child.returncode}:\n{tail(log)}')
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2) as reply:
                if reply.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f'the demo did not answer within {DEMO_START_SECONDS} s:\n{tail(log)}')


@contextlib.contextmanager
def demo_server(tree: Path, python: str, data: Path, log: Path) -> Generator[str]:
    """python -m backend.demo in tree, on a free port, for the length of the block. Its output goes to log."""
    port = free_port()
    with log.open('w') as sink:
        child = subprocess.Popen(
            [python, '-W', 'ignore', '-m', 'backend.demo', '--port', str(port), '--data-dir', str(data)],
            cwd=tree,
            env={**os.environ, 'TICO_DEMO_NOW': demo_now()},
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_demo(port, child, log)
            yield f'http://127.0.0.1:{port}'
        finally:
            child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def capture(
    tree: Path,
    side: str,
    pages: Sequence[str],
    themes: Sequence[str],
    widths: Sequence[str],
    python: str,
    work: Path,
) -> tuple[Path, list[str]]:
    """Shoot pages against the demo running in tree, with this checkout's harness. Returns the folder and its problems."""
    shots = work / side
    shots.mkdir()
    report = work / f'{side}-report.json'
    capture_log = work / f'{side}-capture.log'
    data = Path(tempfile.mkdtemp(prefix=f'demo-{side}-', dir=work))
    with demo_server(tree, python, data, work / f'{side}-demo.log') as url:
        with capture_log.open('w') as sink:
            status = subprocess.run(
                [
                    'node',
                    str(ROOT / HARNESS),
                    '--url',
                    url,
                    '--out',
                    str(shots),
                    '--only',
                    ','.join(pages),
                    '--widths',
                    ','.join(widths),
                    '--themes',
                    ','.join(themes),
                    '--report',
                    str(report),
                ],
                cwd=ROOT,
                env=node_env(),
                stdout=sink,
                stderr=subprocess.STDOUT,
                check=False,
            ).returncode
    if not report.is_file():
        raise RuntimeError(f'the {side} capture did not finish (exit {status}):\n{tail(capture_log)}')
    problems = as_strings(as_dict(json.loads(report.read_text())).get('problems'))
    return shots, problems


def changed_pixels(pairs: Sequence[tuple[str, Path, Path]], work: Path) -> dict[str, float]:
    """Changed pixels, in percent, for each (key, before, after): one browser for all the pairs."""
    if not pairs:
        return {}
    pairs_file = work / 'pairs.json'
    results_file = work / 'compare.json'
    compare_log = work / 'compare.log'
    pairs_file.write_text(
        json.dumps([{'key': key, 'before': str(before), 'after': str(after)} for key, before, after in pairs])
    )
    with compare_log.open('w') as sink:
        status = subprocess.run(
            ['node', str(ROOT / COMPARE), str(pairs_file), str(results_file)],
            cwd=ROOT,
            env=node_env(),
            stdout=sink,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode
    if status != 0 or not results_file.is_file():
        raise RuntimeError(f'the pixel comparison failed (exit {status}):\n{tail(compare_log)}')
    rows: object = json.loads(results_file.read_text())
    percent: dict[str, float] = {}
    for row in cast(list[object], rows):
        item = as_dict(row)
        percent[str(item['key'])] = round(as_float(item['pct']), 3)
    return percent


def shot_file(page: str, width: str, theme: str, side: str) -> str:
    """The name of one picture in the output folder: light has no theme part, dark has '-dark'."""
    theme_part = '' if theme == 'light' else f'-{theme}'
    return f'{page}-{width}{theme_part}-{side}.png'


def copy_picture(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise RuntimeError(f'the capture left no picture {src.name}')
    shutil.copyfile(src, dst)


def shoot(
    base_sha: str,
    base_tree: Path,
    head_tree: Path,
    selection: Selection,
    widths: Sequence[str],
    python: str,
    work: Path,
    out: Path,
) -> tuple[list[Shot], Problems]:
    pages = selection.pages
    if not pages:
        return [], Problems(before=[], after=[])
    themes = list(THEMES) if selection.dark else ['light']
    known = page_names(base_sha)
    old = [page for page in pages if page in known]
    before_dir: Path | None = None
    before_problems: list[str] = []
    if old:
        before_dir, before_problems = capture(base_tree, 'before', old, themes, widths, python, work)
    after_dir, after_problems = capture(head_tree, 'after', pages, themes, widths, python, work)

    planned: list[tuple[str, str, str, str, str | None, str]] = []
    pairs: list[tuple[str, Path, Path]] = []
    for page in pages:
        for theme in themes:
            for width in widths:
                label = f'{page}-{width}-{theme}'
                after_name = shot_file(page, width, theme, 'after')
                copy_picture(after_dir / f'{label}.png', out / after_name)
                before_name: str | None = None
                if before_dir is not None and page in old:
                    before_name = shot_file(page, width, theme, 'before')
                    copy_picture(before_dir / f'{label}.png', out / before_name)
                    pairs.append((label, out / before_name, out / after_name))
                planned.append((label, page, width, theme, before_name, after_name))

    percent = changed_pixels(pairs, work)
    shots = [
        Shot(page, width, theme, before_name, after_name, percent.get(label) if before_name else None)
        for label, page, width, theme, before_name, after_name in planned
    ]
    return shots, Problems(before=before_problems, after=after_problems)


def evidence_url(repo: str, version: str, name: str) -> str:
    return f'https://github.com/{repo}/blob/{EVIDENCE_BRANCH}/{version}/{name}?raw=true'


def evidence_names(shots: Sequence[Shot]) -> list[str]:
    """The pictures the markdown shows: the changed pairs, and the after of each page the head adds."""
    names: list[str] = []
    for shot in shots:
        if shot.unchanged:
            continue
        if shot.before is not None:
            names.append(shot.before)
        names.append(shot.after)
    return names


def percent_text(value: float | None) -> str:
    return '-' if value is None else f'{value:.2f}%'


def title(page: str) -> str:
    return page.replace('-', ' ').capitalize()


def cell_text(text: str) -> str:
    return text.replace('|', '\\|').replace('\n', ' ')


def render_markdown(manifest: Manifest, repo: str) -> str:
    """The "What it looks like" section: per page a light table at desktop and at phone (dark after, when shot)."""
    selection = manifest.selection
    lines = ['## What it looks like', '']
    lines.append(
        f'Before is `{manifest.base_ref}` ({manifest.base_sha[:9]}) and after is `{manifest.head_ref}` '
        f'({manifest.head_sha[:9]}).'
    )
    if not selection.pages:
        lines += [
            '',
            f'No page draws a file this release changed ({selection.reason}), so there is nothing to compare.',
        ]
    else:
        depth = 'light and dark' if selection.dark else 'light'
        lines += ['', f'Pictures are {depth}, at desktop 1440 px and phone 390 px. Pages: {selection.reason}.']
        unchanged: list[str] = []
        for page in selection.pages:
            page_shots = [shot for shot in manifest.shots if shot.page == page]
            unchanged += [
                f'`{shot.page}` {shot.width} {shot.theme} {percent_text(shot.changed_pixels_pct)}'
                for shot in page_shots
                if shot.unchanged
            ]
            changed = [shot for shot in page_shots if not shot.unchanged]
            if not changed:
                continue
            lines += ['', f'### {title(page)}']
            for shot in sorted(changed, key=lambda item: (THEMES.index(item.theme), WIDTHS.index(item.width))):
                caption = WIDTH_LABELS[shot.width]
                if shot.theme != 'light':
                    caption = f'Dark theme, {caption[0].lower()}{caption[1:]}'
                before_cell = 'new page'
                if shot.before is not None:
                    before_url = evidence_url(repo, manifest.version, shot.before)
                    before_cell = f'![{title(page)} before, {caption}]({before_url})'
                after_url = evidence_url(repo, manifest.version, shot.after)
                after_cell = f'![{title(page)} after, {caption}]({after_url})'
                lines += ['', caption, '', '| Before | After |', '| --- | --- |', f'| {before_cell} | {after_cell} |']
        if unchanged:
            lines += ['', f'Unchanged (under {UNCHANGED_BELOW_PCT}% of pixels changed): {"; ".join(unchanged)}.']
    if selection.unshown:
        shown = ', '.join(f'`{path}`' for path in selection.unshown)
        lines += ['', f'Changed, but no picture shows them: {shown}.']
    problems = [f'before: {text}' for text in manifest.problems.before] + [
        f'after: {text}' for text in manifest.problems.after
    ]
    if problems:
        lines += ['', '### Capture problems', ''] + [f'- {cell_text(text)}' for text in problems]
    return '\n'.join(lines) + '\n'


def remote_tip(repo_dir: Path, remote: str) -> str | None:
    """The release-evidence branch's tip on the remote, fetched into FETCH_HEAD only; None when the branch is missing."""
    listed = git(['ls-remote', '--heads', remote, f'refs/heads/{EVIDENCE_BRANCH}'], repo_dir).split()
    if not listed:
        return None
    git(['fetch', '--no-tags', '--depth=1', remote, f'refs/heads/{EVIDENCE_BRANCH}'], repo_dir)
    return git(['rev-parse', 'FETCH_HEAD^{commit}'], repo_dir).strip()


def evidence_commit(files: Sequence[Path], version: str, parent: str | None, repo_dir: Path) -> str:
    """A commit that adds files under <version>/ on top of parent (or starts the branch), built in a private index."""
    with tempfile.TemporaryDirectory(prefix='release-evidence-') as tmp:
        env = {**os.environ, **BOT_IDENTITY, 'GIT_INDEX_FILE': str(Path(tmp) / 'index')}
        if parent is not None:
            git(['read-tree', f'{parent}^{{tree}}'], repo_dir, env)
        for path in files:
            blob = git(['hash-object', '-w', '--', str(path)], repo_dir, env).strip()
            git(['update-index', '--add', '--cacheinfo', f'100644,{blob},{version}/{path.name}'], repo_dir, env)
        tree = git(['write-tree'], repo_dir, env).strip()
        args = ['commit-tree', tree, '-m', f'Release shots for {version}']
        if parent is not None:
            args += ['-p', parent]
        return git(args, repo_dir, env).strip()


def upload_evidence(files: Sequence[Path], version: str, repo_dir: Path = ROOT, remote: str = REMOTE) -> str:
    """Push files under <version>/ on the release-evidence branch, creating the orphan branch when it is missing.

    Never force: each commit sits on the branch's current tip, and a push the remote refuses because the tip moved is
    retried on the new tip. No other branch is touched. Returns the commit pushed.
    """
    refused = ''
    for _ in range(UPLOAD_ATTEMPTS):
        commit = evidence_commit(files, version, remote_tip(repo_dir, remote), repo_dir)
        pushed = subprocess.run(
            ['git', 'push', remote, f'{commit}:refs/heads/{EVIDENCE_BRANCH}'],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if pushed.returncode == 0:
            return commit
        refused = pushed.stderr.strip()
    raise RuntimeError(f'the {EVIDENCE_BRANCH} push was refused {UPLOAD_ATTEMPTS} times: {refused}')


def every_page_selection(only: Sequence[str] = ()) -> Selection:
    """Every page in both themes, whatever changed: the same-ref check, which must come out unchanged everywhere.
    With only, just those pages, so a fix can be re-checked on the pages it touched and on a control or two."""
    pages = [match.group(1) for match in PAGE_NAME.finditer((ROOT / HARNESS).read_text())]
    unknown = [page for page in only if page not in pages]
    if unknown:
        raise RuntimeError(f'--pages: {", ".join(unknown)} is not a page in {HARNESS}')
    if only:
        pages = [page for page in pages if page in only]
    return Selection(pages=pages, every=True, dark=True, reason='every page: the same-ref check', unshown=[])


def run(
    base: str,
    head: str,
    version: str,
    out: Path,
    widths: Sequence[str],
    python: str,
    upload: bool,
    repo: str,
    every_page: bool = False,
    only: Sequence[str] = (),
) -> int:
    if not VERSION_NAME.fullmatch(version):
        raise RuntimeError(f'the version {version!r} is not a folder name')
    unknown = [width for width in widths if width not in WIDTHS]
    if unknown or not widths:
        raise RuntimeError(f'--widths: {", ".join(unknown) or "no width"} (choose from {", ".join(WIDTHS)})')
    out.mkdir(parents=True, exist_ok=True)
    base_sha = resolve(base)
    head_sha = resolve(head)
    with tempfile.TemporaryDirectory(prefix='release-shots-') as tmp:
        work = Path(tmp)
        with worktree(base_sha, work / 'base') as base_tree, worktree(head_sha, work / 'head') as head_tree:
            selection = every_page_selection(only) if every_page else select_pages(base_sha, head_sha)
            shots, problems = shoot(base_sha, base_tree, head_tree, selection, widths, python, work, out)
            manifest = Manifest(
                version=version,
                base_ref=base,
                base_sha=base_sha,
                head_ref=head,
                head_sha=head_sha,
                selection=selection,
                shots=shots,
                problems=problems,
            )
            (out / 'manifest.json').write_text(json.dumps(asdict(manifest), indent=2) + '\n')
            (out / 'release-shots.md').write_text(render_markdown(manifest, repo))

    if upload and evidence_names(shots):
        upload_evidence([out / name for name in evidence_names(shots)], version)
    changed = sum(1 for shot in shots if not shot.unchanged and not shot.new_page)
    print(
        f'release shots: {len(selection.pages)} pages, {len(shots)} pictures, {changed} changed pairs, '
        f'{sum(1 for shot in shots if shot.unchanged)} unchanged -> {out}'
    )
    if problems.before or problems.after:
        # A before problem is not fatal (a page the base lacks has no before), but it still belongs in the log.
        lines_out = [f'before: {text}' for text in problems.before] + [f'after: {text}' for text in problems.after]
        print('release shots: capture problems:\n  ' + '\n  '.join(lines_out), file=sys.stderr)
    if problems.after:
        return 1
    return 0


def split_widths(value: str) -> list[str]:
    return [width.strip() for width in value.split(',') if width.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Before and after screenshots of the pages a release changed.')
    parser.add_argument('--base', required=True, help='the release before: a tag or commit, e.g. v0.5.204')
    parser.add_argument('--head', required=True, help='the candidate: a commit or branch, e.g. origin/main')
    parser.add_argument('--version', required=True, help='the release name: its evidence folder, e.g. v0.5.205')
    parser.add_argument(
        '--out', type=Path, default=Path('release-shots'), help='the folder for pictures, manifest, markdown'
    )
    parser.add_argument('--widths', default=','.join(WIDTHS), help='comma list of desktop-1440 and phone')
    parser.add_argument(
        '--python', default=os.environ.get('TICO_PYTHON', 'python3'), help='the interpreter for the demo'
    )
    parser.add_argument(
        '--upload', action='store_true', help='push the changed pictures to the release-evidence branch'
    )
    parser.add_argument(
        '--every-page',
        action='store_true',
        help='shoot every page in both themes, whatever changed (the same-ref check: base and head equal)',
    )
    parser.add_argument(
        '--pages',
        default='',
        help='with --every-page: only these comma-separated pages, e.g. bot-chat,market',
    )
    parser.add_argument(
        '--repo',
        default=os.environ.get('GITHUB_REPOSITORY', DEFAULT_REPO),
        help='the repository the evidence URLs point at',
    )
    args = parser.parse_args(argv)
    try:
        return run(
            str(args.base),
            str(args.head),
            str(args.version),
            Path(args.out).resolve(),
            split_widths(str(args.widths)),
            str(args.python),
            bool(args.upload),
            str(args.repo),
            every_page=bool(args.every_page),
            only=split_widths(str(args.pages)),
        )
    except (RuntimeError, OSError) as error:
        print(f'release shots: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
