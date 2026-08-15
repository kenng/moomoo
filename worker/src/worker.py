from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import DictLoader, Environment, select_autoescape
from workers import WorkerEntrypoint

from auth import (
    RequireLoginMiddleware,
    auth_enabled,
    clear_session_cookie,
    current_user,
    set_session_cookie,
    verify_credentials,
)
from filters import (
    NEWS_RETENTION_DAYS,
    format_publish_time,
    format_short_date,
    format_sort_key,
    format_updated_at,
    news_is_fresh,
    parse_publish_time,
    render_markdown,
)
from ticker_urls import simplywall_url, yahoo_quote_url
from r2_store import get_json, get_meta
from template_sources import TEMPLATES

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_KNOWN_MARKETS = ("MY", "HK", "US", "SG", "SH", "SZ", "JP")


def _fresh_news_items(items: list | None) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_RETENTION_DAYS)
    return [
        item
        for item in items or []
        if news_is_fresh(item.get("publish_time") or "", cutoff=cutoff)
    ]


def _fresh_digests(digests: list | None) -> list:
    return [
        {**digest, "news": _fresh_news_items(digest.get("news"))}
        for digest in digests or []
    ]

app = FastAPI(title="Momo Digest Mirror", version="0.1.0")
app.add_middleware(RequireLoginMiddleware)

_jinja = Environment(
    loader=DictLoader(TEMPLATES),
    autoescape=select_autoescape(["html", "xml"]),
)
_jinja.filters["format_publish_time"] = format_publish_time
_jinja.filters["format_updated_at"] = format_updated_at
_jinja.filters["format_short_date"] = format_short_date
_jinja.filters["format_sort_key"] = format_sort_key
_jinja.filters["markdown"] = render_markdown
_jinja.filters["yahoo_quote_url"] = yahoo_quote_url
_jinja.filters["simplywall_url"] = simplywall_url


def _env(request: Request):
    return request.scope["env"]


def _ui_mode(env) -> str:
    mode = str(getattr(env, "MOMO_UI_MODE", None) or "cloudflare").strip().lower()
    return mode if mode in ("local", "cloudflare") else "cloudflare"


async def _common(request: Request) -> dict:
    env = _env(request)
    meta = await get_meta(env)
    mode = _ui_mode(env)
    return {
        "request": request,
        "ui_mode": mode,
        "read_only": mode == "cloudflare",
        "synced_at": meta.get("synced_at"),
        "current_user": current_user(request),
        "auth_on": auth_enabled(env),
    }


def _empty_banner(message: str = "No snapshot yet. Run `momo publish` locally.") -> dict:
    return {"error": message}


async def _render(request: Request, name: str, context: dict) -> HTMLResponse:
    ctx = await _common(request)
    ctx.update(context)
    html = _jinja.get_template(name).render(ctx)
    return HTMLResponse(html)


def _bare_code(code: str) -> str:
    code = code.strip().upper()
    if "." in code:
        market, rest = code.split(".", 1)
        if market in _KNOWN_MARKETS:
            return rest
    return code


async def _resolve_symbol(env, code: str) -> str | None:
    raw = (code or "").strip().upper()
    if not raw:
        return None
    index = await get_json(env, "stocks/index.json") or {}
    if not isinstance(index, dict):
        return None
    if raw in index:
        return raw
    bare = _bare_code(raw)
    matches = [
        symbol
        for symbol, stock in index.items()
        if (stock or {}).get("code") == bare
        or (stock or {}).get("code") == raw
        or symbol.endswith(f".{bare}")
    ]
    if len(matches) == 1:
        return matches[0]
    if raw in {m.split(".", 1)[-1] for m in matches}:
        # Prefer exact bare match uniqueness
        exact = [m for m in matches if m.endswith(f".{bare}")]
        if len(exact) == 1:
            return exact[0]
    return matches[0] if matches else None


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None):
    env = _env(request)
    if not auth_enabled(env):
        return RedirectResponse(url="/", status_code=303)
    if current_user(request):
        return RedirectResponse(url=next or "/", status_code=303)
    return await _render(
        request,
        "login.html",
        {"error": None, "next": next or "/"},
    )


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
):
    env = _env(request)
    if not auth_enabled(env):
        return RedirectResponse(url="/", status_code=303)
    if not verify_credentials(env, username, password):
        return await _render(
            request,
            "login.html",
            {"error": "Invalid username or password", "next": next or "/"},
        )
    dest = next if next and next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(url=dest, status_code=303)
    set_session_cookie(response, env, username)
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response)
    return response


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    data = await get_json(_env(request), "home.json")
    if not data:
        return await _render(
            request,
            "index.html",
            {
                "items": [],
                "items_by_diff": [],
                "groups": [],
                "last_updated": None,
                "source": None,
                "trd_env": None,
                **_empty_banner(),
            },
        )
    return await _render(
        request,
        "index.html",
        {
            "items": data.get("items") or [],
            "items_by_diff": data.get("items_by_diff") or data.get("items") or [],
            "groups": data.get("groups") or [],
            "last_updated": data.get("last_updated"),
            "source": data.get("source"),
            "trd_env": data.get("trd_env"),
            "error": data.get("error"),
        },
    )


