# Meeting transcription benchmark

`meeting_bench.py` scores the recorder's real transcript pipeline on four real meetings. It is
opt-in, uses a paid STT provider, and is not part of `cargo test`.

```sh
TICO_RECORDER_STT_KEY=<OpenRouter key> uv run bench/meeting_bench.py --check
```

The first run downloads four AMI meetings (four headsets each, about 800 MB, CC BY 4.0) to
`~/.cache/tico-recorder-bench` and builds `tico-recorder-cli` with `--features diarize`. A run
costs about $0.25 per scenario with the default model. `--meetings ES2004a` or
`ES2004a:540-1020` picks meetings and windows; `--model` tries another OpenRouter model; `--save
DIR` keeps each run's transcript and recorder data.

## How it simulates a call

AMI records each participant on a headset, with word-level timings. In each meeting the person who
talks most is the microphone ("Me") and the other three are mixed as meeting audio. Each headset is
kept only while its wearer speaks, and only where it is within 12 dB of the other side's loudest
headset, so my voice does not leak into theirs (a call sends one cleaned voice per person and never
echoes mine back). Leaks among the far end stay, as in a shared meeting room.

| Meeting | Site | Window | Me | My words | Their words |
|---|---|---|---|---|---|
| ES2004a | Edinburgh | 540–1020 s | B | 623 | 966 |
| IS1009a | IDIAP | 300–780 s | A | 839 | 577 |
| TS3003a | TNO | 300–780 s | A | 758 | 54 |
| EN2002a | Edinburgh (non-scenario) | 300–780 s | C | 648 | 1263 |

- `headphones`: the mic hears only me.
- `speakers`: the meeting audio also reaches the mic 40 ms later and 14 dB down, as from laptop
  speakers.

`tico-recorder-cli transcribe-meeting` cuts each side into chunks with the live recorder's rule,
transcribes them, removes echo, labels speakers and prints the transcript lines.

## Metrics

- `wer_me`, `wer_them`: word error rate of each side after the Whisper English normalizer.
- `cpwer`: concatenated minimum-permutation WER (meeteval). Each reference speaker is matched to
  one transcript speaker, so words on the wrong speaker count as errors.

`--check` compares the four-meeting means with the pass marks `wer_me` 0.16, `wer_them` 0.20 and
`cpwer` 0.20.

## Results (October 22, 2026)

Recorder pipeline, Scribe v2, 45 s chunks, four-meeting means:

| Scenario | wer_me | wer_them | cpwer | Voices found (true: 3, 3, 3*, 3) |
|---|---|---|---|---|
| headphones | 0.099 | 0.175 | 0.119 | 3, 4, 2, 3 |
| speakers | 0.151 | 0.175 | 0.147 | 4, 6, 2, 3 |

\*TS3003a's far end says only 54 words, nearly all from one person.
Per meeting (headphones), cpwer is 0.10 ES2004a, 0.15 IS1009a, 0.09 TS3003a and 0.14 EN2002a.
Repeated runs vary by about ±0.02 because providers are not deterministic.

