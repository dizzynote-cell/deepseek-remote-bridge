# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Peak / off-peak pricing phase for the DeepSeek API.

DeepSeek charges less outside its weekday peak windows and all day at weekends.
The bridge only reports the phase and the countdown to the next switch: it does
not compute money, and it never rewrites the balance that the Platform returns.

The schedule is configuration, not a hardcoded belief: DeepSeek has changed it
before, so every value can be overridden in ``config/.env``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

DEFAULT_WINDOWS = "09:00-12:00,14:00-18:00"
DEFAULT_DAYS = "mon-fri"
DEFAULT_OFFSET_MINUTES = 480  # Asia/Shanghai, UTC+8, no DST

DAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

NOTE = (
    "按当前公开的峰谷安排：工作日 {windows} 为高峰，其余时间与周末为空闲时段，闲时价格约为高峰的一半。"
    " 时段按 UTC{offset} 计算；官方若调整，改 config/.env 里的 DSH_BRIDGE_PEAK_* 即可。"
)


@dataclass(frozen=True)
class PricingSettings:
    windows: tuple[tuple[int, int], ...] = ((540, 720), (840, 1080))  # 09:00-12:00, 14:00-18:00
    days: frozenset[int] = field(default_factory=lambda: frozenset({0, 1, 2, 3, 4}))
    offset_minutes: int = DEFAULT_OFFSET_MINUTES
    invalid: str = ""


def parse_windows(spec: str) -> tuple[tuple[int, int], ...]:
    """Parse ``09:00-12:00,14:00-18:00`` into minutes-of-day ranges."""
    ranges: list[tuple[int, int]] = []
    for chunk in (spec or "").split(","):
        piece = chunk.strip()
        if not piece:
            continue
        start_text, _, end_text = piece.partition("-")
        start = _parse_clock(start_text)
        end = _parse_clock(end_text)
        if start is None or end is None or start >= end:
            continue
        ranges.append((start, end))
    return tuple(sorted(ranges))


def parse_days(spec: str) -> frozenset[int]:
    """Parse ``mon-fri``, ``sat,sun`` or ``all`` into weekday numbers."""
    text = (spec or "").strip().lower()
    if text in {"", "none"}:
        return frozenset()
    if text == "all":
        return frozenset({0, 1, 2, 3, 4, 5, 6})
    days: set[int] = set()
    for chunk in text.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        if "-" in piece:
            first, _, last = piece.partition("-")
            low = DAY_NAMES.get(first.strip()[:3])
            high = DAY_NAMES.get(last.strip()[:3])
            if low is None or high is None or low > high:
                continue
            days.update(range(low, high + 1))
            continue
        day = DAY_NAMES.get(piece[:3])
        if day is not None:
            days.add(day)
    return frozenset(days)


def _parse_clock(text: str) -> int | None:
    piece = (text or "").strip()
    if not piece:
        return None
    hours_text, separator, minutes_text = piece.partition(":")
    if not separator:
        return None
    try:
        hours = int(hours_text)
        minutes = int(minutes_text)
    except ValueError:
        return None
    if not (0 <= hours <= 24 and 0 <= minutes < 60):
        return None
    total = hours * 60 + minutes
    return total if total < 24 * 60 else None


def build_settings(windows_spec: str, days_spec: str, offset_minutes: int) -> PricingSettings:
    windows = parse_windows(windows_spec)
    days = parse_days(days_spec)
    invalid = ""
    if not windows:
        windows = parse_windows(DEFAULT_WINDOWS)
        invalid = "peak-windows"
    if not days:
        days = parse_days(DEFAULT_DAYS)
        invalid = (invalid + ",peak-days").strip(",")
    if not (-14 * 60 <= offset_minutes <= 14 * 60):
        offset_minutes = DEFAULT_OFFSET_MINUTES
        invalid = (invalid + ",peak-offset").strip(",")
    return PricingSettings(windows=windows, days=days, offset_minutes=offset_minutes, invalid=invalid)


def _local(now: datetime, offset_minutes: int) -> datetime:
    utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return (utc + timedelta(minutes=offset_minutes)).replace(tzinfo=None)


def is_peak(local_now: datetime, settings: PricingSettings) -> bool:
    if local_now.weekday() not in settings.days:
        return False
    minutes = local_now.hour * 60 + local_now.minute
    return any(start <= minutes < end for start, end in settings.windows)


def next_switch(local_now: datetime, settings: PricingSettings, horizon_days: int = 10) -> datetime | None:
    """First future minute whose phase differs from the current one."""
    current = is_peak(local_now, settings)
    probe = local_now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(horizon_days * 24 * 60):
        if is_peak(probe, settings) != current:
            return probe
        probe += timedelta(minutes=1)
    return None


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _windows_text(settings: PricingSettings) -> str:
    return "、".join(f"{_clock(start)}–{_clock(end)}" for start, end in settings.windows)


def _days_text(settings: PricingSettings) -> str:
    if len(settings.days) == 7:
        return "每天"
    if not settings.days:
        return "无"
    if settings.days == frozenset({0, 1, 2, 3, 4}):
        return "工作日"
    return "、".join(DAY_LABELS[day] for day in sorted(settings.days))


def phase_payload(settings: PricingSettings, now: datetime | None = None) -> dict:
    """Describe the current pricing phase without inventing any money value."""
    current = now or datetime.now(timezone.utc)
    local = _local(current, settings.offset_minutes)
    peak = is_peak(local, settings)
    switch = next_switch(local, settings)
    offset_total = settings.offset_minutes
    sign = "+" if offset_total >= 0 else "-"
    absolute = abs(offset_total)
    zone = f"UTC{sign}{absolute // 60:02d}:{absolute % 60:02d}"
    seconds_left = 0
    if switch is not None:
        target = switch.replace(tzinfo=timezone.utc) - timedelta(minutes=offset_total)
        seconds_left = max(0, int((target - current.astimezone(timezone.utc)).total_seconds()))
    return {
        "phase": "peak" if peak else "off-peak",
        "label": "忙时" if peak else "闲时",
        "weekend": local.weekday() >= 5,
        "localTime": local.strftime("%Y-%m-%d %H:%M"),
        "localWeekday": DAY_LABELS[local.weekday()],
        "zone": zone,
        "windows": _windows_text(settings),
        "days": _days_text(settings),
        "nextSwitch": switch.strftime("%Y-%m-%d %H:%M") if switch else None,
        "nextSwitchEpoch": int((switch.replace(tzinfo=timezone.utc) - timedelta(minutes=offset_total)).timestamp()) if switch else None,
        "secondsLeft": seconds_left,
        "note": NOTE.format(windows=_windows_text(settings), offset=zone[3:]),
        "invalid": settings.invalid,
    }


def payload_for(windows_spec: str, days_spec: str, offset_minutes: int, now: datetime | None = None) -> dict:
    return phase_payload(build_settings(windows_spec, days_spec, offset_minutes), now)


def describe_ranges(ranges: Iterable[tuple[int, int]]) -> str:
    return "、".join(f"{_clock(start)}–{_clock(end)}" for start, end in ranges)
