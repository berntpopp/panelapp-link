"""WS-5: OTel bootstrap gating + stdio safety.

Span-shape recording (tool/upstream spans under a real SDK provider) is covered
in ``test_tracing.py``; that module sets the process-global ``TracerProvider``,
so this module deliberately does NOT install its own (OTel allows the global
provider to be set only once per process). Here we only exercise the
``setup_tracing`` bootstrap gate.
"""

from __future__ import annotations

from panelapp_link.config import settings
from panelapp_link.observability import tracing


def test_setup_tracing_disabled_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(settings.otel, "enabled", False)
    assert tracing.setup_tracing() is False


def test_setup_tracing_enabled_without_exporter_is_noop(monkeypatch) -> None:
    """Enabled but the OTLP exporter extra is not installed -> graceful no-op.

    The default/dev install ships opentelemetry-sdk but NOT the OTLP exporter
    (only the opt-in ``otel`` extra), so the import inside setup_tracing fails and
    the bootstrap degrades to False instead of raising. With the extra installed
    the install path may run, but it must never raise and always returns a bool.
    """
    monkeypatch.setattr(settings.otel, "enabled", True)
    assert isinstance(tracing.setup_tracing(), bool)
