#!/usr/bin/env bash
# Embed shared UI, deploy Worker, sync Worker-safe secrets/vars from repo-root .env.
# Never uploads OpenD / Finnhub / Cursor / Google / Telegram / R2 API keys.
set -euo pipefail
cd "$(dirname "$0")"

export PATH="${HOME}/.local/bin:${PATH}"

ROOT_ENV="$(cd .. && pwd)/.env"

# Safely load selected keys (do not `source` .env — values may contain spaces).
eval "$(
  ROOT_ENV="$ROOT_ENV" python3 - <<'PY'
from pathlib import Path
import os, shlex
path = Path(os.environ["ROOT_ENV"])
wanted = ("AUTH_USERNAME", "AUTH_PASSWORD", "SESSION_SECRET", "R2_PREFIX")
vals = {k: "" for k in wanted}
if path.is_file():
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k in wanted:
            vals[k] = v.strip().strip("'").strip('"')
for k, v in vals.items():
    print(f"{k}={shlex.quote(v)}")
PY
)"

echo "Embedding shared UI from src/momo/api/templates…"
python3 scripts/embed_templates.py

R2_PREFIX_VAL="${R2_PREFIX:-momo/}"
echo "Deploying momo-digest Worker (MOMO_UI_MODE=cloudflare, R2_PREFIX=${R2_PREFIX_VAL})…"
uv run pywrangler deploy \
  --var "MOMO_UI_MODE:cloudflare" \
  --var "R2_PREFIX:${R2_PREFIX_VAL}"

put_secret() {
  local name="$1"
  local value="${2:-}"
  if [[ -z "$value" ]]; then
    echo "skip secret $name (empty)"
    return 0
  fi
  printf '%s' "$value" | npx wrangler secret put "$name"
  echo "set secret $name"
}

echo "Setting Worker secrets from .env (AUTH_* / SESSION_SECRET)…"
put_secret AUTH_USERNAME "${AUTH_USERNAME:-}"
put_secret AUTH_PASSWORD "${AUTH_PASSWORD:-}"
put_secret SESSION_SECRET "${SESSION_SECRET:-}"

echo "Done. Publish data with: momo publish"
echo "Worker: https://momo-digest.itwonders-sg.workers.dev/"
