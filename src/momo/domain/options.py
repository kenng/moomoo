"""Parse Moomoo option codes: {Market}.{Underlying}{YYMMDD}{C|P}{Strike×1000}."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

# US.QCOM260911P140000 / HK.TCH260522C330000
_OPTION_RE = re.compile(
    r"^(?P<market>[A-Z]{2})\.(?P<root>[A-Z]+)(?P<yymmdd>\d{6})(?P<cp>[CP])(?P<strike>\d+)$"
)


@dataclass(frozen=True)
class OptionInfo:
    code: str
    market: str
    underlying_root: str
    underlying_symbol: str
    expiry: date
    option_type: str  # CALL / PUT
    strike: float
    multiplier: int = 100

    @property
    def days_to_expiry(self) -> int:
        return (self.expiry - date.today()).days


def parse_option_code(code: str) -> OptionInfo | None:
    raw = (code or "").strip().upper()
    match = _OPTION_RE.match(raw)
    if not match:
        return None

    yymmdd = match.group("yymmdd")
    try:
        expiry = datetime.strptime(yymmdd, "%y%m%d").date()
    except ValueError:
        return None

    strike_raw = match.group("strike")
    strike = int(strike_raw) / 1000.0
    market = match.group("market")
    root = match.group("root")
    cp = match.group("cp")

    return OptionInfo(
        code=raw,
        market=market,
        underlying_root=root,
        # US options use ticker as root; HK uses exchange abbreviation (not stock code).
        underlying_symbol=f"{market}.{root}",
        expiry=expiry,
        option_type="CALL" if cp == "C" else "PUT",
        strike=strike,
        multiplier=100,
    )


def is_option_code(code: str) -> bool:
    return parse_option_code(code) is not None
