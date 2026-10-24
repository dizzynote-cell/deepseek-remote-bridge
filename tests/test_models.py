# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Model catalog and route validation."""

from __future__ import annotations

import unittest

from app import models


class ResolveTests(unittest.TestCase):
    def test_defaults_come_from_configuration(self):
        route = models.resolve("deepseek-account", "", "", "deepseek-flash", "max")
        self.assertEqual((route.model, route.effort), ("deepseek-flash", "max"))
        self.assertEqual(route.label, "deepseek-account/deepseek-flash/max")

    def test_explicit_values_win(self):
        route = models.resolve("deepseek-account", "deepseek-v4-pro", "low", "deepseek-flash", "max")
        self.assertEqual((route.model, route.effort), ("deepseek-v4-pro", "low"))

    def test_unknown_model_is_refused(self):
        with self.assertRaises(models.RouteError) as caught:
            models.resolve("deepseek-account", "gpt-4o", "high", "deepseek-flash", "max")
        self.assertEqual(caught.exception.code, "unknown_model")

    def test_unknown_effort_is_refused(self):
        with self.assertRaises(models.RouteError) as caught:
            models.resolve("deepseek-account", "deepseek-flash", "turbo", "deepseek-flash", "max")
        self.assertEqual(caught.exception.code, "unknown_effort")

    def test_catalog_matches_the_shipped_harness(self):
        payload = models.catalog("deepseek-account", "deepseek-flash", "max")
        ids = [item["id"] for item in payload["models"]]
        self.assertIn("deepseek-flash", ids)
        self.assertIn("deepseek-v4-pro", ids)
        efforts = [item["id"] for item in payload["efforts"]]
        self.assertEqual(efforts, ["off", "low", "high", "max"])
        self.assertIsNone(payload["session"])
        self.assertEqual(payload["bridgeDefault"], {"model": "deepseek-flash", "effort": "max"})

    def test_catalog_reports_the_session_route(self):
        route = models.Route("deepseek-account", "deepseek-v4-pro", "low")
        payload = models.catalog("deepseek-account", "deepseek-flash", "max", route)
        self.assertEqual(payload["session"], {"model": "deepseek-v4-pro", "effort": "low", "route": route.label})

    def test_labels_are_short_and_readable(self):
        labels = models.labels("deepseek-v4-pro", "off")
        self.assertEqual(labels["model"], "V4 Pro")
        self.assertEqual(labels["modelName"], "DeepSeek-V4-Pro")
        self.assertEqual(labels["effort"], "关闭")


if __name__ == "__main__":
    unittest.main()
