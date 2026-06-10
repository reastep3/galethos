"""galethos — Kalshi weather market scanner.

Commands:
  galethos            scan markets + advise on holdings (holdings need API key)
  galethos scan       market scan only (no auth required)
  galethos holdings   positions advice only
  galethos web        run the web dashboard (default port 8642)
  galethos discover   list open Climate & Weather series not yet mapped to stations
  galethos auth-test  verify API key signing works
"""

import argparse
import json
import sys

from rich.padding import Padding

from . import report
from .config import load_config
from .engine import collect_holdings, resolve_bankroll, run_scan
from .kalshi import Kalshi
from .stations import SERIES

console = report.console


def render_scan(result, cfg):
    for ev in result.events:
        console.print()
        console.print(report.event_header(ev))
        if ev.notes:
            console.print(f"[dim]  {' · '.join(ev.notes)}[/]")
        console.print(Padding(report.brackets_table(ev.brackets), (0, 0, 0, 2)))
    console.print()
    for w in result.warnings:
        console.print(f"[yellow]⚠ {w}[/]")
    if result.warnings:
        console.print()
    if result.opportunities:
        console.print(report.recommendations_table(result.opportunities))
    else:
        console.print("[yellow]no bets clear the edge threshold "
                      f"(min_edge={cfg.min_edge:.02f}, after fees)[/]")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="galethos", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "scan", "holdings", "web", "discover", "auth-test"])
    ap.add_argument("--bankroll", type=float, help="override bankroll dollars")
    ap.add_argument("--min-edge", type=float, help="override min edge (e.g. 0.04)")
    ap.add_argument("--kelly", type=float, help="override kelly fraction (e.g. 0.25)")
    ap.add_argument("--series", help="scan a single series ticker (e.g. KXHIGHNY)")
    ap.add_argument("--date", help="only events for YYYY-MM-DD")
    ap.add_argument("--port", type=int, default=8642, help="web: port to listen on")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.command == "web":
        from pathlib import Path
        root = str(Path(__file__).resolve().parent.parent)  # repo root holds app.py (ghoul-style)
        if root not in sys.path:
            sys.path.insert(0, root)
        from app import app
        app.run(host="127.0.0.1", port=args.port, debug=False)
        return 0

    if args.json:
        console.quiet = True  # keep stdout pure JSON
    cfg = load_config()
    if args.min_edge is not None:
        cfg.min_edge = args.min_edge
    if args.kelly is not None:
        cfg.kelly_fraction = args.kelly
    k = Kalshi(cfg.api_key_id, cfg.private_key_path, cfg.private_key_pem)

    if args.command == "discover":
        mapped = set(SERIES)
        for s in k.weather_series():
            if s["ticker"] not in mapped:
                console.print(f"{s['ticker']:24s} {s.get('title', '')}")
        return 0

    if args.command == "auth-test":
        if not k.can_auth:
            console.print("[red]no API key configured[/] — set [kalshi] in config.toml")
            return 1
        bal = k.balance()
        n = len(k.positions())
        console.print(f"[green]auth OK[/] — balance ${bal:,.2f}, {n} unsettled position rows")
        return 0

    if args.bankroll is not None:
        bankroll, source = args.bankroll, "cli override"
    else:
        bankroll, source = resolve_bankroll(cfg, k)

    if args.command == "holdings":
        if not k.can_auth:
            console.print("[red]holdings needs a Kalshi API key[/] — see config.example.toml")
            return 1
        with console.status("evaluating positions...") as status:
            advices, note = collect_holdings(k, cfg, progress=status.update)
        if args.json:
            print(json.dumps({"positions": [a.__dict__ for a in advices], "note": note},
                             indent=2, default=str))
        elif advices:
            console.print(report.holdings_table(advices))
        else:
            console.print(f"[dim]{note or 'no open positions'}[/]")
        return 0

    with console.status("scanning...") as status:
        result = run_scan(k, cfg, bankroll, source, only_series=args.series,
                          only_date=args.date, include_holdings=(args.command == "run"),
                          progress=status.update)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return 0

    render_scan(result, cfg)
    if result.holdings:
        console.print()
        console.print(report.holdings_table(result.holdings))
    elif result.holdings_note:
        console.print(f"[dim]{result.holdings_note}[/]")
    console.print()
    console.print(report.summary_panel(result.bankroll, result.bankroll_source,
                                       result.opportunities))
    console.print("[dim]model: Open-Meteo ECMWF+GFS ensembles recentered to NWS point "
                  "forecast; same-day events clamped by live station obs. Quarter-Kelly "
                  "sizing by default. Experimental — verify before trading.[/]")
    if cfg.config_path == "":
        console.print("[dim]tip: cp config.example.toml config.toml to customize "
                      "bankroll/strategy and add your Kalshi API key[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
