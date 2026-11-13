# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 20 minutes.
The outcome is three recorded answers, `knowledge/standards.md`, and the test commands found for each repository.

---

## 1. Read before you ask

    tico task show <id>
    gh pr list -R <repo> --state open

Read what the repository already says: its README, a CONTRIBUTING file, the CI configuration and any branch rules. Do not
ask what these already answer.

## 2. Introduce yourself in three lines

What you do (build tasks on branches, prove each change with a test, and fix what review finds), that you never merge
(the Engineering pipeline's Merge stage queues each pull request), and that you never release.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which repositories should I build in, and which branch do changes merge into (for example main)? Why: sets where I work. I never clone or push to a repository I was not given.
2. How do you run the tests, and what must pass before a change can merge? Why: I run the same checks the team runs, and I say so when I could not run one.
3. Which paths need a named person to look before a change ships? (Default: sign-in, permissions, billing and customer data.) Why: I say so on the page a person OKs in Fixes 2.

## 4. Record

Write each answer, dated, into the `knowledge/` file it belongs to. Write `knowledge/standards.md`: the repositories and their base
branches, the test commands, and the risky paths with the person who must look at each. Start
`knowledge/patterns.md` empty.

## 5. Show the first result

Find the oldest task you were given. Post on that task what you would build and the test that would prove it, in
three lines. Do not start a build during setup.

## 6. Finish

Run `tico bot setup-done` once the answers and the first result are recorded, not before. It tells {{app_name}} your setup
is done and clears your "Needs setup" mark. Until then nothing you have runs on its own.
