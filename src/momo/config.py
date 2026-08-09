from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]

Market = Literal["MY", "HK", "US", "SG", "SH", "SZ", "JP"]
KNOWN_MARKETS = ("MY", "HK", "US", "SG", "SH", "SZ", "JP")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    opend_host: str = "127.0.0.1"
    opend_port: int = 11111

    # Default market for bare numeric codes (e.g. 1810 → HK.01810 when HK)
    default_market: Market = "MY"

    trading_mode: Literal["paper", "live"] = "paper"
    allow_live_trading: bool = False

    # Trade queries (order history / positions). Firm must match OpenD login.
    # FUTUMY = moomoo MY (your live HK/MY/US book); FUTUSG / FUTUSECURITIES for other entities.
    security_firm: str = "FUTUMY"
    # Orders page reads this env. Independent of TRADING_MODE (paper still blocks live *orders*).
    orders_trd_env: Literal["REAL", "SIMULATE"] = "REAL"
    # 0 = all accounts for orders_trd_env; otherwise a specific acc_id
    trd_acc_id: int = 0

    watchlist_path: Path = ROOT_DIR / "watchlist.yaml"
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'momo.db'}"

    news_max_count: int = 50
    news_top_n: int = 10

    finnhub_api_key: str = ""
    finnhub_news_lookback_days: int = 7

    # Cursor SDK for AI news summaries (leave empty to disable)
    # Key: https://cursor.com/dashboard/integrations
    cursor_api_key: str = ""
    cursor_model: str = "composer-2.5"

    # Google Sheets sync (optional — leave empty to disable)
    google_sheets_spreadsheet_id: str = ""
    google_sheets_credentials_path: Path = ROOT_DIR / "credentials" / "google-sheets.json"
    google_sheets_stock_orders_tab: str = "stock orders"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def live_trading_enabled(self) -> bool:
        return self.trading_mode == "live" and self.allow_live_trading

    @property
    def trd_env_name(self) -> Literal["SIMULATE", "REAL"]:
        """Env used when *placing* trades (paper → SIMULATE)."""
        return "REAL" if self.live_trading_enabled else "SIMULATE"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def bare_code(code: str) -> str:
    """Strip market prefix (MY.1155 → 1155, HK.01810 → 01810)."""
    code = code.strip().upper()
    if "." in code:
        market, rest = code.split(".", 1)
        if market in KNOWN_MARKETS:
            return rest
    return code


def to_symbol(code: str, market: str | None = None) -> str:
    """Normalize to Moomoo symbol (MY.1155, HK.01810, …)."""
    code = code.strip().upper()
    if not code:
        raise ValueError("stock code is empty")

    if "." in code:
        prefix, rest = code.split(".", 1)
        if prefix in KNOWN_MARKETS:
            return f"{prefix}.{_normalize_local(prefix, rest)}"

    mkt = (market or get_settings().default_market).upper()
    if mkt not in KNOWN_MARKETS:
        raise ValueError(f"unsupported market: {mkt}")
    return f"{mkt}.{_normalize_local(mkt, code)}"


def to_my_code(code: str) -> str:
    """Backward-compatible alias for to_symbol()."""
    return to_symbol(code)


def _normalize_local(market: str, code: str) -> str:
    code = code.strip().upper()
    # HK Exchange codes are 5 digits in OpenD (1810 → 01810)
    if market == "HK" and code.isdigit():
        return code.zfill(5)
    return code
