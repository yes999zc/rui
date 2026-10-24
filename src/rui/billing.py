"""Volcano Engine account/usage queries for the console.

Three questions the console can answer with the account's AK/SK (never with the speech
API key):

- how much money is left (`QueryBalanceAcct`, billing service),
- how many TTS characters were billed (`UsageMonitoring`, speech service),
- what resource packs are left (`ResourcePacksStatus`).

The calls are signed with the Volcano V4 scheme (HMAC-SHA256 over a canonical request).
All of it is read-only; a missing credential or a vendor error becomes a sentence in
`errors`, never a 500, because the console must still render on a network that cannot
reach the billing endpoint.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx

from . import secrets

HOST = "open.volcengineapi.com"
SPEECH_SERVICE = "speech_saas_prod"
SPEECH_REGION = "cn-north-1"
BILLING_SERVICE = "billing"
BILLING_REGION = "cn-beijing"
# Doubao TTS 2.0 bills under seedtts.default; 10029 is the older big-TTS resource. Query
# both so an account that used either still sees its characters (measured 16:58: only
# seedtts.default carried today's 2305 chars; 10029 was 0).
TTS_RESOURCE_IDS = ("volc.seedtts.default", "volc.service_type.10029")
# Doubao streaming ASR 2.0 hourly pack. UsageMonitoring reports audio_duration in HOURS.
ASR_RESOURCE_ID = "volc.seedasr.sauc.duration"
CREDENTIAL_NAMES = ("VOLC_ACCESS_KEY_ID", "VOLC_SECRET_ACCESS_KEY")


def _hmac(key: bytes, content: str) -> bytes:
    return hmac.new(key, content.encode(), hashlib.sha256).digest()


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _norm_query(params: dict[str, Any]) -> str:
    parts = []
    for key in sorted(params):
        value = params[key]
        values = value if isinstance(value, list) else [value]
        for item in values:
            parts.append(f"{urllib.parse.quote(key, safe='-_.~')}="
                         f"{urllib.parse.quote(str(item), safe='-_.~')}")
    return "&".join(parts).replace("+", "%20")


def sign_headers(
    *,
    ak: str,
    sk: str,
    method: str,
    action: str,
    version: str,
    service: str,
    region: str,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    host: str = HOST,
    now: dt.datetime | None = None,
) -> dict[str, str]:
    """Volcano V4 Authorization headers for one signed request."""
    body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
    moment = now or dt.datetime.now(dt.UTC)
    x_date = moment.strftime("%Y%m%dT%H%M%SZ")
    short = x_date[:8]
    content_type = "application/json; charset=utf-8"
    signed_headers = "content-type;host;x-content-sha256;x-date"
    all_query = {"Action": action, "Version": version, **(query or {})}
    content_sha = _sha(body_str)
    canonical = "\n".join([
        method.upper(), "/", _norm_query(all_query),
        "\n".join([
            "content-type:" + content_type,
            "host:" + host,
            "x-content-sha256:" + content_sha,
            "x-date:" + x_date,
        ]),
        "", signed_headers, content_sha,
    ])
    scope = "/".join([short, region, service, "request"])
    to_sign = "\n".join(["HMAC-SHA256", x_date, scope, _sha(canonical)])
    key = _hmac(sk.encode(), short)
    key = _hmac(key, region)
    key = _hmac(key, service)
    key = _hmac(key, "request")
    signature = _hmac(key, to_sign).hex()
    return {
        "Content-Type": content_type,
        "Host": host,
        "X-Content-Sha256": content_sha,
        "X-Date": x_date,
        "Authorization": (
            f"HMAC-SHA256 Credential={ak}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


async def _post(
    client: httpx.AsyncClient, *, ak: str, sk: str, action: str, version: str,
    service: str, region: str, body: dict[str, Any],
) -> dict[str, Any]:
    body_str = json.dumps(body, separators=(",", ":"))
    headers = sign_headers(ak=ak, sk=sk, method="POST", action=action, version=version,
                           service=service, region=region, body=body)
    # The signature covers the exact bytes sent; one of them using the default
    # separators (spaces) while the other does not is a 401 (measured 15:41).
    resp = await client.post(f"https://{HOST}/", params={"Action": action,
                                                         "Version": version},
                             headers=headers, content=body_str.encode())
    resp.raise_for_status()
    return resp.json()


def _today() -> dt.date:
    return dt.date.today()


def _usage_totals(rows: list[dict[str, Any]], end: str) -> tuple[float, float]:
    """(today, seven_day_total) from UsageMonitoring rows for one resource."""
    total = 0.0
    today = 0.0
    for row in rows:
        value = float(row.get("Value") or 0)
        total += value
        if str(row.get("Day")) == end:
            today += value
    return today, total


async def quota_summary(
    client: httpx.AsyncClient | None = None,
    getter: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Balance + TTS usage + packs, each error isolated in `errors`."""
    get = getter or secrets.get
    errors: list[str] = []
    ak, sk = (get(name) for name in CREDENTIAL_NAMES)
    out: dict[str, Any] = {
        "available_balance": None, "cash_balance": None, "credit_limit": None,
        "currency": "CNY", "tts_chars_today": None, "tts_chars_7d": None,
        "asr_seconds_today": None, "asr_seconds_7d": None,
        "packs": [], "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "errors": errors,
    }
    if not ak or not sk:
        errors.append("未配置 VOLC_ACCESS_KEY_ID / VOLC_SECRET_ACCESS_KEY，额度无法查询"
                      " (missing Volcano AK/SK; quota unavailable)")
        return out
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        try:
            balance = await _post(client, ak=ak, sk=sk, action="QueryBalanceAcct",
                                  version="2022-01-01", service=BILLING_SERVICE,
                                  region=BILLING_REGION, body={})
            result = balance.get("Result") or {}
            out["available_balance"] = result.get("AvailableBalance")
            out["cash_balance"] = result.get("CashBalance")
            out["credit_limit"] = result.get("CreditLimit")
            out["currency"] = result.get("Currency") or out["currency"]
        except Exception as exc:  # noqa: BLE001 - one panel line, not a page failure
            errors.append(f"余额查询失败: {type(exc).__name__}: {str(exc)[:120]}")

        start = (_today() - dt.timedelta(days=6)).isoformat()
        end = _today().isoformat()
        try:
            tts_today = tts_total = 0.0
            for resource in TTS_RESOURCE_IDS:
                usage = await _post(client, ak=ak, sk=sk, action="UsageMonitoring",
                                    version="2025-05-21", service=SPEECH_SERVICE,
                                    region=SPEECH_REGION,
                                    body={"ProjectName": "default",
                                          "ResourceID": resource,
                                          "Start": start, "End": end, "Mode": "daily"})
                rows = ((usage.get("Result") or {}).get("UsageMonitoring")) or []
                today, total = _usage_totals(rows, end)
                tts_today += today
                tts_total += total
            out["tts_chars_today"] = int(tts_today)
            out["tts_chars_7d"] = int(tts_total)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"用量查询失败: {type(exc).__name__}: {str(exc)[:120]}")

        # ASR is billed by audio seconds (the API reports hours). Kept separate from the
        # TTS counter because the two resources have different units and packs.
        try:
            usage = await _post(client, ak=ak, sk=sk, action="UsageMonitoring",
                                version="2025-05-21", service=SPEECH_SERVICE,
                                region=SPEECH_REGION,
                                body={"ProjectName": "default",
                                      "ResourceID": ASR_RESOURCE_ID,
                                      "Start": start, "End": end, "Mode": "daily"})
            rows = ((usage.get("Result") or {}).get("UsageMonitoring")) or []
            today_h, total_h = _usage_totals(rows, end)
            out["asr_seconds_today"] = round(today_h * 3600.0)
            out["asr_seconds_7d"] = round(total_h * 3600.0)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ASR 用量查询失败: {type(exc).__name__}: {str(exc)[:120]}")

        try:
            packs = await _post(client, ak=ak, sk=sk, action="ResourcePacksStatus",
                                version="2025-05-20", service=SPEECH_SERVICE,
                                region=SPEECH_REGION,
                                body={"ProjectName": "default",
                                      "ResourceIDs": list(TTS_RESOURCE_IDS),
                                      "Types": ["TTS"]})
            items = ((packs.get("Result") or {}).get("Packs")) or []
            out["packs"] = [
                {"name": item.get("ResourceName") or item.get("ResourceID") or "TTS",
                 "remaining": item.get("Remaining") or item.get("Available")
                 or item.get("Surplus") or item.get("Remain")}
                for item in items
            ]
        except Exception as exc:  # noqa: BLE001
            errors.append(f"资源包查询失败: {type(exc).__name__}: {str(exc)[:120]}")
    finally:
        if owns_client:
            await client.aclose()
    return out
