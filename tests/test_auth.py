from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from momo.api.main import app
from momo.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        auth_username="momo",
        auth_password="secret",
        session_secret="test-session-secret",
    )
    base.update(overrides)
    return Settings(**base)


def test_login_required_when_configured():
    with patch("momo.api.auth.get_settings", return_value=_settings()):
        with patch("momo.api.main.get_settings", return_value=_settings()):
            client = TestClient(app)
            res = client.get("/", follow_redirects=False)
            assert res.status_code == 303
            assert res.headers["location"].startswith("/login")


def test_login_and_access():
    settings = _settings()
    with patch("momo.api.auth.get_settings", return_value=settings):
        with patch("momo.api.main.get_settings", return_value=settings):
            with patch(
                "momo.api.main.price_targets.get_watchlist_price_targets",
                return_value={
                    "items": [],
                    "items_by_diff": [],
                    "groups": [],
                    "last_updated": None,
                    "source": None,
                    "trd_env": "REAL",
                    "error": None,
                },
            ):
                client = TestClient(app)
                bad = client.post(
                    "/login",
                    data={"username": "momo", "password": "wrong", "next": "/"},
                )
                assert bad.status_code == 401

                ok = client.post(
                    "/login",
                    data={"username": "momo", "password": "secret", "next": "/"},
                    follow_redirects=False,
                )
                assert ok.status_code == 303
                assert ok.headers["location"] == "/"

                home = client.get("/")
                assert home.status_code == 200


def test_auth_disabled_when_empty():
    settings = _settings(auth_username="", auth_password="")
    with patch("momo.api.auth.get_settings", return_value=settings):
        with patch("momo.api.main.get_settings", return_value=settings):
            with patch(
                "momo.api.main.price_targets.get_watchlist_price_targets",
                return_value={
                    "items": [],
                    "items_by_diff": [],
                    "groups": [],
                    "last_updated": None,
                    "source": None,
                    "trd_env": "REAL",
                    "error": None,
                },
            ):
                client = TestClient(app)
                res = client.get("/")
                assert res.status_code == 200
