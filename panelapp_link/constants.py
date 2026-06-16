"""Domain constants for PanelApp-Link.

Confidence maps, ranks, region labels, and citation strings live here so the
ingest builder, repository, services, and MCP discovery surface all derive
confidence labels/ranks from a single source of truth.
"""

from __future__ import annotations

# SQLite schema version (matches data/schema.sql). String for `meta` storage.
SCHEMA_VERSION = "1"

# PanelApp confidence_level (int or str, always cast to str) -> traffic-light
# label. GE uses 0-4; 3 and 4 are "green" (diagnostic-grade), 2 is "amber"
# (borderline), 0 and 1 are "red" (low evidence). Unknown values fall back to
# "red" via confidence_label().
CONFIDENCE_TO_LABEL: dict[str, str] = {
    "4": "green",
    "3": "green",
    "2": "amber",
    "1": "red",
    "0": "red",
}

# Ordinal rank for confidence labels, used for filter ordering (min_confidence)
# and roll-up of the strongest label across panels/regions.
CONFIDENCE_RANK: dict[str, int] = {
    "green": 3,
    "amber": 2,
    "red": 1,
}

# Human-readable source labels per region key.
REGION_LABELS: dict[str, str] = {
    "uk": "Genomics England PanelApp",
    "australia": "PanelApp Australia",
}

# Recommended citations (spec §8, verbatim).
RECOMMENDED_CITATION_UK = (
    "Martin AR, Williams E, Foulger RE, et al. PanelApp crowdsources expert "
    "knowledge to establish consensus diagnostic gene panels. "
    "Nat Genet. 2019;51:1560-1565."
)
RECOMMENDED_CITATION_AU = (
    "Stark Z, et al. Australian Genomics PanelApp: a community-driven resource "
    "for curated gene panels (PanelApp Australia)."
)
CITATION_SHORT = "PanelApp (Genomics England & Australia)"

# Licensing / safety note advertised via the license resource.
DATA_LICENSE = (
    "PanelApp content is provided by Genomics England and PanelApp Australia "
    "under their respective terms of use. This server mirrors that content for "
    "research use only; it is not clinical decision support."
)


def confidence_label(level: str) -> str:
    """Map a PanelApp confidence level to a traffic-light label.

    Args:
        level: Confidence level as it arrives from the API (int or str); it is
            cast to ``str`` before lookup.

    Returns:
        ``"green"``, ``"amber"``, or ``"red"``; unknown levels default to
        ``"red"``.
    """
    return CONFIDENCE_TO_LABEL.get(str(level), "red")


def confidence_rank_for_label(label: str) -> int:
    """Return the ordinal rank for a confidence label (green=3, amber=2, red=1).

    Args:
        label: A confidence label.

    Returns:
        The rank for ordering/filtering; unknown labels default to ``1`` (red).
    """
    return CONFIDENCE_RANK.get(label, 1)
