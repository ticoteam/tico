"""Every import format lands as the same segment list (clients/transcript_formats.py)."""
import unittest

from clients import transcript_formats as TF

VTT = """WEBVTT

NOTE made by a call recorder

1
00:00:05.000 --> 00:00:08.500
<v Ana>Let's raise the annual plan.</v>

2
00:01:10.250 --> 00:01:12.000
Ben: Ten percent, then.
Sounds fine.
"""

SRT = """1
00:00:05,000 --> 00:00:08,500
Ana: Let's raise the annual plan.

2
00:01:10,250 --> 00:01:12,000
Ten percent, then.
"""


class Formats(unittest.TestCase):

    def test_vtt_reads_voice_tags_prefixes_and_multiline_cues(self):
        got = TF.parse(VTT)
        self.assertEqual(got, [
            {"speaker": "Ana", "start_ms": 5000, "end_ms": 8500, "text": "Let's raise the annual plan."},
            {"speaker": "Ben", "start_ms": 70250, "end_ms": 72000, "text": "Ten percent, then. Sounds fine."}])

    def test_a_transcript_with_nothing_in_it_or_a_bad_shape_is_refused(self):
        for data, fmt in (("", "auto"), ("  \n\n", "text"), ("[]", "auto"), ("{}", "json"), ("[1, 2]", "json"),
                          ('[{"start": -1, "text": "x"}]', "json"), ('[{"start": "soon", "text": "x"}]', "json"),
                          ("1\nno time here\n", "srt"), ("[{oops", "json"), ("x", "docx")):
            with self.assertRaises(TF.TranscriptFormatError, msg=repr(data)):
                TF.parse(data, fmt)

if __name__ == "__main__":
    unittest.main()
