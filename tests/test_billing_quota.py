"""Quota summary: the ASR counter is converted from hours to seconds."""
from __future__ import annotations

import asyncio
import datetime as dt

from rui import billing


def test_usage_totals_sums_today_and_the_window() -> None:
    rows = [{"Day": "2026-10-22", "Value": 1.5}, {"Day": "2026-10-23", "Value": 2.5}]
    today, total = billing._usage_totals(rows, "2026-10-23")
    assert today == 2.5
    assert total == 4.0


def test_quota_summary_reports_asr_seconds(monkeypatch) -> None:
    end = dt.date.today().isoformat()

    async def fake_post(_client, *, ak, sk, action, version, service, region, body):
        if action == "QueryBalanceAcct":
            return {"Result": {"AvailableBalance": 12.0, "Currency": "CNY"}}
        if action == "UsageMonitoring":
            if body["ResourceID"] in billing.TTS_RESOURCE_IDS:
                value = 1.0 if body["ResourceID"] == billing.TTS_RESOURCE_IDS[0] else 0.0
            else:
                value = 2.0
            return {"Result": {"UsageMonitoring": [{"Day": end, "Value": value}]}}
        return {"Result": {"Packs": []}}

    monkeypatch.setattr(billing, "_post", fake_post)
    out = asyncio.run(billing.quota_summary(getter=lambda name: "x"))
    assert out["tts_chars_today"] == 1
    # ASR rows are hours: 2 h -> 7200 s
    assert out["asr_seconds_today"] == 7200
    assert out["asr_seconds_7d"] == 7200
    assert out["errors"] == []


def test_missing_credentials_still_return_the_asr_fields() -> None:
    out = asyncio.run(billing.quota_summary(getter=lambda name: ""))
    assert out["asr_seconds_today"] is None
    assert out["asr_seconds_7d"] is None
