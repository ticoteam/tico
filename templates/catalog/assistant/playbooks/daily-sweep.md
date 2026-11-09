# Morning message: one message per person a day

A routine, once a day. It is not any person's chat, so it acts as you, the Assistant. Its one write is the morning message,
one per person per company day. It is not a tidy-up: no card, no close, no decline, no answer.

1. `hub human list`, once, for the people and their ids. Then work through them one at a time.
2. For each person, run `skills/where-we-stand/SKILL.md` for that person alone (its morning-run reads). Never mix one
   person's goals into another's message.
3. Nothing worth their time: send nothing for them, and go to the next person. A quiet day gets no message.
4. Otherwise send ONE message: `hub assistant morning --for human:<id> --body "<message>"`. Under 120 words, plain words,
   to "you":
   - one line on what moved since yesterday, only if the record shows it;
   - then one to three things only they can do today, each with its goal linked and one link to act.
   No queue counts, no task lists, no offer of more, no Next line.
5. The server answers 409 `already_sent_today`, or refuses in any other way: stop for that person. Never retry, never send
   them a second message. Go to the next person.
6. A stalled Marketing task (the skill's step 5) is its bot's to move. Add at most one comment for that bot, if your last
   comment on it is more than two weeks old (`hub task show <id>`): `hub task comment <id> "<what is stalled, and what it
   needs>"`. Never ask the person to move it.

Your final reply is the run record: one line per person, "sent", "nothing to say" or "refused: <reason>". Nothing else.

The run's only writes are `hub assistant morning` and that comment. It closes, declines, answers and proposes nothing, and
sends no other message: those are the person's, in their own chat.
