# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Native DeepSeek account balance, cached and never fabricated.

The amount comes from the harness runtime's own account provider, which reads the
signed-in Platform wallet. A failure is reported as a failure: it never becomes
zero, and a stale snapshot keeps its original timestamp.
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from .dsh_adapter import AdapterClient, AdapterError

CURRENCY_SYMBOL = {"CNY": "¥", "USD": "$"}


class BalanceService:
    """Fetch, cache, and describe the bridge account's balance."""

    def __init__(self, adapter_factory, provider_id: str, ttl_seconds: float = 60.0, timeout: float = 45.0):
        self._adapter_factory = adapter_factory
        self.provider_id = provider_id
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self.lock = threading.RLock()
        self.snapshot: dict[str, Any] | None = None
        self.fetched_at = 0.0
        self.last_error = ""
        self.refreshing = False

    @property
    def adapter(self) -> AdapterClient:
        return self._adapter_factory()

    def snapshot_for_page(self, force: bool = False) -> dict[str, Any]:
        with self.lock:
            age = time.time() - self.fetched_at
            fresh = self.snapshot is not None and age < self.ttl_seconds
            if fresh and not force:
                return self._decorate(self.snapshot, stale=False, age=age)
        payload = self._fetch()
        with self.lock:
            if payload is not None:
                self.snapshot = payload
                self.fetched_at = time.time()
                self.last_error = ""
                return self._decorate(payload, stale=False, age=0.0)
            if self.snapshot is not None:
                return self._decorate(self.snapshot, stale=True, age=time.time() - self.fetched_at)
        return {
            "providerId": self.provider_id,
            "available": False,
            "status": "unavailable",
            "source": "harness-account",
            "updatedAt": int(time.time()),
            "stale": False,
            "entries": [],
            "display": {"label": "余额暂不可用", "primary": None},
            "error": self.last_error or "账户余额读取失败",
        }

    def _fetch(self) -> dict[str, Any] | None:
        with self.lock:
            if self.refreshing:
                return None
            self.refreshing = True
        try:
            raw = self.adapter.balance()
        except AdapterError as error:
            with self.lock:
                self.last_error = str(error)
            return None
        finally:
            with self.lock:
                self.refreshing = False
        entries = []
        for item in raw.get("entries") or []:
            if not isinstance(item, dict):
                continue
            amount = str(item.get("amount") or "")
            if not _valid_decimal(amount):
                continue
            entries.append(
                {
                    "currency": str(item.get("currency") or ""),
                    "wallet": str(item.get("wallet") or "normal"),
                    "amount": amount,
                }
            )
        return {
            "providerId": self.provider_id,
            "available": bool(raw.get("available")),
            "status": str(raw.get("status") or ("ok" if raw.get("available") else "unavailable")),
            "source": str(raw.get("source") or "harness-account"),
            "updatedAt": int(raw.get("updatedAt") or time.time()),
            "stale": False,
            "entries": entries if raw.get("available") else [],
            "detail": raw.get("detail") or {},
        }

    def _decorate(self, payload: dict[str, Any], stale: bool, age: float) -> dict[str, Any]:
        result = dict(payload)
        result["stale"] = stale
        result["ageSeconds"] = round(age, 1)
        result["display"] = _display_of(result.get("entries") or [], bool(result.get("available")))
        return result

    def invalidate(self) -> None:
        with self.lock:
            self.fetched_at = 0.0


def _valid_decimal(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return False
    return parsed.is_finite()


def _display_of(entries: list[dict[str, Any]], available: bool) -> dict[str, Any]:
    """Build the header label without adding incompatible amounts together."""
    if not available:
        return {"label": "余额暂不可用", "primary": None}
    normal = [entry for entry in entries if entry.get("wallet") == "normal"]
    currencies = sorted({entry.get("currency") for entry in normal if entry.get("currency")})
    if not normal:
        return {"label": "余额暂不可用", "primary": None}
    if len(currencies) == 1:
        currency = currencies[0]
        try:
            total = sum((Decimal(entry["amount"]) for entry in normal), Decimal(0))
        except (InvalidOperation, KeyError):
            return {"label": "余额暂不可用", "primary": None}
        symbol = CURRENCY_SYMBOL.get(currency, "")
        text = _trim_amount(total)
        return {"label": f"余额 {symbol}{_display_amount(total)}", "primary": {"currency": currency, "amount": text}}
    parts = []
    for currency in currencies:
        amounts = [entry["amount"] for entry in normal if entry.get("currency") == currency]
        symbol = CURRENCY_SYMBOL.get(currency, "")
        parts.append(f"{symbol}{_display_amount(sum((Decimal(value) for value in amounts), Decimal(0)))}")
    return {"label": "余额 " + " / ".join(parts), "primary": None}


def _trim_amount(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _display_amount(value: Decimal) -> str:
    """Round only the presentation label; the exact decimal string stays in entries."""
    try:
        return _trim_amount(value.quantize(Decimal('0.01')))
    except (InvalidOperation, ValueError):
        return _trim_amount(value)