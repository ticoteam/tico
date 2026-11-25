---
name: Git
category: engineering
summary: "Version control for code and documents: branches, commits, diffs and history"
commands: [git]
---
## What it is for
Keeping every change to code, scripts and documents in a repository, with who changed what and why.

## How to use it

    git switch -c <who>/<topic> origin/main --no-track
    git status --short
    git diff --stat origin/main...HEAD
    git log --oneline -20 -- path/to/file
    git commit -m "Say what changed and why"

## Good to know
- Work in your own repository or the task's worktree, never in another agent's checkout.
- Never commit a key or a `.env` file; name the variable instead.
