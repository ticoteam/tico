# Weekly developer pulse

Schedule: Thursdays at 10:00 team time (routine `weekly-developer-pulse`), after setup. Also run by hand. Budget 50
minutes. Nothing is posted.

---

## Inputs

- `knowledge/channels.md`, `knowledge/do-not-say.md`, `knowledge/friction-log.md`, and last week's pulse.
- Public questions in each channel since the last pulse. The first run after setup covers the last 14 days.
- The product's public docs, and the SDK and example repositories the owner has listed.

## Steps

1. **Read where things stand.** `hub task show <id>`, then the inputs above. Note which answers were posted and
   whether the asker replied.
2. **Sweep the channels.** For each channel in `knowledge/channels.md`, list new questions and older ones with no
   team reply. For each: link, date, the question in one line, and the language and SDK version if given.
3. **Prepare the answers.** For each open question, find the answer in the docs (`hub doc search`, `hub doc ask`) and
   in the code. Write the reply: the fix, one line of why, the doc link. Run any code using
   `skills/test-a-code-sample/SKILL.md`, and name the version. Check the reply against `knowledge/do-not-say.md`.
   A requested reply is posted only when the task asks for it, a Tool allows it and a person has turned mail sending
   on in Tico. Otherwise it stays a draft with its destination. A question you cannot answer goes to the named
   engineer as a proposed task.
4. **Count the friction.** Use `skills/tag-and-count-friction/SKILL.md`: tag each question with its integration step,
   add counts and links to `knowledge/friction-log.md`, and name the fix for each theme.
5. **Propose one sample.** The theme with the highest count that a working example would answer. One paragraph: what
   it shows, the language, the rough effort. Write it only when the requester asked for it, with
   `playbooks/write-a-sample.md`.
6. **Write and hand over.** Write the report, `hub file publish` it, commit, then close the task.

## Output

`reports/YYYY-MM-DD-developer-pulse.md`, in this order, in the shape of `knowledge/examples/developer-pulse.md`:

- **Headline:** new questions, questions waiting more than two working days, and the top friction with its count.
- **Answers ready to use:** one entry per open question, with the channel, date, the reply text, and the SDK version it
  was run against (or "not run" and why).
- **Needs an engineer:** questions the docs and the code cannot answer, each with its proposed task.
- **Friction:** the table for this period from `knowledge/friction-log.md`.
- **Proposed sample:** one line, then one paragraph.
- **Could not read:** each channel or page you could not reach, with the reason.
- **Sources:** the channels, the date range, the SDK versions, and the date the docs were read.

## Done when

- Every question in the window is listed as answered in draft, waiting for an engineer, or unreadable.
- Each code answer names an SDK version and a run date, or says "not run".
- Every friction theme has two or more linked threads.
- The report is published with `hub file publish`, and the task is closed with `hub task update <id> --status done
  --note`. The note gives the report path and the number of questions still waiting.
