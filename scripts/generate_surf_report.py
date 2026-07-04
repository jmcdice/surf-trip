#!/usr/bin/env python3
"""
Generate the twice-daily surf report for the Nica '26 site.

Pulls a free (no-API-key) marine + weather forecast from Open-Meteo for
Playa Remanso, picks out the morning (dawn patrol) and afternoon sessions,
rates each with simple surf logic, and asks the `claude` CLI to write a
laid-back, Dude-style narration on top. Writes the whole thing to
site/data/surf-report.json, which surf.html reads client-side.

Designed to be run from cron, e.g. twice a day:
    0 6,14 * * *  cd /path/to/surf-trip && python3 scripts/generate_surf_report.py

It only writes the JSON file. Pushing to GitHub (which redeploys the site)
is handled by the repo's existing auto-commit Stop hook when run via Claude,
or you can add `&& git commit -am ... && git push` to the cron line.

No third-party Python packages required — standard library only.
"""

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ── Spot config ──────────────────────────────────────────────────────────
SPOT_NAME = "Playa Remanso"
LAT = 11.2205
LON = -85.8494
TZ_NAME = "America/Managua"      # UTC-6, no DST
TZ = timezone(timedelta(hours=-6))

# Playa Remanso faces roughly SW (~230°). Offshore wind therefore blows
# FROM the NE (~050°). We classify the wind's *source* direction.
OFFSHORE_FROM = 50               # degrees the ideal offshore wind comes from

# Which local hours represent each session.
MORNING_HOUR = 6                 # dawn patrol
AFTERNOON_HOUR = 15             # 3 PM

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "..", "site", "data", "surf-report.json")

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def deg_to_compass(deg):
    if deg is None:
        return "—"
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def m_to_ft(m):
    return None if m is None else round(m * 3.28084, 1)


def c_to_f(c):
    return None if c is None else round(c * 9 / 5 + 32)


# WMO weather codes → (emoji, short label). Keyed to what shows up on the
# Nicaraguan Pacific in July (clear, clouds, showers, thunder).
WEATHER_CODES = {
    0: ("☀️", "Clear"),
    1: ("🌤️", "Mostly clear"), 2: ("⛅", "Partly cloudy"), 3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"), 48: ("🌫️", "Fog"),
    51: ("🌦️", "Light drizzle"), 53: ("🌦️", "Drizzle"), 55: ("🌦️", "Heavy drizzle"),
    61: ("🌧️", "Light rain"), 63: ("🌧️", "Rain"), 65: ("🌧️", "Heavy rain"),
    66: ("🌧️", "Freezing rain"), 67: ("🌧️", "Freezing rain"),
    71: ("🌨️", "Snow"), 73: ("🌨️", "Snow"), 75: ("🌨️", "Heavy snow"),
    80: ("🌦️", "Showers"), 81: ("🌧️", "Showers"), 82: ("⛈️", "Violent showers"),
    95: ("⛈️", "Thunderstorm"),
    96: ("⛈️", "Thunderstorm"), 99: ("⛈️", "Thunderstorm w/ hail"),
}


def weather_code_info(code):
    if code is None:
        return "🌊", "—"
    return WEATHER_CODES.get(int(code), ("🌊", "—"))


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nica26-surf-report/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_forecast():
    """Fetch marine + weather hourly data from Open-Meteo."""
    marine_url = (
        "https://marine-api.open-meteo.com/v1/marine"
        f"?latitude={LAT}&longitude={LON}"
        "&hourly=wave_height,wave_period,wave_direction,"
        "swell_wave_height,swell_wave_period,swell_wave_direction,"
        "sea_surface_temperature"
        f"&timezone={TZ_NAME}&forecast_days=2"
    )
    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&hourly=temperature_2m,apparent_temperature,precipitation_probability,"
        "uv_index,cloud_cover,weather_code,"
        "wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        "&wind_speed_unit=mph&temperature_unit=fahrenheit"
        f"&timezone={TZ_NAME}&forecast_days=2"
    )
    return fetch_json(marine_url), fetch_json(weather_url)


def wind_type(from_deg):
    """Classify wind relative to an offshore ideal of NE (~050°)."""
    if from_deg is None:
        return "unknown"
    diff = abs((from_deg - OFFSHORE_FROM + 180) % 360 - 180)
    if diff <= 45:
        return "offshore"        # clean, holds the wave up
    if diff <= 90:
        return "cross-shore"
    if diff <= 135:
        return "cross-onshore"
    return "onshore"             # blown out / bumpy


