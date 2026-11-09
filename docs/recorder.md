# Recorder speech and assist

Tico Recorder, the Mac meeting recorder, gets its speech-to-text, live text and assist (drafts, private questions)
from the install's Tico AI Gateway, so it needs no model keys of its own. The install relays for it; the Gateway key
never leaves the server.

Every route here takes the recorder's person token (the one it signs in with, as `Authorization: Bearer`). Only a
company human may call them: a bot or an anonymous caller gets 401 or 403. A rehearsal install answers 503 `rehearsal`.
With no Gateway connection (Settings > Computers) a route answers 409 `gateway_not_connected`.

| Route | What it does |
| --- | --- |
| `GET /api/v2/recorder/services` | `{"transcribe":<bool>,"assist":<bool>,"stream":<bool>}`. `assist` is true with a Gateway connection; `transcribe` and `stream` are what the Gateway says it serves (`GET <base>/audio`). The Gateway's answer is kept for 5 minutes per key. Any Gateway error makes all three false; this route never fails. |
| `POST /api/v2/recorder/audio/transcriptions` | Passes the body to the Gateway's `<base>/audio/transcriptions` unchanged (OpenRouter's speech-to-text shape, `model` `tico-transcribe-1.0` or `elevenlabs/scribe-v2`) and returns its answer unchanged. Up to 25 MB. A Gateway error keeps its status and body, so the recorder can show it. |
| `POST /api/v2/recorder/chat/completions` | The Gateway's chat, not streamed. Only `messages`, `response_format`, `temperature` and `max_tokens` are passed on; `model` is the install's meeting model (`providers.MEETING_MODEL`, or the company's own Tico meeting model). Request size is the usual 2 MB limit. Errors pass through as above. |
| `WS /api/v2/recorder/stream?<query>` | The Gateway's live speech stream. The query (`encoding`, `sample_rate`, `interim_results`, `language`, `diarize`) goes on unchanged; text and binary frames are relayed both ways unchanged. When either side closes, the other is closed with the same outcome. A refused stream closes the recorder's socket with code 1011 and reason `stream_unavailable` (the Gateway has no live speech service for this install), or `stream_refused_<status>`. The stream passes through the Rust front proxy (`relay-rs/src/gateway.rs`), which dials the backend before it accepts the recorder, so a refusal from the backend (for example 403 for a bad token) reaches the recorder as that HTTP status. |

The Gateway's own paths are under `<base>/` (by default `https://updates.tico.team/v1/gateway`); the install's Gateway
URL and key are the ones Settings > Computers holds (`backend/gateway_connection.py`).

Code: `backend/recorder_relay.py` (routes and relay), `clients/gateway.py` (`relay`, the no-redirect transport),
`backend/tests/test_recorder_relay.py` (tests against a stub Gateway; nothing reaches the network).
