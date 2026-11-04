# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "soundfile", "jiwer", "whisper-normalizer", "meeteval==0.4.3", "websockets>=14"]
# ///
"""Streaming transcription benchmark: the same four AMI calls as meeting_bench.py, streamed live.

Each side of each call (my microphone, the meeting audio) is sent to a streaming STT provider at
real-time pace in 100 ms frames, all sides at once, as the recorder would during a call. Scores:

  wer_me / wer_them  word error rate of each side's final transcript (whisper English normalizer)
  cpwer              speaker-attributed WER, from the provider's own speaker labels on the meeting side
  final_lag_s        seconds from the end of a word's audio to the final transcript holding it (p50/p90)
  partial_lag_s      the same for the first interim result, when words first appear on screen

    uv run bench/stream_bench.py --provider xai

The key is read from the macOS keychain (service team.tico.recorder.native, account = provider) or
XAI_API_KEY. Each run streams 64 minutes of audio and takes about 9 minutes. Not part of `cargo test`.
"""
import argparse, asyncio, json, os, statistics, subprocess, sys, time
from pathlib import Path

import jiwer, meeteval, numpy as np
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meeting_bench import MEETINGS, RATE, channels, fetch, norm, reference  # noqa: E402

FRAME_S = 0.1


def key(provider: str) -> str:
    value = os.environ.get(f"{provider.upper()}_API_KEY", "")
    if not value:
        found = subprocess.run(["security", "find-generic-password", "-s", "team.tico.recorder.native",
                                "-a", provider, "-w"], capture_output=True, text=True)
        value = found.stdout.strip()
    if not value:
        sys.exit(f"No {provider} key in the keychain or {provider.upper()}_API_KEY. Nothing ran.")
    return value


def xai_url(diarize: bool) -> str:
    query = "model=grok-voice-transcribe-2.0&encoding=pcm&sample_rate=16000&interim_results=true&language=en"
    return "wss://api.x.ai/v1/stt?" + query + ("&diarize=true" if diarize else "")


async def stream(provider: str, secret: str, audio: np.ndarray, diarize: bool, log: Path):
    """Streams one side live. Returns (final words, lags, partial lags)."""
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    step = int(RATE * FRAME_S) * 2
    finals, final_lags, partial_lags = [], [], []
    seen_partial = 0.0      # audio time through which some interim text has already been shown
    started = None
    with log.open("w") as out:
        async with websockets.connect(xai_url(diarize), additional_headers={"Authorization": f"Bearer {secret}"},
                                      max_size=None) as ws:
            while True:  # wait for the ready event before sending audio
                event = json.loads(await ws.recv())
                out.write(json.dumps(event) + "\n")
                if event.get("type") == "transcript.created":
                    break
                if event.get("type") == "error":
                    raise RuntimeError(event)

            async def send():
                nonlocal started
                started = time.monotonic()
                for index, at in enumerate(range(0, len(pcm), step)):
                    await ws.send(pcm[at:at + step])
                    # Real-time pace against the wall clock, not sleep drift.
                    delay = started + (index + 1) * FRAME_S - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                await ws.send(json.dumps({"type": "audio.done"}))

            sender = asyncio.create_task(send())
            async for message in ws:
                if isinstance(message, bytes):
                    continue
                event = json.loads(message)
                now = time.monotonic() - (started or time.monotonic())
                out.write(json.dumps({**event, "_t": round(now, 3)}) + "\n")
                kind = event.get("type")
                if kind == "error":
                    raise RuntimeError(event)
                words = event.get("words") or []
                ends = [float(w["end"]) for w in words if w.get("end") is not None]
                if kind == "transcript.partial" and ends:
                    if event.get("is_final"):
                        # A final covers [start, start + duration]; the end of a sentence restates
                        # the whole sentence, so it replaces what it covers instead of adding to it.
                        lo = float(event.get("start", min(float(w["start"]) for w in words)))
                        hi = lo + float(event.get("duration", 0)) if event.get("duration") else max(ends)
                        finals[:] = [w for w in finals if not lo - 0.01 <= float(w["start"]) < hi + 0.01]
                        finals.extend(words)
                        finals.sort(key=lambda w: float(w["start"]))
                        final_lags.append(now - max(ends))
                    elif max(ends) > seen_partial:
                        partial_lags.append(now - max(ends))
                        seen_partial = max(ends)
                if kind == "transcript.done":
                    break
            await sender
    return finals, final_lags, partial_lags


