"""Minimal Kalshi trade-api/v2 client.

Market data needs no auth. Portfolio endpoints sign requests with the RSA key
Kalshi issues per API key (RSA-PSS/SHA256 over "<ts_ms><METHOD><path>").
"""

import base64
import datetime as dt
import time

import requests

BASE = "https://api.elections.kalshi.com"
API = "/trade-api/v2"
THROTTLE = 0.07  # seconds between requests, stays under the public rate limit


class KalshiError(RuntimeError):
    pass


class Kalshi:
    def __init__(self, key_id: str = "", private_key_path: str = "", private_key_pem: str = ""):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "galethos/0.1 (personal weather-market scanner)"
        self.key_id = key_id
        self._pk = None
        self._last_call = 0.0
        pem = private_key_pem.encode() if private_key_pem else None
        if not pem and key_id and private_key_path:
            with open(private_key_path, "rb") as f:
                pem = f.read()
        if key_id and pem:
            from cryptography.hazmat.primitives import serialization
            self._pk = serialization.load_pem_private_key(pem, password=None)

    @property
    def can_auth(self) -> bool:
        return self._pk is not None

    def _auth_headers(self, method: str, path: str) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        ts = str(int(time.time() * 1000))
        msg = f"{ts}{method}{path}".encode()
        sig = self._pk.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    def _get(self, path: str, params: dict | None = None, auth: bool = False) -> dict:
        wait = THROTTLE - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        headers = {}
        if auth:
            if not self.can_auth:
                raise KalshiError("Kalshi API key not configured (see config.example.toml)")
            headers = self._auth_headers("GET", API + path)
        r = self.s.get(BASE + API + path, params=params, headers=headers, timeout=30)
        self._last_call = time.monotonic()
        if r.status_code != 200:
            raise KalshiError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def _paginate(self, path: str, key: str, params: dict | None = None, auth: bool = False):
        params = dict(params or {})
        params.setdefault("limit", 200)
        out = []
        while True:
            d = self._get(path, params, auth=auth)
            out.extend(d.get(key) or [])
            cursor = d.get("cursor")
            if not cursor:
                return out
            params["cursor"] = cursor

    # ---- public market data ----

    def weather_series(self) -> list[dict]:
        d = self._get("/series", {"category": "Climate and Weather"})
        return d.get("series") or []

    def open_markets(self, series_ticker: str) -> list[dict]:
        return self._paginate("/markets", "markets", {"series_ticker": series_ticker, "status": "open"})

    def market(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}")["market"]

    def orderbook(self, ticker: str) -> dict:
        """Return {"yes": [(price$, qty)...], "no": [...]} sorted best (highest bid) first."""
        d = self._get(f"/markets/{ticker}/orderbook")
        raw = d.get("orderbook_fp") or d.get("orderbook") or {}
        book = {}
        for side in ("yes", "no"):
            levels = raw.get(f"{side}_dollars") or raw.get(side) or []
            parsed = []
            for price, qty in levels:
                p = float(price)
                if p > 1.0:  # legacy cents format
                    p /= 100.0
                parsed.append((p, float(qty)))
            book[side] = sorted(parsed, reverse=True)
        return book

    # ---- portfolio (auth) ----

    def balance(self) -> float:
        d = self._get("/portfolio/balance", auth=True)
        if d.get("balance_dollars") is not None:
            return float(d["balance_dollars"])
        return float(d.get("balance", 0)) / 100.0

    def positions(self) -> list[dict]:
        return self._paginate(
            "/portfolio/positions", "market_positions",
            {"settlement_status": "unsettled", "count_filter": "position"}, auth=True,
        )


# ---- orderbook helpers (Kalshi book holds YES bids and NO bids; a YES buy
#      crosses the NO bid at 1 - price, and vice versa) ----

def best_quotes(book: dict) -> dict:
    yes_bid = book["yes"][0][0] if book["yes"] else None
    no_bid = book["no"][0][0] if book["no"] else None
    return {
        "yes_bid": yes_bid,
        "no_bid": no_bid,
        "yes_ask": round(1 - no_bid, 2) if no_bid is not None else None,
        "no_ask": round(1 - yes_bid, 2) if yes_bid is not None else None,
    }


def buy_depth(book: dict, side: str, max_slippage: float):
    """(best_ask, qty fillable within best_ask + max_slippage) for buying `side`."""
    opposite = book["no"] if side == "yes" else book["yes"]
    if not opposite:
        return None, 0.0
    best_ask = round(1 - opposite[0][0], 2)
    qty = 0.0
    for price, q in opposite:
        ask = round(1 - price, 2)
        if ask > best_ask + max_slippage + 1e-9:
            break
        qty += q
    return best_ask, qty


def sell_depth(book: dict, side: str, max_slippage: float):
    """(best_bid, qty sellable within best_bid - max_slippage) for selling `side`."""
    same = book[side]
    if not same:
        return None, 0.0
    best_bid = same[0][0]
    qty = 0.0
    for price, q in same:
        if price < best_bid - max_slippage - 1e-9:
            break
        qty += q
    return best_bid, qty


def event_date(event_ticker: str) -> dt.date | None:
    """KXHIGHNY-26JUN10 -> date(2026, 6, 10)."""
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    try:
        token = event_ticker.split("-")[1]
        yy, mon, day = int(token[:2]), token[2:5].upper(), int(token[5:])
        return dt.date(2000 + yy, months[mon], day)
    except (IndexError, KeyError, ValueError):
        return None
