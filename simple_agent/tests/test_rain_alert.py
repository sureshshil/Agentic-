"""Regression tests for the rain alert.

The alert had three defects that together made it useless: geocode() took
the first search result, so "Hanahata" resolved to Fukuoka rather than the
Adachi-ku neighbourhood 900 km away; RAIN_THRESHOLD_MM=0 made
`precipitation >= threshold` true on dry, clear days, so it pushed on every
run; and it only ever read current conditions, so an alert arrived once you
were already in the rain. See scheduled/rain_alert.py.

No live API calls: requests.request is mocked.

Run: python3 -m unittest tests.test_rain_alert -v   (from simple_agent/)
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduled"))
import rain_alert as ra


NOW = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)


def hour(hours_out, mm, symbol="rain", temp=20.0, wind=2.0):
    """One MET Norway timeseries entry, `hours_out` from NOW."""
    return {
        "time": (NOW + timedelta(hours=hours_out)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": {
            "instant": {"details": {"air_temperature": temp, "wind_speed": wind}},
            "next_1_hours": {
                "summary": {"symbol_code": symbol},
                "details": {"precipitation_amount": mm},
            },
        },
    }


class ThresholdTest(unittest.TestCase):
    def test_zero_threshold_does_not_mean_always_alert(self):
        # The old bug: RAIN_THRESHOLD_MM=0 with `precipitation >= 0` fired
        # on clear weather, every run.
        with patch.object(ra, "RAIN_THRESHOLD_MM", 0.0):
            self.assertEqual(ra.effective_threshold(), ra.MIN_MEASURABLE_MM)
            self.assertIsNone(ra.find_worst_hour([hour(1, 0.0, "clearsky_day")], now=NOW))

    def test_zero_threshold_still_reports_light_rain(self):
        with patch.object(ra, "RAIN_THRESHOLD_MM", 0.0):
            worst = ra.find_worst_hour([hour(2, 0.4, "lightrain")], now=NOW)
        self.assertIsNotNone(worst)
        self.assertEqual(worst["mm"], 0.4)

    def test_normal_threshold_ignores_drizzle(self):
        with patch.object(ra, "RAIN_THRESHOLD_MM", 7.5):
            self.assertIsNone(ra.find_worst_hour([hour(1, 1.2, "lightrain")], now=NOW))


class LookaheadTest(unittest.TestCase):
    def test_finds_rain_before_it_starts(self):
        series = [hour(0, 0.0, "cloudy"), hour(1, 0.2), hour(3, 14.1, "heavyrain")]
        with patch.object(ra, "RAIN_THRESHOLD_MM", 7.5), patch.object(ra, "LOOKAHEAD_HOURS", 6):
            worst = ra.find_worst_hour(series, now=NOW)
        self.assertEqual(worst["mm"], 14.1)
        self.assertAlmostEqual(worst["hours_out"], 3.0)

    def test_ignores_rain_beyond_the_window(self):
        with patch.object(ra, "RAIN_THRESHOLD_MM", 7.5), patch.object(ra, "LOOKAHEAD_HOURS", 6):
            self.assertIsNone(ra.find_worst_hour([hour(9, 30.0, "heavyrain")], now=NOW))

    def test_picks_the_worst_hour_not_the_first(self):
        series = [hour(1, 8.0), hour(2, 21.0), hour(3, 9.0)]
        with patch.object(ra, "RAIN_THRESHOLD_MM", 7.5), patch.object(ra, "LOOKAHEAD_HOURS", 6):
            worst = ra.find_worst_hour(series, now=NOW)
        self.assertEqual(worst["mm"], 21.0)

    def test_thunder_outranks_a_wetter_plain_hour(self):
        series = [hour(1, 12.0, "rain"), hour(2, 8.0, "rainandthunder_day")]
        with patch.object(ra, "RAIN_THRESHOLD_MM", 7.5), patch.object(ra, "LOOKAHEAD_HOURS", 6):
            worst = ra.find_worst_hour(series, now=NOW)
        self.assertTrue(worst["severe"])
        self.assertEqual(worst["mm"], 8.0)

    def test_severe_symbol_with_no_rain_is_not_an_alert(self):
        with patch.object(ra, "RAIN_THRESHOLD_MM", 7.5), patch.object(ra, "LOOKAHEAD_HOURS", 6):
            self.assertIsNone(ra.find_worst_hour([hour(2, 0.0, "heavyrain_day")], now=NOW))

    def test_entries_without_a_next_1_hours_block_are_skipped(self):
        # The tail of MET Norway's series only carries next_6_hours.
        entry = hour(2, 9.0)
        del entry["data"]["next_1_hours"]
        with patch.object(ra, "RAIN_THRESHOLD_MM", 7.5):
            self.assertIsNone(ra.find_worst_hour([entry], now=NOW))


class SymbolTest(unittest.TestCase):
    def test_day_night_suffixes_are_stripped(self):
        self.assertTrue(ra._symbol_is_severe("heavyrainshowers_day"))
        self.assertTrue(ra._symbol_is_severe("thunder_polartwilight"))
        self.assertFalse(ra._symbol_is_severe("lightrain_night"))

    def test_unknown_symbol_still_describes_something(self):
        self.assertEqual(ra.describe_symbol("heavyrain_day"), "Heavy rain")
        self.assertTrue(ra.describe_symbol("somethingnew_day"))


class GeocodeTest(unittest.TestCase):
    def test_ambiguous_name_is_reported(self):
        resp = MagicMock()
        resp.json.return_value = {"results": [
            {"name": "Hanahata", "latitude": 33.5, "longitude": 130.4, "admin1": "Fukuoka"},
            {"name": "Hanahata", "latitude": 35.8, "longitude": 139.8, "admin1": "Tokyo"},
        ]}
        with patch.object(ra.requests, "request", return_value=resp), \
             patch("builtins.print") as printed:
            ra.geocode("Hanahata")
        warning = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("ambiguous", warning)
        self.assertIn("Tokyo", warning)  # the match it would have silently dropped

    def test_no_results_raises(self):
        resp = MagicMock()
        resp.json.return_value = {"results": []}
        with patch.object(ra.requests, "request", return_value=resp):
            with self.assertRaises(RuntimeError):
                ra.geocode("nowhere at all")

    def test_explicit_coordinates_skip_geocoding_entirely(self):
        with patch.object(ra, "ALERT_LAT", "35.80408"), \
             patch.object(ra, "ALERT_LON", "139.81168"), \
             patch.object(ra, "ALERT_LABEL", "Hanahata, Adachi-ku"), \
             patch.object(ra, "geocode", side_effect=AssertionError("should not geocode")):
            place = ra.resolve_place()
        self.assertEqual(place["label"], "Hanahata, Adachi-ku")
        self.assertAlmostEqual(place["latitude"], 35.80408)


class CooldownTest(unittest.TestCase):
    def setUp(self):
        self.state = os.path.join(
            os.environ.get("CLAUDE_JOB_DIR", "/tmp"), "rain_alert_test_state.json"
        )
        if os.path.exists(self.state):
            os.remove(self.state)

    tearDown = setUp

    def test_no_state_file_means_no_cooldown(self):
        with patch.object(ra, "STATE_FILE", self.state), patch.object(ra, "COOLDOWN_MIN", 180):
            self.assertFalse(ra.in_cooldown())

    def test_recent_alert_suppresses_the_next_one(self):
        with patch.object(ra, "STATE_FILE", self.state), patch.object(ra, "COOLDOWN_MIN", 180):
            ra.record_alert(now_ts=1000.0)
            self.assertTrue(ra.in_cooldown(now_ts=1000.0 + 60 * 60))       # 1h later
            self.assertFalse(ra.in_cooldown(now_ts=1000.0 + 4 * 60 * 60))  # 4h later

    def test_cooldown_can_be_disabled(self):
        with patch.object(ra, "STATE_FILE", self.state), patch.object(ra, "COOLDOWN_MIN", 0):
            ra.record_alert(now_ts=1000.0)
            self.assertFalse(ra.in_cooldown(now_ts=1000.0))


class FormatTest(unittest.TestCase):
    def _worst(self, hours_out, mm=14.1, symbol="heavyrain_day"):
        return {"time": NOW + timedelta(hours=hours_out), "hours_out": hours_out,
                "mm": mm, "symbol": symbol, "severe": True,
                "temperature": 24.1, "wind": 2.0}

    def test_imminent_rain_reads_as_now(self):
        _, body = ra.format_alert("Hanahata", self._worst(0.2))
        self.assertIn("starting now", body)

    def test_lead_time_is_in_the_message(self):
        title, body = ra.format_alert("Hanahata, Adachi-ku", self._worst(3))
        self.assertEqual(title, "Weather alert: Hanahata, Adachi-ku")
        self.assertIn("~3h", body)
        self.assertIn("14.1 mm", body)
        self.assertIn("Heavy rain", body)


class ForecastRequestTest(unittest.TestCase):
    def test_sends_a_user_agent_and_rounds_coordinates(self):
        # MET Norway 403s a default requests UA, and asks that coordinates
        # be truncated to 4 decimals so their cache isn't fragmented.
        resp = MagicMock()
        resp.json.return_value = {"properties": {"timeseries": []}}
        with patch.object(ra.requests, "request", return_value=resp) as req:
            ra.get_forecast(35.804081234, 139.811681234)
        kwargs = req.call_args.kwargs
        self.assertIn("agentic-rain-alert", kwargs["headers"]["User-Agent"])
        self.assertEqual(kwargs["params"]["lat"], 35.8041)
        self.assertEqual(kwargs["params"]["lon"], 139.8117)


if __name__ == "__main__":
    unittest.main()
