"""Terminal report rendering (rich)."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def event_header(ev) -> str:
    bits = [f"model median [bold]{ev.median:.1f}°F[/]"]
    if ev.nws_anchor is not None:
        bits.append(f"NWS {ev.nws_anchor:.0f}°F")
    if ev.obs_extreme is not None:
        bits.append(f"obs running {ev.kind} {ev.obs_extreme:.1f}°F")
    if ev.divergence is not None:
        bits.append(f"mkt center Δ{ev.divergence:+.1f}°F")
    return (f"[bold cyan]{ev.station} — {ev.kind.upper()} — {ev.date_label}[/]   "
            + " · ".join(bits))


def brackets_table(rows) -> Table:
    """rows: list[engine.BracketRow]"""
    t = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
    t.add_column("bracket", min_width=14)
    t.add_column("yes bid/ask", justify="right")
    t.add_column("model P(yes)", justify="right")
    t.add_column("", min_width=8)
    for b in rows:
        quote = f"{_c(b.yes_bid)} / {_c(b.yes_ask)}"
        flag = f"BUY {b.flag.upper()}" if b.flag else ""
        t.add_row(b.label, quote, f"{b.model_p * 100:5.1f}%", flag,
                  style="bold green" if flag else None)
    return t


def _c(p) -> str:
    return f"{round(p * 100):d}¢" if p is not None else "—"


def recommendations_table(opps) -> Table:
    t = Table(title="RECOMMENDED BETS (best edge first)", title_style="bold green",
              header_style="bold")
    t.add_column("#", justify="right")
    t.add_column("market")
    t.add_column("side", justify="center")
    t.add_column("model P", justify="right")
    t.add_column("ask+fee", justify="right")
    t.add_column("edge", justify="right")
    t.add_column("buy", justify="right")
    t.add_column("outlay", justify="right")
    t.add_column("edge $ (if model right)", justify="right")
    for i, o in enumerate(opps, 1):
        t.add_row(
            str(i), o.label, o.side.upper(), f"{o.prob * 100:.0f}%",
            f"{_c(o.ask)}+{_c(o.fee)}", f"+{o.edge * 100:.0f}¢",
            f"{o.contracts}x", f"${o.outlay:.2f}", f"${o.exp_profit:.2f}",
        )
    return t


def holdings_table(advices) -> Table:
    t = Table(title="YOUR POSITIONS", title_style="bold cyan", header_style="bold")
    t.add_column("market")
    t.add_column("side", justify="center")
    t.add_column("qty", justify="right")
    t.add_column("avg cost", justify="right")
    t.add_column("bid now", justify="right")
    t.add_column("model P", justify="right")
    t.add_column("sell nets", justify="right")
    t.add_column("hold EV", justify="right")
    t.add_column("action", justify="center")
    t.add_column("why")
    for a in advices:
        style = {"SELL": "bold red", "HOLD": "green"}.get(a.action, "dim")
        t.add_row(
            a.title[:46], a.side.upper(), str(a.qty),
            _c(a.avg_cost) if a.avg_cost is not None else "—",
            _c(a.bid), f"{a.model_p * 100:.0f}%" if a.model_p is not None else "—",
            _c(a.sell_value) if a.sell_value is not None else "—",
            _c(a.hold_ev) if a.hold_ev is not None else "—",
            f"[{style}]{a.action}[/]", a.reason,
        )
    return t


def summary_panel(bankroll: float, source: str, opps) -> Panel:
    outlay = sum(o.outlay for o in opps)
    profit = sum(o.exp_profit for o in opps)
    body = (f"bankroll [bold]${bankroll:,.2f}[/] ({source})   "
            f"recommended outlay [bold]${outlay:,.2f}[/]   "
            f"model-implied edge [bold green]${profit:,.2f}[/]   "
            f"({len(opps)} bets)")
    return Panel(body, border_style="green" if opps else "dim")
