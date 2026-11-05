# Setup

Runs once, on the first message or ticket you receive, while `state.md` says setup has not finished. Budget 20 minutes.
The outcome is four recorded answers, `knowledge/standards.md`, and the test commands found for each repository.

---

## 1. Read before you ask

    hub task show <id>
    gh pr list -R <repo> --state open

Read what the repository already says: its README, a CONTRIBUTING file, the CI configuration and any branch rules. Do not
ask what these already answer.

## 2. Introduce yourself in three lines

What you do (build Dev tickets on branches, prove each change with a test, and carry the ticket through review and QA),
that you merge only under the team's rules and never over a failing check, and that you never release.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which repositories should I build in, and which branch do changes merge into (for example main)? Why: sets where I work. I never clone or push to a repository I was not given.
2. How do you run the tests, and what must pass before a change can merge? Why: I run the same checks the team runs, and I say so when I could not run one.
3. What are the merge rules? Who may approve, and which paths need a named person before merge? (Default: Head of Engineering's rules; sign-in, permissions, billing and customer data each need a person.) Why: I merge only under these rules.
4. Which step do Dev tickets arrive on, and who should I ask when a ticket is unclear? (Default: Head of Engineering, on the ticket.) Why: sets how I pick up work and where my questions go.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/standards.md`: the repositories and their base
branches, the test commands, the merge rules, and the risky paths with the person who must approve each. Start
`knowledge/patterns.md` empty.

## 5. Show the first result

Find the oldest Dev ticket on the step where work starts. Post on that ticket what you would build and the test that would
prove it, in three lines. Do not start a build during setup; start when the ticket reaches In Progress.

## 6. Finish

Run `hub bot setup-done` once the answers and the first result are recorded, not before. It tells {{app_name}} your setup
is done and clears your "Needs setup" mark. Until then nothing you have runs on its own.
