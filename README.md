# momo

Helpers around Moomoo OpenD: quote smoke tests, watchlist news digest, and a small local web UI. Defaults to **HK** trading symbols; MY (Bursa) and other markets are supported via prefix or `market` on each watchlist entry.

## Commands

Cloudflare: 
```bash
# Export read-only digest to R2 for the Worker mirror
momo publish
momo publish --dry-run

# Deploy the Cloudflare Worker (then publish data)
./worker/deploy.sh
```

```bash
# Daily — pull watchlist news into the local DB (OpenD must be running)
momo refresh --watchlist

# Daily — pull consensus / institution price targets for open positions
momo refresh --targets

# Daily — local UI at http://127.0.0.1:8000 (`-w` refreshes news first)
./start.sh
./start.sh -w

# Verify OpenD + quote rights
momo smoke --code 1810
momo smoke --code HK.1810
momo smoke --code 1155 --market MY

# Watchlist
momo watchlist

# Refresh a single symbol
momo refresh --code 1810

# Show cached digest
momo news --watchlist
momo news --code 1810 --json

```

## Requirements

- Python 3.11+
- [Moomoo OpenD](https://www.moomoo.com/download/OpenAPI) running and logged in (default `127.0.0.1:11111`)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
# or: pip install -r requirements.txt && pip install -e .
```

Copy or edit `.env` as needed:

| Variable | Default | Notes |
|---|---|---|
| `OPEND_HOST` / `OPEND_PORT` | `127.0.0.1` / `11111` | OpenD quote API |
| `DEFAULT_MARKET` | `HK` | Used for bare codes (`1810` → `HK.01810`) |
| `TRADING_MODE` | `paper` | Keep paper until you intentionally go live |
| `ALLOW_LIVE_TRADING` | `false` | Must be `true` with `TRADING_MODE=live` for live |
| `WATCHLIST_PATH` | `watchlist.yaml` | Watchlist file |
| `DATABASE_URL` | `sqlite:///./data/momo.db` | News/snapshot cache |

## Symbols

OpenD wants market-prefixed codes:

| Input | Resolved (with `DEFAULT_MARKET=HK`) |
|---|---|
| `1810` | `HK.01810` |
| `HK.1810` / `HK.01810` | `HK.01810` |
| `1155` + `--market MY` | `MY.1155` |
| `MY.1155` | `MY.1155` |

HK numeric codes are zero-padded to 5 digits.

## Watchlist

`watchlist.yaml`:

```yaml
stocks:
  - code: "1810"
    market: HK
    name: Xiaomi
  - code: "1155"
    market: MY
    name: Maybank
```

Or use a prefixed code (`HK.01810`, `MY.1155`) and omit `market`.

## Web UI

`./start.sh` runs uvicorn with the venv active (`--reload --host 127.0.0.1 --port 8000`). Open http://127.0.0.1:8000 — home shows the watchlist digest; stock pages support refresh.

## Project layout

```
src/momo/
  adapters/     # OpenD quote / news / trade wrappers
  api/          # FastAPI + templates
  cli.py        # momo command
  config.py     # settings + symbol normalization
  db/           # SQLAlchemy models / repo
  domain/       # ranking, risk
  jobs/         # refresh_news job entry
  services/     # news digest, alerts, paper stop-loss
watchlist.yaml
.env
```

## Notes

- Live trading stays disabled until both `TRADING_MODE=live` and `ALLOW_LIVE_TRADING=true`.
- Telegram alert fields in `.env` are reserved for a later phase.
- If smoke fails with `Unknown stock`, check market prefix / padding (`HK.01810`, not bare `1810` without `DEFAULT_MARKET=HK`).
