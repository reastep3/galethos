"""Probability model for a daily temperature extreme.

Distribution = mixture of normals: one kernel per ensemble member's daily
max/min, recentered toward the NWS point forecast (which carries forecaster
knowledge of the settlement station — gridded models miss quirks like Central
Park reading several degrees cool in summer). For same-day markets the
observed running extreme truncates the distribution: the high can't come in
below what the station has already printed.

Settlement values are whole degrees F, so a bracket "81 to 82" is the
continuous interval (80.5, 82.5).
"""

import datetime as dt
import math
from dataclasses import dataclass, field
from statistics import median
from zoneinfo import ZoneInfo

from .config import Config
from .stations import Station
from .weather import WeatherCache


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class TempDistribution:
    samples: list[float]
    sigma: float
    kind: str                      # "high" | "low"
    notes: list[str] = field(default_factory=list)
    nws_anchor: float | None = None
    obs_extreme: float | None = None
    low_confidence: bool = False
    same_day: bool = False

    def cdf(self, x: float) -> float:
        if not self.samples:
            return 0.5
        return sum(_phi((x - s) / self.sigma) for s in self.samples) / len(self.samples)

    def prob_market(self, strike_type: str, floor: float | None, cap: float | None) -> float | None:
        """P(YES) for a Kalshi temp market. Integer strikes are inclusive bounds."""
        def lo_bound(f):   # ">= floor+1" in whole degrees
            return f + 0.5 if float(f).is_integer() else f

        def hi_bound(c):   # "<= cap-1" for 'less', inclusive cap for 'between'
            return c + 0.5 if float(c).is_integer() else c

        if strike_type == "between" and floor is not None and cap is not None:
            return self.cdf(hi_bound(cap)) - self.cdf(floor - 0.5 if float(floor).is_integer() else floor)
        if strike_type in ("greater", "greater_or_equal") and floor is not None:
            return 1.0 - self.cdf(lo_bound(floor))
        if strike_type in ("less", "less_or_equal") and cap is not None:
            return self.cdf(cap - 0.5 if float(cap).is_integer() else cap)
        return None

    def median_value(self) -> float:
        if not self.samples:
            return float("nan")
        lo, hi = min(self.samples) - 5 * self.sigma, max(self.samples) + 5 * self.sigma
        for _ in range(60):
            mid = (lo + hi) / 2
            if self.cdf(mid) < 0.5:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2


def build_distribution(st: Station, date: dt.date, kind: str,
                       wx: WeatherCache, cfg: Config) -> TempDistribution | None:
    now_local = dt.datetime.now(ZoneInfo(st.tz))
    today = now_local.date()
    lead_days = max((date - today).days, 0)
    notes: list[str] = []

    samples = wx.member_extremes(st, date, kind)
    if len(samples) < 10:
        return None
    bias = cfg.station_bias.get(st.sid, 0.0)
    if bias:
        samples = [s + bias for s in samples]
        notes.append(f"station bias {bias:+.1f}F")

    # Recenter ensemble toward the NWS point forecast for the settlement station.
    anchor = wx.nws_daily(st).get(date, {}).get(kind)
    low_confidence = False
    if anchor is not None:
        gap = anchor - median(samples)
        shift = cfg.nws_blend * gap
        samples = [s + shift for s in samples]
        if abs(shift) > 1.0:
            notes.append(f"recentered {shift:+.1f}F toward NWS {anchor:.0f}F")
        if abs(gap) > cfg.divergence_warn:
            # Our two sources disagree badly; whichever is wrong, we can't tell.
            low_confidence = True
            notes.append(f"ensemble vs NWS gap {gap:+.1f}F — low confidence")
    else:
        notes.append("no NWS anchor — raw ensemble only")
        low_confidence = True

    obs_extreme = None
    if date == today:
        obs = wx.obs_today(st)
        if obs:
            obs_extreme = obs["max"] if kind == "high" else obs["min"]
            # Re-derive member extremes from the remaining hours only, then clamp
            # by what the station has already recorded.
            rem = wx.member_extremes(st, date, kind, after_hour=now_local.hour)
            shift = (median(samples) - median(rem)) if rem else 0.0
            clamp = max if kind == "high" else min
            base = [r + shift for r in rem] if rem else samples
            samples = [clamp(obs_extreme, s) for s in base]
            notes.append(f"obs running {kind} {obs_extreme:.0f}F @ {obs['last']:%H:%M}")
        else:
            # Same-day market without live station data: traders watching the
            # thermometer know more than we do right now.
            low_confidence = True
            notes.append("no station obs for same-day market — low confidence")
    elif date < today:
        return None  # already resolved-in-fact, nothing to model

    sigma = cfg.kernel_sigma + cfg.sigma_per_day * lead_days
    if date == today and obs_extreme is not None and now_local.hour >= (16 if kind == "high" else 8):
        sigma = max(0.8, sigma * 0.5)  # the extreme is mostly locked in by late day
        notes.append("late-day: tightened sigma")

    return TempDistribution(samples=samples, sigma=sigma, kind=kind, notes=notes,
                            nws_anchor=anchor, obs_extreme=obs_extreme,
                            low_confidence=low_confidence, same_day=(date == today))
