# Write a sample

Triggered by a task asking for an example or tutorial ("a Python example for webhooks"), or by a sample
chosen from the pulse. Budget 60 minutes. The outcome is one runnable sample or tutorial in `samples/`,
tested against the documented API, for an engineer to review and merge. Nothing is pushed or published.

---

## 1. Pin the job

    hub task show <id>

One reader, one job: "a Python developer receives and verifies a booking webhook". Note the SDK version and
the docs pages that cover it (`hub doc search`).

## 2. Write the smallest thing that works

A single file or a small folder: setup (install, the environment variable for the key, never a real key),
the code, and the expected output. Use the SDK's current public methods only. Handle the error the friction
log says people hit.

## 3. Run it

Run it against the sandbox the docs describe, if the team gave you sandbox credentials on the computer;
otherwise say "not run" in the first line and why. Record the SDK version and the date.

## 4. Write the tutorial around it

Steps a developer follows top to bottom: what you will build, prerequisites, numbered steps with the code,
how to check it worked, what to try next. Second person, present tense.

## 5. Hand over

Save to `samples/<topic>/`, attach it to the task, `hub file publish` it, commit, and `hub task update <id>
--status done --note` with the reviewer from `knowledge/channels.md`. Public publication uses your Tools and stays a draft until a person turns mail sending on in Tico.
