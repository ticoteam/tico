"""Default projections for a P&L line, from its posted history (spec 4.4): `recurring` for subscriptions, payroll and
scheduled bills, `trend` for revenue, `run_rate` for usage like AI services. A stored projection always wins; these
only fill the months nobody projected. Pure: the caller reads the history and passes the totals in."""
from collections.abc import Sequence
from fractions import Fraction

TREND_MONTHS = 6            # full months a trend is fitted to
RATE_DAYS = 30              # days of posted spend a run rate averages
USAGE_SUBTYPES = ("ai", "video")


def default_method(account_type: str, subtype: str) -> str:
    """Revenue trends; usage (AI services, video production) runs at its last 30 days; the rest recurs."""
    if account_type == "income":
        return "trend"
    if subtype in USAGE_SUBTYPES:
        return "run_rate"
    return "recurring"


def estimate(method: str, monthly: Sequence[int], recent: int, days: int, ahead: int) -> int | None:
    """One month's projected amount in minor units. `monthly` is each full month before it, oldest first (at most
    TREND_MONTHS); `recent` is the posted total of the last RATE_DAYS days; `days` is the month's length; `ahead` is
    how many months past the last full month this one is (1 for the month right after it). None without any posted
    history to go on."""
    if not any(monthly) and not recent:
        return None
    if method == "run_rate":
        return round(Fraction(recent * days, RATE_DAYS))
    if method == "trend":
        return max(0, round(_trend(list(monthly)[-TREND_MONTHS:], ahead)))
    return monthly[-1] if monthly else 0


def _trend(series: list[int], ahead: int) -> Fraction:
    """The least-squares line through the series, read `ahead` steps past its last point."""
    n = len(series)
    if n == 1:
        return Fraction(series[0])
    mean_x = Fraction(n - 1, 2)
    mean_y = Fraction(sum(series), n)
    cov = sum(((x - mean_x) * (y - mean_y) for x, y in enumerate(series)), Fraction(0))
    var = sum(((x - mean_x) * (x - mean_x) for x in range(n)), Fraction(0))
    return mean_y + cov / var * (n - 1 + ahead - mean_x)
