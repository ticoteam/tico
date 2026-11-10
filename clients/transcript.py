"""The stored forms of a meeting transcript, shared by the cloud store and the importers.

`raw_transcript` writes one line per turn; `readable_transcript` is formatting only: every
spoken word is preserved and long turns are broken into paragraphs at sentence boundaries. The
cloud keeps both (`backend/meetings.py`). Pure: no files, no clock, no network.
"""

import re


def stamp(ms):
    """[mm:ss], or [h:mm:ss] past an hour."""
    seconds = max(0, int(ms or 0)) // 1000
    if seconds >= 3600:
        return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)
    return "%02d:%02d" % (seconds // 60, seconds % 60)


def raw_transcript(turns):
    """The original transcript: one line per turn, speaker label then [mm:ss]. No words changed."""
    lines = []
    for turn in turns or []:
        text = str((turn or {}).get("text") or "").strip()
        if not text:
            continue
        speaker = str(turn.get("speaker") or "").strip()
        lines.append(("**" + speaker + "** " if speaker else "") + "[" + stamp(turn.get("start_ms")) + "] " + text)
    return "\n".join(lines)


def readable_transcript(raw):
    """Formatting only: preserve every spoken word; scope letters to diarization chunks."""
    parts = raw.split('---', 2)
    header, text = ('# Transcript\n', parts[2]) if len(parts) == 3 and not parts[0].strip() else ('# Transcript\n', raw)
    out = [header, '> Speaker labels are local to each segment. Calendar invitees are not verified speakers.\n']
    segment = 1
    for line in text.strip().splitlines():
        if line.strip() == '[speaker labels restart]':
            segment += 1
            out.append(f'\n---\n\n### Segment {segment}\n')
            continue
        match = re.match(r'\*\*([^*]+)\*\*\s*\[([^]]+)\]\s*(.*)', line)
        if not match:
            out.append(line)
            continue
        speaker, stamp, words = match.groups()
        out.append(f'\n**Segment {segment} · Speaker {speaker} · [{stamp}]**\n')
        # Break at sentence boundaries, without editing words, numbers or punctuation.
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', words)
        paragraph = []
        for sentence in sentences:
            paragraph.append(sentence)
            if len(' '.join(paragraph).split()) >= 90:
                out.append(' '.join(paragraph) + '\n')
                paragraph = []
        if paragraph:
            out.append(' '.join(paragraph) + '\n')
    return '\n'.join(out).strip() + '\n'
