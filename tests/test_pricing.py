# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Peak / off-peak phase: window parsing, boundaries and the countdown."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.pricing import (
    DEFAULT_DAYS,
    DEFAULT_OFFSET_MINUTES,
    DEFAULT_WINDOWS,
    build_settings,
    is_peak,
    next_switch,
    parse_days,
    parse_windows,
    payload_for,
)

UTC = timezone.utc


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """A Beijing wall-clock moment, which is what is_peak/next_switch take."""
    return datetime(year, month, day, hour, minute)


def at_utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """The same moment expressed as an aware UTC datetime (payload_for input)."""
    return at(year, month, day, hour, minute).replace(tzinfo=timezone.utc) - timedelta(minutes=DEFAULT_OFFSET_MINUTES)


SETTINGS = build_settings(DEFAULT_WINDOWS, DEFAULT_DAYS, DEFAULT_OFFSET_MINUTES)


class WindowParsingTests(unittest.TestCase):
    def test_parses_minutes_of_day(self):
        self.assertEqual(parse_windows("09:00-12:00,14:00-18:00"), ((540, 720), (840, 1080)))

    def test_ignores_malformed_ranges(self):
        self.assertEqual(parse_windows("09:00-12:00, oops, 13:00-12:00, 25:00-26:00"), ((540, 720),))

    def test_days(self):
        self.assertEqual(parse_days("mon-fri"), frozenset({0, 1, 2, 3, 4}))
        self.assertEqual(parse_days("sat,sun"), frozenset({5, 6}))
        self.assertEqual(parse_days("all"), frozenset(range(7)))
        self.assertEqual(parse_days(""), frozenset())

    def test_invalid_specs_fall_back_and_are_reported(self):
        settings = build_settings("oops", "oops", 5000)
        self.assertEqual(settings.windows, ((540, 720), (840, 1080)))
        self.assertEqual(settings.days, frozenset({0, 1, 2, 3, 4}))
        self.assertEqual(settings.offset_minutes, DEFAULT_OFFSET_MINUTES)
        self.assertIn("peak-windows", settings.invalid)


class PhaseTests(unittest.TestCase):
    def test_weekday_peak_and_trough(self):
        # 2026-10-26 is a Monday.
        self.assertTrue(is_peak(at(2026, 10, 26, 9, 0), SETTINGS))
        self.assertTrue(is_peak(at(2026, 10, 26, 11, 59), SETTINGS))
        self.assertFalse(is_peak(at(2026, 10, 26, 12, 0), SETTINGS))
        self.assertFalse(is_peak(at(2026, 10, 26, 13, 59), SETTINGS))
        self.assertTrue(is_peak(at(2026, 10, 26, 14, 0), SETTINGS))
        self.assertTrue(is_peak(at(2026, 10, 26, 17, 59), SETTINGS))
        self.assertFalse(is_peak(at(2026, 10, 26, 18, 0), SETTINGS))
        self.assertFalse(is_peak(at(2026, 10, 26, 8, 59), SETTINGS))

    def test_weekend_is_always_off_peak(self):
        # 2026-10-24 is a Saturday, 2026-10-25 a Sunday.
        for hour in (9, 10, 14, 17):
            self.assertFalse(is_peak(at(2026, 10, 24, hour), SETTINGS))
            self.assertFalse(is_peak(at(2026, 10, 25, hour), SETTINGS))

    def test_next_switch_from_a_weekend_points_at_monday_morning(self):
        switch = next_switch(at(2026, 10, 25, 4, 19), SETTINGS)
        self.assertEqual(switch, datetime(2026, 10, 26, 9, 0))

    def test_next_switch_inside_the_peak_points_at_noon(self):
        switch = next_switch(at(2026, 10, 26, 10, 30), SETTINGS)
        self.assertEqual(switch, datetime(2026, 10, 26, 12, 0))

    def test_next_switch_after_the_last_peak_points_at_the_next_workday(self):
        switch = next_switch(at(2026, 10, 30, 18, 5), SETTINGS)  # Friday evening
        self.assertEqual(switch, datetime(2026, 11, 2, 9, 0))

    def test_payload_has_no_money_and_a_countdown(self):
        payload = payload_for(DEFAULT_WINDOWS, DEFAULT_DAYS, DEFAULT_OFFSET_MINUTES, now=at_utc(2026, 10, 24, 10, 0))
        self.assertEqual(payload["phase"], "off-peak")
        self.assertEqual(payload["label"], "闲时")
        self.assertTrue(payload["weekend"])
        self.assertEqual(payload["nextSwitch"], "2026-10-26 09:00")
        self.assertGreater(payload["secondsLeft"], 0)
        self.assertNotIn("amount", payload)
        self.assertIn("09:00", payload["windows"])

    def test_payload_marks_peak_on_a_weekday(self):
        payload = payload_for(DEFAULT_WINDOWS, DEFAULT_DAYS, DEFAULT_OFFSET_MINUTES, now=at_utc(2026, 10, 26, 15, 0))
        self.assertEqual(payload["phase"], "peak")
        self.assertEqual(payload["label"], "忙时")
        self.assertFalse(payload["weekend"])
        self.assertEqual(payload["nextSwitch"], "2026-10-26 18:00")

    def test_custom_schedule_is_honoured(self):
        payload = payload_for("00:00-23:59", "all", DEFAULT_OFFSET_MINUTES, now=at_utc(2026, 10, 25, 12, 0))
        self.assertEqual(payload["phase"], "peak")
        self.assertEqual(payload["days"], "每天")

    def test_evening_trough_points_at_the_next_morning(self):
        switch = next_switch(at(2026, 10, 26, 23, 0), SETTINGS)
        self.assertEqual(switch, datetime(2026, 10, 27, 9, 0))


if __name__ == "__main__":
    unittest.main()
