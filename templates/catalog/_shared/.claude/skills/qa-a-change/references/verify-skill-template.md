# Verify skill template

A verify skill is a small skill that lives in the product's own repository and tells any bot how to launch the app,
check it is the right build, drive it like a customer, collect evidence and clean up. One per app. It is written
once, proven once, and then kept current by whoever does QA.

## Where it lives

```
<product repo>/.claude/skills/verify-<app>/      (Claude Code)
<product repo>/.agents/skills/verify-<app>/      (Codex)
    SKILL.md
    features/
        README.md          one line per feature file
        <feature>.md       one file per feature, start with the top 3 to 5
```

Use the folder for the bot runtime the team uses. If the team uses both, keep the skill in one and say in the repo's
agent instruction file where it is.

## SKILL.md sections

Fill every section with real commands and real names from this repository. No placeholders left behind.

### Launch

- The exact command to build and start the app from a clean checkout, and the folder to run it from.
- What "ready" means: the app is usable starting from an empty data folder or a fresh test account, not just "the
  process started". Give the line or the response that shows it.
- How to start it against a given commit or preview build.
- How to stop it.

### Doctor

One read-only check that answers four questions and changes nothing:

1. Is the process up?
2. Is it the build I think it is (version or commit shown by the app)?
3. Is the port or address ours, not something else already running?
4. Is the test sign-in valid?

Give the command and what a healthy answer looks like. Doctor runs before every QA session.

### Drive

How a bot operates the app the way a customer does.

- The tool used (browser automation, the public API, the command line) and how to start it.
- Stable handles for the things to press and read: labels, roles, test ids, command names. Prefer handles that
  survive a redesign, such as accessible labels.
- How to sign in as each test role.

### Evidence

What counts as proof for this app.

- Follow the real customer path. Never call internal setters to reach a state.
- Capture both the action and the state it produced.
- Check side effects where they land: the stored record, the sent message, the file written.
- Fakes are allowed only at the outer boundary (another company's service). Name each one that is in place.
- If a dry-run mode exists, say exactly what it skips, so nobody mistakes a dry run for the real thing.
- Where screenshots and outputs are saved, and at which sizes (default 390 px and 1280 px wide).

### Cleanup

- Stop only what this session started.
- Remove test data the session created.
- Evidence files stay.

### Helpers

Short commands worth reusing: seed a test account, reset the data folder, tail the log.

## Feature files

`features/README.md` lists each feature file in one line. Each `features/<feature>.md` has these headings:

- **Sub-features**: the parts of the feature a customer can tell apart.
- **How to get to it**: from the customer's point of view, starting at sign-in.
- **Driving it**: the handles and commands for this feature, using the tool named in Drive.
- **Gotchas**: what tripped a past session (a slow first load, a dialog that appears once, data that must exist).

Start with the three to five flows the team says must never break. Add a file whenever QA covers a new feature.

## Prove it once

Before relying on a new verify skill, run it end to end one time and keep the output:

1. Launch from a clean state.
2. Doctor is healthy.
3. Drive one feature through its happy path.
4. Evidence is captured and shows the result.
5. Cleanup runs.
6. The evidence files are still there after cleanup.

## Keeping it current

Run Doctor at the start of every QA session. When a step in the skill no longer works, decide which of these it is:

- **The map is out of date.** The app changed on purpose (a renamed button, a new step). Fix the verify skill. Edit
  only the skill's own folder.
- **The product regressed.** The app is wrong. That is a bug: file it. Do not edit the skill to route around it.

Never weaken a journey to make it pass. Mark a feature "cannot be reached" only with the concrete prerequisite that
is missing (for example "needs a paid test account; none exists"). After each update, record the outcome in the QA
report as one of: clean (nothing needed changing), changed (what and why), blocked (what is missing).
