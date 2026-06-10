"""Config loading: GALETHOS_CONFIG env var, ./config.toml, or ~/.config/galethos/config.toml."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SEARCH_PATHS = [
    Path(os.environ.get("GALETHOS_CONFIG", "")),
    Path(__file__).resolve().parent.parent / "config.toml",
    Path.cwd() / "config.toml",
    Path.home() / ".config" / "galethos" / "config.toml",
]


@dataclass
class Config:
    bankroll: float = 500.0
    use_balance: bool = True
    api_key_id: str = ""
    private_key_path: str = ""
    private_key_pem: str = ""
    kelly_fraction: float = 0.25
    min_edge: float = 0.05
    per_event_cap: float = 0.05
    max_total_exposure: float = 0.25
    sell_margin: float = 0.03
    max_slippage: float = 0.02
    min_contracts: int = 1
    fee_rate: float = 0.07
    nws_blend: float = 0.75
    kernel_sigma: float = 1.6
    sigma_per_day: float = 0.5
    divergence_warn: float = 5.0
    max_disagreement: float = 0.35
    ensemble_models: str = "ecmwf_ifs025,gfs025"
    station_bias: dict = field(default_factory=dict)
    config_path: str = ""


def load_config() -> Config:
    cfg = Config()
    for p in SEARCH_PATHS:
        if p and p.is_file():
            with open(p, "rb") as f:
                raw = tomllib.load(f)
            b = raw.get("bankroll", {})
            cfg.bankroll = float(b.get("amount", cfg.bankroll))
            cfg.use_balance = bool(b.get("use_balance", cfg.use_balance))
            k = raw.get("kalshi", {})
            cfg.api_key_id = k.get("api_key_id", "")
            cfg.private_key_path = os.path.expanduser(k.get("private_key_path", ""))
            s = raw.get("strategy", {})
            for name in ("kelly_fraction", "min_edge", "per_event_cap", "max_total_exposure",
                         "sell_margin", "max_slippage", "fee_rate"):
                if name in s:
                    setattr(cfg, name, float(s[name]))
            if "min_contracts" in s:
                cfg.min_contracts = int(s["min_contracts"])
            m = raw.get("model", {})
            for name in ("nws_blend", "kernel_sigma", "sigma_per_day", "divergence_warn",
                         "max_disagreement"):
                if name in m:
                    setattr(cfg, name, float(m[name]))
            if "ensemble_models" in m:
                cfg.ensemble_models = m["ensemble_models"]
            cfg.station_bias = {k2: float(v) for k2, v in m.get("station_bias", {}).items()}
            cfg.config_path = str(p)
            break
    # Env overrides — how secrets reach a deployed instance (fly secrets set ...).
    env = os.environ
    if env.get("GALETHOS_BANKROLL"):
        cfg.bankroll = float(env["GALETHOS_BANKROLL"])
    if env.get("KALSHI_API_KEY_ID"):
        cfg.api_key_id = env["KALSHI_API_KEY_ID"]
    if env.get("KALSHI_PRIVATE_KEY_PATH"):
        cfg.private_key_path = os.path.expanduser(env["KALSHI_PRIVATE_KEY_PATH"])
    if env.get("KALSHI_PRIVATE_KEY_PEM"):
        cfg.private_key_pem = env["KALSHI_PRIVATE_KEY_PEM"]
    return cfg
