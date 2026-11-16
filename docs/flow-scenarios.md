# Flow scenarios

How we check that Flow's setup questions work, area by area. Flow is the full-screen walk where a person answers
what is waiting on them (`ui/app/decide.ts`). Setup questions fill in a page the person can already save; Strategy is
the first area (`backend/flow_questions.py`, `ui/app/decide-setup.ts`, [Strategy › Flow](strategy.md#flow)). Every
fixture uses invented companies and people (Acme, acme.example, Globex, Ana, Ben, Cara, Dana Lee).

## The rules every area keeps

The owners' round 2 replies set these rules.

- **Ask or question.** An ask has someone waiting on it. It counts in Needs you and lights the Flow tab. A question has
  nobody waiting. It never counts, and it goes away once the page holds the answer.
- **Order.** Asks always come first, then questions one area at a time, each area ending on its own Publish card, then
  All done. Opening Flow from an area's page puts that area first. A stage's or a pipeline's walk never shows a
  question.
- **Who.** Only someone who can already save the answer is asked: for Strategy, the owner themselves, never an admin,
  a member, a bot or the owner's Assistant.
- **Saving.** An answer saves through the routes the area's page already uses. Strategy saves to the draft, and nobody
  else's reads change before Publish.
- **The card.** One sentence ending in "?", two lines at most at 390 px, no ids. The suggestion quotes stored data and
  comes first. Everything shows on the first screen with the keyboard closed, every control is at least 44x44, and the
  main button sits far right.
- **Quality.** A question passes the eight checks in `evals/flow/rubric.json` (one decision, plain sentence, no jargon
  or ids, real choices, recommended first, not already answered, says what it changes, right person with no detour).
  Any no fails it.

The checks test properties, not words. Each part is getting its own Flow wording, so no test pins a question's text.

## The layers

| Layer | File | What it covers | Model |
|---|---|---|---|
| Scenarios | `backend/tests/test_flow_scenarios.py`, data in `backend/tests/helpers/flow_scenarios.py` | Each registered area's states. It checks what is asked, in what order, and where each suggestion comes from. On every state: the card's properties and the rubric's mechanical checks, a suggestion quotes stored data, a confirmed part is never asked, nobody but the owner is asked or may split an answer, answering every question through the page's routes ends publishable with nothing left, and nothing others read changes (nor the Needs you count) before Publish | none |
| Pending states | same files | States whose trigger Flow lacks (Strategy: three drafted bets as choices, a stale bet, a bet proved wrong, a new competitor). The expected question and choices are written down as a strict xfail | none |
| Pending areas | `backend/tests/test_flow_pending_areas.py`, data in `backend/tests/helpers/flow_pending.py` | Growth, Goals, Company, Rules, Finance, Contacts, Onboarding and bot conversations, each as its owner's state table: who is asked, the expected question, the suggestion's source, where the answer saves, and the gap. It checks the expected questions against the card and the rubric. Proposed words that break a rule are a strict xfail with the reason | none |
| Ramble | `backend/tests/test_flow_ramble_cases.py`, cases in `evals/flow/ramble_cases.json` | A long answer split into lines with a fake model. A careful split meets each case. Whatever a model answers (obeying an instruction, rewriting, translating, flooding, overrunning limits, junk, failing), the route keeps only known sections, never the summary or vision, mostly the owner's own words, within limits, or the whole text | fake |
| Quality | `backend/tests/test_flow_question_quality.py`, `evals/flow/quality.py`, `evals/flow/question_examples.json` | The rubric's mechanical checks on 32 invented good and bad questions, each with the verdict code must give. Also checks that the judge's questions validate and that the live evals' dry runs pass | none |
| Browser | `ui/tests/flow-scenarios.cts` | Run states at 390x844 against fake routes: no asks; asks waiting (closed early too); a skipped question asked again from the Strategy page before a waiting ask, with the end screen blocked and then published; a stage walk; everything done. It runs the UI and design owners' phone checks on every card. A check waiting on an unmerged change is in `WAITS` and must still fail | none |
| Live evals | `evals/flow/run.py` | The ramble cases against a real writer, and the rubric's judgement checks against the judge model (Jev) | real, on demand |

## Running them

```sh
.venv/bin/python -m pytest -q -n 2 backend/tests/test_flow_scenarios.py backend/tests/test_flow_pending_areas.py \
  backend/tests/test_flow_ramble_cases.py backend/tests/test_flow_question_quality.py
node scripts/ui-tests.cts -j 1 flow-scenarios
```

The live evals make real model calls. Never run them in CI. Run them on demand only, with at most two calls at once.
`--dry-run` sends nothing and checks the harness.

```sh
.venv/bin/python -m evals.flow.run ramble --dry-run          # each case's careful split, no model
.venv/bin/python -m evals.flow.run judge --dry-run           # a right judge passes, a yes-to-everything judge fails
FLOW_EVAL_PROVIDER=anthropic ANTHROPIC_API_KEY=... FLOW_EVAL_MODEL=... .venv/bin/python -m evals.flow.run ramble -j 2
TICO_GATEWAY_KEY=... .venv/bin/python -m evals.flow.run ramble   # the Tico AI Gateway, the server's writer model
TICO_GATEWAY_KEY=... .venv/bin/python -m evals.flow.run judge    # the examples; --flow for Flow's own questions,
                                                                 # --file x.json for any {question, choices, context}
```

`--only id,id` picks cases or questions, and `--out rows.json` keeps each row. The exit code is 0 at the bar, 1 below
it, and 2 when no model is configured (then nothing is sent). Ramble passes with no hard failure, no failed call and at
least 80% of cases passed. Judge passes with at least 85% agreement on the judged checks and every bad example caught.

## Adding an area (Growth next)

1. Build the area's questions read and its saves. The tests never invent a backend.
2. In `backend/tests/helpers/flow_scenarios.py`, register an `Area`. It needs `prepare` (the viewers who must get
   nothing), `build` (a state, as its owner makes it), `read`, `answer` (the way the page saves, through the area's own
   routes), `source`, `stored`, `confirmed`, `untouched` (what Flow never writes), `done`, `publish` (a no-op for an area
   that saves at once), `seen` (everything the others read) and `wording` (keys whose words break the card today, with
   why). Add it to `AREAS`.
3. Turn the area's states in `backend/tests/helpers/flow_pending.py` into `Scenario`s, and delete its `PendingArea`.
   Every invariant then runs on every state with no new test code. A state whose trigger is still missing keeps a
   `pending` reason and stays a strict xfail.
4. Add the area's run to `ui/tests/flow-scenarios.cts`: its questions after Strategy's Publish card, and first when
   Flow is opened from its page.
5. Add good and bad examples of its questions to `evals/flow/question_examples.json` when they teach the rubric
   something new.
