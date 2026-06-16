"""Async crawl of both PanelApp regions into in-memory structures.

For each region we list every panel, fetch the signed-off map, and fetch the
full ``/panels/{id}/`` detail (genes/regions/strs) under bounded concurrency.
The builder consumes the returned dict directly; it performs no network I/O.

Crawl shape::

    {
      "uk":        {"panels": [...], "signed_off": {id: {"version", "signed_off"}}, "details": {id: detail}},
      "australia": {...},
    }
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from panelapp_link.api.client import PanelAppRestClient

if TYPE_CHECKING:
    from panelapp_link.config import PanelAppDataConfigModel

# Region key -> config attribute holding that region's base URL.
_REGION_URL_ATTR = {"uk": "uk_api_url", "australia": "au_api_url"}


async def crawl_region(
    client: PanelAppRestClient,
    region: str,
    base_url: str,
    *,
    only_panel_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Crawl one region: panel list, signed-off map, and per-panel details.

    Args:
        client: Shared REST client.
        region: Region key (``"uk"`` or ``"australia"``).
        base_url: Region base URL (``.../api/v1``).
        only_panel_ids: When given, only these panel ids have their details
            fetched (incremental refresh); the full panel list is still returned.

    Returns:
        A mapping with ``panels``, ``signed_off``, and ``details`` keys.
    """
    panels = await client.list_panels(base_url)
    signed_off_rows = await client.list_signed_off(base_url)
    signed_off: dict[int, dict[str, Any]] = {}
    for row in signed_off_rows:
        pid = row.get("id")
        if pid is None:
            continue
        signed_off[int(pid)] = {
            "version": row.get("version"),
            "signed_off": row.get("signed_off"),
        }

    panel_ids: list[int] = []
    for panel in panels:
        pid = panel.get("id")
        if pid is None:
            continue
        pid = int(pid)
        if only_panel_ids is not None and pid not in only_panel_ids:
            continue
        panel_ids.append(pid)

    detail_list = await asyncio.gather(*(client.get_panel(base_url, pid) for pid in panel_ids))
    details = dict(zip(panel_ids, detail_list, strict=True))
    return {"panels": panels, "signed_off": signed_off, "details": details}


async def crawl_all(
    config: PanelAppDataConfigModel,
    *,
    client: PanelAppRestClient | None = None,
    only_panel_ids: dict[str, set[int]] | None = None,
) -> dict[str, Any]:
    """Crawl both regions and return ``{"uk": ..., "australia": ...}``.

    Args:
        config: Active data configuration (region base URLs, concurrency).
        client: Optional injected REST client (for tests); created otherwise.
        only_panel_ids: Optional per-region set of panel ids to fetch details
            for (incremental refresh).

    Returns:
        A crawl mapping keyed by region.
    """
    owns_client = client is None
    rest = client or PanelAppRestClient(config)
    try:
        crawled: dict[str, Any] = {}
        for region, attr in _REGION_URL_ATTR.items():
            base_url = getattr(config, attr)
            ids = None if only_panel_ids is None else only_panel_ids.get(region, set())
            crawled[region] = await crawl_region(rest, region, base_url, only_panel_ids=ids)
    finally:
        if owns_client:
            await rest.aclose()
    return crawled
