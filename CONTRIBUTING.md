# Contributing to Tico

Thanks for helping. Tico is released under the [PolyForm Perimeter License 1.0.1](LICENSE).
By submitting a contribution, you license that contribution under both PolyForm Perimeter 1.0.1
and [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). This lets the maintainers
include your contribution in the current Tico or in a future Apache 2.0 release. It does not
license Tico as a whole under Apache 2.0.

There is no separate contributor license agreement (CLA): you sign off each commit instead
(see below). Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Open an issue on [ticoteam/tico](https://github.com/ticoteam/tico/issues) that states the
  current behavior, the wanted outcome and how you will check it. Small fixes can go straight
  to a pull request.
- Report security problems privately, as described in `SECURITY.md`.

## Running the tests

```bash
pip install -r backend/requirements-dev.txt
python -m pytest -q                            # the default Python suite, in parallel (about a minute)

npm ci
npx playwright install chromium
npm run test:ui                                # the core browser scripts (CORE in scripts/ui-tests.cjs)
```

Run both locally before you open a pull request: GitHub Actions does not run tests. The default run leaves out
tests marked `@pytest.mark.slow` (real git, Docker, servers, long timers) and the browser scripts outside the core
list; `python -m pytest -q -m slow` and `node scripts/ui-tests.cjs --all` run them, and the release check does. Run
them too when you change what they cover. To run less while you work: `python -m pytest -q backend/tests/test_x.py`
for one file, `node scripts/ui-tests.cjs <name>` for one browser script. If you touch `app/`, also run `cargo check`
there.

## Developer Certificate of Origin

Every commit in a pull request must be signed off, which says you wrote it or have the right to
submit it under the contribution licenses above, as set out in the
[Developer Certificate of Origin](https://developercertificate.org/). Add the sign-off with `-s`:

```bash
git commit -s -m "Fix the thing"
```

That appends `Signed-off-by: Your Name <you@example.com>`, using your git `user.name` and
`user.email`, which must be your real name and an address you use. To add it to commits you
have already made: `git rebase --signoff main` (then `git push --force-with-lease`). A pull request
with unsigned commits will be asked to sign them before it is merged.

## Pull requests

- Make the smallest change that fixes the problem, against `main`.
- Few, high-value tests. Write tests while you build if they help, then keep only the ones that guard a
  security or privacy boundary, data safety (migrations, backup, restore) or a core contract (the
  updater and release path, job claim and lease, task writes, the API schema), plus at most one happy
  path per feature. No tests of wording or docs prose, and one or two cases instead of an exhaustive
  parametrization. Delete the rest before you open the pull request; the default Python suite has to run in about
  a minute, so a new test that would slow it means cutting another, or marking it `@pytest.mark.slow` if it needs
  real git, Docker or a server.
- Added or changed an icon in `ui/`? Run `python3 scripts/build-icon-font.py` to rebuild the icon font subset (a test fails until you do).
- Keep the tests green, and do not add a dependency, a network call in the UI or a build step
  without saying why.
- Comments explain why, in the present tense. They do not name who asked or when.
- Keep real team names, human names, domains and credentials out of the repository. Examples use
  the fictional team Acme (`acme.example`).
- Describe the behavior change and how you checked it in the pull request.

## Releases

A release is a version tag. Maintainers move the `[Unreleased]` notes in [CHANGELOG.md](CHANGELOG.md)
under the new version, run the full suite locally, then push a tag `vX.Y.Z` on `main`. The tag builds
the Docker images and publishes the GitHub release, with that CHANGELOG section as its notes, and
running installs then offer the update. Contributors do not tag; add your change to `[Unreleased]` in
the CHANGELOG instead. The steps are in [docs/releasing.md](docs/releasing.md).
