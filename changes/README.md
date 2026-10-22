# Changelog fragments

Pull requests never edit [CHANGELOG.md](../CHANGELOG.md). Each one adds its release notes here, as
`changes/unreleased/<branch>.md` (one file per pull request, named after its branch so two never collide):

```markdown
### Fixed
- **Short lead.** What changed for the person using Tico, wrapped at 120 characters
  with a two-space indent.
```

Use the Keep a Changelog headings `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed` and `Security`, as many as the
change needs, and write each entry exactly as it should read in the release notes. A change with nothing to tell users
(tests, CI, internal refactors) adds no file.

```sh
python scripts/changelog_fragments.py add fixed "**Short lead.** What changed."   # creates or extends your file
python scripts/changelog_fragments.py adopt   # a branch that wrote its line in CHANGELOG.md: move it here
python scripts/changelog_fragments.py check   # every fragment parses (the PR tests run this too)
```

The release commit runs `python scripts/changelog_fragments.py compile X.Y.Z`, which writes the new version's section
from these files and deletes them ([docs/releasing.md](../docs/releasing.md#tag)).