def pct(values, q):
    return round(float(np.percentile(values, q)), 2) if values else None


async def run_call(provider, secret, data, meeting, me_agent, start, end, logs):
    fetch(data, meeting)
    ref = reference(data, meeting, start, end)
    mic, them = channels(data, meeting, me_agent, ref, start, end, "headphones")
    (mine, mine_lag, mine_part), (theirs, their_lag, their_part) = await asyncio.gather(
        stream(provider, secret, mic, False, logs / f"{meeting}-me.jsonl"),
        stream(provider, secret, them, True, logs / f"{meeting}-them.jsonl"))
    side = lambda rows: " ".join(w for t in rows for w in norm(t))
    text = lambda words: [w.get("text") or w.get("word") or "" for w in words]
    row = {"meeting": meeting,
           "wer_me": jiwer.wer(side(t for a, *_, t in ref if a == me_agent), side(text(mine))),
           "wer_them": jiwer.wer(side(t for a, *_, t in ref if a != me_agent), side(text(theirs)))}
    stm = lambda rows: meeteval.io.STM.parse("\n".join(
        f"m 1 {spk} {t0:.2f} {t1:.2f} {' '.join(norm(t))}" for spk, t0, t1, t in rows if norm(t)))
    hyp = [("Me", float(w["start"]), float(w["end"]), w.get("text") or "") for w in mine] + \
          [(f"S{w.get('speaker', 0)}", float(w["start"]), float(w["end"]), w.get("text") or "") for w in theirs]
    row["cpwer"] = float(next(iter(meeteval.wer.cpwer(stm(ref), stm(sorted(hyp, key=lambda h: h[1]))).values())).error_rate)
    row["voices"] = len({w.get("speaker") for w in theirs})
    row = {k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()}
    lags, parts = mine_lag + their_lag, mine_part + their_part
    row |= {"final_lag_s": [pct(lags, 50), pct(lags, 90)], "partial_lag_s": [pct(parts, 50), pct(parts, 90)]}
    return row, lags, parts


async def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", default="xai", choices=["xai"])
    p.add_argument("--data", type=Path, default=Path.home() / ".cache" / "tico-recorder-bench")
    p.add_argument("--meetings", default=",".join(m for m, *_ in MEETINGS))
    p.add_argument("--logs", type=Path, default=Path.home() / ".cache" / "tico-recorder-bench" / "stream-logs")
    a = p.parse_args()
    secret = key(a.provider)
    a.logs.mkdir(parents=True, exist_ok=True)
    known = {m: (who, s, e) for m, who, s, e in MEETINGS}
    results = await asyncio.gather(*(run_call(a.provider, secret, a.data, m, *known[m], a.logs)
                                     for m in a.meetings.split(",")))
    for row, *_ in results:
        print(json.dumps(row), flush=True)
    lags = [x for _, l, _ in results for x in l]
    parts = [x for *_, p_ in results for x in p_]
    mean = {k: round(statistics.mean(r[k] for r, *_ in results), 3) for k in ("wer_me", "wer_them", "cpwer", "voices")}
    print(json.dumps({"provider": a.provider, "mean_of": len(results), **mean,
                      "final_lag_s": [pct(lags, 50), pct(lags, 90)], "partial_lag_s": [pct(parts, 50), pct(parts, 90)]}))


if __name__ == "__main__":
    asyncio.run(main())
