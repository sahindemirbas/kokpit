"""Weather connection: Open-Meteo, no key. Fetched by the ``weather`` job, read by Today.

The forecast is cached in the ``weather`` table (one row per fetch), so the page
never calls the network. A stale cache is shown with its age, not hidden.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from urllib.parse import urlencode

from ... import fetch

API = "https://api.open-meteo.com/v1/forecast?"
SCHEMA = """
CREATE TABLE IF NOT EXISTS weather (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched TEXT NOT NULL,
    lat REAL, lon REAL,
    payload TEXT NOT NULL
);
"""
# WMO weather interpretation codes, grouped.
CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
         66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
         80: "rain showers", 81: "rain showers", 82: "violent showers", 85: "snow showers", 86: "snow showers",
         95: "thunderstorm", 96: "thunderstorm, hail", 99: "thunderstorm, hail"}


def parse(body: str) -> dict:
    """Compact summary from an Open-Meteo answer: current conditions plus today and the next 4 days."""
    data = json.loads(body)
    daily = data.get("daily") or {}
    days = []
    for i, d in enumerate(daily.get("time") or []):
        code = (daily.get("weather_code") or [None] * 99)[i]
        days.append({
            "date": d, "code": code, "text": CODES.get(code, "unknown") if code is not None else "unknown",
            "max": (daily.get("temperature_2m_max") or [None] * 99)[i],
            "min": (daily.get("temperature_2m_min") or [None] * 99)[i],
            "rain_mm": (daily.get("precipitation_sum") or [None] * 99)[i],
            "rain_pct": (daily.get("precipitation_probability_max") or [None] * 99)[i],
        })
    cur = data.get("current") or {}
    return {"current": {"temp": cur.get("temperature_2m"), "code": cur.get("weather_code"),
                        "text": CODES.get(cur.get("weather_code"), "unknown")}, "days": days[:5],
            "timezone": data.get("timezone")}


def fetch_forecast(lat: float, lon: float) -> dict:
    q = urlencode({"latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code",
                   "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
                   "timezone": "auto", "forecast_days": 5})
    return parse(fetch.get(API + q, timeout=20))


def save(conn: sqlite3.Connection, lat: float, lon: float, payload: dict, now: str | None = None) -> None:
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO weather(fetched, lat, lon, payload) VALUES (?,?,?,?)",
                 (now or datetime.now().isoformat(timespec="seconds"), lat, lon, json.dumps(payload, ensure_ascii=False)))
    conn.execute("DELETE FROM weather WHERE id NOT IN (SELECT id FROM weather ORDER BY id DESC LIMIT 7)")
    conn.commit()


def latest(conn: sqlite3.Connection) -> dict | None:
    try:
        r = conn.execute("SELECT fetched, payload FROM weather ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    if not r:
        return None
    out = json.loads(r["payload"])
    out["fetched"] = r["fetched"]
    age_h = (datetime.now() - datetime.fromisoformat(r["fetched"])).total_seconds() / 3600
    out["age_hours"] = round(age_h, 1)
    out["stale"] = age_h > 24
    return out