def rate_session(wave_ft, period_s, wtype):
    """Return (emoji, one-word label, 0-100 score)."""
    score = 0
    # Wave size — sweet spot 3-8 ft.
    if wave_ft is None:
        score += 0
    elif wave_ft < 1.5:
        score += 5
    elif wave_ft < 3:
        score += 25
    elif wave_ft <= 8:
        score += 45
    else:
        score += 30              # big & possibly gnarly
    # Swell period — longer = cleaner groundswell.
    if period_s is None:
        pass
    elif period_s >= 14:
        score += 35
    elif period_s >= 11:
        score += 25
    elif period_s >= 8:
        score += 12
    else:
        score += 4
    # Wind quality.
    score += {"offshore": 20, "cross-shore": 12,
              "cross-onshore": 5, "onshore": 0, "unknown": 8}[wtype]

    if score >= 75:
        return "🔥", "Firing", score
    if score >= 55:
        return "👍", "Fun", score
    if score >= 35:
        return "🆗", "Rideable", score
    return "😕", "Marginal", score


def hour_index(times, target_date, hour):
    """Find the index in the ISO time list matching target_date @ hour."""
    stamp = f"{target_date}T{hour:02d}:00"
    for i, t in enumerate(times):
        if t == stamp:
            return i
    return None


def build_session(label, time_label, marine, weather, target_date, hour):
    mh = marine["hourly"]
    wh = weather["hourly"]
    mi = hour_index(mh["time"], target_date, hour)
    wi = hour_index(wh["time"], target_date, hour)

    def mget(key):
        return mh[key][mi] if mi is not None and mh.get(key) else None

    def wget(key):
        return wh[key][wi] if wi is not None and wh.get(key) else None

    wave_m = mget("wave_height")
    # Prefer the ground/swell component for period & direction when present.
    period = mget("swell_wave_period") or mget("wave_period")
    sdir = mget("swell_wave_direction")
    if sdir is None:
        sdir = mget("wave_direction")
    wind_mph = wget("wind_speed_10m")
    wind_gust = wget("wind_gusts_10m")
    wind_from = wget("wind_direction_10m")

    # Weather
    air_f = wget("temperature_2m")
    feels_f = wget("apparent_temperature")
    precip_pct = wget("precipitation_probability")
    uv = wget("uv_index")
    cloud = wget("cloud_cover")
    water_f = c_to_f(mget("sea_surface_temperature"))
    wx_emoji, wx_label = weather_code_info(wget("weather_code"))

    wave_ft = m_to_ft(wave_m)
    wtype = wind_type(wind_from)
    emoji, rating_label, score = rate_session(wave_ft, period, wtype)

    return {
        "label": label,
        "time": time_label,
        "wave_ft": wave_ft,
        "wave_m": round(wave_m, 1) if wave_m is not None else None,
        "period_s": round(period) if period is not None else None,
        "swell_dir": deg_to_compass(sdir),
        "swell_dir_deg": round(sdir) if sdir is not None else None,
        "wind_mph": round(wind_mph) if wind_mph is not None else None,
        "wind_gust_mph": round(wind_gust) if wind_gust is not None else None,
        "wind_dir": deg_to_compass(wind_from),
        "wind_dir_deg": round(wind_from) if wind_from is not None else None,
        "wind_type": wtype,
        "air_f": round(air_f) if air_f is not None else None,
        "feels_f": round(feels_f) if feels_f is not None else None,
        "water_f": water_f,
        "precip_pct": precip_pct,
        "uv": round(uv) if uv is not None else None,
        "cloud_pct": round(cloud) if cloud is not None else None,
        "wx_emoji": wx_emoji,
        "wx_label": wx_label,
        "rating": emoji,
        "rating_label": rating_label,
        "score": score,
    }


def rule_based_blurb(sessions):
    """A dependable non-AI fallback summary."""
    m, a = sessions["morning"], sessions["afternoon"]
    best = "morning" if m["score"] >= a["score"] else "afternoon"
    parts = [f"{SPOT_NAME}: {m['rating_label'].lower()} in the AM "
             f"({m['wave_ft']}ft, {m['period_s']}s, {m['wind_type']} wind), "
             f"{a['rating_label'].lower()} in the PM "
             f"({a['wave_ft']}ft, {a['wind_type']} wind)."]
    if best == "morning":
        parts.append("Dawn patrol looks like the call — get out early before "
                     "the afternoon wind picks up.")
    else:
        parts.append("The afternoon session edges it today.")
    return " ".join(parts)


