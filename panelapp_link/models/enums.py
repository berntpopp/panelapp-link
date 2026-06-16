"""Enums and literals shared across PanelApp-Link layers."""

from __future__ import annotations

from typing import Literal

# Response verbosity. Tools default to "compact"; widen only when needed.
#   minimal  - ids + name + counts only
#   compact  - default; key fields (panel summary, entity symbol/confidence/moi)
#   standard - adds phenotypes, penetrance, signed-off detail, region coords
#   full     - adds evidence, publications, omim, tags, raw `extra`
ResponseMode = Literal["minimal", "compact", "standard", "full"]

# Data region. "both" queries Genomics England UK and PanelApp Australia.
Region = Literal["uk", "australia", "both"]

# Entity kind within a panel. "all" returns genes, regions, and STRs together.
EntityType = Literal["gene", "region", "str", "all"]

# Confidence rating of a panel entity (GE traffic-light system).
ConfidenceLabel = Literal["green", "amber", "red"]

RESPONSE_MODES: tuple[ResponseMode, ...] = ("minimal", "compact", "standard", "full")
REGIONS: tuple[Region, ...] = ("uk", "australia", "both")
ENTITY_TYPES: tuple[EntityType, ...] = ("gene", "region", "str", "all")
CONFIDENCE_LABELS: tuple[ConfidenceLabel, ...] = ("green", "amber", "red")
