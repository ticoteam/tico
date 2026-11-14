"""Add an optional plain zero to Atkinson Hyperlegible Next.

The default zero keeps its slash. The plain zero is reached through the `ss09` feature
(CSS: font-feature-settings: "ss09" 1). Two glyphs are added:

- zero.plain     = the zero with the slash removed: the two counter pieces become one counter
                   (counter_fit.py). Same advance and outer ring as the default.
- zero.tf.plain  = the tabular zero (zero.tf is a composite of zero) rebuilt on zero.plain.

Variations: the plain zero varies with weight like the default. Its outer ring and phantom
points copy the default zero's variation deltas. Its counter is refitted at each peak of the
zero's variation regions, and the deltas are the difference from the default fit, point for point.
zero.tf.plain copies zero.tf's variation data (a composite of one component).

Usage: build_plain_zero.py <source.woff2> <out.woff2> [--attach]
  --attach lists ss09 in each script's language system. Browsers only apply a CSS feature setting
  that the font lists there. The default rendering is unchanged either way.
Source: the Latin subset vendored in ui/vendor/fonts. Output keeps the OFL licence
(see LICENSE-atkinson-hyperlegible-next.txt); the family name is changed because the file is modified.
"""

import copy
import sys
from bisect import bisect_left

from fontTools.otlLib.builder import buildSingleSubstSubtable
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables as ot
from fontTools.ttLib.tables._g_l_y_f import GlyphCoordinates
from fontTools.ttLib.tables.ttProgram import Program
from fontTools.ttLib.tables.TupleVariation import TupleVariation

from counter_fit import CONTROL_COUNT, fit_counter

SRC, OUT = sys.argv[1], sys.argv[2]
ATTACH_TO_LANGSYS = "--attach" in sys.argv[3:]
NEW_FAMILY = "Atkinson Hyperlegible Next Plain Zero"

f = TTFont(SRC)
f.recalcTimestamp = False  # the same input builds the same bytes
glyf, hmtx, gvar = f["glyf"], f["hmtx"], f["gvar"]
order = list(f.getGlyphOrder())

zero = glyf["zero"]
assert zero.numberOfContours == 3, "zero should have three contours: outer ring and two counter pieces"
coords, ends, flags = zero.getCoordinates(glyf)
coords = [tuple(c) for c in coords]
flags = [int(x) for x in flags]
starts = [0] + [e + 1 for e in ends[:-1]]
OUTER = list(range(starts[0], ends[0] + 1))  # 0..15
LOWER_LEFT = list(range(starts[1], ends[1] + 1))  # counter piece 1
UPPER_RIGHT = list(range(starts[2], ends[2] + 1))  # counter piece 2

assert coords[LOWER_LEFT[3]] == (443, 116) and coords[LOWER_LEFT[4]] == (176, 501)
assert coords[UPPER_RIGHT[0]] == (211, 561) and coords[UPPER_RIGHT[1]] == (476, 180)


def fit_at(pts):
    """Fit the one-piece counter to the zero's points at one location (default or a region peak)."""
    chain_lower_left = [
        (pts[LOWER_LEFT[i]][0], pts[LOWER_LEFT[i]][1], bool(flags[LOWER_LEFT[i]] & 1))
        for i in [4, 5, 6, 7, 8, 9, 10, 0, 1, 2, 3]
    ]
    chain_upper_right = [
        (pts[UPPER_RIGHT[i]][0], pts[UPPER_RIGHT[i]][1], bool(flags[UPPER_RIGHT[i]] & 1))
        for i in list(range(1, 12)) + [0]
    ]
    return fit_counter(chain_lower_left, chain_upper_right, anchor=pts[LOWER_LEFT[0]], n_controls=CONTROL_COUNT)


# 1. zero.plain: default outline.
default_counter, default_gap = fit_at(coords)
print(
    "default counter: %d points, largest gap from the original pieces %.1f units" % (len(default_counter), default_gap)
)
new_coords = [coords[i] for i in OUTER] + [(x, y) for x, y, _ in default_counter]
new_flags = [flags[i] for i in OUTER] + [1 if on else 0 for _, _, on in default_counter]
new_ends = [len(OUTER) - 1, len(new_coords) - 1]

plain = copy.deepcopy(zero)
plain.coordinates = GlyphCoordinates(new_coords)
plain.flags = bytearray(new_flags)
plain.endPtsOfContours = new_ends
plain.numberOfContours = len(new_ends)
plain.recalcBounds(glyf)
plain.program = Program()
plain.program.fromBytecode(b"")  # the default zero's hinting refers to the slash points; drop it
glyf["zero.plain"] = plain
hmtx["zero.plain"] = hmtx["zero"]