def dude_narration(sessions, updated_human):
    """Ask the claude CLI for a laid-back surf report. Falls back gracefully."""
    m, a = sessions["morning"], sessions["afternoon"]
    facts = (
        f"Spot: {SPOT_NAME}, Nicaragua. Report time: {updated_human}.\n"
        f"MORNING (dawn patrol): {m['wave_ft']} ft waves, {m['period_s']}s swell "
        f"period from {m['swell_dir']}, wind {m['wind_mph']} mph from {m['wind_dir']} "
        f"({m['wind_type']}). Air {m['air_f']}F, water {m['water_f']}F, "
        f"{m['precip_pct']}% rain chance, {m['wx_label']}, UV {m['uv']}. "
        f"Rating: {m['rating_label']}.\n"
        f"AFTERNOON: {a['wave_ft']} ft waves, {a['period_s']}s swell period from "
        f"{a['swell_dir']}, wind {a['wind_mph']} mph from {a['wind_dir']} "
        f"({a['wind_type']}). Air {a['air_f']}F, water {a['water_f']}F, "
        f"{a['precip_pct']}% rain chance, {a['wx_label']}, UV {a['uv']}. "
        f"Rating: {a['rating_label']}."
    )
    prompt = (
        "You are the surf reporter for a crew of 12 friends on a surf trip. "
        "Write a short, fun surf report (2-4 sentences, max ~75 words) in the "
        "laid-back voice of The Dude from The Big Lebowski — casual, stoked, "
        "phrases like 'yeah man' and 'far out' are welcome but don't overdo it. "
        "Give a clear call on whether to paddle out morning or afternoon and why. "
        "Work in a quick word on the weather (temp, rain, or sun) where it's "
        "relevant. Use the real numbers. Do NOT use markdown, headers, or bullet "
        "points — just plain sentences. Here's the data:\n\n" + facts
    )
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=120,
        )
        text = result.stdout.strip()
        if result.returncode == 0 and text:
            return text, "claude"
        print(f"[warn] claude CLI returned no text (rc={result.returncode}): "
              f"{result.stderr.strip()[:200]}", file=sys.stderr)
    except FileNotFoundError:
        print("[warn] claude CLI not found on PATH — using rule-based blurb.",
              file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("[warn] claude CLI timed out — using rule-based blurb.",
              file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - never let narration kill the report
        print(f"[warn] claude CLI failed ({e}) — using rule-based blurb.",
              file=sys.stderr)
    return rule_based_blurb(sessions), "rules"


def now_local():
    """Current time in Nicaragua. Env var NICA_NOW (ISO) overrides for testing."""
    override = os.environ.get("NICA_NOW")
    if override:
        return datetime.fromisoformat(override).astimezone(TZ)
    return datetime.now(timezone.utc).astimezone(TZ)


def main():
    now = now_local()
    # If it's already past the afternoon session, report on tomorrow so the
    # crew is looking at the next surfable window rather than the past.
    target = now
    if now.hour >= AFTERNOON_HOUR + 3:
        target = now + timedelta(days=1)
    target_date = target.strftime("%Y-%m-%d")

    try:
        marine, weather = fetch_forecast()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[error] Failed to fetch forecast: {e}", file=sys.stderr)
        return 1

    sessions = {
        "morning": build_session("Dawn Patrol", "6:00 AM", marine, weather,
                                  target_date, MORNING_HOUR),
        "afternoon": build_session("Afternoon", "3:00 PM", marine, weather,
                                    target_date, AFTERNOON_HOUR),
    }

    updated_human = now.strftime("%a, %b ") + str(now.day) + now.strftime(" · %-I:%M %p")
    for_human = target.strftime("%A, %b ") + str(target.day)
    dude, source = dude_narration(sessions, updated_human)

    report = {
        "spot": SPOT_NAME,
        "lat": LAT,
        "lon": LON,
        "updated": now.isoformat(),
        "updated_human": updated_human,
        "for_date": target_date,
        "for_human": for_human,
        "sessions": sessions,
        "dude": dude,
        "dude_source": source,
        "source": "Open-Meteo",
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    print(f"Wrote {OUT_PATH}")
    print(f"  For: {for_human}  ·  narration via: {source}")
    print(f"  AM: {sessions['morning']['rating']} {sessions['morning']['rating_label']}"
          f" ({sessions['morning']['wave_ft']}ft)   "
          f"PM: {sessions['afternoon']['rating']} {sessions['afternoon']['rating_label']}"
          f" ({sessions['afternoon']['wave_ft']}ft)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