@app.get("/stock-news", response_class=HTMLResponse)
async def stock_news_page(request: Request, source: str = "all"):
    source = (source or "all").strip().lower()
    if source not in ("all", "opend", "finnhub"):
        source = "all"
    digests = await get_json(_env(request), f"stock-news/{source}.json")
    if digests is None:
        return await _render(
            request,
            "momo_news.html",
            {"digests": [], "source": source, "settings": None, **_empty_banner()},
        )
    return await _render(
        request,
        "momo_news.html",
        {
            "digests": _fresh_digests(digests),
            "source": source,
            "settings": None,
            "error": None,
        },
    )


@app.get("/holdings-news", response_class=HTMLResponse)
async def holdings_news_page(request: Request):
    data = await get_json(_env(request), "holdings-news.json")
    if not data:
        return await _render(
            request,
            "holdings_news.html",
            {
                "trd_env": "",
                "accounts": [],
                "selected_acc_id": None,
                "sections": [],
                **_empty_banner(),
            },
        )
    return await _render(
        request,
        "holdings_news.html",
        {
            "trd_env": data.get("trd_env") or "",
            "accounts": data.get("accounts") or [],
            "selected_acc_id": data.get("selected_acc_id"),
            "sections": data.get("sections") or [],
            "error": data.get("error"),
        },
    )


def _orders_context(data: dict | None, *, asset_kind: str) -> dict:
    empty_totals = {
        "open_positions": 0,
        "symbols": 0,
        "orders": 0,
        "market_val": None,
        "unrealized_pl": None,
    }
    if not data:
        return {
            "asset_kind": asset_kind,
            "trd_env": "",
            "accounts": [],
            "selected_acc_id": None,
            "start": "",
            "end": "",
            "as_of": "",
            "stock_groups": [],
            "option_clusters": [],
            "totals": empty_totals,
            "stock_totals": empty_totals,
            "option_totals": empty_totals,
            **_empty_banner(),
        }
    page_totals = (
        data.get("option_totals")
        if asset_kind == "options"
        else data.get("stock_totals")
    )
    return {
        **data,
        "asset_kind": asset_kind,
        "totals": page_totals or data.get("totals") or empty_totals,
        "error": data.get("error"),
    }


@app.get("/orders", response_class=HTMLResponse)
async def orders_options_page(request: Request):
    data = await get_json(_env(request), "orders.json")
    return await _render(request, "orders.html", _orders_context(data, asset_kind="options"))


@app.get("/orders/options", response_class=HTMLResponse)
async def orders_options_alias(request: Request):
    return await orders_options_page(request)


@app.get("/orders/stocks", response_class=HTMLResponse)
async def orders_stocks_page(request: Request):
    data = await get_json(_env(request), "orders.json")
    return await _render(request, "orders.html", _orders_context(data, asset_kind="stocks"))


@app.get("/dividends", response_class=HTMLResponse)
async def dividends_page(request: Request):
    data = await get_json(_env(request), "dividends.json")
    if not data:
        return await _render(
            request,
            "dividends.html",
            {
                "trd_env": "",
                "accounts": [],
                "selected_acc_id": None,
                "start": "",
                "end": "",
                "show_all": True,
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
                **_empty_banner(),
            },
        )
    data = {**data, "auto_sync": False}
    return await _render(request, "dividends.html", data)


@app.get("/stock/{code}", response_class=HTMLResponse)
async def stock_detail(
    request: Request, code: str, sort: str = "date", source: str = "all"
):
    env = _env(request)
    source = (source or "all").strip().lower()
    if source not in ("all", "opend", "finnhub"):
        source = "all"
    sort = sort if sort in ("date", "score") else "date"

    symbol = await _resolve_symbol(env, code)
    if not symbol:
        return await _render(
            request,
            "stock.html",
            {
                "stock": {"code": code, "symbol": code, "name": code, "market": ""},
                "digest": {"news": [], "snapshot": None},
                "sort": sort,
                "source": source,
                **_empty_banner(f"No snapshot for {code}. Run `momo publish` locally."),
            },
        )

    payload = await get_json(env, f"stocks/{symbol}.json")
    if not payload:
        return await _render(
            request,
            "stock.html",
            {
                "stock": {"code": code, "symbol": symbol, "name": symbol, "market": ""},
                "digest": {"news": [], "snapshot": None},
                "sort": sort,
                "source": source,
                **_empty_banner(),
            },
        )

    stock = payload.get("stock") or {"symbol": symbol, "code": _bare_code(symbol)}
    by_provider = payload.get("digests_by_provider") or {}
    digest = by_provider.get(source) or payload.get("digest") or {}
    news = _fresh_news_items(digest.get("news"))
    if sort == "score":
        news.sort(key=lambda n: n.get("importance_score") or 0, reverse=True)
    else:
        news.sort(
            key=lambda n: parse_publish_time(n.get("publish_time") or "") or _EPOCH,
            reverse=True,
        )
    digest = {**digest, "news": news}
    return await _render(
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


@app.get("/api/meta")
async def api_meta(request: Request):
    return await get_meta(_env(request))


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = str(getattr(request, "url", "") or "")
        path = ""
        try:
            from urllib.parse import urlparse

            path = urlparse(url).path or ""
        except Exception:
            path = ""

        if path.startswith("/static/"):
            assets = getattr(self.env, "ASSETS", None)
            if assets is not None:
                return await assets.fetch(request)

        import asgi

        return await asgi.fetch(app, request.js_object, self.env)
