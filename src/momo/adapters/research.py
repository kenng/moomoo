from __future__ import annotations

from momo.opend_client import OpenDError, quote_context

RATING_LABELS = {
    1: "Sell",
    2: "Underperform",
    3: "Hold",
    4: "Buy",
    5: "Strong Buy",
}

_INST_PAGE_SIZE = 20
_INST_MAX_PAGES = 5


def rating_label(value) -> str:
    if value is None:
        return ""
    try:
        return RATING_LABELS.get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value)


def get_analyst_consensus(symbol: str) -> dict | None:
    """Fetch consensus target price / rating for one symbol."""
    import moomoo as ft

    with quote_context() as ctx:
        ret, data = ctx.get_research_analyst_consensus(symbol)
        if ret != ft.RET_OK:
            raise OpenDError(f"get_research_analyst_consensus failed: {data}")
        if not data or not any(data.values()):
            return None
        return {
            "symbol": symbol,
            "highest": _float(data.get("highest")),
            "average": _float(data.get("average")),
            "lowest": _float(data.get("lowest")),
            "rating": _int(data.get("rating")),
            "rating_label": rating_label(data.get("rating")),
            "total_analysts": _int(data.get("total")),
            "update_time_str": str(data.get("update_time_str") or ""),
            "buy": _float(data.get("buy")),
            "hold": _float(data.get("hold")),
            "sell": _float(data.get("sell")),
            "strong_buy": _float(data.get("strong_buy")),
            "underperform": _float(data.get("underperform")),
        }


def get_institution_targets(symbol: str) -> list[dict]:
    """Fetch per-institution target prices (US equities/REITs only)."""
    import moomoo as ft

    rows: list[dict] = []
    next_key = None
    with quote_context() as ctx:
        for _ in range(_INST_MAX_PAGES):
            kwargs = {
                "code": symbol,
                "rating_dimension_type": 1,
                "num": _INST_PAGE_SIZE,
            }
            if next_key is not None:
                kwargs["next_key"] = next_key
            ret, data = ctx.get_research_rating_summary(**kwargs)
            if ret != ft.RET_OK:
                msg = str(data)
                # Non-US markets are unsupported for institution lists.
                if "Only US" in msg or "not supported" in msg.lower():
                    return []
                raise OpenDError(f"get_research_rating_summary failed: {data}")
            if not data:
                break
            for entry in data.get("inst_rating_summary_list") or []:
                info = entry.get("institution_info") or {}
                items = entry.get("rating_item_list") or []
                latest = items[0] if items else {}
                uid = str(
                    info.get("institution_uid")
                    or latest.get("institution_uid")
                    or ""
                )
                if not uid:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "institution_uid": uid,
                        "institution_name": str(
                            info.get("institution_name") or ""
                        ),
                        "institution_en_name": str(
                            info.get("institution_en_name") or ""
                        ),
                        "institution_source_name": str(
                            info.get("institution_source_name") or ""
                        ),
                        "rating": _int(latest.get("rating")),
                        "rating_label": rating_label(latest.get("rating")),
                        "target_price": _float(latest.get("target_price")),
                        "recommendation_date_str": str(
                            latest.get("recommendation_date_str") or ""
                        ),
                        "rating_url": str(latest.get("rating_url") or ""),
                        "update_time_str": str(
                            latest.get("update_time_str")
                            or info.get("update_time_str")
                            or ""
                        ),
                    }
                )
            next_key = data.get("next_key")
            if next_key in (None, "", "-1", -1):
                break
    return rows


def _float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