The default four meetings were used to tune speaker labels, so two more sets check that the
settings carry over (`--meetings`, `headphones`; each meeting's 300–780 s):

| Set | Meetings | wer_me | wer_them | cpwer |
|---|---|---|---|---|
| held out (never used for a choice) | ES2008a, IS1003b, TS3007a, EN2002b | 0.137 | 0.202 | 0.212 |
| fresh (never used for any choice) | ES2011a, IS1006b, TS3009c, EN2003a | 0.087 | 0.161 | 0.149 |

IS1003b (cpwer 0.32) is the weakest: about 70 of the microphone wearer's words still leak into the
other headsets, which a real call would not send.

AMI's spontaneous, overlapping meeting speech is hard. Strong recognisers typically score 10–20%
WER on AMI headset audio, against 2–3% on the read speech used for leaderboards.

### Choosing the model

Artificial Analysis' speech-to-text leaderboard (AA-WER, October 22, 2026) starts Fun-Realtime-ASR
1.7%, MAI-Transcribe-2 2.0%, Voxtral Small Transcribe 3 2.1%, Scribe v2 2.2%, Voxtral Mini
Transcribe 3 2.2%, Grok Voice Transcribe 2.0 2.3%, MAI-Transcribe-1.5 2.4%, Gemini 3.5 Transcribe
2.6%. Everything from it on OpenRouter, plus other OpenRouter models, through the same pipeline
(`headphones`, four-meeting means):

| Model (OpenRouter) | wer_me | wer_them | cpwer | $/hour |
|---|---|---|---|---|
| **elevenlabs/scribe-v2** (default) | 0.099 | 0.175 | **0.119** | 0.22 |
| microsoft/mai-transcribe-2 | 0.108 | **0.174** | 0.245 | 0.10 |
| mistralai/voxtral-mini-transcribe-3-0 | 0.093 | 0.212 | 0.229 | 0.21 |
| fish-audio/transcribe-1-pro | 0.110 | 0.180 | 0.246 | 0.36 |
| google/gemini-3.5-transcribe | 0.111 | 0.189 | 0.249 | per token |
| mistralai/voxtral-small-transcribe-3-0 | **0.093** | 0.205 | 0.231 | 0.24 |
| deepgram/nova-3 | 0.115 | 0.203 | 0.254 | 0.26 |
| x-ai/grok-stt-1.0 | 0.115 | 0.234 | 0.251 | 0.10 |
| assemblyai/universal-3-6-pro | 0.131 | 0.202 | 0.268 | 0.30 |
| openai/gpt-transcribe | 0.116 | 0.219 | 0.455 | 0.27 |
| qwen/qwen3-asr-1.7b | 0.124 | 0.852† | 0.310 | 0.03 |
| microsoft/mai-transcribe-1.5 | **0.087** | 0.943† | 0.517 | 0.36 |

†Qwen and MAI-Transcribe-1.5 invent text on TS3003a's near-silent meeting audio; elsewhere their
far-end WER is 0.14–0.23.
Scribe and MAI-Transcribe-2 recognise the far end equally well; Scribe wins on speakers because
it is the only one whose own speaker labels come back through OpenRouter (see Approach).
MAI-Transcribe-2 is the cheaper choice when speaker labels matter less. GPT-Transcribe returns no
word timings, so its meeting audio cannot be split into speakers. Voxtral rejects word timings and
is asked for segment timings instead; MAI-Transcribe-1.5 returns text only. Not tried: Fun-Realtime-ASR
(Alibaba Cloud Model Studio only, needs its own account; its open sibling Fun-ASR-Nano-2512, run
locally with its own voice-activity splitter, scored far-end WER 0.357 against Scribe's 0.175 and
returns no timings) and Grok Voice Transcribe 2.0 (OpenRouter
has only grok-stt-1.0). Only Scribe's row includes voiceprint linking; the others give no
speaker turns to link, so their cpwer would not change.

An earlier single-meeting harness compared chunk lengths: at the old 5 s chunks the previous
default (Muse) scored 0.46 on my side and 0.20 on theirs, against 0.086 and 0.142 at 45 s. Chunk
length mattered more than the model.

Echo removal on `speakers`: without it, `wer_me` is 1.46 because the mic transcript repeats the
other side; with it, about 0.15 (word-level match within 1 s).

### Streaming (October 29, 2026)

`bench/stream_bench.py` streams each side of the same four calls live, in 100 ms frames, to a
streaming provider, both sides at once, the meeting side with the provider's own diarization. It
also measures lag: seconds from the end of a word's audio to the first interim text holding it
(`partial`) and to the final text (`final`). About 9 minutes per run.

| Provider (headphones) | wer_me | wer_them | cpwer | partial lag p50/p90 | final lag p50/p90 | $/hour per side |
|---|---|---|---|---|---|---|
| xAI grok-voice-transcribe-2.0, streaming | 0.122 | 0.252 | 0.205 | 0.11 / 0.20 s | 0.96 / 1.51 s | 0.20 |
| Scribe v2 in 30–55 s chunks (above) | 0.099 | 0.175 | 0.119 | 30–55 s | 30–55 s | 0.22 |

Words appear about a tenth of a second after they are spoken, against half a minute or more for
chunks, but streaming is less accurate, mostly on the far end (0.252 against 0.175), and its
speaker labels are worse than linked Scribe turns (0.205 against 0.119; it found 4, 4, 2, 3
voices for 3, 3, 3*, 3). So streaming suits live text and early drafts, and the chunked pass
stays for the transcript that is kept.

### Speaker labels

Three steps, each measured:

1. Local pyannote 3.1 voices only (anarlog's port): 0.863 of meeting-audio words on the right
   speaker under the best one-to-one mapping (four default meetings, earlier setup). Most errors
   are in overlapping speech (518 of 1304 overlapped words wrong, against 152 of 3318 clean).
2. Scribe's own speaker labels per chunk, each label taking the local voice most of its words
   got: 0.946. Scribe separates turns within a chunk well, but its labels restart every request.
3. Each Scribe turn with at least a second of speech gets a WeSpeaker voiceprint, and turns are
   joined across chunks by average cosine distance under 0.9, never two turns of one chunk. If
   Scribe's turns were linked perfectly, 0.89–0.99 of words would be right; the local voices
   linking them reached only 0.51–0.75 on the hard held-out meetings.

| cpwer (`headphones`) | default four | held out | fresh |
|---|---|---|---|
| Step 2 (local voices link turns) | 0.185 | 0.330 | 0.213 |
| Step 3 (voiceprints link turns) | **0.123** | **0.212** | **0.149** |
| Whole far end in one Scribe request after Stop | 0.163 | 0.225 | 0.143 |

One request per meeting gives Scribe's labels for the whole call, with no linking, but it merged
people in IS1009a (0.288 against 0.185) and ES2008a (0.267 against 0.120) and doubles the
transcription cost, so chunks with linked turns stay.

The voiceprint distance was chosen on the four default meetings only: 0.6–0.8 scored 0.132,
0.85–1.0 0.122–0.123, so 0.9 (the middle). The held-out and fresh sets were then scored once at
0.9; at 0.8 they were the same within 0.001. With step 3 the local diarizer only labels leftovers: swapping
in speakrs 0.6 (a Rust port of pyannote community-1 with VBx clustering) changed cpwer by at most
0.002. Without step 3 it was level on the default meetings and better held out (0.301 against
0.330) but found up to 9 voices for 3 people. Giving the local diarizer the true number of
speakers, as a calendar might, helped step 2 a little (held out 0.330 to 0.300) and step 3 not at
all. The local clustering threshold is 0.8 instead of pyannote's 0.7045 (fewer stray voices).

`tico-recorder-cli relabel` re-runs labels on a `--save`d run without paying for transcription:
`--config '{...}'`, `--link 0.9` (0 is off), `--speakers N`, or `--segments file.json` from
another diarizer.

## Approach

Same design as Granola and its open-source alternatives (anarlog, formerly Hyprnote, and others):
microphone and system audio are separate channels, so "Me" versus "Them" needs no
model. The far end is diarized on-device after the meeting with a pyannote 3.1 pipeline, and words
take the overlapping speaker. When the transcription model labels speakers within each chunk
(Scribe), those turns are joined across the meeting by WeSpeaker voiceprints, the usual way to
combine an STT provider's per-request speaker labels into one meeting. Echo is removed in the
transcript, not with acoustic echo cancellation. Speech defaults to English (Settings › Language;
empty guesses per chunk, which turned short "mm-hmm"s into Japanese and Chinese).
