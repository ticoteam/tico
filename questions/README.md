# Decision questions

One file per decision the company makes with the decision model (optionally TypeSafe's Jev, System One)
(`skills/decisions/SKILL.md`). The decision model takes a JSON state and typed
questions (`noul` for yes/no, `choice`, `score`, the same format as OpenRouter's Decisions API) and
answers each with a calibrated probability; it writes no prose. The primitive is
one call (`hub_decision_ask`, `hub decision ask`, `clients/judge.py`) and it never changes. What changes,
and what makes a decision reusable across bots, is the question set: a named, versioned file
here that every caller loads and sends inline. The hub server knows nothing about these files;
the `label` on each call (`id@version`) is what groups them in the audit.

JSON, not YAML, so the `hub` CLI on a bare Python, a bot's own script, the mail venv and the
cloud service all read the same file with nothing installed.

`load_set` checks `TICO_REGISTRY_DIR/questions/<name>.json` before the shipped copy, so a company can keep its own
versioned questions outside the release image. Custom sets pass the same validation; an invalid override is refused.
Registry lookup accepts regular files only, beneath real registry and `questions` directories. Symlinks (including
dangling links), nonfiles and read failures produce a fixed diagnostic; they never silently select shipped questions.
Directory handles and no-follow opens keep a replaced path from escaping the registry during loading.
On platforms without those safe opens, an existing registry override is refused without reading it; absent overrides,
shipped sets and explicit-root loading continue to work.
An explicit `root` still selects that directory. When neither registry nor shipped copies exist, installed runners retain
the `TICO_QUESTIONS_DIR`, `HUB_DIR/questions` and bot checkout fallbacks.

```json
{
  "id": "mail-triage",            // the file name
  "version": 1,                   // bump when a question or a threshold changes meaning
  "owner": "ana",               // who decides changes
  "summary": "One line: what is decided, from what state, and who calls it.",
  "state": ["from", "subject"],   // the fields a caller sends; documentation, not enforced
  "questions": {
    "bucket": {"type": "choice", "instructions": "...", "criteria": {"name": "description"}},
    "urgency": {"type": "score", "instructions": "...", "criteria": ["level 0", "level 1"]},
    "is_ask": {"type": "noul", "instructions": "..."},
    "covered": {"type": "choice", "dynamic": true, "instructions": "...", "criteria": {"new": "..."}}
  },
  "thresholds": {"archive": 0.85}  // the confidences callers act at; the code reads these
}
```

- `choice` answers `{"choice", "confidence", "probabilities"}`; `criteria` is a map of option
  name to description, two to 255 options. The model never sees the question id, only the
  options and their descriptions. Give a list an escape option (`other`, `new`, `none`).
- `score` answers `{"score", "confidence", "probabilities", "legend"}`; `criteria` is two to ten
  ordered level descriptions, and `score` is the probability-weighted position on them, so it can
  be fractional.
- `noul` answers `{"noul"}`: the probability the statement is true. There is no separate
  confidence; 0.5 is a coin toss.
- `instructions`, an option's description, a level and a noul's `true`/`false` are each a
  string or JSON structure: an object when the question has parts (`{"question": ..., "focus":
  ..., "not_for": ..., "examples": [...]}`), a list when it is a list of things to check;
  `null` for an option that needs no description. Name the state's fields in backticks
  (``"`message`"``, ``"`items[3]`"``). See `https://docs.typesafe.ai/primitives/advanced.md`.
- One judgment per question, in the literal words you want answered; Jev reads scoping words
  and negations at face value. A set may carry as many questions as the callers need (forty a
  call): they are answered in parallel, and a speculative question costs tokens, not time.
- `dynamic: true` marks a choice whose options the caller completes at run time (attendees,
  existing items, the fleet); the file holds only the fixed options, and `with_options` in
  `clients/judge.py` merges them.

Thresholds are the callers' to act on, not the model's: pick the option with the highest
confidence when all you want is the best option, and threshold only when doing nothing is a
real outcome. Make the threshold asymmetric where the costs are. Changing a threshold is a
version bump when it changes what a bot does with the answer.

| Set | Decides | Called by |
|---|---|---|
| `mail-triage` | one inbox message from its brief listing | `mail inbox --decisions`, before any thread is opened |
| `mail-draft-gate` | a draft against its thread | `mail draft`, before the second reviewer |
| `covered` | is a candidate one of these existing things | any bot deduplicating anything |
| `slack-route` | a DM or @Tico: does it ask, is it a reply, does it name a bot; the gateway adds one noul per active bot | the Slack gateway, every accepted message |
| `listening-card` | a fetched public card's likely owner opportunity, competitor move and impact | Listening's competitor sweep and Reddit/X mention checks |
| `listening-item` | one saved post: an independent probability per category (market, content, lead, creator, partner), so a post can go to several inboxes, and `vendor_pitch`, which keeps a vendor's promotion out of leads | `hub listening decide`, which stores the scores and routes by the destinations in the company's `registry/listening.yaml` |
| `learnings-route` | one item of the day's activity: is it learnable; the run adds one relevance noul per recipient | the nightly learning run (`backend/learnings.py`) |
| `reply-intent` | an ambiguous inbound SMS or email reply's explicit intent | Response Rate's read-only scorecards |

Adding a set is a pull request here: the file, a line in this table, and the change in the
caller that reads it. Read a month of one set's calls back with
`hub sql "SELECT target, detail_json FROM events WHERE action='judge.call' AND target='mail-triage@1'"`.
