# Tico Recorder

Rust desktop V1, macOS first. The window is Tauri 2 on the system WebView (WKWebView on macOS), with a plain HTML/CSS/JS page in `ui/app` and no Chromium, Electron, framework, or build step. `src/app` holds the Tauri commands and the `Recorder` controller the page polls. Capture, durable storage, transcription, and delivery run separately from the interface.

## Run

Requires Rust 1.92 or newer and the Tauri CLI (`cargo install tauri-cli --version "^2" --locked`) for bundles. On macOS, meeting/system audio requires macOS 14.2 or newer and the system's audio-recording permission. Microphone recording requires microphone permission.

```sh
cargo run --manifest-path recorder/Cargo.toml --features desktop --bin tico-recorder
bash recorder/package-macos.sh
```

`package-macos.sh` runs `cargo tauri build` and writes `recorder/target/release/bundle/macos/Tico Recorder.app`. It signs with your Apple Development identity when one is in the keychain (set `TICO_SIGN_IDENTITY` to choose another), so keychain "Always Allow" and microphone permission survive rebuilds; without one it signs ad hoc. It is not notarized and makes no updater archive. It uses its own library, separate from the earlier recorder. `TICO_RECORDER_DATA_DIR` selects an isolated test library.

The bundle is described by `tauri.conf.json` (product name "Tico Recorder", identifier `team.tico.recorder.native`, icons, macOS 14.2 minimum, hardened runtime), `Info.plist` (the microphone and meeting-audio usage text and the `tico-recorder://` link scheme) and `entitlements.plist` (audio input and network client; the app is not sandboxed). The identifier and the data folder `~/Library/Application Support/Tico Recorder Native` stay as they are, so microphone permission, keychain entries and recordings carry over from the earlier build. The folder name is read in `src/settings.rs`.

## Releases and updates

- `.github/workflows/recorder.yml` builds a universal (Apple silicon and Intel) `Tico-Recorder_<version>_universal.dmg`, the updater archive `Tico-Recorder.app.tar.gz` with its `.sig`, and `Tico-Recorder.dmg`, a copy under a stable name for the download link. GitHub turns spaces in asset names into dots, so the release names have no spaces; the product name keeps its space. It uses the desktop app's signing and updater secrets (see `app.yml`). Notarization runs when the Apple ID secrets are set.
- A version tag gives the recorder that version (it is written into `tauri.conf.json` at build time). `release.yml` builds the recorder only when `recorder/` changed since the previous release; otherwise the previous release's recorder files are carried forward. Either way they are attached to the GitHub release, and `recorder-latest.json` is uploaded last, so the updater never sees a manifest whose files are missing. A recorder failure leaves the server release in place.
- Download: `https://github.com/ticoteam/tico/releases/latest/download/Tico-Recorder.dmg`.
- Updates: the app reads `recorder-latest.json` on launch and every six hours, downloads a newer build in the background, and installs it once no recording is running and no live meeting is connected. A small banner (`ui/app/update.js`) shows "Update ready · Restart", or "Updating after this recording" (or after the live meeting ends) while it waits. The Restart button does not override that wait. `update_check` and `update_install` are the commands behind it, and `status.update` in the view carries the state.
- Pull requests that change `recorder/` run `cargo test --locked --features desktop,cli` on macOS (`.github/workflows/pr-checks.yml`).

## Workflow

