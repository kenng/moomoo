from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from momo.config import get_settings
from momo.domain.ranking import format_publish_time, parse_publish_time
from momo.opend_client import OpenDError
from momo.services import news_digest
from momo.watchlist import load_watchlist, resolve_stock

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Momo News", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["format_publish_time"] = format_publish_time


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    digests = news_digest.get_watchlist_digest()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "digests": digests,
            "settings": get_settings(),
            "error": None,
        },
    )


@app.get("/stock/{code}", response_class=HTMLResponse)
def stock_detail(request: Request, code: str, sort: str = "date"):
    stock = resolve_stock(code)
    digest = news_digest.get_digest_for_code(stock["code"], market=stock["market"])
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
def api_news(code: str | None = None, limit: int | None = None):
    if code:
        stock = resolve_stock(code)
        return news_digest.get_digest_for_code(
            stock["code"], limit=limit, market=stock["market"]
        )
    return news_digest.get_watchlist_digest(limit_per_stock=limit)


@app.post("/api/refresh")
def api_refresh(code: str | None = Form(default=None)):
    if code:
        stock = resolve_stock(code)
        return news_digest.refresh_stock_news(stock)
    return news_digest.refresh_watchlist()
