---
name: test-a-code-sample
description: Run a code sample or tutorial step from a clean folder against the stated SDK version, compare the output exactly, and record what was run, so no untested code is proposed.
---
# Test a code sample

When to use: before you propose a sample, put a code block in a public reply, or publish a tutorial step.

Steps:
1. Pin the environment: the SDK version, the language runtime version and the operating system. Write them in the first
   line of the run record.
2. Create a clean folder with a fresh virtual environment or the equivalent. Do not reuse a folder that has an older SDK
   installed.
3. Copy the code from the file that will ship, not from memory or a chat. The code shown to a developer and the code that
   runs must come from one source.
4. Set the key from an environment variable. Use a sandbox key the team gave you on the computer, or a placeholder. Never
   a real key, and never a key written into a file.
5. Run the sample exactly as the tutorial says. Where the output is fixed, compare it with the expected output in the
   tutorial, character for character. Where it varies (timestamps, ids), name the parts that vary.
6. Run the error path the friction log names, and confirm the error text matches what the tutorial says.
7. Record the run: version, runtime, date, the output, and "run" or "not run" with the reason. A step that needs a live
   credential, a paid account or a network call you cannot make is marked "not run". It is never assumed to work.
8. Mark any step that cannot run in CI with a skip and its reason. Do not leave it out silently.
9. Run it again after any change to the code or the SDK version, and before each new release of the tutorial.

Checks before you finish:
- The record names the SDK version, the runtime and the date.
- The output the tutorial shows matches the run output, except the parts you named as variable.
- No key, token or private file path appears in the code or the record.
- Every dependency is pinned, or named with its version.
- Steps that were not run are marked, not omitted.

Example (invented, for illustration): A webhook sample runs in a new virtual environment with SDK 3.4.1 on Python 3.12 on
2026-10-01. The verify step prints "signature ok", as the tutorial shows. The error path with a wrong secret prints
"signature mismatch", as the tutorial says it will. The live-send step needs a sandbox key that was not on the computer,
so the record reads "not run: no sandbox key on the computer."
