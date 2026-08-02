from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from momo.config import get_settings, to_my_code
from momo.opend_client import OpenDError
from momo.services import news_digest
from momo.watchlist import load_watchlist

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Momo MY News", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
def stock_detail(request: Request, code: str):
    digest = news_digest.get_digest_for_code(code)
    stock = next(
        (s for s in load_watchlist() if s["code"] == code or s["symbol"] == to_my_code(code)),
        {"code": code, "symbol": to_my_code(code), "name": code},
    )
    return templates.TemplateResponse(
        request,
        "stock.html",
        {
            "stock": stock,
            "digest": digest,
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
    stock = next(
        (s for s in load_watchlist() if s["code"] == code or s["symbol"] == to_my_code(code)),
        {
            "code": code.removeprefix("MY."),
            "symbol": to_my_code(code),
            "name": code,
        },
    )
    try:
        news_digest.refresh_stock_news(stock)
    except OpenDError as exc:
        return RedirectResponse(url=f"/stock/{code}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/stock/{code}", status_code=303)


@app.get("/api/watchlist")
def api_watchlist():
    return load_watchlist()


@app.get("/api/news")
def api_news(code: str | None = None, limit: int | None = None):
    if code:
        return news_digest.get_digest_for_code(code, limit=limit)
    return news_digest.get_watchlist_digest(limit_per_stock=limit)


@app.post("/api/refresh")
def api_refresh(code: str | None = Form(default=None)):
    if code:
        stock = {
            "code": code.removeprefix("MY."),
            "symbol": to_my_code(code),
            "name": code,
        }
        return news_digest.refresh_stock_news(stock)
    return news_digest.refresh_watchlist()
