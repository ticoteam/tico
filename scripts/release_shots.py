# pyright: strict
"""The "What it looks like" section of a release's approval issue.

    python3 scripts/release_shots.py --base v0.5.204 --head origin/main --version v0.5.205 --out DIR [--upload]

The before and after pictures came from the old web app's screenshot harness, which went with ui/. The web app has no
release screenshots yet, so DIR gets release-shots.md saying so, and the release train puts that in the approval issue
(scripts/release_train.py shots-into-approval). .github/workflows/release-shots.yml still runs this with its pictures'
arguments; they are read and left unused.
"""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys


def markdown(base: str, head: str) -> str:
    return (
        '## What it looks like\n\n'
        f'Before is `{base}` and after is `{head}`. The web app has no release screenshots yet, so there are no '
        'pictures to compare.\n'
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='The approval issue\'s "What it looks like" section.')
    parser.add_argument('--base', required=True, help='the release before: a tag or commit, e.g. v0.5.204')
    parser.add_argument('--head', required=True, help='the candidate: a commit or branch, e.g. origin/main')
    parser.add_argument('--version', required=True, help='the release name, e.g. v0.5.205')
    parser.add_argument('--out', type=Path, default=Path('release-shots'), help='the folder for the markdown')
    for unused in ('--widths', '--python', '--repo', '--pages'):
        parser.add_argument(unused, default='', help='kept for the workflow; unused')
    for unused in ('--upload', '--every-page'):
        parser.add_argument(unused, action='store_true', help='kept for the workflow; unused')
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'release-shots.md').write_text(markdown(str(args.base), str(args.head)))
    print(f'release shots: no pictures for {args.version} -> {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
