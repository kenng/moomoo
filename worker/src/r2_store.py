from __future__ import annotations

import json
from typing import Any


def _prefix(env) -> str:
    prefix = getattr(env, "R2_PREFIX", None) or "momo/"
    prefix = str(prefix).lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return prefix


def object_key(env, relative: str) -> str:
    return f"{_prefix(env)}{relative.lstrip('/')}"


async def get_json(env, relative: str) -> Any | None:
    """Load a JSON object from R2; return None if missing."""
    key = object_key(env, relative)
    obj = await env.MOMO_BUCKET.get(key)
    if obj is None:
        return None
    text = await obj.text()
    if not text:
        return None
    return json.loads(text)


async def get_meta(env) -> dict:
    meta = await get_json(env, "meta.json")
    return meta if isinstance(meta, dict) else {}
