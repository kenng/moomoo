from __future__ import annotations

from datetime import datetime, timezone

import httpx

from momo.config import get_settings

FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"


class FinnhubError(Exception):
    """Finnhub API or payload error."""


def to_finnhub_symbol(stock: dict) -> str:
    """Map Moomoo stock dict to Finnhub ticker."""
    market = (stock.get("market") or "").upper()
    code = str(stock.get("code") or "").strip().upper()
    if not code:
        raise ValueError("stock code is empty")
    if market == "US":
        return code
    if market == "HK":
        return f"{code}.HK"
    if market:
        return f"{code}.{market}"
    return code


def _normalize_item(raw: dict) -> dict | None:
    title = str(raw.get("headline") or "").strip()
    url = str(raw.get("url") or "").strip()
    if not title and not url:
        return None

    related_raw = raw.get("related") or ""
    if isinstance(related_raw, str):
        related_list = [p.strip() for p in related_raw.split(",") if p.strip()]
    elif isinstance(related_raw, list):
        related_list = [str(x).strip() for x in related_raw if str(x).strip()]
    else:
        related_list = []

    publish_time = ""
    ts = raw.get("datetime")
    if ts is not None:
        try:
            publish_time = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except (TypeError, ValueError, OSError):
            publish_time = ""

    return {
        "title": title,
        "news_sub_type": "NEWS",
        "source": str(raw.get("source") or ""),
        "publish_time": publish_time,
        "view_count": 0,
        "related_securities": related_list,
        "url": url,
    }


def company_news(
    symbol: str,
    *,
    from_date: str,
    to_date: str,
    api_key: str | None = None,
    timeout: float = 15.0,
) -> list[dict]:
    """Fetch company news from Finnhub; returns OpenD-compatible news dicts."""
    settings = get_settings()
    token = api_key if api_key is not None else settings.finnhub_api_key
    if not token:
        raise FinnhubError("FINNHUB_API_KEY is not set")

    try:
        response = httpx.get(
            FINNHUB_COMPANY_NEWS_URL,
            params={"symbol": symbol, "from": from_date, "to": to_date},
            headers={"X-Finnhub-Token": token},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise FinnhubError(f"company-news request failed: {exc}") from exc

    if response.status_code != 200:
        raise FinnhubError(
            f"company-news failed: HTTP {response.status_code}: {response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise FinnhubError("company-news returned invalid JSON") from exc

    if not isinstance(payload, list):
        raise FinnhubError(f"company-news unexpected payload: {type(payload).__name__}")

    items: list[dict] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        item = _normalize_item(raw)
        if item:
            items.append(item)
    return items
