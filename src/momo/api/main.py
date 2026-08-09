from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from momo.adapters.google_sheets import GoogleSheetsError
from momo.config import bare_code, get_settings
from momo.domain.ranking import format_publish_time, parse_publish_time
from momo.opend_client import OpenDError
from momo.services import dividend_history, news_digest, order_history, sheets_sync
from momo.watchlist import load_watchlist, resolve_stock

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_STOCK_NEWS_MARKET_ORDER = ("HK", "US", "MY")
_KLSE_NEWS_URL = "https://www.klsescreener.com/v2/news/stock/{code}"


def _group_position_stocks_by_market(stock_groups: list[dict]) -> list[dict]:
    """Group open stock positions as HK → US → MY (then any other markets)."""
    buckets: dict[str, list[dict]] = {m: [] for m in _STOCK_NEWS_MARKET_ORDER}
    other: list[dict] = []
    for g in stock_groups:
        symbol = (g.get("code") or "").strip().upper()
        if not symbol:
            continue
        market = symbol.split(".", 1)[0] if "." in symbol else ""
        code = bare_code(symbol)
        row = {
            "code": code,
            "symbol": symbol,
            "name": g.get("name") or code,
            "market": market,
            "qty": (g.get("position") or {}).get("qty"),
            "klse_news_url": (
                _KLSE_NEWS_URL.format(code=code) if market == "MY" else None
            ),
        }
        if market in buckets:
            buckets[market].append(row)
        else:
            other.append(row)

    sections: list[dict] = []
    for market in _STOCK_NEWS_MARKET_ORDER:
        stocks = sorted(buckets[market], key=lambda s: s["code"])
        if stocks:
            sections.append({"market": market, "stocks": stocks})
    if other:
        sections.append(
            {
                "market": "OTHER",
                "stocks": sorted(other, key=lambda s: (s["market"], s["code"])),
            }
        )
    return sections

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Momo News", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["format_publish_time"] = format_publish_time


@app.get("/", response_class=HTMLResponse)
def home(request: Request, source: str = "all"):
    source = news_digest.normalize_news_provider(source)
    digests = news_digest.get_watchlist_digest(provider=source)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "digests": digests,
            "source": source,
            "settings": get_settings(),
            "error": None,
        },
    )


def _empty_orders_payload(
    *,
    acc_id: int | None,
    start: str | None,
    end: str | None,
    trd_env: str | None,
) -> dict:
    empty_totals = {
        "open_positions": 0,
        "symbols": 0,
        "orders": 0,
        "market_val": None,
        "unrealized_pl": None,
    }
    return {
        "trd_env": (trd_env or get_settings().orders_trd_env).upper(),
        "accounts": [],
        "selected_acc_id": acc_id,
        "start": start or "",
        "end": end or "",
        "as_of": "",
        "stock_groups": [],
        "option_clusters": [],
        "totals": empty_totals,
        "stock_totals": empty_totals,
        "option_totals": empty_totals,
    }


def _render_orders_page(
    request: Request,
    *,
    asset_kind: str,
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
):
    try:
        data = order_history.get_order_history(
            acc_id=acc_id, start=start, end=end, trd_env=trd_env
        )
        error = data.get("error")
    except OpenDError as exc:
        data = _empty_orders_payload(
            acc_id=acc_id, start=start, end=end, trd_env=trd_env
        )
        error = str(exc)

    page_totals = (
        data.get("option_totals")
        if asset_kind == "options"
        else data.get("stock_totals")
    )
    return templates.TemplateResponse(
        request,
        "orders.html",
        {
            **data,
            "asset_kind": asset_kind,
            "totals": page_totals or data.get("totals"),
            "error": error,
        },
    )


@app.get("/orders", response_class=HTMLResponse)
def orders_options_page(
    request: Request,
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
):
    """Default orders page: options first."""
    return _render_orders_page(
        request,
        asset_kind="options",
        acc_id=acc_id,
        start=start,
        end=end,
        trd_env=trd_env,
    )


@app.get("/orders/options", response_class=HTMLResponse)
def orders_options_alias(
    request: Request,
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
):
    return _render_orders_page(
        request,
        asset_kind="options",
        acc_id=acc_id,
        start=start,
        end=end,
        trd_env=trd_env,
    )


@app.get("/orders/stocks", response_class=HTMLResponse)
def orders_stocks_page(
    request: Request,
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
):
    return _render_orders_page(
        request,
        asset_kind="stocks",
        acc_id=acc_id,
        start=start,
        end=end,
        trd_env=trd_env,
    )


