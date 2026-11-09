# Write a sample

Triggered by a task asking for an example or tutorial ("a Python example for webhooks"), or by a sample chosen from
the pulse. Budget 60 minutes. The outcome is one runnable sample and its tutorial in `samples/`, tested against the
documented API, for an engineer to review and merge. Nothing is pushed or published.

---

## Inputs

- The task and the reader it names. If it names none, ask one question: who is the reader, and what job do they do?
- The SDK version and the docs pages that cover the job (`hub doc search`).
- The friction log entry the sample answers, if there is one.
- The reviewer named in `knowledge/channels.md`.

## Steps

1. **Pin the job.** `hub task show <id>`. One reader, one job: "a Python developer receives and verifies a booking
   webhook". Note the SDK version and the docs pages that cover it.
2. **Write the smallest thing that works.** One file or a small folder: install, the environment variable for the key
   (never a real key), the code, and the expected output. Use the SDK's current public methods only. Handle the error
   the friction log says people hit.
3. **Test it.** Follow `skills/test-a-code-sample/SKILL.md`. Run it against the sandbox the docs describe, if the team
   gave you sandbox credentials on the computer. Otherwise say "not run" and why, in the first line.
4. **Write the tutorial around it.** Steps a developer follows top to bottom: what you will build, prerequisites,
   numbered steps with the code, how to check it worked, what to try next. Second person, present tense.
5. **Hand over.** Save to `samples/<topic>/`, commit, publish `tutorial.md` and `run.md` on the task with
   `hub file publish <path>`, and run `hub task update <id> --status done --note` with the reviewer's name and the folder
   path. `hub file publish` takes one file at a time and refuses folders, and code files such as `.py` are not on its
   list, so the reviewer reads the code from the commit. Public publication uses your Tools and stays a draft until a
   person turns mail sending on in Tico.

## Output

A folder `samples/<topic>/` holding:

- the code files, with the expected output shown in the tutorial;
- `tutorial.md`: what you will build, prerequisites, numbered steps, how to check it worked, what to try next;
- `run.md`: one short record with the SDK version, the runtime version, the date, and "run" or "not run" with the reason.

## Done when

- The code runs from a clean folder using only the steps in the tutorial.
- The output the tutorial shows matches what the run printed.
- `run.md` names the SDK version, the runtime and the date, or says "not run" and why.
- The reviewer has the task, with the folder path in the note.
