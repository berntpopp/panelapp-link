"""Tests for process-wide RED metrics (panelapp_link.observability.metrics).

RED = Rate (requests), Errors (by code), Duration (percentiles). Plus a cache
hit-ratio and per-region upstream timing. Exported as Prometheus text and folded
into get_panelapp_diagnostics.
"""

from __future__ import annotations

from panelapp_link.observability.metrics import MetricsRegistry, get_metrics, reset_metrics


def test_records_requests_and_errors_by_code() -> None:
    reg = MetricsRegistry()
    reg.record_request("search_panels", None, 120.0)
    reg.record_request("search_panels", None, 80.0)
    reg.record_request("get_panel", "not_found", 5.0)
    snap = reg.snapshot()
    assert snap["requests_total"] == 3
    assert snap["requests_by_tool"]["search_panels"] == 2
    assert snap["errors_total"] == 1
    assert snap["errors_by_code"]["not_found"] == 1


def test_cache_hit_ratio() -> None:
    reg = MetricsRegistry()
    for _ in range(3):
        reg.record_cache("hit")
    reg.record_cache("miss")
    reg.record_cache("coalesced")
    cache = reg.snapshot()["cache"]
    assert cache["hit"] == 3
    assert cache["miss"] == 1
    assert cache["coalesced"] == 1
    # hit_ratio = hits / (hits + misses); coalesced does not count as an upstream miss
    assert cache["hit_ratio"] == 0.75


def test_duration_percentiles_nearest_rank() -> None:
    reg = MetricsRegistry()
    for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
        reg.record_request("search_panels", None, float(v))
    dur = reg.snapshot()["tool_duration_ms"]["search_panels"]
    assert dur["count"] == 10
    assert dur["p50"] == 50.0
    assert dur["p95"] == 100.0
    assert dur["p99"] == 100.0
    assert dur["max"] == 100.0


def test_upstream_duration_by_region() -> None:
    reg = MetricsRegistry()
    reg.record_upstream("uk", 200.0)
    reg.record_upstream("australia", 50.0)
    up = reg.snapshot()["upstream_duration_ms"]
    assert up["uk"]["count"] == 1
    assert up["uk"]["p95"] == 200.0
    assert up["australia"]["p95"] == 50.0


def test_prometheus_render_has_red_series() -> None:
    reg = MetricsRegistry()
    reg.record_request("search_panels", None, 120.0)
    reg.record_request("get_panel", "not_found", 5.0)
    reg.record_cache("hit")
    reg.record_upstream("uk", 200.0)
    text = reg.render_prometheus()
    assert "# TYPE panelapp_requests_total counter" in text
    assert 'panelapp_requests_total{tool="search_panels"} 1' in text
    assert 'panelapp_errors_total{tool="get_panel",code="not_found"} 1' in text
    assert 'panelapp_cache_events_total{result="hit"} 1' in text
    assert 'panelapp_tool_duration_ms{tool="search_panels",quantile="0.95"}' in text
    assert 'panelapp_upstream_duration_ms{region="uk",quantile="0.95"}' in text
    # Prometheus exposition must end with a trailing newline.
    assert text.endswith("\n")


def test_get_metrics_is_singleton_and_resettable() -> None:
    reset_metrics()
    a = get_metrics()
    b = get_metrics()
    assert a is b
    a.record_request("resolve_gene", None, 1.0)
    assert get_metrics().snapshot()["requests_total"] == 1
    reset_metrics()
    assert get_metrics().snapshot()["requests_total"] == 0


def test_refresh_metrics_snapshot_tracks_bounded_values() -> None:
    reg = MetricsRegistry()
    reg.record_refresh(
        failures_total=7,
        consecutive_failures=2,
        age_seconds=123.5,
        status="degraded",
    )

    assert reg.snapshot()["refresh"] == {
        "failures_total": 7,
        "consecutive_failures": 2,
        "age_seconds": 123.5,
        "status": "degraded",
    }


def test_prometheus_refresh_metrics_have_one_active_bounded_status() -> None:
    reg = MetricsRegistry()
    reg.record_refresh(
        failures_total=3,
        consecutive_failures=3,
        age_seconds=600.0,
        status="stale",
    )
    text = reg.render_prometheus()

    assert "panelapp_refresh_failures_total 3" in text
    assert "panelapp_refresh_consecutive_failures 3" in text
    assert "panelapp_refresh_age_seconds 600.0" in text
    statuses = {
        line.split('status="', 1)[1].split('"', 1)[0]: int(line.rsplit(" ", 1)[1])
        for line in text.splitlines()
        if line.startswith("panelapp_refresh_status{")
    }
    assert statuses == {
        "disabled": 0,
        "initializing": 0,
        "healthy": 0,
        "degraded": 0,
        "stale": 1,
    }
    assert "DownloadError" not in text


def test_refresh_status_rejects_unbounded_label() -> None:
    reg = MetricsRegistry()
    try:
        reg.record_refresh(
            failures_total=1,
            consecutive_failures=1,
            age_seconds=None,
            status="DownloadError",
        )
    except ValueError as exc:
        assert "status" in str(exc)
    else:
        raise AssertionError("unbounded refresh status was accepted")
