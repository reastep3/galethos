"""Edge math: Kalshi fees, EV after fees, fractional Kelly sizing, exposure caps,
and sell-vs-hold advice for open positions. Cost basis is sunk — sell decisions
compare what the market pays now vs the model's expected settlement value."""

import math
from dataclasses import dataclass, field

from .kalshi import buy_depth, sell_depth
from .model import TempDistribution


def fee_per_contract(price: float, fee_rate: float) -> float:
    """Kalshi quadratic taker fee, rounded up to the cent (per contract)."""
    return math.ceil(fee_rate * price * (1 - price) * 100) / 100


@dataclass
class Opportunity:
    event_ticker: str
    ticker: str
    label: str           # e.g. "NYC high Jun 10: 77° to 78°"
    side: str            # "yes" | "no"
    prob: float          # model P(side wins)
    ask: float
    fee: float
    cost: float          # ask + fee
    edge: float          # prob - cost
    kelly: float         # full-Kelly fraction of bankroll
    contracts: int = 0
    outlay: float = 0.0
    exp_profit: float = 0.0
    depth: float = 0.0
    notes: list[str] = field(default_factory=list)


def strike_label(market: dict) -> str:
    """Human bracket text; newer series leave subtitle empty so derive from strikes."""
    if market.get("subtitle"):
        return market["subtitle"]
    st, f, c = market.get("strike_type"), market.get("floor_strike"), market.get("cap_strike")
    fmt = lambda v: f"{v:g}"
    if st == "between" and f is not None and c is not None:
        return f"{fmt(f)}° to {fmt(c)}°"
    if st in ("greater", "greater_or_equal") and f is not None:
        return f"{fmt(f + 1 if float(f).is_integer() else f)}° or above"
    if st in ("less", "less_or_equal") and c is not None:
        return f"{fmt(c - 1 if float(c).is_integer() else c)}° or below"
    return market["ticker"]


def market_label(market: dict, station_name: str, kind: str, date) -> str:
    return f"{station_name} {kind} {date:%b %-d}: {strike_label(market)}"


def implied_event_center(rows: list[tuple[dict, dict]]) -> float | None:
    """Market-implied central temp for an event from bracket mid prices.
    rows: [(market, book quotes with yes_bid/yes_ask)]"""
    weighted, total = 0.0, 0.0
    for m, q in rows:
        if m.get("strike_type") != "between":
            continue
        yb, ya = q.get("yes_bid"), q.get("yes_ask")
        if yb is None or ya is None:
            continue
        mid = (yb + ya) / 2
        if mid < 0.03:
            continue
        center = (m["floor_strike"] + m["cap_strike"]) / 2
        weighted += center * mid
        total += mid
    return weighted / total if total > 0.10 else None


def find_opportunities(event_ticker: str, label_fn, markets_books: list[tuple[dict, dict]],
                       dist: TempDistribution, cfg,
                       divergence: float | None) -> tuple[list[Opportunity], list[str]]:
    """Returns (opportunities, warnings). Extreme model/market disagreement is a
    refuse-and-warn, not a bet: it usually means a wrong station mapping, a stale
    forecast, or the market watching live data we don't have."""
    opps, warnings = [], []
    if divergence is not None and abs(divergence) > cfg.divergence_warn:
        warnings.append(f"{event_ticker}: model median is {divergence:+.1f}°F vs market "
                        "center — refusing to bet this event; verify station/forecast")
        return opps, warnings
    for market, book in markets_books:
        q = dist.prob_market(market.get("strike_type"),
                             market.get("floor_strike"), market.get("cap_strike"))
        if q is None:
            continue
        for side in ("yes", "no"):
            p_win = q if side == "yes" else 1 - q
            ask, depth = buy_depth(book, side, cfg.max_slippage)
            if ask is None or not 0.01 < ask < 0.97:
                continue
            fee = fee_per_contract(ask, cfg.fee_rate)
            cost = ask + fee
            edge = p_win - cost
            if edge < cfg.min_edge:
                continue
            if p_win - ask > cfg.max_disagreement:
                warnings.append(f"{label_fn(market)} {side.upper()}: model {p_win:.0%} vs "
                                f"market {ask:.0%} — too far apart to trust, skipped")
                continue
            kelly = edge / (1 - cost)
            notes = list(dist.notes)
            if dist.same_day:
                # Intraday, the market is watching live obs at finer resolution
                # than our hourly data; respect that.
                kelly *= 0.5
                notes.append("same-day market — half Kelly")
            if dist.low_confidence:
                kelly *= 0.5
                notes.append("low confidence — half Kelly")
            opps.append(Opportunity(
                event_ticker=event_ticker, ticker=market["ticker"],
                label=label_fn(market), side=side, prob=p_win, ask=ask,
                fee=fee, cost=cost, edge=edge, kelly=kelly, depth=depth, notes=notes,
            ))
    return opps, warnings


