# Team context and meeting search

## The strategy in every run

Once the owner has published a [strategy](strategy.md) version, every bot's run context carries the text of
`GET /api/v2/strategy/summary` under a **Team strategy** header, about 500 characters: the summary, one line per active
bet with its wrong-if, then "version N". It is there on every turn, not only the Strategy Bot's, so a bot reads the team's bets
before it reads its task. Nothing is added while no version is published, and a bot never sees the owner's draft. The full
published page (the four sections, every bet with its why, what it rules out and its health) is `hub strategy show` /
`hub_strategy_show`; the same short text is `hub strategy summary` / `hub_strategy_summary`. Publishing sends no note to any
bot: the context is how they learn of it. A bot reads; only the owner writes the draft and publishes.

## Search and reads

The `hub` CLI and MCP expose the same read-only functions, and one that files a meeting:

| CLI | MCP | Purpose |
| --- | --- | --- |
| `hub strategy show` | `hub_strategy_show` | The published strategy version, with its bets, the goals under each and each bet's health. |
| `hub strategy summary` | `hub_strategy_summary` | The short text every bot gets in context. |
| `hub doc search --market "pricing"` | `hub_doc_search` | Search full team document text and market entities/evidence. |
| `hub doc read <document-id>` | `hub_doc_read` | Read a complete document found by search. |
| `hub meeting search "pricing"` | `hub_meeting_search` | Search meeting titles, notes, and transcripts; omit the query for recent history. |
| `hub meeting read <meeting-id>` | `hub_meeting_read` | Read the full transcript in bounded pages. |
| `hub meeting import call.vtt --source zoom` | `hub_meeting_import` | File a transcript from another tool as a meeting of yours (humans only; [Meetings](meetings.md)). |

Doc search accepts `--market` (also the market's notes, entities and evidence), `--manual` (only the Tico manual) and `--limit` (1–50). Results
include source IDs, links, and matching excerpts. Use the existing `hub market show`
to inspect a market entity. Search is keyword-based, not a generated answer.

Meeting search accepts `--person`, `--since YYYY-MM-DD`, `--until YYYY-MM-DD`,
`--limit` (1–50), and `--offset`. Dates are inclusive and use the meeting start
when present, otherwise its creation date. Results include up to five matching
speaker passages with timestamps when the source provides them. Follow `next_offset`
for additional results. Transcript reads accept `--offset` and `--limit` in characters
(default 20,000, maximum 50,000); follow `next_offset` until it is null.

Every teammate (owner, human or bot) can list and read internal and linked team Docs ([Docs](docs.md)). Market knowledge follows the
existing market access rules. Bots may read explicitly non-private team meetings;
they do not inherit their owner's access to private meetings or personal notes.
Human access follows the existing owner/attendee rules. Deleted meetings are excluded.
The read tools do not expose audio, attachments, or credentials.
