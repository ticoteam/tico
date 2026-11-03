# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "soundfile", "jiwer", "whisper-normalizer", "meeteval==0.4.3"]
# ///
"""Meeting transcription benchmark for the recorder's real pipeline.

Four AMI meetings (four people each, all on headsets, CC BY 4.0) stand in for calls: one
participant's headset is the microphone ("Me"), the other three are mixed into the meeting audio.
`tico-recorder-cli transcribe-meeting` then chunks, transcribes (real, paid STT), removes echo and
labels speakers exactly as the app does, and this script scores the transcript lines against
AMI's word-level reference:

  wer_me / wer_them  word error rate of each side (whisper English normalizer)
  cpwer              speaker-attributed WER (meeteval): words must also carry the right speaker

Scenarios: `headphones` (clean mic) and `speakers` (the meeting audio also reaches the mic 40 ms
later, 14 dB down, as from laptop speakers).

    TICO_RECORDER_STT_KEY=... uv run bench/meeting_bench.py --check

It costs about $0.25 per scenario (four meetings) with the default model. Not part of `cargo test`.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, urllib.request, xml.etree.ElementTree as ET, zipfile
from pathlib import Path

import jiwer, meeteval, numpy as np, soundfile as sf
from whisper_normalizer.english import EnglishTextNormalizer

HERE = Path(__file__).resolve().parent
AMI = "https://groups.inf.ed.ac.uk/ami"
RATE = 16000
# Four meetings from different sites and groups, 8 minutes each: (meeting, whose headset is the
# microphone, start s, end s). "Me" is whoever talks most there; AMI headset n is participant
# "ABCD"[n] in every meeting.
MEETINGS = [("ES2004a", "B", 540, 1020), ("IS1009a", "A", 300, 780), ("TS3003a", "A", 300, 780),
            ("EN2002a", "C", 300, 780)]
NORM = EnglishTextNormalizer()
# Pass marks for --check, a little above the measured results in bench/README.md.
LIMITS = {"wer_me": 0.16, "wer_them": 0.20, "cpwer": 0.20}


def fetch(data: Path, meeting: str):
    data.mkdir(parents=True, exist_ok=True)
    for ch in range(4):
        path = data / f"{meeting}.Headset-{ch}.wav"
        if not path.exists():
            urllib.request.urlretrieve(f"{AMI}/AMICorpusMirror/amicorpus/{meeting}/audio/{meeting}.Headset-{ch}.wav", path)
    words = data / "words"
    if not (words / f"{meeting}.A.words.xml").exists():
        archive = data / "ami_manual.zip"
        if not archive.exists():
            urllib.request.urlretrieve(f"{AMI}/AMICorpusAnnotations/ami_public_manual_1.6.2.zip", archive)
        words.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if name.startswith("words/") and name.endswith(".words.xml"):
                    (words / name.removeprefix("words/")).write_bytes(z.read(name))


def reference(data: Path, meeting: str, start: float, end: float):
    words = []
    for agent in "ABCD":
        for w in ET.parse(data / "words" / f"{meeting}.{agent}.words.xml").getroot():
            if w.tag != "w" or w.get("punc") or not w.text or not w.get("starttime"):
                continue
            t0, t1 = float(w.get("starttime")), float(w.get("endtime") or w.get("starttime"))
            if start <= t0 < end:
                words.append((agent, t0 - start, t1 - start, w.text))
    return sorted(words, key=lambda w: w[1])


def channels(data: Path, meeting: str, me: str, ref, start: float, end: float, scenario: str):
    heads = {}
    for ch, agent in enumerate("ABCD"):
        audio, rate = sf.read(data / f"{meeting}.Headset-{ch}.wav", dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        assert rate == RATE
        audio = audio[int(start * RATE):int(end * RATE)]
        # Each headset only while its wearer speaks: a call app sends one voice per person and
        # never echoes mine back, and headsets otherwise pick up the whole room.
        mask = np.zeros(len(audio), dtype=np.float32)
        for who, t0, t1, _ in ref:
            if who == agent:
                mask[max(0, int((t0 - 0.3) * RATE)):int((t1 + 0.3) * RATE)] = 1
        heads[agent] = audio * mask
    # My voice still leaks into the others' headsets inside those spans (and theirs into mine),
    # which a call never sends. Keep each far-end headset only where it is within 12 dB of mine and
    # mine only where it is within 12 dB of the loudest of theirs (power smoothed over 100 ms).
    # Leaks among the far end stay: several people in one meeting room sound like that.
    frame = RATE // 50
    n = min(len(h) for h in heads.values()) // frame
    power = {a: np.convolve((h[:n * frame].reshape(n, frame) ** 2).mean(axis=1), np.ones(5) / 5, "same")
             for a, h in heads.items()}
    others = np.max([p for a, p in power.items() if a != me], axis=0)
    for a in heads:
        keep = power[a] >= (others if a == me else power[me]) / 16
        heads[a] = heads[a][:n * frame] * np.repeat(keep.astype(np.float32), frame)
    them = sum(heads[a] for a in "ABCD" if a != me)
    me = heads[me]
    them = them / max(1.0, np.abs(them).max() / 0.9)
    if scenario == "speakers":
        delay = int(0.04 * RATE)
        me = me + 0.2 * np.concatenate([np.zeros(delay, dtype=np.float32), them[:-delay]])
    return me, them


def norm(text):
    return NORM(text).split()


def score(ref, lines, me: str):
    side = lambda rows: " ".join(w for t in rows for w in norm(t))
    out = {
        "wer_me": jiwer.wer(side(t for a, *_, t in ref if a == me), side(l["text"] for l in lines if l["me"])),
        "wer_them": jiwer.wer(side(t for a, *_, t in ref if a != me), side(l["text"] for l in lines if not l["me"])),
    }
    stm = lambda rows: meeteval.io.STM.parse("\n".join(
        f"m 1 {spk.replace(' ', '_')} {t0:.2f} {t1:.2f} {' '.join(norm(text))}" for spk, t0, t1, text in rows if norm(text)))
    hyp = [("Me" if l["me"] else l["speaker"], l["start_ms"] / 1000, l["end_ms"] / 1000, l["text"]) for l in lines]
    result = meeteval.wer.cpwer(stm(ref), stm(hyp))
    out["cpwer"] = float(next(iter(result.values())).error_rate)
    out["voices"] = len({l["speaker"] for l in lines if not l["me"]})
    return {k: round(v, 3) for k, v in out.items()}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path.home() / ".cache" / "tico-recorder-bench")
    p.add_argument("--cli", type=Path, default=HERE.parent / "target" / "release" / "tico-recorder-cli")
    p.add_argument("--model", default=None, help="STT model (default: the recorder's)")
    p.add_argument("--scenarios", default="headphones,speakers")
    p.add_argument("--meetings", default=",".join(m for m, *_ in MEETINGS),
                   help="AMI meetings, optionally with a window: ES2004a or ES2004a:540-1020 (others: 300-780)")
    p.add_argument("--language", default="en", help="spoken language passed to the model (empty: auto)")
    p.add_argument("--check", action="store_true", help="exit 1 when an average misses its pass mark")
    p.add_argument("--save", type=Path, help="folder for each run's transcript lines and recorder data "
                   "(`tico-recorder-cli relabel` re-runs speaker labels on that data for free)")
    a = p.parse_args()
    if not os.environ.get("TICO_RECORDER_STT_KEY"):
        sys.exit("Set TICO_RECORDER_STT_KEY (an OpenRouter key). Nothing ran.")
    if not a.cli.exists():
        subprocess.run(["cargo", "build", "--release", "--features", "cli,diarize", "--bin", "tico-recorder-cli"],
                       cwd=HERE.parent, check=True)
    known = {m: (who, s, e) for m, who, s, e in MEETINGS}
    rows = []
    for item in a.meetings.split(","):
        meeting, _, span = item.partition(":")
        me, start, end = known.get(meeting, (None, 300, 780))
        if span:
            start, end = map(float, span.split("-"))
        fetch(a.data, meeting)
        ref = reference(a.data, meeting, start, end)
        if me is None:  # another AMI meeting: whoever talks most in the window is me
            me = max("ABCD", key=lambda agent: sum(w[0] == agent for w in ref))
        for scenario in a.scenarios.split(","):
            with tempfile.TemporaryDirectory() as tmp:
                mic, them = channels(a.data, meeting, me, ref, start, end, scenario)
                sf.write(f"{tmp}/me.wav", mic, RATE, subtype="PCM_16")
                sf.write(f"{tmp}/them.wav", them, RATE, subtype="PCM_16")
                command = [str(a.cli), "transcribe-meeting", "--me", f"{tmp}/me.wav", "--them", f"{tmp}/them.wav"]
                if a.model:
                    command += ["--model", a.model]
                command += ["--language", a.language]
                folder = Path(tmp) / "data"
                if a.save:
                    folder = a.save / f"{meeting}-{scenario}-data"
                    shutil.rmtree(folder, ignore_errors=True)
                run = subprocess.run(command, env={**os.environ, "TICO_RECORDER_DATA_DIR": str(folder)},
                                     capture_output=True, text=True)
                if run.returncode:
                    sys.exit(run.stderr)
                out = json.loads(run.stdout)
            if a.save:
                a.save.mkdir(parents=True, exist_ok=True)
                (a.save / f"{meeting}-{scenario}.json").write_text(json.dumps(out, indent=1))
            row = {"meeting": meeting, "scenario": scenario, "model": out["model"], **score(ref, out["lines"], me),
                   "errors": len(out["errors"]), "seconds": round(out["total_seconds"])}
            rows.append(row)
            print(json.dumps(row), flush=True)
    failed = any(row["errors"] for row in rows)
    for scenario in a.scenarios.split(","):
        group = [row for row in rows if row["scenario"] == scenario]
        mean = {k: round(sum(row[k] for row in group) / len(group), 3) for k in (*LIMITS, "voices")}
        misses = [k for k, limit in LIMITS.items() if mean[k] > limit]
        failed |= bool(misses)
        print(json.dumps({"scenario": scenario, "mean_of": len(group), **mean} | ({"over_limit": misses} if misses else {})), flush=True)
    sys.exit(1 if a.check and failed else 0)


if __name__ == "__main__":
    main()
