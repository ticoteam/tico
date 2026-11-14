"""Fit one smooth counter for the plain zero from the two counter pieces of the default zero.

The default zero's counter is two closed contours (lower-left and upper-right) split by the
slash. Walking each piece the long way round, and joining the two across the slash's two end
caps, gives one closed loop. The loop is resampled, lightly smoothed to round the cap corners,
then written back as quadratic off-curve points about CONTROL_SPACING apart. A quadratic
B-spline through such points follows the loop closely; fit_counter reports the largest gap.
"""

import math

STEP = 3.0  # resampling step along the loop, font units
SIGMA = 14.0  # Gaussian smoothing width, font units
CONTROL_COUNT = 58  # off-curve control points in the counter (about 25 font units apart). Fixed, so every
# weight master has the same point count and the variation deltas line up point for point.


def _quad(p0, c, p1, steps):
    out = []
    for k in range(1, steps + 1):
        t = k / steps
        a, b, d = (1 - t) ** 2, 2 * (1 - t) * t, t * t
        out.append((a * p0[0] + b * c[0] + d * p1[0], a * p0[1] + b * c[1] + d * p1[1]))
    return out


def flatten(run, steps=10):
    """Polyline for a run of TrueType points (x, y, on) that starts and ends on-curve."""
    cur = (run[0][0], run[0][1])
    ctrl = None
    out = [cur]
    for x, y, on in run[1:]:
        p = (x, y)
        if on:
            if ctrl is None:
                out.append(p)
            else:
                out.extend(_quad(cur, ctrl, p, steps))
            cur, ctrl = p, None
        else:
            if ctrl is not None:
                mid = ((ctrl[0] + p[0]) / 2, (ctrl[1] + p[1]) / 2)
                out.extend(_quad(cur, ctrl, mid, steps))
                cur = mid
            ctrl = p
    return out


def _resample(pts, count):
    n = len(pts)
    cum = [0.0]
    for i in range(n):
        cum.append(cum[-1] + math.dist(pts[i], pts[(i + 1) % n]))
    total = cum[-1]
    out, j = [], 0
    for k in range(count):
        s = total * k / count
        while cum[j + 1] < s:
            j += 1
        seg = cum[j + 1] - cum[j]
        t = (s - cum[j]) / seg if seg else 0.0
        a, b = pts[j], pts[(j + 1) % n]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out, total


def _smooth(pts, sigma_samples):
    n = len(pts)
    r = int(3 * sigma_samples) + 1
    offsets = range(-r, r + 1)
    weights = [math.exp(-0.5 * (d / sigma_samples) ** 2) for d in offsets]
    out = []
    for i in range(n):
        sx = sy = sw = 0.0
        for d, w in zip(offsets, weights, strict=True):
            p = pts[(i + d) % n]
            sx += w * p[0]
            sy += w * p[1]
            sw += w
        out.append((sx / sw, sy / sw))
    return out


def fit_counter(chain_a, chain_b, anchor, n_controls):
    """chain_a, chain_b: TrueType runs (x, y, on) going round each piece, as flatten() expects.

    Returns (x, y, on) integer points for one closed contour. The point nearest `anchor` is
    on-curve; the rest are off-curve. Also returns the largest distance (font units) between the
    original pieces' outline and the new curve, for checking the fit.
    """
    loop = flatten(chain_a) + flatten(chain_b)
    count = max(8, round(_perimeter(loop) / STEP))
    pts, total = _resample(loop, count)
    spacing = total / count
    pts = _smooth(pts, SIGMA / spacing)
    ctrls, _ = _resample(pts, n_controls)
    k = min(range(len(ctrls)), key=lambda i: math.dist(ctrls[i], anchor))
    ctrls = ctrls[k:] + ctrls[:k]
    points = [(round(x), round(y), i == 0) for i, (x, y) in enumerate(ctrls)]

    # Check: the new curve is the anchor, the off-curve controls, and the anchor again.
    run = [(points[0][0], points[0][1], True)] + [(x, y, False) for x, y, _ in points[1:]]
    run.append((points[0][0], points[0][1], True))
    curve = flatten(run, steps=12)
    gap = max(min(math.dist(q, c) for c in curve) for q in loop)
    return points, gap


def _perimeter(pts):
    return sum(math.dist(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))
