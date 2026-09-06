from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import markdown as markdown_lib
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware

from momo.adapters.google_sheets import GoogleSheetsError
from momo.api.auth import RequireLoginMiddleware, auth_enabled, verify_credentials
from momo.config import bare_code, get_settings
from momo.ticker_urls import ticker_ext_links
from momo.domain.ranking import format_publish_time, parse_publish_time
from momo.opend_client import OpenDError
from momo.services import (
    ai_news_summary,
    dividend_history,
    news_digest,
    order_history,
    price_targets,
    sheets_sync,
    stock_oracle,
    strategy_timing,
)
from momo.watchlist import load_watchlist, resolve_stock

_MD_EXTENSIONS = ["sane_lists", "nl2br", "fenced_code", "tables"]
_UNSAFE_HTML_RE = re.compile(
    r"</?(script|style|iframe|object|embed|link|meta|base)[^>]*>",
    re.IGNORECASE,
)
_MD_H3_RE = re.compile(r"<h3>(.*?)</h3>", re.IGNORECASE | re.DOTALL)
_MD_TONE_MARKERS = (
    ("🟢", "tone-green"),
    ("🟡", "tone-yellow"),
    ("🔴", "tone-red"),
)


def _tone_class_for_heading(inner_html: str) -> str:
    for marker, tone in _MD_TONE_MARKERS:
        if marker in inner_html:
            return tone
    return ""


def _style_markdown_headings(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        tone = _tone_class_for_heading(inner)
        cls = f' class="ai-summary-section {tone}"' if tone else ""
        return f"<h3{cls}>{inner}</h3>"

    return _MD_H3_RE.sub(repl, html)

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_STOCK_NEWS_MARKET_ORDER = ("HK", "US", "MY")
_KLSE_NEWS_URL = "https://www.klsescreener.com/v2/news/stock/{code}"


def _group_position_stocks_by_market(
    stock_groups: list[dict],
    option_clusters: list[dict] | None = None,
) -> list[dict]:
    """Group open stock (+ option underlying) positions as HK → US → MY."""
    by_symbol: dict[str, dict] = {}
    for g in stock_groups:
        symbol = (g.get("code") or "").strip().upper()
        if not symbol:
            continue
        market = symbol.split(".", 1)[0] if "." in symbol else ""
        code = bare_code(symbol)
        by_symbol[symbol] = {
            "code": code,
            "symbol": symbol,
            "name": g.get("name") or code,
            "market": market,
            "qty": (g.get("position") or {}).get("qty"),
            "option_contracts": 0,
            "klse_news_url": (
                _KLSE_NEWS_URL.format(code=code) if market == "MY" else None
            ),
        }

    for cluster in option_clusters or []:
        symbol = (cluster.get("underlying_symbol") or "").strip().upper()
        if not symbol:
            continue
        contracts = cluster.get("contracts") or []
        option_contracts = sum(
            1
            for c in contracts
            if ((c.get("position") or {}).get("qty") or 0)
        )
        if not option_contracts:
            continue
        market = symbol.split(".", 1)[0] if "." in symbol else ""
        code = bare_code(symbol)
        existing = by_symbol.get(symbol)
        if existing:
            existing["option_contracts"] = option_contracts
            continue
        root = (cluster.get("underlying_root") or code).strip()
        by_symbol[symbol] = {
            "code": code,
            "symbol": symbol,
            "name": root or code,
            "market": market,
            "qty": None,
            "option_contracts": option_contracts,
            "klse_news_url": (
                _KLSE_NEWS_URL.format(code=code) if market == "MY" else None
            ),
        }

    buckets: dict[str, list[dict]] = {m: [] for m in _STOCK_NEWS_MARKET_ORDER}
    other: list[dict] = []
    for row in by_symbol.values():
        if row["market"] in buckets:
            buckets[row["market"]].append(row)
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
_settings = get_settings()
app.add_middleware(RequireLoginMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.session_secret or "momo-dev-session-secret",
    same_site="lax",
    https_only=False,
)


def _as_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def format_updated_at(value: datetime | str | None) -> str:
    """Display AI summary timestamps as dd-mmm-YYYY HH:MM."""
    dt = _as_datetime(value)
    if dt is None:
        return "" if value is None or value == "" else str(value)
    return dt.astimezone(timezone.utc).strftime("%d-%b-%Y %H:%M")


def format_short_date(value: datetime | str | None) -> str:
    """dd-mm-yy for table cells (works with datetime or ISO strings)."""
    dt = _as_datetime(value)
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).strftime("%d-%m-%y")


