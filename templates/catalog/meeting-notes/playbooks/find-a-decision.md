# Find a decision

Triggered by a human asking "what did we decide about X?", "what is still open from the pricing
call?" or "what did Dana commit to?". Budget 10 minutes. The outcome is a short answer with citations,
or an honest "not found".

Inputs: the question, `knowledge/decision-log.md`, and the meetings from the last 90 days.

Done when: the first line answers the question, every decision carries its meeting, date and timestamp, the log is corrected if it was wrong, and the task is closed with that answer.

---

## 1. Look in your own log first

    grep -i "<topic>" knowledge/decision-log.md

Then search meetings from the last 90 days:

    hub meeting search "<topic>" --since <90 days ago>

## 2. Read the passage, not the summary

Open the transcript at the hit (`hub meeting read <id>`) and read the passage around it. A
decision counts only if it was confirmed. If the discussion never concluded, say "discussed on <date>,
not decided".

## 3. Answer

First line: the decision, or "no decision found". Then who confirmed it and when, with meeting title,
date and timestamp. If two meetings disagree, give both with dates and say which is later; do not
choose. If a human is asking about an open action item, check `hub task list --owner <person>` and
say whether it exists and its status.

## 4. Keep the log honest

If the answer was not in `knowledge/decision-log.md`, add it with its source. If it was wrong, fix the
line and say so on the task. Private meetings are invisible to you; if the answer may live in one, say
so.
