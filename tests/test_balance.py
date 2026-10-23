# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Balance mapping: real values, honest failures, no float arithmetic."""

from __future__ import annotations

import unittest
from decimal import Decimal

from app.balance import BalanceService, _display_of, _trim_amount, _valid_decimal


class FakeAdapter:
    def __init__(self, payload=None, raises=None):
        self.payload = payload
        self.raises = raises
        self.calls = 0

    def balance(self, locale="zh-CN", tz_offset_seconds=0):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.payload


def service(adapter):
    return BalanceService(lambda: adapter, "deepseek-harness", ttl_seconds=60)


class BalanceTests(unittest.TestCase):
    def test_ready_payload_keeps_decimal_strings(self):
        adapter = FakeAdapter(
            {
                "available": True,
                "status": "ok",
                "source": "harness-account",
                "updatedAt": 1800000000,
                "entries": [
                    {"currency": "CNY", "wallet": "normal", "amount": "12.3400000000000000"},
                    {"currency": "CNY", "wallet": "bonus", "amount": "0"},
                ],
            }
        )
        snapshot = service(adapter).snapshot_for_page()
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["entries"][0]["amount"], "12.3400000000000000")
        self.assertEqual(snapshot["display"]["label"], "余额 ¥12.34")
        self.assertEqual(snapshot["display"]["primary"]["amount"], "12.34")

    def test_multi_currency_never_sums_across_currencies(self):
        display = _display_of(
            [
                {"currency": "CNY", "wallet": "normal", "amount": "10.00"},
                {"currency": "USD", "wallet": "normal", "amount": "2.50"},
            ],
            True,
        )
        self.assertEqual(display["label"], "余额 ¥10 / $2.5")
        self.assertIsNone(display["primary"])

    def test_unavailable_payload_is_not_zero(self):
        adapter = FakeAdapter({"available": False, "status": "auth_required", "entries": []})
        snapshot = service(adapter).snapshot_for_page()
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["status"], "auth_required")
        self.assertEqual(snapshot["display"]["label"], "余额暂不可用")
        self.assertNotIn("0", snapshot["display"]["label"])

    def test_failure_with_no_previous_snapshot_reports_unavailable(self):
        from app.dsh_adapter import AdapterError

        adapter = FakeAdapter(raises=AdapterError("runtime unreachable"))
        snapshot = service(adapter).snapshot_for_page()
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["status"], "unavailable")
        self.assertIn("unreachable", snapshot["error"])

    def test_stale_snapshot_is_marked_but_keeps_its_timestamp(self):
        from app.dsh_adapter import AdapterError

        adapter = FakeAdapter(
            {"available": True, "status": "ok", "updatedAt": 1800000000, "entries": [{"currency": "CNY", "wallet": "normal", "amount": "5"}]}
        )
        balance = BalanceService(lambda: adapter, "deepseek-harness", ttl_seconds=0)
        first = balance.snapshot_for_page()
        self.assertFalse(first["stale"])
        adapter.raises = AdapterError("timeout")
        adapter.payload = None
        second = balance.snapshot_for_page()
        self.assertTrue(second["stale"])
        self.assertEqual(second["updatedAt"], first["updatedAt"])
        self.assertTrue(second["available"])

    def test_cache_avoids_repeated_platform_calls(self):
        adapter = FakeAdapter({"available": True, "status": "ok", "updatedAt": 1, "entries": [{"currency": "CNY", "wallet": "normal", "amount": "1"}]})
        balance = service(adapter)
        balance.snapshot_for_page()
        balance.snapshot_for_page()
        self.assertEqual(adapter.calls, 1)
        balance.snapshot_for_page(force=True)
        self.assertEqual(adapter.calls, 2)

    def test_invalid_amounts_are_dropped_not_rounded(self):
        self.assertTrue(_valid_decimal("0E-16"))
        self.assertTrue(_valid_decimal("5.0000000000000000"))
        self.assertFalse(_valid_decimal("NaN"))
        self.assertFalse(_valid_decimal("abc"))
        self.assertEqual(_trim_amount(Decimal("5.0000000000000000")), "5")


if __name__ == "__main__":
    unittest.main()
