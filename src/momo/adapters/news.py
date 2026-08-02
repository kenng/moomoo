from __future__ import annotations

from momo.opend_client import OpenDError, quote_context


def search_news(keyword: str, max_count: int = 50) -> list[dict]:
    import moomoo as ft

    with quote_context() as ctx:
        kwargs = {"keyword": keyword, "max_count": max_count}
        # news_sub_type available on newer SDKs
        if hasattr(ft, "NewsSubType"):
            kwargs["news_sub_type"] = ft.NewsSubType.ALL

        ret, data = ctx.get_search_news(**kwargs)
        if ret != ft.RET_OK:
            raise OpenDError(f"get_search_news failed: {data}")
        if data is None or getattr(data, "empty", True):
            return []

        items = []
        for _, row in data.iterrows():
            related = row.get("related_securities")
            if related is None:
                related_list: list[str] = []
            elif isinstance(related, str):
                related_list = [related] if related else []
            else:
                try:
                    related_list = [str(x) for x in list(related)]
                except TypeError:
                    related_list = [str(related)]

            items.append(
                {
                    "title": str(row.get("title") or ""),
                    "news_sub_type": str(row.get("news_sub_type") or ""),
                    "source": str(row.get("source") or ""),
                    "publish_time": str(row.get("publish_time") or ""),
                    "view_count": int(row.get("view_count") or 0),
                    "related_securities": related_list,
                    "url": str(row.get("url") or ""),
                }
            )
        return items
