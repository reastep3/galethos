"""Galethos — Flask web UI for the Kalshi weather-market scanner.

Single-page dashboard: recommended bets up top (the action items), refused-bet
warnings, holdings with sell/hold calls, then per-event bracket ladders showing
model probability vs market price — the visual sanity check before betting.

Scans run in a background thread (~60s); the page polls /api/status and
reloads when fresh numbers land. Last scan persists to disk across restarts.
"""

import hmac
import json
import os
import threading
import datetime as dt
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template_string, request

from galethos.config import load_config
from galethos.engine import resolve_bankroll, run_scan
from galethos.kalshi import Kalshi

app = Flask(__name__)

DATA_DIR = Path(os.environ.get("GALETHOS_DATA", Path(__file__).parent / "data"))
LAST_PATH = DATA_DIR / "last_scan.json"
PASSWORD = os.environ.get("GALETHOS_PASSWORD", "")

_LOCK = threading.Lock()
STATE = {"running": False, "progress": "", "error": ""}
_LAST: dict | None = None


def _load_last():
    global _LAST
    try:
        _LAST = json.loads(LAST_PATH.read_text())
    except (OSError, ValueError):
        _LAST = None


_load_last()


def _save_last(d: dict):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LAST_PATH.write_text(json.dumps(d))
    except OSError as exc:  # read-only fs shouldn't kill the scanner
        app.logger.warning("could not persist scan: %s", exc)


@app.before_request
def _gate():
    if not PASSWORD or request.path == "/healthz":
        return None
    auth = request.authorization
    if auth and auth.password and hmac.compare_digest(auth.password, PASSWORD):
        return None
    return Response("galethos", 401, {"WWW-Authenticate": 'Basic realm="galethos"'})


def _do_scan(overrides: dict):
    global _LAST
    try:
        cfg = load_config()
        if overrides.get("min_edge") is not None:
            cfg.min_edge = overrides["min_edge"]
        if overrides.get("kelly") is not None:
            cfg.kelly_fraction = overrides["kelly"]
        k = Kalshi(cfg.api_key_id, cfg.private_key_path, cfg.private_key_pem)
        if overrides.get("bankroll") is not None:
            bankroll, source = overrides["bankroll"], "manual override"
        else:
            bankroll, source = resolve_bankroll(cfg, k)
        result = run_scan(k, cfg, bankroll, source,
                          progress=lambda m: STATE.update(progress=m))
        _LAST = result.to_dict()
        _save_last(_LAST)
        STATE["error"] = ""
    except Exception as exc:
        app.logger.exception("scan failed")
        STATE["error"] = f"scan failed: {type(exc).__name__}: {exc}"
    finally:
        STATE["running"] = False
        STATE["progress"] = ""


@app.post("/scan")
def scan():
    def _f(name, scale=1.0):
        raw = (request.form.get(name) or "").strip()
        try:
            return float(raw) * scale if raw else None
        except ValueError:
            return None

    with _LOCK:
        if not STATE["running"]:
            STATE.update(running=True, progress="starting scan...", error="")
            overrides = {"bankroll": _f("bankroll"), "min_edge": _f("min_edge", 0.01),
                         "kelly": _f("kelly")}
            threading.Thread(target=_do_scan, args=(overrides,), daemon=True).start()
    return redirect("/")


@app.get("/api/status")
def status():
    return jsonify(running=STATE["running"], progress=STATE["progress"],
                   error=STATE["error"], at=(_LAST or {}).get("at"))


@app.get("/api/last.json")
def last_json():
    if _LAST is None:
        return jsonify(error="no scan yet"), 404
    return jsonify(_LAST)


@app.get("/healthz")
def healthz():
    return "ok"


# ---------- formatting filters ----------

@app.template_filter()
def cents(v) -> str:
    return f"{round(v * 100)}¢" if v is not None else "—"


@app.template_filter()
def pct(v) -> str:
    return f"{v * 100:.0f}%" if v is not None else "—"


@app.template_filter()
def mktp(b) -> float | None:
    """Best market estimate of P(yes) for a bracket row: mid, else ask, else bid."""
    yb, ya = b.get("yes_bid"), b.get("yes_ask")
    if yb is not None and ya is not None:
        return (yb + ya) / 2
    return ya if ya is not None else yb


@app.template_filter()
def barw(p) -> str:
    return f"{max(p * 100, 1.5):.1f}" if p is not None else "0"


@app.template_filter()
def series_url(ticker: str) -> str:
    return f"https://kalshi.com/markets/{ticker.split('-')[0].lower()}"


