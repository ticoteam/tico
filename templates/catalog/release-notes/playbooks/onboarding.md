# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, a real draft of the next release's notes on the task, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    gh release list -R <repo> --limit 5
    gh pr list -R <repo> --state merged --limit 40 --json number,title,labels,mergedAt

Check what you can already reach: the last release and its notes, the repository's CHANGELOG format, and the labels its pull
requests carry. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (draft a changelog entry and plain-language release notes from merged pull requests), that you never publish, tag or edit the CHANGELOG, and that a human decides the version.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which repositories are released, and how: on every merge, on a schedule, or by hand? What was the last release? Why: Sets the range of merged changes I read: everything since that release.
2. Where do release notes go today (a CHANGELOG file, GitHub releases, a help page, an email), and who reads them: customers, internal staff, both? Can you paste the last two? Why: I match your format and voice, and write for the readers you have. Customers get what changed for them, never a pull request title.
3. Which labels or prefixes mark a change as a feature, a fix, a breaking change or a security fix? Which changes never appear (dependency bumps, refactors)? Why: I group by your labels first. Without them I classify from the text and mark each guess.
4. How do you number versions? (Default: semantic versioning, major.minor.patch.) Why: I suggest the next number from what changed: breaking is major, a feature is minor, a fix is patch.
5. Which day should the draft land, and who reviews it? (Default: Fridays at 14:00, to you.) Why: Sets the recipient and the first routine's schedule.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/versioning.md` (how versions are numbered and what makes a breaking change) and `knowledge/voice.md` (the format and voice, with the two pasted examples).

## 5. Do the first piece of work now

Take everything merged since the last release and follow `playbooks/weekly-release-notes.md`. Write the draft in the shape of `knowledge/examples/release-notes.md` to `reports/`, attach it to the task, labelled "First draft, not yet reviewed". Publish nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will draft the release notes every Friday at 14:00, and a human publishes them." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
