"""Kalshi weather series -> NWS settlement station mapping.

Daily temperature markets settle on the NWS Climatological Report (CLI) for a
specific station, so forecasts must target that station's coordinates and the
local calendar day. Run `galethos discover` to find open weather series that
aren't mapped here yet.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    sid: str          # NWS station id used for live observations
    name: str
    lat: float
    lon: float
    tz: str


STATIONS = {
    "KNYC": Station("KNYC", "NYC Central Park", 40.7790, -73.9693, "America/New_York"),
    "KMDW": Station("KMDW", "Chicago Midway", 41.7861, -87.7522, "America/Chicago"),
    "KMIA": Station("KMIA", "Miami Intl", 25.7881, -80.3169, "America/New_York"),
    "KATT": Station("KATT", "Austin Camp Mabry", 30.3208, -97.7604, "America/Chicago"),
    "KDEN": Station("KDEN", "Denver Intl", 39.8467, -104.6562, "America/Denver"),
    "KPHL": Station("KPHL", "Philadelphia Intl", 39.8729, -75.2437, "America/New_York"),
    "KLAX": Station("KLAX", "Los Angeles Intl", 33.9382, -118.3866, "America/Los_Angeles"),
    "KSFO": Station("KSFO", "San Francisco Intl", 37.6197, -122.3647, "America/Los_Angeles"),
    "KPHX": Station("KPHX", "Phoenix Sky Harbor", 33.4278, -112.0044, "America/Phoenix"),
    "KMSP": Station("KMSP", "Minneapolis-St Paul", 44.8831, -93.2289, "America/Chicago"),
    "KSEA": Station("KSEA", "Seattle-Tacoma", 47.4444, -122.3139, "America/Los_Angeles"),
    "KATL": Station("KATL", "Atlanta Hartsfield", 33.6301, -84.4418, "America/New_York"),
    "KIAH": Station("KIAH", "Houston Bush", 29.9844, -95.3414, "America/Chicago"),
    "KOKC": Station("KOKC", "Oklahoma City Will Rogers", 35.3889, -97.6006, "America/Chicago"),
    "KMSY": Station("KMSY", "New Orleans Intl", 29.9928, -90.2510, "America/Chicago"),
    "KDFW": Station("KDFW", "Dallas-Fort Worth", 32.8978, -97.0189, "America/Chicago"),
    "KLAS": Station("KLAS", "Las Vegas Harry Reid", 36.0719, -115.1634, "America/Los_Angeles"),
    "KDCA": Station("KDCA", "Washington Reagan", 38.8483, -77.0342, "America/New_York"),
    "KBOS": Station("KBOS", "Boston Logan", 42.3606, -71.0106, "America/New_York"),
    "KSAT": Station("KSAT", "San Antonio Intl", 29.5337, -98.4644, "America/Chicago"),
}

# series ticker -> (station id, "high" | "low")
# Kalshi has duplicate/legacy series per city; map them all, scanner skips empty ones.
SERIES = {
    # daily highs
    "KXHIGHNY": ("KNYC", "high"),
    "KXHIGHCHI": ("KMDW", "high"),
    "KXHIGHMIA": ("KMIA", "high"),
    "KXHIGHAUS": ("KATT", "high"),
    "KXHIGHDEN": ("KDEN", "high"),
    "KXHIGHTEMPDEN": ("KDEN", "high"),
    "KXHIGHPHIL": ("KPHL", "high"),
    "KXHIGHLAX": ("KLAX", "high"),
    "KXHIGHHOU": ("KIAH", "high"),
    "KXHIGHOU": ("KIAH", "high"),
    "KXHIGHTHOU": ("KIAH", "high"),
    "KXHIGHTSFO": ("KSFO", "high"),
    "KXHIGHTPHX": ("KPHX", "high"),
    "KXHIGHTMIN": ("KMSP", "high"),
    "KXHIGHTSEA": ("KSEA", "high"),
    "KXHIGHTATL": ("KATL", "high"),
    "KXHIGHTOKC": ("KOKC", "high"),
    "KXHIGHTNOLA": ("KMSY", "high"),
    "KXHIGHTDAL": ("KDFW", "high"),
    "KXHIGHTLV": ("KLAS", "high"),
    "KXHIGHTDC": ("KDCA", "high"),
    "KXHIGHTBOS": ("KBOS", "high"),
    "KXHIGHTSATX": ("KSAT", "high"),
    # daily lows
    "KXLOWNY": ("KNYC", "low"),
    "KXLOWNYC": ("KNYC", "low"),
    "KXLOWTNYC": ("KNYC", "low"),
    "KXLOWCHI": ("KMDW", "low"),
    "KXLOWTCHI": ("KMDW", "low"),
    "KXLOWMIA": ("KMIA", "low"),
    "KXLOWTMIA": ("KMIA", "low"),
    "KXLOWAUS": ("KATT", "low"),
    "KXLOWTAUS": ("KATT", "low"),
    "KXLOWDEN": ("KDEN", "low"),
    "KXLOWTDEN": ("KDEN", "low"),
    "KXLOWPHIL": ("KPHL", "low"),
    "KXLOWTPHIL": ("KPHL", "low"),
    "KXLOWLAX": ("KLAX", "low"),
    "KXLOWTLAX": ("KLAX", "low"),
    "KXLOWTSFO": ("KSFO", "low"),
    "KXLOWTPHX": ("KPHX", "low"),
    "KXLOWTMIN": ("KMSP", "low"),
    "KXLOWTSEA": ("KSEA", "low"),
    "KXLOWTATL": ("KATL", "low"),
    "KXLOWTHOU": ("KIAH", "low"),
    "KXLOWTOKC": ("KOKC", "low"),
    "KXLOWTNOLA": ("KMSY", "low"),
    "KXLOWTDAL": ("KDFW", "low"),
    "KXLOWTLV": ("KLAS", "low"),
    "KXLOWTDC": ("KDCA", "low"),
    "KXLOWTBOS": ("KBOS", "low"),
    "KXLOWTSATX": ("KSAT", "low"),
}


def series_for_ticker(market_or_event_ticker: str):
    """Map a market/event ticker back to (series_ticker, Station, kind), or None."""
    prefix = market_or_event_ticker.split("-")[0]
    if prefix in SERIES:
        sid, kind = SERIES[prefix]
        return prefix, STATIONS[sid], kind
    return None