- The library is a Record button and one row per recording: title and date/time (status only when something needs attention), Send (pick the Tico connection, then Send) and Delete (asks first, removes audio and transcript from this Mac). Clicking a row opens it.
- Dialogs are compact modals; Esc or Cancel closes them and discards unsaved Settings edits.
- Record opens a meeting workspace: transcript in the center, one side panel with Chat, Tasks, Docs, Notes, People, and (once in Tico) Questions tabs. Esc returns to the library.
- Chat on an unshared recording is private: questions about the transcript go through the Tico router (`POST /api/v2/meeting-assist`) and create no Tico meeting. A shared meeting's panel switches between Meeting chat and Private.
- With Auto on in the Tasks or Docs tab, recordings made in this session get task and doc-change suggestions from new speech every 10 s once 400+ characters accumulate (streamed text counts, so suggestions follow the conversation within seconds), plus once after Stop when transcription settles. A suggestion's quote is checked against the text it was drafted from, so a later chunk that rewords streamed text does not drop it. Suggestions arrive unselected. Suggest now reviews the whole transcript in batches; a Tico without the suggestion endpoint gets a plain "needs a newer Tico" note instead of retries. Hand-added tasks and doc changes pick their supporting quote from the transcript.
- The transcript is rebuilt from word timings: the microphone is "Me", meeting audio is split into the voices found on this computer after Stop ("Speaker 1", "Speaker 2", …; one voice keeps "Meeting audio"), and everyone's speech is ordered by when it was said. Consecutive speech from one speaker is one block, split into paragraphs at pauses and long finished sentences. Microphone words that repeat meeting audio within a second (the call playing on speakers) are dropped. Speaker labels use anarlog's pyannote port (`vendor/anarlog`, CoreML on macOS, about 80 s per hour of meeting), combined with Scribe's own per-chunk speaker turns, which are joined across the meeting by voiceprint; new meeting-audio words find them again. Storage chunks, silent intervals, and chunks with no recognized speech do not become conversation rows. Audio status and transcription retry details sit behind the Audio toggle above the transcript; transcript rows have no quote or playback controls. A line already shared live can be edited or removed (below).
- Live text: while recording, each input also streams to xAI (`grok-voice-transcribe-2.0`, 100 ms frames) so words appear within about a second; meeting audio gets the service's provisional speakers (S1, S2, …). On by default when an xAI key is saved in the keychain (account `xai`); `streaming`, `streaming_url` and `streaming_model` in settings.json change it, and language follows Settings › Language. Final streamed words are saved locally (`live_words`), partial text is kept in memory. Chunk transcripts replace streamed text for their time range once done and stay the transcript of record. A live meeting gets each streamed final within about a second, so bots hear it; when the chunk is transcribed its words correct those turns (each word goes to the streamed turn it falls in, or the nearest; words past the last streamed turn are new turns), so nothing is shared twice. A line already shared live can be edited or removed in the transcript: each change goes up as that line's next revision, and a removed line reads "[removed]" in Tico. A person's edit wins over any later automatic correction from the finished transcript. Send holds edits to that meeting from its last pass until it ends, sends the changes first, and refuses to file if one could not reach Tico. Without live text, or after it drops, finished chunks are shared as before. With Your Tico, live text, transcription and drafting go through the default Tico (`/api/v2/recorder/...`) with your own sign-in, so no keys of your own are needed; `speech_source` in settings.json (`tico` or `own`) chooses between them, and an install that already had keys of its own keeps them. Streaming never slows capture: a full queue (10 s) or a reconnect skips streamed audio only.
- Microphone and system audio are stored as independent, timestamped WAV chunks of 30–55 s, cut at the quietest pause in that window so words are not split. Long chunks give the model context; see `bench/README.md`. Failed transcriptions retry automatically twice (15 s, then 60 s), then wait for Retry. An input that stops delivering frames for 3 s is flagged. Audio survives transcription/network failures. Stop releases inputs and flushes the final buffer. Missing inputs, silence, device errors, and dropped frames remain visible.
- If microphone setup fails, meeting-audio setup is still attempted. The surviving input records; failures of both inputs preserve both causes. Device-error warnings identify the input and appear under the Audio toggle.
- Each input joins the capture loop as soon as it is ready. Microphone audio keeps saving while system-audio setup or permission is pending. Stop closes active inputs without waiting for a pending initializer; an input that finishes connecting after cancellation is closed immediately. Empty captures show No audio, and background Tico-delivery failures are visible in the library and meeting workspace.
- Settings holds input selection and transcription endpoint/model. Tico connects with Sign in with Tico: type the address you use in the browser (filled in from the Tico desktop app), choose Allow in the browser, and the recorder gets its own token, renewed hourly for 90 days and revocable under Settings › Computers › API tokens in Tico. Runner addresses are found automatically. The Tico Recorder tile on Tico's Meetings page opens `tico-recorder://connect?server=…`; the recorder asks before signing in. Several Ticos can be connected: the first is the default (live shares, private chat), Send picks one and shows which already have the meeting, and Sign out ends that Tico's sign-in. Secrets use the operating system's credential store. The default STT is ElevenLabs Scribe v2 through OpenRouter's transcription API, asked for word timings and, on meeting audio, speaker labels (settings still on the earlier Muse default move to it); speech defaults to English (Settings › Language, empty to auto-detect); meeting text inference uses the Tico gateway.
- Recording and transcription happen before a meeting is sent to Tico. Audio goes to the configured transcription provider. Starting a recording does not automatically create or share a Tico meeting.
- Nothing reaches Tico until you add a bot or a person, or choose Everyone, in the People tab. The meeting then streams into Tico as a private live meeting (you, added people and bots); Everyone opens it to the whole team. An older Tico that cannot keep a meeting private refuses, so nothing is opened to the team by accident. A durable ordered outbox retries lost acknowledgements. Background delivery continues when another recording or the library is visible.
- Settings → Use my Tico calendar uses the existing Tico calendar connector and your own roster email. It refreshes enabled calendar context every minute and marks stale results. Recording a calendar event uses its title/invitees; invitees are not treated as verified attendees or identified speakers.
- Bot questions run in explicit circuits, three context reviews at a time. A full circuit repeats bounded batches. Direct reports go first. Coverage distinguishes waiting, running, questions, no questions, interrupted, and failed reviews. This mode bypasses full-agent dispatch on transcript/chat messages.
- The question board keeps canonical wording, original contributions, uncertain deduplication, and host controls for answered/dismissed/split.
- Tasks/doc changes are proposals with supporting transcript quotes. Suggestions are optional, routed through Tico, and unselected until reviewed. Selected tasks require verified owners before creation. Document changes create a review task through Tico's existing document-update flow; they do not edit documents automatically.
- Finish Meeting finalizes the live meeting. Send approves the finalized record and creates only explicitly selected outputs when requested. Retries preserve one canonical meeting and durable item receipts.
- Empty or silent shared sessions can be ended without creating a fabricated imported meeting. Closed sessions and recordings whose delivery has begun cannot reopen capture; retries keep the original frozen transcript.
- Settings → Import an older recorder library copies existing recordings, transcripts, action drafts, destination metadata, and delivery receipts. Source audio and metadata are not modified. Credentials must be saved in the native app's keychain separately.

