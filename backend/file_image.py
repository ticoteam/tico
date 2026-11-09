"""Restricted image decoder, invoked only in a short-lived subprocess."""

import json
import resource
import sys
import warnings


def main():
    memory = 1024 * 1024**2
    # macOS rejects memory rlimits; the parent enforces its RSS budget there.
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024**2, 10 * 1024**2))
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = 50_000_000
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    with Image.open(sys.argv[1], formats=["PNG", "JPEG", "GIF", "WEBP"]) as opened:
        if opened.width * opened.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError("Image exceeds 50 MP")
        opened.seek(0)
        preview = ImageOps.exif_transpose(opened)
        width, height = preview.size
        preview.thumbnail((480, 480))
        preview.convert("RGB").save(sys.argv[2], "JPEG")
    print(json.dumps([width, height]))


if __name__ == "__main__":
    main()
