"""Helpers that other tests share, moved out of test_team_icon.py so they import no test file."""

import io
from PIL import Image


def image(kind="PNG", colour="red"):
    out = io.BytesIO()
    Image.new("RGB", (16, 16), colour).save(out, kind)
    return out.getvalue()
