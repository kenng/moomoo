from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from momo.config import ROOT_DIR, get_settings

try:
    import boto3
    from botocore.client import BaseClient
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]
    BaseClient = Any  # type: ignore[misc,assignment]


class R2ConfigError(RuntimeError):
    pass


def _endpoint_url() -> str:
    settings = get_settings()
    if settings.r2_endpoint:
        return settings.r2_endpoint.rstrip("/")
    if not settings.r2_account_id:
        raise R2ConfigError("R2_ACCOUNT_ID is not set")
    return f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"


def s3_configured() -> bool:
    s = get_settings()
    return bool(
        s.r2_account_id
        and s.r2_access_key_id
        and s.r2_secret_access_key
        and s.r2_bucket
        and boto3 is not None
    )


def wrangler_available() -> bool:
    return bool(shutil.which("npx") or shutil.which("wrangler"))


def r2_configured() -> bool:
    """True if S3 API keys are set, or wrangler can upload with OAuth."""
    s = get_settings()
    if not s.r2_bucket:
        return False
    return s3_configured() or wrangler_available()


@lru_cache
def _client() -> BaseClient:
    settings = get_settings()
    if not s3_configured():
        raise R2ConfigError(
            "R2 S3 API is not configured — set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY, and R2_BUCKET (or use wrangler login)"
        )
    return boto3.client(
        "s3",
        endpoint_url=_endpoint_url(),
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def object_key(relative: str) -> str:
    """Join R2_PREFIX with a relative key (no leading slash)."""
    prefix = (get_settings().r2_prefix or "").lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    rel = relative.lstrip("/")
    return f"{prefix}{rel}"


def _put_via_wrangler(key: str, data: bytes, *, content_type: str) -> None:
    settings = get_settings()
    worker_dir = ROOT_DIR / "worker"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        cmd = [
            "npx",
            "wrangler",
            "r2",
            "object",
            "put",
            f"{settings.r2_bucket}/{key}",
            "--file",
            str(tmp_path),
            "--content-type",
            content_type,
            "--remote",
        ]
        result = subprocess.run(
            cmd,
            cwd=str(worker_dir if worker_dir.is_dir() else ROOT_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise R2ConfigError(
                f"wrangler r2 object put failed for {key}: {detail}"
            )
    finally:
        tmp_path.unlink(missing_ok=True)


def put_bytes(
    relative_key: str,
    data: bytes,
    *,
    content_type: str = "application/octet-stream",
) -> str:
    settings = get_settings()
    if not settings.r2_bucket:
        raise R2ConfigError("R2_BUCKET is not set")
    key = object_key(relative_key)
    if s3_configured():
        _client().put_object(
            Bucket=settings.r2_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    elif wrangler_available():
        _put_via_wrangler(key, data, content_type=content_type)
    else:
        raise R2ConfigError(
            "R2 is not configured — set S3 API keys or install wrangler and run "
            "`wrangler login`"
        )
    return key


def put_json(relative_key: str, obj: Any) -> str:
    """Upload a JSON object; returns the full object key."""
    body = json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8")
    return put_bytes(
        relative_key,
        body,
        content_type="application/json; charset=utf-8",
    )
