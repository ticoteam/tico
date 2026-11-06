"""Any transcript an importer can hand over, as one list of segments.

    parse(data, fmt="auto") -> [{"speaker", "start_ms", "end_ms", "text"}, ...]

`data` is the file's text, or a list of segment objects already decoded from JSON. `fmt` is
"auto", "text", "vtt", "srt" or "json"; auto looks at the first bytes. Every format lands as the
same segments, in file order, so the store and the UI never learn where a transcript came from.
Pure: no files, no clock, no network. Times are integer milliseconds; a format that has no times
(plain text) gives each segment the time of the last one that did.

  text   one line per turn. A leading [mm:ss], [h:mm:ss] or (mm:ss) is its time and "Name: text"
         its speaker. Blank lines separate nothing; every non-empty line is a turn.
  vtt    WebVTT cues. The speaker is a <v Name> voice tag or a leading "Name: " (Zoom, Teams).
  srt    SubRip blocks; the same "Name: " convention.
  json   a list of {speaker, start, end, text}, or an object holding one under segments,
         transcript, utterances or results. start and end are seconds (a number, or "mm:ss" /
         "h:mm:ss[.fff]"); start_ms and end_ms are milliseconds. speaker may be speaker_name,
         name or a {name} object.
"""

import json
import re

FORMATS = ("auto", "text", "vtt", "srt", "json")
MAX_SEGMENT_TEXT = 20_000
DAY_MS = 24 * 60 * 60 * 1000


class TranscriptFormatError(ValueError):
    pass


CLOCK = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
CUE_TIME = re.compile(
    r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)
LEADING_TIME = re.compile(r"^[\[(]" + CLOCK + r"[\])]\s*")
SPEAKER = re.compile(r"^([^:\n]{1,60}):\s+(.+)$")
VOICE = re.compile(r"^<v(?:\.[^\s>]+)?\s+([^>]+)>(.*)$", re.S)
TAG = re.compile(r"</?[^>]+>")


def clock_ms(hours, minutes, seconds, fraction=None):
    millis = int((fraction or "0").ljust(3, "0")[:3])
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + millis


def seconds_ms(value):
    """A JSON time: seconds as a number, or a clock string."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if value < 0:
            raise TranscriptFormatError("A segment time is negative")
        return int(round(value * 1000))
    text = str(value).strip()
    if not text:
        return None
    match = re.fullmatch(CLOCK, text)
    if match:
        return clock_ms(*match.groups())
    try:
        return int(round(float(text) * 1000))
    except ValueError:
        raise TranscriptFormatError(f"Cannot read the time {text!r}") from None


def segment(speaker, start, end, text):
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_SEGMENT_TEXT]
    if not text:
        return None
    start = max(0, int(start or 0))
    end = max(start, int(end if end is not None else start))
    if start > DAY_MS or end > DAY_MS:
        raise TranscriptFormatError("A segment is past the 24-hour limit")
    return {
        "speaker": re.sub(r"\s+", " ", str(speaker or "")).strip()[:100],
        "start_ms": start,
        "end_ms": end,
        "text": text,
    }


def detect(text):
    head = text.lstrip("﻿ \t\r\n")
    if head.startswith("WEBVTT"):
        return "vtt"
    if head[:1] and head[0] in "[{":
        try:
            json.loads(head)
            return "json"
        except ValueError:
            pass
    if re.match(r"\d+\s*\n\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->", head):
        return "srt"
    return "text"


def parse_text(text):
    out, last = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        at = last
        match = LEADING_TIME.match(line)
        if match:
            at = clock_ms(*match.groups())
            line = line[match.end() :]
        speaker, words = "", line
        named = SPEAKER.match(line)
        if named and not named.group(1).strip().lower().startswith("http"):
            speaker, words = named.group(1), named.group(2)
        made = segment(speaker, at, at, words)
        if made:
            out.append(made)
            last = at
    return out


def cue_speaker(payload):
    """(speaker, words) from a cue's text: a <v Name> tag, else a leading "Name: "."""
    payload = payload.strip()
    voice = VOICE.match(payload)
    speaker = ""
    if voice:
        speaker, payload = voice.group(1), voice.group(2)
    payload = re.sub(r"\s+", " ", TAG.sub("", payload)).strip()
    if not speaker:
        named = SPEAKER.match(payload)
        if named and not named.group(1).strip().lower().startswith("http"):
            speaker, payload = named.group(1), named.group(2)
    return speaker, payload


def parse_cues(text, fmt):
    """Shared by WebVTT and SRT: a block is an optional id, a time line, then the words."""
    out = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿"))
    for block in blocks:
        lines = [line for line in block.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            continue
        if fmt == "vtt" and re.match(r"(WEBVTT|NOTE|STYLE|REGION)\b", lines[0]):
            continue
        for index, line in enumerate(lines):
            match = CUE_TIME.match(line)
            if match:
                g = match.groups()
                start, end = clock_ms(*g[:4]), clock_ms(*g[4:])
                speaker, words = cue_speaker(" ".join(part.strip() for part in lines[index + 1 :]))
                made = segment(speaker, start, end, words)
                if made:
                    out.append(made)
                break
        else:
            if fmt == "srt" and any(line.strip() for line in lines):
                raise TranscriptFormatError("An SRT block has no time line")
    return out


ROWS = ("segments", "transcript", "utterances", "results", "turns")


def rows_of(data):
    if isinstance(data, dict):
        for key in ROWS:
            if isinstance(data.get(key), list):
                return data[key]
        raise TranscriptFormatError("JSON transcript needs a list of segments (or one under segments)")
    if isinstance(data, list):
        return data
    raise TranscriptFormatError("JSON transcript must be a list of segments")


def parse_json(data):
    out = []
    for row in rows_of(data):
        if not isinstance(row, dict):
            raise TranscriptFormatError("Every JSON segment is an object")
        speaker = row.get("speaker", row.get("speaker_name", row.get("name")))
        if isinstance(speaker, dict):
            speaker = speaker.get("name") or speaker.get("email") or ""
        if row.get("start_ms") is not None or row.get("end_ms") is not None:
            start, end = row.get("start_ms"), row.get("end_ms")
            start = 0 if start is None else int(start)
            end = None if end is None else int(end)
        else:
            start = seconds_ms(row.get("start", row.get("start_time", row.get("timestamp"))))
            end = seconds_ms(row.get("end", row.get("end_time")))
        made = segment(speaker, start or 0, end, row.get("text", row.get("content", row.get("sentence"))))
        if made:
            out.append(made)
    return out


def parse(data, fmt="auto"):
    """Segments from `data`; TranscriptFormatError when nothing in it reads as a transcript."""
    if fmt not in FORMATS:
        raise TranscriptFormatError("format is one of " + ", ".join(FORMATS))
    if isinstance(data, (list, dict)):
        segments = parse_json(data)
    else:
        text = (data.decode("utf-8-sig") if isinstance(data, bytes) else str(data or "")).lstrip("\ufeff")
        chosen = detect(text) if fmt == "auto" else fmt
        if chosen == "json":
            try:
                segments = parse_json(json.loads(text))
            except ValueError as exc:
                if isinstance(exc, TranscriptFormatError):
                    raise
                raise TranscriptFormatError("The transcript is not valid JSON") from None
        elif chosen in ("vtt", "srt"):
            segments = parse_cues(text, chosen)
        else:
            segments = parse_text(text)
    if not segments:
        raise TranscriptFormatError("No transcript text was found")
    return segments


def duration_ms(segments):
    return max((s["end_ms"] for s in segments), default=0)
