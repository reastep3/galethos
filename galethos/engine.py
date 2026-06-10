"""Scan pipeline shared by the CLI and the web UI.

run_scan() produces a ScanResult of plain JSON-safe dataclasses: ranked
opportunities, refused-bet warnings, per-event model-vs-market views, and
holdings advice when API auth is configured.
"""

import datetime as dt
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from .config import Config
from .kalshi import Kalshi, KalshiError, best_quotes, event_date
from .model import build_distribution
from .stations import SERIES, series_for_ticker
from .strategy import (Opportunity, PositionAdvice, advise_position,
                       find_opportunities, implied_event_center, market_label,
                       size_opportunities, strike_label)
from .weather import WeatherCache


@dataclass
class BracketRow:
    ticker: str
    label: str
    yes_bid: float | None
    yes_ask: float | None
    model_p: float
    flag: str = ""           # "yes" | "no" | "" — recommended buy side


@dataclass
class EventView:
    event_ticker: str
    station: str
    sid: str
    kind: str
    date: str                # ISO
    date_label: str          # "Wed Jun 10"
    median: float
    nws_anchor: float | None
    obs_extreme: float | None
    divergence: float | None
    notes: list[str]
    low_confidence: bool
    same_day: bool
    brackets: list[BracketRow] = field(default_factory=list)


@dataclass
class ScanResult:
    at: str                  # ISO UTC
    duration_s: float
    bankroll: float
    bankroll_source: str
    min_edge: float
    kelly_fraction: float
    opportunities: list[Opportunity]
    warnings: list[str]
    events: list[EventView]
    holdings: list[PositionAdvice]
    holdings_note: str = ""  # why holdings are absent/partial, "" if fine

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_bankroll(cfg: Config, k: Kalshi) -> tuple[float, str]:
    if k.can_auth and cfg.use_balance:
        try:
            return k.balance(), "live Kalshi balance"
        except KalshiError:
            return cfg.bankroll, "config (balance fetch failed)"
    return cfg.bankroll, "config"


def _collect_markets(k: Kalshi, progress, only_series: str | None) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = defaultdict(list)
    tickers = [only_series] if only_series else list(SERIES)
    for i, st in enumerate(tickers, 1):
        progress(f"scanning Kalshi weather series {i}/{len(tickers)} ({st})")
        try:
            for m in k.open_markets(st):
                events[m["event_ticker"]].append(m)
        except KalshiError:
            continue
    return events


def run_scan(k: Kalshi, cfg: Config, bankroll: float, bankroll_source: str, *,
             only_series: str | None = None, only_date: str | None = None,
             include_holdings: bool = True, progress=lambda msg: None) -> ScanResult:
    t0 = time.monotonic()
    events = _collect_markets(k, progress, only_series)

    # Resolve each event to (station, kind, date); drop unmapped/undated ones.
    targets = {}
    for ev, markets in events.items():
        mapped = series_for_ticker(ev)
        date = event_date(ev)
        if not mapped or not date:
            continue
        if only_date and date.isoformat() != only_date:
            continue
        _, station, kind = mapped
        targets[ev] = (station, kind, date, markets)

    wx = WeatherCache(cfg.ensemble_models)
    dists, books = {}, {}
    for i, (ev, (station, kind, date, markets)) in enumerate(sorted(targets.items()), 1):
        progress(f"modeling {ev} ({i}/{len(targets)})")
        wx.ensemble(station, date)
        dist = build_distribution(station, date, kind, wx, cfg)
        if dist is None:
            continue
        dists[ev] = dist
        for m in markets:
            try:
                books[m["ticker"]] = k.orderbook(m["ticker"])
            except KalshiError:
                books[m["ticker"]] = {"yes": [], "no": []}

    all_opps, warnings, views = [], [], []
    for ev, (station, kind, date, markets) in sorted(targets.items(),
                                                     key=lambda x: (x[1][2], x[0])):
        dist = dists.get(ev)
        if dist is None:
            continue
        markets = sorted(markets, key=lambda m: m.get("floor_strike") or m.get("cap_strike") or 0)
        mb = [(m, books.get(m["ticker"], {"yes": [], "no": []})) for m in markets]
        quotes = [(m, best_quotes(b)) for m, b in mb]
        center = implied_event_center(quotes)
        divergence = (dist.median_value() - center) if center is not None else None
        label_fn = lambda m, s=station.name, k2=kind, d=date: market_label(m, s, k2, d)
        opps, warns = find_opportunities(ev, label_fn, mb, dist, cfg, divergence)
        all_opps.extend(opps)
        warnings.extend(warns)

        brackets = []
        for m, q in quotes:
            p = dist.prob_market(m.get("strike_type"), m.get("floor_strike"), m.get("cap_strike"))
            if p is None:
                continue
            brackets.append(BracketRow(ticker=m["ticker"], label=strike_label(m),
                                       yes_bid=q["yes_bid"], yes_ask=q["yes_ask"], model_p=p))
        views.append(EventView(
            event_ticker=ev, station=station.name, sid=station.sid, kind=kind,
            date=date.isoformat(), date_label=f"{date:%a %b %-d}",
            median=round(dist.median_value(), 1), nws_anchor=dist.nws_anchor,
            obs_extreme=dist.obs_extreme, divergence=divergence,
            notes=dist.notes, low_confidence=dist.low_confidence,
            same_day=dist.same_day, brackets=brackets,
        ))

    sized = size_opportunities(all_opps, bankroll, cfg)
    rec = {(o.ticker, o.side) for o in sized}
    for v in views:
        for b in v.brackets:
            b.flag = "yes" if (b.ticker, "yes") in rec else ("no" if (b.ticker, "no") in rec else "")

    holdings, note = [], ""
    if include_holdings:
        if k.can_auth:
            holdings, note = collect_holdings(k, cfg, wx, progress)
        else:
            note = "no Kalshi API key configured — holdings not tracked"

    return ScanResult(
        at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        duration_s=round(time.monotonic() - t0, 1),
        bankroll=bankroll, bankroll_source=bankroll_source,
        min_edge=cfg.min_edge, kelly_fraction=cfg.kelly_fraction,
        opportunities=sized, warnings=warnings, events=views,
        holdings=holdings, holdings_note=note,
    )


def collect_holdings(k: Kalshi, cfg: Config, wx: WeatherCache | None = None,
                     progress=lambda msg: None) -> tuple[list[PositionAdvice], str]:
    wx = wx or WeatherCache(cfg.ensemble_models)
    try:
        positions = [p for p in k.positions() if int(p.get("position") or 0) != 0]
    except KalshiError as e:
        return [], f"holdings unavailable: {e}"
    advices = []
    for i, pos in enumerate(positions, 1):
        progress(f"evaluating position {i}/{len(positions)}")
        ticker = pos["ticker"]
        try:
            market = k.market(ticker)
            book = k.orderbook(ticker)
        except KalshiError:
            market, book = {"title": ticker}, None
        dist = None
        mapped = series_for_ticker(ticker)
        date = event_date(market.get("event_ticker") or ticker)
        if mapped and date:
            _, station, kind = mapped
            try:
                wx.ensemble(station, date)
                dist = build_distribution(station, date, kind, wx, cfg)
            except Exception:
                dist = None
        advices.append(advise_position(pos, market, book, dist, cfg))
    return advices, ""
