# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Model catalog and route validation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

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


class NativeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.registry = patch.object(models, "_discovered", {})
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def publish(self):
        models.refresh(SimpleNamespace(models=lambda: {"models": [
            {"provider": "gateway", "id": "deepseek-v4-pro", "name": "Gateway Pro",
             "efforts": [{"id": "medium"}, {"id": "xhigh"}], "defaultEffort": "medium",
             "apiKey": "must-not-leak", "baseURL": "must-not-leak"},
            {"provider": "deepseek-account", "id": "deepseek-v4-pro", "name": "Account Pro",
             "efforts": [{"id": "high"}], "defaultEffort": "high"},
            {"provider": "gateway", "id": "plain", "name": "Plain",
             "efforts": [{"id": "default"}], "defaultEffort": "default"},
        ]}))

    def test_provider_identity_and_supported_efforts(self):
        self.publish()
        route = models.resolve("deepseek-account", "gateway::deepseek-v4-pro", "xhigh", "deepseek-v4-pro", "high")
        self.assertEqual((route.provider, route.model, route.effort), ("gateway", "deepseek-v4-pro", "xhigh"))
        with self.assertRaises(models.RouteError):
            models.resolve("deepseek-account", "gateway::deepseek-v4-pro", "max", "deepseek-v4-pro", "high")
        ids = [item["id"] for item in models.available()]
        self.assertEqual(len(set(ids)), 3)
        payload = models.catalog("deepseek-account", "deepseek-v4-pro", "high", route)
        self.assertEqual(payload["session"]["model"], "gateway::deepseek-v4-pro")
        self.assertNotIn("must-not-leak", str(payload))

    def test_missing_effort_uses_model_default_and_plain_model_supported(self):
        self.publish()
        route = models.resolve("deepseek-account", "gateway::plain", "", "deepseek-v4-pro", "high")
        self.assertEqual(route.effort, "default")
        route = models.resolve("deepseek-account", "gateway::deepseek-v4-pro", None, "deepseek-v4-pro", "high")
        self.assertEqual(route.effort, "medium")

    def test_refresh_removes_stale_models(self):
        self.publish()
        models.refresh(SimpleNamespace(models=lambda: {"models": [
            {"provider": "gateway", "id": "new", "efforts": [{"id": "low"}]}
        ]}))
        self.assertEqual([item["id"] for item in models.available()], ["gateway::new"])


if __name__ == "__main__":
    unittest.main()
