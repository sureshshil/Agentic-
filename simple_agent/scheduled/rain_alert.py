"""Heavy-rain/storm alert. Deterministic - no LLM call, so this costs
nothing to run as often as you like. Meant to run on a schedule via
GitHub Actions (see ../../.github/workflows/rain-alert.yml); checks
current conditions via Open-Meteo and pushes a notification via ntfy.sh
only when they cross a "heavy" threshold.

Env vars:
  NTFY_TOPIC       required - your ntfy.sh topic (see notebook 07)
  ALERT_LOCATION   optional - default "Tokyo, Japan"
  RAIN_THRESHOLD_MM  optional - precipitation mm/hour to count as "heavy",
                     default 7.5 (standard meteorological heavy-rain cutoff)
"""

import os

import requests

LOCATION = os.environ.get("ALERT_LOCATION", "Tokyo, Japan")
RAIN_THRESHOLD_MM = float(os.environ.get("RAIN_THRESHOLD_MM", "7.5"))

# WMO weather codes that count as "heavy" regardless of the precipitation
# reading (e.g. any thunderstorm, even a brief one, is worth flagging).
HEAVY_WEATHER_CODES = {65, 67, 75, 82, 86, 95, 96, 99}

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
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


def geocode(location: str) -> dict:
    resp = _request_with_retry(
        "GET",
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1},
    )
    results = resp.json().get("results")
    if not results:
        raise RuntimeError(f"could not find a location matching '{location}'")
    return results[0]


def get_current_conditions(lat: float, lon: float) -> dict:
    resp = _request_with_retry(
        "GET",
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
        },
    )
    return resp.json().get("current", {})


def is_heavy(current: dict) -> bool:
    code = current.get("weather_code")
    precipitation = current.get("precipitation") or 0
    return code in HEAVY_WEATHER_CODES or precipitation >= RAIN_THRESHOLD_MM


def send_notification(title: str, message: str) -> None:
    topic = os.environ["NTFY_TOPIC"]
    _request_with_retry(
        "POST",
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "5", "Tags": "warning"},
    )


def main() -> None:
    place = geocode(LOCATION)
    current = get_current_conditions(place["latitude"], place["longitude"])
    condition = WMO_CODES.get(current.get("weather_code"), "Unknown conditions")
    precipitation = current.get("precipitation")

    print(f"{place['name']}: {condition}, precipitation {precipitation} mm/h")

    if not is_heavy(current):
        print("Below alert threshold - no notification sent.")
        return

    message = (
        f"{condition} in {place['name']} - {precipitation} mm/h precipitation, "
        f"{current.get('temperature_2m')}°C, "
        f"wind {current.get('wind_speed_10m')} km/h"
    )
    send_notification(f"Weather alert: {place['name']}", message)
    print("Alert sent:", message)


if __name__ == "__main__":
    main()
