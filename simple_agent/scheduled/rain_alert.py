"""Heavy-rain/storm alert. Deterministic - no LLM call, so this costs
nothing to run as often as you like. Meant to run on a schedule (GitHub
Actions, see ../../.github/workflows/rain-alert.yml, or VPS cron via
rain_alert_cron.py); it looks at the *forecast* for the next few hours
from MET Norway and pushes a notification via ntfy.sh only when rain
above a threshold is actually coming.

Why the forecast and not current conditions: an alert that fires on what
is falling right now reaches you when you are already standing in it.
Looking ahead ALERT_LOOKAHEAD_HOURS gives you time to grab an umbrella,
and the message says how far out the rain is.

Weather data: MET Norway (Norwegian Meteorological Institute)
locationforecast 2.0. No API key or signup - their terms just ask for a
User-Agent that identifies the app and gives them a way to make contact,
which is what ALERT_CONTACT is for. Geocoding is still Open-Meteo's
geocoder, which is a separate service from their forecast API.

Env vars:
  NTFY_TOPIC            required - your ntfy.sh topic (see notebook 07)

  ALERT_LAT / ALERT_LON optional - exact coordinates. Set BOTH to skip
                        geocoding entirely. Strongly preferred for any
                        place name that isn't unique: "Hanahata" matches
                        a neighbourhood in Adachi-ku, Tokyo AND one in
                        Fukuoka, 900 km apart.
  ALERT_LOCATION        optional - place name to geocode, default
                        "Tokyo, Japan". Ignored when ALERT_LAT/ALERT_LON
                        are set. Add a region to disambiguate
                        ("Hanahata, Tokyo"); an ambiguous name is
                        reported on stdout so it can't silently pick the
                        wrong city.
  ALERT_LABEL           optional - what to call the place in the alert
                        text. Defaults to the geocoded name, or
                        "lat,lon" when coordinates were given directly.

  RAIN_THRESHOLD_MM     optional - forecast mm in a single hour that
                        counts as "heavy", default 7.5 (the standard
                        meteorological heavy-rain cutoff). Values <= 0
                        mean "any measurable rain" (>= 0.1 mm) rather
                        than "always alert".
  ALERT_LOOKAHEAD_HOURS optional - how far ahead to look, default 6.
  ALERT_COOLDOWN_MIN    optional - minutes to stay quiet after sending,
                        default 180, so one storm doesn't generate one
                        push per cron tick. 0 disables the cooldown.
  ALERT_STATE_FILE      optional - where the cooldown timestamp lives,
                        default ../.rain_alert_state.json (gitignored).
  ALERT_CONTACT         optional - contact address put in the User-Agent
                        sent to MET Norway, per their terms of service.
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

LOCATION = os.environ.get("ALERT_LOCATION") or "Tokyo, Japan"
ALERT_LAT = os.environ.get("ALERT_LAT")
ALERT_LON = os.environ.get("ALERT_LON")
ALERT_LABEL = os.environ.get("ALERT_LABEL")
RAIN_THRESHOLD_MM = float(os.environ.get("RAIN_THRESHOLD_MM") or "7.5")
LOOKAHEAD_HOURS = int(os.environ.get("ALERT_LOOKAHEAD_HOURS") or "6")
COOLDOWN_MIN = int(os.environ.get("ALERT_COOLDOWN_MIN") or "180")
CONTACT = os.environ.get("ALERT_CONTACT") or "https://github.com/"

STATE_FILE = os.environ.get("ALERT_STATE_FILE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".rain_alert_state.json"
)

# MET Norway's terms require a User-Agent identifying the application with
# a way to contact whoever runs it. A generic requests/urllib UA gets 403.
USER_AGENT = f"agentic-rain-alert/2.0 ({CONTACT})"

FORECAST_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

# The smallest precipitation worth calling rain at all. Also the floor for
# RAIN_THRESHOLD_MM, so a threshold of 0 in scheduled.env means "tell me
# about any rain" instead of "fire on every single run, forever".
MIN_MEASURABLE_MM = 0.1

# MET Norway symbol_code values that are worth flagging on their own,
# whatever the millimetre reading says - a thunderstorm is worth knowing
# about even when it drops very little water. Codes arrive suffixed with
# _day / _night / _polartwilight, which _symbol_is_severe() strips.
SEVERE_SYMBOLS = {
    "heavyrain", "heavyrainshowers",
    "heavysleet", "heavysleetshowers",
    "heavysnow", "heavysnowshowers",
    "thunder",
    "rainandthunder", "heavyrainandthunder", "rainshowersandthunder",
    "heavyrainshowersandthunder",
    "sleetandthunder", "heavysleetandthunder", "sleetshowersandthunder",
    "heavysleetshowersandthunder",
    "snowandthunder", "heavysnowandthunder", "snowshowersandthunder",
    "heavysnowshowersandthunder",
}

# Prettier names for the symbol codes we actually put in a message.
SYMBOL_LABELS = {
    "lightrain": "Light rain", "rain": "Rain", "heavyrain": "Heavy rain",
    "lightrainshowers": "Light rain showers", "rainshowers": "Rain showers",
    "heavyrainshowers": "Heavy rain showers",
    "lightsleet": "Light sleet", "sleet": "Sleet", "heavysleet": "Heavy sleet",
    "lightsnow": "Light snow", "snow": "Snow", "heavysnow": "Heavy snow",
    "thunder": "Thunderstorm",
    "rainandthunder": "Rain and thunder",
    "heavyrainandthunder": "Heavy rain and thunder",
    "rainshowersandthunder": "Rain showers and thunder",
    "sleetandthunder": "Sleet and thunder",
    "snowandthunder": "Snow and thunder",
    "fog": "Fog", "cloudy": "Cloudy", "partlycloudy": "Partly cloudy",
    "fair": "Fair", "clearsky": "Clear sky",
}


def _request_with_retry(method, url, attempts=3, **kwargs):
    last_exc = None
    for _ in range(attempts):
        try:
            resp = requests.request(method, url, timeout=20, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
    raise last_exc


def _base_symbol(symbol_code: str) -> str:
    """'heavyrainshowers_day' -> 'heavyrainshowers'."""
    return (symbol_code or "").split("_")[0]


def _symbol_is_severe(symbol_code: str) -> bool:
    return _base_symbol(symbol_code) in SEVERE_SYMBOLS


def describe_symbol(symbol_code: str) -> str:
    base = _base_symbol(symbol_code)
    return SYMBOL_LABELS.get(base, base.replace("_", " ").capitalize() or "Unknown conditions")


def effective_threshold() -> float:
    """RAIN_THRESHOLD_MM, floored so 0 can't mean 'alert on 0.0 mm'.

    The old version compared `precipitation >= RAIN_THRESHOLD_MM` directly,
    so RAIN_THRESHOLD_MM=0 made every run an alert - dry, clear weather
    included.
    """
    return max(RAIN_THRESHOLD_MM, MIN_MEASURABLE_MM)


def geocode(location: str) -> dict:
    """Resolve a place name, and complain loudly when it's ambiguous.

    Fetches several matches rather than one so a name shared by more than
    one town can be reported instead of silently resolving to whichever
    Open-Meteo happens to rank first.
    """
    resp = _request_with_retry(
        "GET",
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 5},
    )
    results = resp.json().get("results")
    if not results:
        raise RuntimeError(f"could not find a location matching '{location}'")

    if len(results) > 1:
        others = ", ".join(
            f"{r.get('name')} ({r.get('admin1') or r.get('country')})" for r in results[1:]
        )
        print(
            f"warning: '{location}' is ambiguous - using "
            f"{results[0].get('name')} ({results[0].get('admin1') or results[0].get('country')}); "
            f"also matched: {others}. Set ALERT_LAT/ALERT_LON to pin it exactly."
        )
    return results[0]


def resolve_place() -> dict:
    """Where to check, as {'label', 'latitude', 'longitude'}.

    Explicit coordinates win over the place name, which is the only way to
    be certain about a name several towns share.
    """
    if ALERT_LAT and ALERT_LON:
        lat, lon = float(ALERT_LAT), float(ALERT_LON)
        return {"label": ALERT_LABEL or f"{lat:.4f},{lon:.4f}", "latitude": lat, "longitude": lon}

    place = geocode(LOCATION)
    return {
        "label": ALERT_LABEL or place["name"],
        "latitude": place["latitude"],
        "longitude": place["longitude"],
    }


def get_forecast(lat: float, lon: float) -> list:
    """Hourly timeseries from MET Norway.

    Coordinates are truncated to 4 decimals because MET Norway asks for
    that - it keeps their cache from being fragmented by needless
    precision, and 4 decimals is ~11 m anyway.
    """
    resp = _request_with_retry(
        "GET",
        FORECAST_URL,
        params={"lat": round(lat, 4), "lon": round(lon, 4)},
        headers={"User-Agent": USER_AGENT},
    )
    return resp.json().get("properties", {}).get("timeseries", [])


def _parse_time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def find_worst_hour(timeseries: list, now: datetime = None) -> dict:
    """The most severe hour inside the lookahead window, or None.

    Returns the entry with the highest forecast precipitation; a severe
    symbol (thunder, heavy anything) always beats a non-severe one even if
    fewer millimetres are attached to it.
    """
    now = now or datetime.now(timezone.utc)
    threshold = effective_threshold()
    best = None

    for entry in timeseries:
        when = _parse_time(entry["time"])
        hours_out = (when - now).total_seconds() / 3600
        if hours_out < -1 or hours_out > LOOKAHEAD_HOURS:
            continue

        block = entry.get("data", {}).get("next_1_hours")
        if not block:
            continue

        mm = block.get("details", {}).get("precipitation_amount") or 0.0
        symbol = block.get("summary", {}).get("symbol_code", "")
        severe = _symbol_is_severe(symbol)

        # Severity alone is not enough - a "heavyrain" symbol with 0.0 mm
        # attached is not something to wake someone up for.
        if mm < threshold and not (severe and mm >= MIN_MEASURABLE_MM):
            continue

        candidate = {
            "time": when,
            "hours_out": max(hours_out, 0),
            "mm": mm,
            "symbol": symbol,
            "severe": severe,
            "temperature": entry.get("data", {}).get("instant", {}).get("details", {}).get("air_temperature"),
            "wind": entry.get("data", {}).get("instant", {}).get("details", {}).get("wind_speed"),
        }
        if best is None or (candidate["severe"], candidate["mm"]) > (best["severe"], best["mm"]):
            best = candidate

    return best


def _read_state() -> dict:
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as fh:
            json.dump(state, fh)
    except OSError as exc:
        print(f"warning: could not write {STATE_FILE}: {exc}")


def in_cooldown(now_ts: float = None) -> bool:
    """True when an alert went out recently enough to skip this one.

    A storm sitting over the city for three hours used to mean six pushes
    at a 30-minute cron interval. One is enough.
    """
    if COOLDOWN_MIN <= 0:
        return False
    last = _read_state().get("last_alert_ts")
    if not last:
        return False
    now_ts = now_ts if now_ts is not None else time.time()
    return (now_ts - last) < COOLDOWN_MIN * 60


def record_alert(now_ts: float = None) -> None:
    _write_state({"last_alert_ts": now_ts if now_ts is not None else time.time()})


def format_alert(label: str, worst: dict) -> tuple:
    condition = describe_symbol(worst["symbol"])
    hours_out = worst["hours_out"]

    if hours_out < 0.5:
        timing = "starting now"
    elif hours_out < 1.5:
        timing = "within the hour"
    else:
        local = worst["time"].astimezone()
        timing = f"in ~{round(hours_out)}h (around {local:%H:%M})"

    parts = [f"{condition} {timing} - {worst['mm']:.1f} mm expected"]
    if worst.get("temperature") is not None:
        parts.append(f"{worst['temperature']}°C")
    if worst.get("wind") is not None:
        parts.append(f"wind {worst['wind']} m/s")

    return f"Weather alert: {label}", ", ".join(parts)


def send_notification(title: str, message: str) -> None:
    topic = os.environ["NTFY_TOPIC"]
    _request_with_retry(
        "POST",
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "5", "Tags": "warning"},
    )


def main() -> None:
    if not os.environ.get("NTFY_TOPIC"):
        raise SystemExit(
            "NTFY_TOPIC is not set. Add it as a repository secret: "
            "Settings -> Secrets and variables -> Actions -> New repository secret."
        )

    place = resolve_place()
    timeseries = get_forecast(place["latitude"], place["longitude"])
    if not timeseries:
        raise SystemExit("MET Norway returned an empty forecast - nothing to check.")

    worst = find_worst_hour(timeseries)
    threshold = effective_threshold()
    print(
        f"{place['label']} ({place['latitude']:.4f},{place['longitude']:.4f}): "
        f"checked next {LOOKAHEAD_HOURS}h, threshold {threshold} mm/h"
    )

    if worst is None:
        print("Nothing above threshold in the lookahead window - no notification sent.")
        return

    if in_cooldown():
        print(
            f"{worst['mm']:.1f} mm forecast, but an alert went out within the last "
            f"{COOLDOWN_MIN} min - staying quiet."
        )
        return

    title, message = format_alert(place["label"], worst)
    send_notification(title, message)
    record_alert()
    print("Alert sent:", message)


if __name__ == "__main__":
    main()
