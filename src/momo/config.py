from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    opend_host: str = "127.0.0.1"
    opend_port: int = 11111

    trading_mode: Literal["paper", "live"] = "paper"
    allow_live_trading: bool = False

    watchlist_path: Path = ROOT_DIR / "watchlist.yaml"
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'momo.db'}"

    news_max_count: int = 50
    news_top_n: int = 10

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def live_trading_enabled(self) -> bool:
        return self.trading_mode == "live" and self.allow_live_trading


@lru_cache
def get_settings() -> Settings:
    return Settings()


def to_my_code(code: str) -> str:
    """Normalize Bursa code to Moomoo MY format."""
    code = code.strip().upper()
    if code.startswith("MY."):
        return code
    return f"MY.{code}"
