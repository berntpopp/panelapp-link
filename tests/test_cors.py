"""Security guard: CORS on this unauthenticated backend must not send
credentials. The backend holds no cookies/session, so credentialed CORS is
meaningless and a footgun if origins were ever widened to ``*``. The configured
method list (GET/POST/OPTIONS) must be preserved so GET ``/health``/root keep
working. Research use only; not clinical decision support."""

from __future__ import annotations

import warnings

import pytest
from starlette.middleware.cors import CORSMiddleware

from panelapp_link.config import settings
from panelapp_link.server_manager import create_app

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient


def _cors_kwargs(app: object) -> dict[str, object]:
    for mw in app.user_middleware:  # type: ignore[attr-defined]
        if mw.cls is CORSMiddleware:
            return dict(mw.kwargs)
    raise AssertionError("CORSMiddleware is not installed on the app")


def test_cors_credentials_disabled_by_default() -> None:
    # Unauthenticated backend: credentials must be off out of the box.
    assert settings.cors_allow_credentials is False
    assert _cors_kwargs(create_app())["allow_credentials"] is False


def test_cors_methods_match_settings_in_installed_middleware() -> None:
    # Strong check: assert the verb list actually wired into the installed
    # CORSMiddleware -- not merely what ``settings`` holds. The old assertion
    # (``"GET" in settings.cors_allow_methods``) passed even if the middleware
    # were handed a different/empty method list, so it guarded nothing.
    installed_methods = _cors_kwargs(create_app())["allow_methods"]
    assert installed_methods == settings.cors_allow_methods
    assert "GET" in installed_methods  # type: ignore[operator]


def test_health_get_still_200() -> None:
    # Separate behavioural check: disabling credentials must not disable the
    # GET verb -- ``/health`` is a GET and must keep returning 200.
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200


def test_startup_guard_rejects_credentials_with_wildcard_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fail closed: a credentialed wildcard-origin CORS config is invalid.
    monkeypatch.setattr(settings, "cors_allow_credentials", True)
    monkeypatch.setattr(settings, "cors_origins", ["*"])
    with pytest.raises(ValueError, match="credential"):
        create_app()