def format_sort_key(value: datetime | str | None) -> str:
    """Compact sortable timestamp for data-* attributes."""
    dt = _as_datetime(value)
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def render_markdown(value: str | None) -> Markup:
    """Render AI summary markdown to HTML for templates."""
    text = (value or "").strip()
    if not text:
        return Markup("")
    html = markdown_lib.markdown(text, extensions=_MD_EXTENSIONS)
    html = _UNSAFE_HTML_RE.sub("", html)
    html = _style_markdown_headings(html)
    return Markup(html)


def _ui_context(request: Request) -> dict:
    s = get_settings()
    user = None
    if auth_enabled():
        user = request.session.get("user")
    return {
        "read_only": s.read_only_ui,
        "ui_mode": s.momo_ui_mode,
        "current_user": user,
        "synced_at": None,
    }


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
    context_processors=[_ui_context],
)
templates.env.filters["format_publish_time"] = format_publish_time
templates.env.filters["format_updated_at"] = format_updated_at
templates.env.filters["format_short_date"] = format_short_date
templates.env.filters["format_sort_key"] = format_sort_key
templates.env.filters["markdown"] = render_markdown
templates.env.filters["external_ticker_links"] = ticker_ext_links
templates.env.globals["auth_enabled"] = auth_enabled


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None):
    if auth_enabled() and request.session.get("user") == get_settings().auth_username:
        return RedirectResponse(url=_safe_next_url(next, "/"), status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None, "next": next or "/"},
    )


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
):
    next_url = _safe_next_url(next, "/")
    if not auth_enabled():
        return RedirectResponse(url=next_url, status_code=303)
    if verify_credentials(username, password):
        request.session["user"] = get_settings().auth_username
        return RedirectResponse(url=next_url, status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "Invalid username or password", "next": next_url},
        status_code=401,
    )


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Home: institutional / consensus price targets for open positions."""
    data = price_targets.get_watchlist_price_targets()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "items": data["items"],
            "items_by_diff": data.get("items_by_diff") or data["items"],
            "groups": data.get("groups") or [],
            "last_updated": data.get("last_updated"),
            "source": data.get("source"),
            "trd_env": data.get("trd_env"),
            "error": data.get("error"),
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
def stock_news_page(request: Request, source: str = "all"):
    """Momo News: watchlist news digests (formerly the home page)."""
    source = news_digest.normalize_news_provider(source)
    digests = news_digest.get_watchlist_digest(provider=source)
    return templates.TemplateResponse(
        request,
        "momo_news.html",
        {
            "digests": digests,
            "source": source,
            "settings": get_settings(),
            "error": None,
        },
    )


@app.get("/holdings-news", response_class=HTMLResponse)
def holdings_news_page(
    request: Request,
    acc_id: int | None = None,
    trd_env: str | None = None,
):
    """List open stock (+ option underlying) positions by market, with KLSE links."""
    try:
        data = order_history.get_order_history(acc_id=acc_id, trd_env=trd_env)
        error = data.get("error")
    except OpenDError as exc:
        data = _empty_orders_payload(
            acc_id=acc_id, start=None, end=None, trd_env=trd_env
        )
        error = str(exc)

    sections = _group_position_stocks_by_market(
        data.get("stock_groups") or [],
        option_clusters=data.get("option_clusters") or [],
    )
    symbols = [
        s["symbol"] for section in sections for s in section.get("stocks") or []
    ]
    summaries = ai_news_summary.get_summaries_for_symbols(symbols)
    for section in sections:
        for stock in section.get("stocks") or []:
            stock["ai_summary"] = summaries.get(stock["symbol"])
    return templates.TemplateResponse(
        request,
        "holdings_news.html",
        {
            "trd_env": data.get("trd_env"),
            "accounts": data.get("accounts") or [],
            "selected_acc_id": data.get("selected_acc_id"),
            "sections": sections,
            "error": error,
        },
    )


def _safe_next_url(next_url: str | None, fallback: str) -> str:
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return fallback


def _reject_if_read_only(fallback: str = "/") -> RedirectResponse | None:
    if get_settings().read_only_ui:
        return RedirectResponse(
            url=f"{fallback}?error={quote('Read-only UI mode (MOMO_UI_MODE=cloudflare)')}",
            status_code=303,
        )
    return None


@app.post("/ai-summary/{code}")
def create_ai_summary(
    code: str,
    force: int = Form(0),
    next: str | None = Form(None),
):
    """Generate (or re-generate) an AI value-investor summary for a stock's news."""
    if (blocked := _reject_if_read_only("/stock-news")) is not None:
        return blocked
    stock = resolve_stock(code)
    fallback = f"/stock/{stock['symbol']}"
    next_url = _safe_next_url(next, fallback)
    try:
        ai_news_summary.summarize_stock(
            stock["symbol"], force=bool(force)
        )
    except ai_news_summary.AiSummaryError as exc:
        sep = "&" if "?" in next_url else "?"
        return RedirectResponse(
            url=f"{next_url}{sep}error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(url=next_url, status_code=303)


@app.get("/api/ai-summary/{code}")
def api_get_ai_summary(code: str):
    stock = resolve_stock(code)
    data = ai_news_summary.get_summary_for_symbol(stock["symbol"])
    if not data:
        return {
            "symbol": stock["symbol"],
            "stock_code": stock["code"],
            "stock_name": stock.get("name") or stock["code"],
            "summary": "",
            "analyst_ratings_preview": None,
            "section_headers": [],
            "model": None,
            "updated_at": None,
        }
    return data


@app.post("/api/ai-summary/preview")
async def api_preview_ai_summary(request: Request):
    """Format pasted summary text and return markdown + rendered HTML preview."""
    payload = await request.json()
    text = str((payload or {}).get("text") or "")
    formatted = ai_news_summary.format_pasted_summary(text)
    return {
        "formatted": formatted,
        "html": str(render_markdown(formatted)),
        "analyst_ratings_preview": ai_news_summary._parse_analyst_ratings_preview(
            formatted
        ),
        "section_headers": ai_news_summary._parse_section_headers(formatted),
    }


@app.put("/api/ai-summary/{code}")
async def api_save_ai_summary(code: str, request: Request):
    """Save a manually pasted/edited AI summary after formatting."""
    if get_settings().read_only_ui:
        return JSONResponse(
            {"ok": False, "error": "Read-only UI mode"},
            status_code=403,
        )
    payload = await request.json()
    text = str((payload or {}).get("text") or "")
    stock_name = (payload or {}).get("stock_name")
    try:
        data = ai_news_summary.save_manual_summary(
            code,
            text,
            stock_name=str(stock_name) if stock_name else None,
        )
    except ai_news_summary.AiSummaryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "summary": data}


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


