"""Weather data: Open-Meteo ensembles (distribution shape), NWS forecast (anchor),
NWS station observations (running max/min for same-day markets). All free, no keys."""

import datetime as dt
from collections import defaultdict
from zoneinfo import ZoneInfo

import requests

from .stations import Station

NWS_UA = {"User-Agent": "galethos-kalshi-weather (re81194@gmail.com)", "Accept": "application/geo+json"}


class WeatherCache:
    """One fetch per station per run, shared across events/dates."""

    def __init__(self, ensemble_models: str):
        self.models = ensemble_models
        self.s = requests.Session()
        self._ens: dict[str, dict] = {}
        self._nws: dict[str, dict] = {}
        self._obs: dict[str, dict] = {}

    # ---- Open-Meteo ensemble: hourly temps per member, local time ----

    def ensemble(self, st: Station, max_date: dt.date) -> dict:
        """{"times": [datetime...], "members": [[temp...], ...]} in station-local time."""
        if st.sid in self._ens:
            return self._ens[st.sid]
        today = dt.datetime.now(ZoneInfo(st.tz)).date()
        days = min(max((max_date - today).days + 2, 3), 14)
        r = self.s.get(
            "https://ensemble-api.open-meteo.com/v1/ensemble",
            params={
                "latitude": st.lat, "longitude": st.lon,
                "hourly": "temperature_2m", "models": self.models,
                "forecast_days": days, "temperature_unit": "fahrenheit",
                "timezone": st.tz,
            },
            timeout=60,
        )
        r.raise_for_status()
        h = r.json()["hourly"]
        times = [dt.datetime.fromisoformat(t) for t in h["time"]]
        members = [h[k] for k in h if k.startswith("temperature_2m")]
        self._ens[st.sid] = {"times": times, "members": members}
        return self._ens[st.sid]

    def member_extremes(self, st: Station, date: dt.date, kind: str,
                        after_hour: int | None = None) -> list[float]:
        """Per-member daily max/min for `date`. after_hour limits to hours >= it
        (for same-day markets where the past is already observed)."""
        ens = self._ens[st.sid]
        idx = [i for i, t in enumerate(ens["times"])
               if t.date() == date and (after_hour is None or t.hour >= after_hour)]
        agg = max if kind == "high" else min
        out = []
        for m in ens["members"]:
            vals = [m[i] for i in idx if i < len(m) and m[i] is not None]
            if vals:
                out.append(agg(vals))
        return out

    # ---- NWS hourly point forecast -> per-calendar-day high/low anchor ----

    def nws_daily(self, st: Station) -> dict[dt.date, dict]:
        """{date: {"high": f, "low": f}} from the NWS hourly forecast grid."""
        if st.sid in self._nws:
            return self._nws[st.sid]
        result: dict[dt.date, dict] = {}
        try:
            p = self.s.get(f"https://api.weather.gov/points/{st.lat:.4f},{st.lon:.4f}",
                           headers=NWS_UA, timeout=30)
            p.raise_for_status()
            url = p.json()["properties"]["forecastHourly"]
            f = self.s.get(url, headers=NWS_UA, timeout=30)
            f.raise_for_status()
            by_day = defaultdict(list)
            for period in f.json()["properties"]["periods"]:
                t = dt.datetime.fromisoformat(period["startTime"])
                temp = period.get("temperature")
                if temp is not None:
                    by_day[t.date()].append(float(temp))
            for d, temps in by_day.items():
                result[d] = {"high": max(temps), "low": min(temps), "hours": len(temps)}
        except requests.RequestException:
            pass  # NWS flakes sometimes; model falls back to raw ensemble
        self._nws[st.sid] = result
        return result

    # ---- NWS live observations: today's running extreme at the settlement station ----

    def obs_today(self, st: Station) -> dict | None:
        """{"max": F, "min": F, "last": datetime, "n": count} for today (station-local)."""
        if st.sid in self._obs:
            return self._obs[st.sid]
        tz = ZoneInfo(st.tz)
        midnight = dt.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        out = None
        try:
            r = self.s.get(
                f"https://api.weather.gov/stations/{st.sid}/observations",
                params={"start": midnight.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "limit": 100},
                headers=NWS_UA, timeout=30,
            )
            r.raise_for_status()
            temps, last = [], None
            for feat in r.json().get("features", []):
                v = feat["properties"].get("temperature", {}).get("value")
                ts = feat["properties"].get("timestamp")
                if v is not None:
                    temps.append(v * 9 / 5 + 32)
                    t = dt.datetime.fromisoformat(ts)
                    last = t if last is None or t > last else last
            if temps:
                out = {"max": max(temps), "min": min(temps), "last": last.astimezone(tz), "n": len(temps)}
        except requests.RequestException:
            pass
        self._obs[st.sid] = out
        return out
