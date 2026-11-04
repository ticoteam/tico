# Weekly interface copy review

Schedule: Thursdays at 10:00 team time (routine `weekly-copy-review`), after setup. Budget 35 minutes. The outcome is one review with a rewrite for every string that breaks a
rule, ready for an engineer to post. Nothing is posted.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/glossary.md`, `knowledge/error-rules.md` and last week's review. Note which of last week's
rewrites were applied (read the merged diffs).

## 2. Find the changed strings

    gh pr list --state all --search "updated:>=<last week>" --limit 100
    gh pr diff <number>

Keep pull requests that touch translation files, templates or components with visible text. List any
repository you could not read.

## 3. Check each string

Against the glossary (banned synonyms, capitalisation, one name per object; `skills/audit-one-name-per-thing/SKILL.md`), the error rules (what
happened, what to do, no blame, no bare codes; `skills/rewrite-an-error-message/SKILL.md`), and the patterns: buttons that say what they do, empty
states with one action, confirmations that name the thing. Skip strings that are fine; do not restyle
working copy for taste.

## 4. Write the rewrites

For each problem: repository, pull request, file and line, the current string, the rewrite, and the rule
it follows. Mark it blocking (wrong name, misleading error, legal/pricing text changed) or a suggestion.
Legal, pricing and privacy strings go to their owner as a flag with no rewrite.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-copy-review.md` in the shape of `knowledge/examples/copy-review.md`, `hub file
publish` it, commit, and `hub task update <id> --status done --note`: how many pull requests, how many
blocking, and new terms proposed for the glossary.
