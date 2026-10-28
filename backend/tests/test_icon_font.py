"""The UI's icon font is a subset: an icon missing from it renders as its raw name."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("build_icon_font", ROOT / "scripts" / "build-icon-font.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


def test_every_icon_in_use_is_in_the_font_manifest():
    used = set(build.used_icons())
    listed = set(build.read_manifest())
    assert used, "the scanner found no icons; it has drifted from how the UI renders them"
    assert not used - listed, "icons used but not in the font; run scripts/build-icon-font.py: %s" % sorted(used - listed)
    assert not listed - used, "icons.txt lists icons nothing uses; run scripts/build-icon-font.py: %s" % sorted(listed - used)



def test_every_url_of_the_font_carries_its_hash():
    # A cache that kept the old subset under the same URL would show an icon added since as its name.
    assert not build.stale_font_urls(), "run scripts/build-icon-font.py --stamp"