@app.get("/stock-news", response_class=HTMLResponse)
def stock_news_page(
    request: Request,
    acc_id: int | None = None,
    trd_env: str | None = None,
):
    """List open stock positions grouped by market, with KLSE news links for MY."""
    try:
        data = order_history.get_order_history(acc_id=acc_id, trd_env=trd_env)
        error = data.get("error")
    except OpenDError as exc:
        data = _empty_orders_payload(
            acc_id=acc_id, start=None, end=None, trd_env=trd_env
        )
        error = str(exc)

    sections = _group_position_stocks_by_market(data.get("stock_groups") or [])
    return templates.TemplateResponse(
        request,
        "stock_news.html",
        {
            "trd_env": data.get("trd_env"),
            "accounts": data.get("accounts") or [],
            "selected_acc_id": data.get("selected_acc_id"),
            "sections": sections,
            "error": error,
        },
    )


@app.get("/dividends", response_class=HTMLResponse)
def dividends_page(
    request: Request,
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
    sync: int = 0,
    all_collected: int = Query(0, alias="all"),
):
    show_all = bool(all_collected)
    try:
        data = dividend_history.get_dividend_history(
            acc_id=acc_id,
            start=start,
            end=end,
            trd_env=trd_env,
            sync=bool(sync),
            show_all=show_all,
        )
        error = data.get("error")
    except OpenDError as exc:
        data = {
            "trd_env": (trd_env or get_settings().orders_trd_env).upper(),
            "accounts": [],
            "selected_acc_id": acc_id,
            "start": start or "",
            "end": end or "",
            "show_all": show_all,
            "as_of": "",
            "dividends": [],
            "new_dividends": [],
            "totals": {"count": 0, "by_currency": []},
            "sync": {
                "fetched_days": 0,
                "remaining_days": 0,
                "accounts_synced": 0,
                "fetched_dates": [],
            },
            "auto_sync": False,
        }
        error = str(exc)
    return templates.TemplateResponse(
        request,
        "dividends.html",
        {**data, "error": error},
    )


@app.get("/stock/{code}", response_class=HTMLResponse)
def stock_detail(
    request: Request, code: str, sort: str = "date", source: str = "all"
):
    stock = resolve_stock(code)
    source = news_digest.normalize_news_provider(source)
    digest = news_digest.get_digest_for_code(
        stock["code"],
        market=stock["market"],
        provider=source,
        limit=news_digest.FINNHUB_DIGEST_LIMIT,
    )
    sort = sort if sort in ("date", "score") else "date"
    news = list(digest.get("news") or [])
    if sort == "score":
        news.sort(key=lambda n: n.get("importance_score") or 0, reverse=True)
    else:
        news.sort(
            key=lambda n: parse_publish_time(n.get("publish_time") or "") or _EPOCH,
            reverse=True,
        )
    digest = {**digest, "news": news}
    return templates.TemplateResponse(
        request,
        "stock.html",
        {
            "stock": stock,
            "digest": digest,
            "sort": sort,
            "source": source,
            "error": None,
        },
    )


@app.post("/refresh")
def refresh_all():
    try:
        news_digest.refresh_watchlist()
    except OpenDError as exc:
        return RedirectResponse(url=f"/?error={exc}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@app.post("/refresh/{code}")
def refresh_one(code: str):
    stock = resolve_stock(code)
    try:
        news_digest.refresh_stock_news(stock)
    except OpenDError as exc:
        return RedirectResponse(url=f"/stock/{stock['symbol']}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/stock/{stock['symbol']}", status_code=303)


@app.get("/api/watchlist")
def api_watchlist():
    return load_watchlist()


@app.get("/api/news")
def api_news(
    code: str | None = None,
    limit: int | None = None,
    source: str | None = None,
):
    if code:
        stock = resolve_stock(code)
        return news_digest.get_digest_for_code(
            stock["code"],
            limit=limit,
            market=stock["market"],
            provider=source,
        )
    return news_digest.get_watchlist_digest(
        limit_per_stock=limit, provider=source
    )


@app.post("/api/refresh")
def api_refresh(code: str | None = Form(default=None)):
    if code:
        stock = resolve_stock(code)
        return news_digest.refresh_stock_news(stock)
    return news_digest.refresh_watchlist()


@app.post("/api/sync/stock-orders")
def api_sync_stock_orders(
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
):
    """Fetch OpenD stock orders and replace the Google Sheet 'stock orders' tab."""
    try:
        return sheets_sync.sync_stock_orders(
            acc_id=acc_id, start=start, end=end, trd_env=trd_env
        )
    except OpenDError as exc:
        return {"ok": False, "error": str(exc)}
    except GoogleSheetsError as exc:
        return {"ok": False, "error": str(exc)}