@app.get("/strategies", response_class=HTMLResponse)
def strategies_page(
    request: Request,
    symbol: str | None = None,
    strategy: str = "bull_put",
):
    data = strategy_timing.get_page(symbol=symbol, strategy=strategy)
    return templates.TemplateResponse(request, "strategies.html", data)


@app.post("/strategies/checks/delete")
def delete_strategy_checks_route(check_ids: list[int] = Form(default=[])):
    if (blocked := _reject_if_read_only("/strategies")) is not None:
        return blocked
    strategy_timing.delete_checks(check_ids)
    return RedirectResponse(url="/strategies", status_code=303)


@app.post("/strategies/checks/{check_id}/delete")
def delete_strategy_check_route(check_id: int):
    if (blocked := _reject_if_read_only("/strategies")) is not None:
        return blocked
    strategy_timing.delete_check(check_id)
    return RedirectResponse(url="/strategies", status_code=303)


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
    if (blocked := _reject_if_read_only("/stock-news")) is not None:
        return blocked
    try:
        news_digest.refresh_watchlist()
    except OpenDError as exc:
        return RedirectResponse(
            url=f"/stock-news?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(url="/stock-news", status_code=303)


@app.post("/refresh-targets")
def refresh_targets():
    if (blocked := _reject_if_read_only("/")) is not None:
        return blocked
    try:
        result = price_targets.refresh_watchlist_targets()
    except OpenDError as exc:
        return RedirectResponse(
            url=f"/?error={quote(str(exc))}", status_code=303
        )
    if result.get("errors"):
        msg = "; ".join(result["errors"][:3])
        return RedirectResponse(
            url=f"/?error={quote(msg)}", status_code=303
        )
    return RedirectResponse(url="/", status_code=303)


@app.post("/refresh-oracle")
def refresh_oracle():
    if (blocked := _reject_if_read_only("/")) is not None:
        return blocked
    result = stock_oracle.refresh_watchlist_oracle()
    if result.get("errors"):
        msg = "; ".join(result["errors"][:3])
        return RedirectResponse(
            url=f"/?error={quote(msg)}", status_code=303
        )
    return RedirectResponse(url="/", status_code=303)


@app.post("/refresh/{code}")
def refresh_one(code: str):
    if (blocked := _reject_if_read_only("/stock-news")) is not None:
        return blocked
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