## Tico server integration

Use the companion `codex/meeting-questions-v1` worktree. It adds question-mode sessions, gateway-only context reviews, serial Jev classification, and quoted proposals while preserving existing live-meeting endpoints.

Configure the server's existing Tico gateway connection, plus `TICO_MEETING_QUESTION_MODEL` with a **published Tico gateway model profile** for the fast meeting agent. The intended upstream is Mercury 3. This repository does not publish that profile or assume it exists. With no profile/key, the feature fails closed and never falls back to a direct vendor. Deduplication uses the gateway's existing Decide endpoint.

## Validation

```sh
cargo test --manifest-path recorder/Cargo.toml --features desktop,cli
# In the companion Tico checkout, with its test environment:
TICO_NATIVE_CLI=/absolute/path/to/recorder/target/debug/tico-recorder-cli \
  .venv/bin/python -m pytest backend/tests/test_native_recorder.py -q
```

The native tests cover shared-line edits and removals (one revision each, retries, a person's edit kept over a later automatic correction, Send refusing an edit Tico never received), audio filtering/resampling, silence, queue overflow, recovery, exclusive STT claims, legacy migration, lost import acknowledgements, ordered live delivery, explicit output creation, and the desktop review/share controls. `ui/preview` runs the page against a mock `invoke` with fixture scenes; `node ui/preview/shoot.mjs` (Playwright WebKit, `PLAYWRIGHT` pointing at a playwright install) renders them at 1040×690 and 760×490 in light and dark into `ui/screenshots/` and checks delete confirmation, sending an item and Esc. The Tico integration replay runs the native executable against a local Tico server and synthetic STT/gateway fixtures, including finalization and task retry.

The 200-context test verifies circuit scheduling, first-layer priority, coverage, grouping, and contribution preservation with synthetic inference. **It is not evidence of real routed model latency, cost, or quality.** Fresh device/meeting checks, the published Mercury profile, and the real 200-context gateway sweep remain release gates. Windows/Linux system-audio capture, voice participation, onboarding voice, calendar OAuth inside Recorder, and automatic document editing are outside this V1.

### Meeting transcription benchmark

`bench/meeting_bench.py` runs the CLI's `transcribe-meeting` (the app's chunking, real STT, echo removal and speaker labels) on four AMI meetings and scores word error rate per side and speaker-attributed WER. It is opt-in and paid (about $0.50 a run); see `bench/README.md`.

```sh
TICO_RECORDER_STT_KEY=... uv run recorder/bench/meeting_bench.py --check
```

### Real transcription gate

Build the CLI, then run the generated-speech check on macOS:

```sh
cargo build --manifest-path recorder/Cargo.toml --features cli --bin tico-recorder-cli
python3 recorder/tests/real_transcription.py \
  --cli recorder/target/debug/tico-recorder-cli \
  --output /absolute/private/validation-directory
```

Provide `TICO_REAL_RECORDER_STT_KEY` through the caller's environment, or explicitly add `--keychain` after saving a transcription credential in native Settings. No key is accepted in command arguments or written to the report. The check reads native transcription endpoint/model settings; it requires a remote HTTPS endpoint and does not write to the meeting library. Without a credential, it exits unsuccessfully and saves a `not_run` receipt. It never starts a microphone/system-audio input or plays sound.

The check generates a short WAV through macOS `say`, submits it through the production transcription adapter, and saves recognized/expected words, whole-response latency and word error rate in a mode-600 receipt. A pass requires word error rate ≤0.25. This proves fixture recognition through the configured real provider; it does **not** prove physical capture, live first-turn latency, Tico delivery, or the 200-context router gate. Keep the validation directory private; receipts contain fixture transcript text. The CLI's `verify-transcription` command can also check another short WAV with explicit expected text.

For an explicit loopback test gateway, the diagnostic CLI accepts `--local-test-gateway` and records that transport separately. The companion Tico tool `scripts/meeting_test_gateway.py` configures an isolated native test profile and supplies a generated gateway test key. Its real OpenRouter key remains in the server process. This verifies the same native STT adapter through the gateway; it does not configure the person's Recorder library or claim a hosted gateway exists. The test gateway also exposes a speech profile and validates returned TTS audio without playing it.

For an authorized credential migration, the signed desktop executable accepts `--import-transcription-key-stdin`. This mode reads one bounded credential from standard input, saves and verifies it using the native app's own keychain identity, and exits without opening capture, a window or the meeting database. It preserves an already configured native credential. The normal setup path is Settings; automation must never put keys in arguments, source files or logs.