def _age_minutes(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        then = dt.datetime.fromisoformat(iso)
        return int((dt.datetime.now(dt.timezone.utc) - then).total_seconds() // 60)
    except ValueError:
        return None


@app.get("/")
def index():
    cfg = load_config()
    last = _LAST
    age = _age_minutes(last.get("at")) if last else None
    return render_template_string(
        PAGE, css=CSS, last=last, state=STATE, age=age,
        has_auth=bool(cfg.api_key_id and (cfg.private_key_path or cfg.private_key_pem)),
        defaults={"min_edge": cfg.min_edge, "kelly": cfg.kelly_fraction},
    )


CSS = """
:root { --bg:#0b0e14; --card:#11161f; --line:#1f2733; --txt:#c9d3df; --dim:#7d8893;
        --cyan:#4cc2ff; --green:#3fb950; --red:#f85149; --amber:#d29922; --mkt:#5a6573; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--txt);
       font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; }
a { color:var(--cyan); text-decoration:none; }
.wrap { max-width:1180px; margin:0 auto; padding:18px 20px 60px; }
header { display:flex; flex-wrap:wrap; align-items:baseline; gap:14px; margin-bottom:14px; }
h1 { font-size:22px; margin:0; letter-spacing:2px; color:#fff; }
h1 .bolt { color:var(--cyan); }
.tag { color:var(--dim); font-size:12px; }
.chips { margin-left:auto; display:flex; gap:8px; flex-wrap:wrap; }
.chip { background:var(--card); border:1px solid var(--line); border-radius:6px;
        padding:4px 10px; font-size:12px; color:var(--dim); }
.chip b { color:var(--txt); }
.chip.stale b { color:var(--amber); }
.controls { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
            background:var(--card); border:1px solid var(--line); border-radius:8px;
            padding:10px 12px; margin-bottom:18px; }
.controls label { font-size:11px; color:var(--dim); display:flex; flex-direction:column; gap:3px; }
.controls input { width:90px; background:var(--bg); border:1px solid var(--line);
                  border-radius:5px; color:var(--txt); padding:5px 7px; font:inherit; }
button { background:var(--cyan); color:#06121c; font:inherit; font-weight:700;
         border:0; border-radius:6px; padding:9px 18px; cursor:pointer; }
button:disabled { opacity:.45; cursor:default; }
.progress { color:var(--cyan); font-size:12px; }
.progress .dot { animation:blink 1s infinite; }
@keyframes blink { 50% { opacity:.2; } }
.error { color:var(--red); font-size:12px; }
h2 { font-size:13px; letter-spacing:1.5px; color:var(--dim); text-transform:uppercase;
     margin:26px 0 10px; }
table { width:100%; border-collapse:collapse; background:var(--card);
        border:1px solid var(--line); border-radius:8px; overflow:hidden; }
th { text-align:left; font-size:11px; color:var(--dim); font-weight:600;
     padding:8px 10px; border-bottom:1px solid var(--line); }
td { padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
tr:last-child td { border-bottom:0; }
td.num, th.num { text-align:right; }
.side { display:inline-block; padding:1px 8px; border-radius:4px; font-size:11px; font-weight:700; }
.side.yes { background:#16301f; color:var(--green); }
.side.no  { background:#33181a; color:var(--red); }
.act { display:inline-block; padding:1px 8px; border-radius:4px; font-size:11px; font-weight:700; }
.act.SELL { background:#33181a; color:var(--red); }
.act.HOLD { background:#16301f; color:var(--green); }
.act.NOMODEL { background:#222833; color:var(--dim); }
.edge { color:var(--green); font-weight:700; }
.note { color:var(--dim); font-size:11px; }
.warnbox { background:#2a2110; border:1px solid #4d3c12; border-radius:8px;
           padding:10px 12px; font-size:12px; color:var(--amber); }
.warnbox div { margin:2px 0; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 14px; }
.card h3 { margin:0 0 2px; font-size:14px; color:#fff; }
.kind { font-size:10px; padding:1px 6px; border-radius:4px; margin-left:6px; vertical-align:1px; }
.kind.high { background:#33231a; color:#f0883e; }
.kind.low  { background:#16242f; color:var(--cyan); }
.meta { font-size:11px; color:var(--dim); margin-bottom:10px; }
.meta b { color:var(--txt); }
.lrow { display:grid; grid-template-columns:96px 1fr 88px 70px; gap:8px;
        align-items:center; padding:4px 0; font-size:12px; }
.lbl { color:var(--txt); white-space:nowrap; }
.bars { display:flex; flex-direction:column; gap:2px; }
.bar { height:6px; border-radius:3px; background:var(--line); position:relative; overflow:hidden; }
.bar i { position:absolute; inset:0; border-radius:3px; }
.bar.model i { background:var(--cyan); }
.bar.market i { background:var(--mkt); }
.nums { color:var(--dim); font-size:11px; text-align:right; white-space:nowrap; }
.nums b { color:var(--cyan); }
.buy { font-size:10px; font-weight:700; text-align:center; border-radius:4px; padding:2px 4px; }
.buy.yes { background:#16301f; color:var(--green); }
.buy.no  { background:#33181a; color:var(--red); }
.cardnotes { margin-top:8px; font-size:10.5px; color:var(--dim); border-top:1px solid var(--line);
             padding-top:6px; }
.legend { font-size:11px; color:var(--dim); margin:0 0 10px; }
.legend .sw { display:inline-block; width:18px; height:6px; border-radius:3px; vertical-align:2px; }
.empty { color:var(--dim); background:var(--card); border:1px dashed var(--line);
         border-radius:8px; padding:18px; text-align:center; }
footer { margin-top:34px; color:var(--dim); font-size:11px; border-top:1px solid var(--line);
         padding-top:12px; }
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>galethos</title>
<style>{{ css }}</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>GALETH<span class="bolt">⚡</span>S</h1>
  <span class="tag">kalshi weather edge scanner</span>
  <div class="chips">
    {% if last %}
      <span class="chip">bankroll <b>${{ '%.2f'|format(last.bankroll) }}</b> · {{ last.bankroll_source }}</span>
      <span class="chip {{ 'stale' if age is not none and age > 30 }}">scanned <b>{{ age }}m ago</b> in {{ last.duration_s }}s</span>
    {% endif %}
    <span class="chip">holdings <b>{{ 'linked' if has_auth else 'no API key' }}</b></span>
  </div>
</header>

<form class="controls" method="post" action="/scan">
  <label>bankroll $ <input name="bankroll" placeholder="auto"></label>
  <label>min edge ¢ <input name="min_edge" placeholder="{{ (defaults.min_edge*100)|round|int }}"></label>
  <label>kelly fraction <input name="kelly" placeholder="{{ defaults.kelly }}"></label>
  <button id="scanbtn" {{ 'disabled' if state.running }}>{{ 'SCANNING…' if state.running else 'RESCAN' }}</button>
  {% if state.running %}<span class="progress" id="progress">{{ state.progress }} <span class="dot">●</span></span>{% endif %}
  {% if state.error %}<span class="error">{{ state.error }}</span>{% endif %}
</form>

{% if not last %}
  <div class="empty">no scan yet — hit RESCAN to pull every Kalshi temperature market and model it
    (takes about a minute)</div>
{% else %}

<h2>Recommended bets</h2>
{% if last.opportunities %}
<table>
<tr><th>#</th><th>market</th><th>side</th><th class="num">model P</th><th class="num">ask+fee</th>
    <th class="num">edge</th><th class="num">buy</th><th class="num">outlay</th>
    <th class="num">edge $ if model right</th></tr>
{% for o in last.opportunities %}
<tr title="{{ o.notes|join(' · ') }}">
  <td>{{ loop.index }}</td>
  <td><a href="{{ o.ticker|series_url }}" target="_blank">{{ o.label }} ↗</a>
      <div class="note">{{ o.notes|join(' · ') }}</div></td>
  <td><span class="side {{ o.side }}">{{ o.side|upper }}</span></td>
  <td class="num">{{ o.prob|pct }}</td>
  <td class="num">{{ o.ask|cents }}+{{ o.fee|cents }}</td>
  <td class="num edge">+{{ o.edge|cents }}</td>
  <td class="num">{{ o.contracts }}×</td>
  <td class="num">${{ '%.2f'|format(o.outlay) }}</td>
  <td class="num">${{ '%.2f'|format(o.exp_profit) }}</td>
</tr>
{% endfor %}
<tr>
  <td></td><td><b>total</b></td><td></td><td></td><td></td><td></td><td></td>
  <td class="num"><b>${{ '%.2f'|format(last.opportunities|sum(attribute='outlay')) }}</b></td>
  <td class="num"><b>${{ '%.2f'|format(last.opportunities|sum(attribute='exp_profit')) }}</b></td>
</tr>
</table>
{% else %}
<div class="empty">no bets clear the {{ (last.min_edge*100)|round|int }}¢ after-fee edge bar right now</div>
{% endif %}

{% if last.warnings %}
<h2>Refused (model and market disagree too much to trust)</h2>
<div class="warnbox">
  {% for w in last.warnings %}<div>⚠ {{ w }}</div>{% endfor %}
</div>
{% endif %}

<h2>Holdings</h2>
{% if last.holdings %}
<table>
<tr><th>market</th><th>side</th><th class="num">qty</th><th class="num">avg cost</th>
    <th class="num">bid now</th><th class="num">model P</th><th class="num">sell nets</th>
    <th class="num">hold EV</th><th>action</th><th>why</th></tr>
{% for a in last.holdings %}
<tr>
  <td><a href="{{ a.ticker|series_url }}" target="_blank">{{ a.title }}</a></td>
  <td><span class="side {{ a.side }}">{{ a.side|upper }}</span></td>
  <td class="num">{{ a.qty }}</td>
  <td class="num">{{ a.avg_cost|cents }}</td>
  <td class="num">{{ a.bid|cents }}</td>
  <td class="num">{{ a.model_p|pct }}</td>
  <td class="num">{{ a.sell_value|cents }}</td>
  <td class="num">{{ a.hold_ev|cents }}</td>
  <td><span class="act {{ a.action.replace(' ','') }}">{{ a.action }}</span></td>
  <td class="note">{{ a.reason }}</td>
</tr>
{% endfor %}
</table>
{% else %}
<div class="empty">{{ last.holdings_note or 'no open positions' }}</div>
{% endif %}

<h2>Markets — model vs market, every bracket</h2>
<p class="legend"><span class="sw" style="background:var(--cyan)"></span> model P(yes)
   &nbsp;&nbsp;<span class="sw" style="background:var(--mkt)"></span> market price
   &nbsp;&nbsp;— where cyan sticks out past gray, the model thinks the market is wrong</p>
<div class="grid">
{% for ev in last.events %}
  <div class="card">
    <h3><a href="{{ ev.event_ticker|series_url }}" target="_blank">{{ ev.station }}</a>
        <span class="kind {{ ev.kind }}">{{ ev.kind|upper }}</span>
        <span class="tag">{{ ev.date_label }}</span></h3>
    <div class="meta">
      model <b>{{ ev.median }}°F</b>
      {% if ev.nws_anchor is not none %} · NWS <b>{{ ev.nws_anchor|round|int }}°</b>{% endif %}
      {% if ev.obs_extreme is not none %} · obs {{ ev.kind }} <b>{{ '%.1f'|format(ev.obs_extreme) }}°</b>{% endif %}
      {% if ev.divergence is not none %} · Δmkt {{ '%+.1f'|format(ev.divergence) }}°{% endif %}
    </div>
    {% for b in ev.brackets %}
    {% set mp = b|mktp %}
    <div class="lrow">
      <span class="lbl">{{ b.label }}</span>
      <span class="bars">
        <span class="bar model"><i style="width:{{ b.model_p|barw }}%"></i></span>
        <span class="bar market"><i style="width:{{ mp|barw }}%"></i></span>
      </span>
      <span class="nums"><b>{{ b.model_p|pct }}</b> / {{ b.yes_bid|cents }}–{{ b.yes_ask|cents }}</span>
      {% if b.flag %}<span class="buy {{ b.flag }}">BUY {{ b.flag|upper }}</span>{% else %}<span></span>{% endif %}
    </div>
    {% endfor %}
    {% if ev.notes %}<div class="cardnotes">{{ ev.notes|join(' · ') }}</div>{% endif %}
  </div>
{% endfor %}
</div>

{% endif %}

<footer>
model: ECMWF+GFS ensembles (~82 members) at the settlement station, recentered toward the NWS
point forecast; same-day events clamped by live station obs. Kalshi fees included; quarter-Kelly
sizing capped per event and in total. Galethos recommends — it never places orders.
Experimental: paper-track before betting real size.
</footer>
</div>

<script>
const running = {{ 'true' if state.running else 'false' }};
if (running) {
  const el = document.getElementById('progress');
  const timer = setInterval(async () => {
    try {
      const r = await fetch('/api/status');
      const s = await r.json();
      if (!s.running) { clearInterval(timer); location.reload(); return; }
      if (el) el.innerHTML = s.progress + ' <span class="dot">●</span>';
    } catch (e) { /* transient */ }
  }, 2000);
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8642)), debug=False)
