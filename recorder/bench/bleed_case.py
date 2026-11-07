"""Deterministic transcript replay of delayed speaker bleed. No STT or AEC quality claims.

Use meeting_bench.py --scenarios bleed for the paid, room-filtered AMI audio experiment.
"""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def fixture():
    # Known remote-only spans make residual short fragments count as bleed too. Each case has
    # recognition variation that exact word deletion leaves behind, plus a separate local reply.
    phrases = [
        ("We should ship the in progress release tomorrow", "Uh we should ship the in-progress relese tomorrow an"),
        ("There are twenty one updates waiting for review", "there are 21 updatez waiting for review uh"),
        ("Please check the deployment status before the next meeting", "please check the deploymnt status before the next meeting a"),
        ("The documentation and configuration changes are ready now", "the documentaton and configuraton changes are ready now uh"),
    ]
    mic, remote, local_spans, bleed_spans = [], [], [], []
    for i, (far, echo) in enumerate(phrases):
        start = i * 12_000 + 1000
        def timed(text, offset, speaker=""):
            return [dict(start_ms=offset+j*240, end_ms=offset+j*240+200, text=word, speaker=speaker)
                    for j, word in enumerate(text.split())]
        remote.extend(timed(far, start, "S4"))
        mic.extend(timed(echo, start+20))
        bleed_spans.append((start, start+4000))
        local = timed("My independent proposal stays intact", start+7000)
        mic.extend(local)
        local_spans.append((start+7000, start+9500))
    def chunk(channel, words):
        return dict(id=channel, recording_id="bleed", channel=channel, speaker="Me" if channel == "me" else "Meeting audio",
                    start_ms=0, end_ms=48_000, file="", status="done", text=" ".join(w["text"] for w in words),
                    words=words, error="", attempts=1, provider={},
                    signal=dict(samples=768000, nonzero=100, peak=0.5, rms=0.1, duration_ms=48000, sample_rate=16000))
    return dict(id="bleed", title="Synthetic room bleed", started_at="", state="stopped", microphone="",
                meeting_audio=True, warning="", deliveries=[], chunks=[chunk("me",mic),chunk("them",remote)]), bleed_spans, local_spans


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--save", type=Path)
    a = parser.parse_args(args)
    record, bleed, local = fixture()
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "bleed.json"
        path.write_text(json.dumps(record))
        out = json.loads(subprocess.check_output([str(a.cli.resolve()), "echo-replay", str(path)], text=True))
    count = lambda words, spans: sum(any(start <= w["start_ms"] < end for start,end in spans) for w in words)
    result = dict(case="delayed-room-bleed-transcript", input_bleed_words=count(record["chunks"][0]["words"],bleed),
                  before_bleed_words=count(out["before"],bleed), after_bleed_words=count(out["after"],bleed),
                  local_words_before=count(out["before"],local), local_words_after=count(out["after"],local),
                  aec="not measured: requires a Mac with a real built-in mic/speaker route")
    print(json.dumps(result, sort_keys=True))
    if a.save:
        a.save.mkdir(parents=True, exist_ok=True)
        (a.save / "bleed-replay.json").write_text(json.dumps(dict(result=result,record=record,output=out),indent=2))
    if result["after_bleed_words"] != 5 or result["local_words_after"] != 20:
        raise SystemExit("Bleed replay regressed")


if __name__ == "__main__":
    main()
