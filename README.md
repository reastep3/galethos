# galethos ⚡

Kalshi weather-market edge finder, as a web dashboard + CLI. Builds a probability
distribution for each daily high/low temperature market from weather model
ensembles, compares it to live orderbooks, recommends bets sized by fractional
Kelly, and gives sell/hold calls on your open positions.

**Recommendation-only**: galethos never places orders.

## Web dashboard

```bash
cd ~/Projects/galethos
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/galethos web            # http://127.0.0.1:8642
```

Hit **RESCAN** (takes ~1 min, progress streams live; auto-reloads when done).
The page shows, top to bottom:

1. **Recommended bets** — ranked by after-fee edge, with side, model probability,
   executable ask+fee, contracts to buy, outlay, and the model's reasoning under
   each row. Links jump to the Kalshi market.
2. **Refused** — bets the model *wanted* but galethos blocked because model and
   market disagree too much to trust (wrong station mapping, stale forecast, or
   the market watching live data we don't have).
3. **Holdings** — each open position with what selling nets now vs model hold EV,
   and a SELL / HOLD call. Needs a Kalshi API key.
4. **Markets** — every event as a card: model median vs NWS vs live obs, and a
   bracket ladder of cyan (model) vs gray (market) bars. Where cyan sticks out
   past gray, the model thinks the market is wrong — eyeball this before betting.

Scan-time overrides (bankroll, min edge, kelly) live in the top bar; durable
settings in `config.toml`. Last scan persists to `data/` across restarts.

## How the model works

For each event (settlement station + local calendar day): fetch the ECMWF + GFS
ensembles (~82 members, Open-Meteo, free) at the station coordinates, take each
member's daily max/min, recenter the cloud toward the NWS point forecast (NWS
knows station quirks — Central Park reads ~5°F cool vs raw models, LAX rides the
marine layer), and for same-day markets clamp by the running observed extreme.
Bracket probabilities come from that distribution; edges are net of Kalshi's
quadratic taker fee; stakes are quarter-Kelly, halved again for same-day or
low-confidence models, capped per event / in total / by book depth.

## CLI

```bash
.venv/bin/galethos                 # scan + holdings in the terminal
.venv/bin/galethos scan --series KXHIGHNY --min-edge 0.03
.venv/bin/galethos scan --json     # machine-readable (Telegram bot feed, etc.)
.venv/bin/galethos holdings        # positions advice only
.venv/bin/galethos discover        # weather series not yet mapped in stations.py
.venv/bin/galethos auth-test       # verify API key signing
```

## Kalshi API key (holdings + live balance)

Create at kalshi.com → Account → API Keys, download the RSA private key, then in
`config.toml` (copy from `config.example.toml`) set `api_key_id` and
`private_key_path`. Verify with `galethos auth-test`. Without a key, market
scanning still works fully.

## Deploy to Fly (like ghoul)

```bash
fly launch --no-deploy        # uses fly.toml; create the galethos_data volume when prompted
fly secrets set GALETHOS_PASSWORD=...                 # basic-auth gate — set this, it's your money
fly secrets set KALSHI_API_KEY_ID=...                 # optional, holdings
fly secrets set KALSHI_PRIVATE_KEY_PEM="$(cat ~/.keys/kalshi.pem)"
fly secrets set GALETHOS_BANKROLL=500
fly deploy
```

Config precedence: env vars (Fly secrets) override `config.toml`, which overrides
defaults.

## Calibrate before betting real size

Per-station bias (urban heat island, sea breeze, sensor siting) is the model's
weak spot. Paper-track for a couple of weeks; when a station runs consistently
hot/cold vs settlement, set `[model.station_bias]` in config.toml (e.g.
`KPHX = 3.0`). Settlement truth is the NWS CLI report, e.g.
https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC
Mornings/afternoons (next-day markets list each evening) beat overnight scans —
overnight "edges" are usually traders reading live thermometers faster than us.

## Strategy knobs (config.toml)

| key | default | meaning |
|---|---|---|
| `kelly_fraction` | 0.25 | fraction of full Kelly |
| `min_edge` | 0.05 | min after-fee edge to recommend |
| `per_event_cap` | 0.05 | max bankroll share per city-day |
| `max_total_exposure` | 0.25 | max bankroll share across all new bets |
| `sell_margin` | 0.03 | SELL when exit beats model EV by this |
| `nws_blend` | 0.75 | ensemble recentering weight toward NWS |
| `divergence_warn` | 5.0 | refuse event at this model/market gap (°F) |
| `max_disagreement` | 0.35 | refuse bracket at this prob/price gap |