# 2. zero.plain variations: one tuple per region of zero, with the counter refitted at that peak.
new_tuples = []
for tv in gvar.variations["zero"]:
    deltas = list(tv.coordinates)
    assert all(d is not None for d in deltas), "explicit deltas expected"
    at_peak = [(coords[i][0] + deltas[i][0], coords[i][1] + deltas[i][1]) for i in range(len(coords))]
    counter_at_peak, gap = fit_at(at_peak)
    print("  region %s: counter gap %.1f units" % (dict(tv.axes), gap))
    counter_deltas = [(x - dx, y - dy) for (x, y, _), (dx, dy, _) in zip(counter_at_peak, default_counter, strict=True)]
    outer_deltas = [deltas[i] for i in OUTER]
    phantom_deltas = deltas[len(coords) :]
    new_tuples.append(TupleVariation(dict(tv.axes), outer_deltas + counter_deltas + phantom_deltas))
gvar.variations["zero.plain"] = new_tuples

# 3. zero.tf.plain: the tabular zero, rebuilt on zero.plain. Same metrics and variations as zero.tf.
tf = copy.deepcopy(glyf["zero.tf"])
assert tf.isComposite() and [c.glyphName for c in tf.components] == ["zero"]
tf.components[0].glyphName = "zero.plain"
glyf["zero.tf.plain"] = tf
hmtx["zero.tf.plain"] = hmtx["zero.tf"]
gvar.variations["zero.tf.plain"] = copy.deepcopy(gvar.variations["zero.tf"])

# The advance-width map: the new glyphs take the advance variations of the glyphs they replace.
hvar = f["HVAR"].table
adv = hvar.AdvWidthMap.mapping
adv["zero.plain"] = adv["zero"]
adv["zero.tf.plain"] = adv["zero.tf"]

order += ["zero.plain", "zero.tf.plain"]
f.setGlyphOrder(order)
glyf.glyphOrder = order
f["maxp"].numGlyphs = len(order)
gvar.glyphCount = len(order)

# 4. GSUB: a new single-substitution lookup, under a new feature ss09 (sorted by tag).
gsub = f["GSUB"].table
lookup_index = len(gsub.LookupList.Lookup)
sub = buildSingleSubstSubtable({"zero": "zero.plain", "zero.tf": "zero.tf.plain"})
lookup = ot.Lookup()
lookup.LookupType = 1
lookup.LookupFlag = 0
lookup.SubTable = [sub]
lookup.SubTableCount = 1
gsub.LookupList.Lookup.append(lookup)
gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)

fl = gsub.FeatureList
tags = [r.FeatureTag for r in fl.FeatureRecord]
pos = bisect_left(tags, "ss09")
rec = ot.FeatureRecord()
rec.FeatureTag = "ss09"
rec.Feature = ot.Feature()
rec.Feature.FeatureParams = None
rec.Feature.LookupListIndex = [lookup_index]
rec.Feature.LookupCount = 1
fl.FeatureRecord.insert(pos, rec)
fl.FeatureCount = len(fl.FeatureRecord)


def remap(i):
    return i + 1 if i >= pos else i


if getattr(gsub, "FeatureVariations", None):
    raise SystemExit("FeatureVariations present: remap it before using this script")
for sr in gsub.ScriptList.ScriptRecord:
    systems = [sr.Script.DefaultLangSys] + [lr.LangSys for lr in sr.Script.LangSysRecord]
    for ls in systems:
        if ls is None:
            continue
        ls.FeatureIndex = [remap(i) for i in ls.FeatureIndex] + ([pos] if ATTACH_TO_LANGSYS else [])
        ls.FeatureCount = len(ls.FeatureIndex)

# 5. Rename the modified file so it does not claim to be the original.
name = f["name"]
for nid, value in {
    1: NEW_FAMILY,
    3: NEW_FAMILY + ";modified",
    4: NEW_FAMILY,
    6: NEW_FAMILY.replace(" ", "") + "-PlainZero",
}.items():
    name.setName(value, nid, 3, 1, 0x409)
    name.setName(value, nid, 1, 0, 0)
name.removeNames(nameID=16)
name.removeNames(nameID=17)

f.flavor = "woff2"
f.save(OUT)
print("wrote", OUT, "glyphs:", len(order), "ss09 lookup:", lookup_index, "feature index:", pos)