def size_opportunities(opps: list[Opportunity], bankroll: float, cfg) -> list[Opportunity]:
    """Allocate bankroll best-edge-first under per-event and total exposure caps."""
    opps = sorted(opps, key=lambda o: o.edge, reverse=True)
    spent_total = 0.0
    spent_event: dict[str, float] = {}
    sized = []
    for o in opps:
        stake = bankroll * cfg.kelly_fraction * o.kelly
        n = int(min(stake / o.cost, o.depth))
        room_event = bankroll * cfg.per_event_cap - spent_event.get(o.event_ticker, 0.0)
        room_total = bankroll * cfg.max_total_exposure - spent_total
        n = int(min(n, max(room_event, 0) / o.cost, max(room_total, 0) / o.cost))
        if n < cfg.min_contracts:
            continue
        o.contracts = n
        o.outlay = round(n * o.cost, 2)
        o.exp_profit = round(n * o.edge, 2)
        spent_total += o.outlay
        spent_event[o.event_ticker] = spent_event.get(o.event_ticker, 0.0) + o.outlay
        sized.append(o)
    return sized


@dataclass
class PositionAdvice:
    ticker: str
    title: str
    side: str
    qty: int
    avg_cost: float | None
    bid: float | None
    model_p: float | None
    sell_value: float | None   # per contract, after fee
    hold_ev: float | None      # per contract = P(win)
    action: str                # SELL | HOLD | ADD? | NO MODEL
    reason: str


def advise_position(pos: dict, market: dict, book: dict | None,
                    dist: TempDistribution | None, cfg) -> PositionAdvice:
    n = int(pos.get("position") or 0)
    side = "yes" if n > 0 else "no"
    qty = abs(n)
    exposure = pos.get("market_exposure_dollars")
    if exposure is None and pos.get("market_exposure") is not None:
        exposure = pos["market_exposure"] / 100.0
    avg_cost = round(float(exposure) / qty, 2) if exposure and qty else None
    title = market.get("title") or pos["ticker"]

    bid = None
    if book:
        bid, _ = sell_depth(book, side, cfg.max_slippage)

    if dist is None:
        return PositionAdvice(pos["ticker"], title, side, qty, avg_cost, bid,
                              None, None, None, "NO MODEL",
                              "not a modeled weather market — judge manually")

    q = dist.prob_market(market.get("strike_type"),
                         market.get("floor_strike"), market.get("cap_strike"))
    if q is None:
        return PositionAdvice(pos["ticker"], title, side, qty, avg_cost, bid,
                              None, None, None, "NO MODEL", "unparseable strikes")

    p_win = q if side == "yes" else 1 - q
    if bid is None:
        return PositionAdvice(pos["ticker"], title, side, qty, avg_cost, None,
                              p_win, None, p_win, "HOLD", "no bids to sell into")

    sell_value = bid - fee_per_contract(bid, cfg.fee_rate)
    hold_ev = p_win
    delta = sell_value - hold_ev
    if delta >= cfg.sell_margin:
        action, reason = "SELL", (f"market pays {sell_value:.2f} vs model EV {hold_ev:.2f} "
                                  f"(+{delta:.2f}/contract)")
    elif hold_ev - sell_value >= cfg.sell_margin:
        action, reason = "HOLD", (f"model EV {hold_ev:.2f} beats exit {sell_value:.2f} "
                                  f"(+{hold_ev - sell_value:.2f}/contract)")
    else:
        action, reason = "HOLD", f"fair: exit {sell_value:.2f} ≈ model EV {hold_ev:.2f}"
    if dist.low_confidence:
        reason += " — low confidence model"
    return PositionAdvice(pos["ticker"], title, side, qty, avg_cost, bid,
                          p_win, sell_value, hold_ev, action, reason)
