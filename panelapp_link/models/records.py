"""Pydantic record models mapping PanelApp database rows to typed objects.

These are the shared contract between the repository (which builds them from
SQLite rows), the services (which aggregate/shape them), and the MCP tools
(which serialize them). Field names are snake_case.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PanelSummary(BaseModel):
    """A PanelApp panel with headline metadata and entity counts."""

    panel_id: int = Field(description="PanelApp numeric panel id (unique per region)")
    name: str = Field(description="Panel name")
    version: str | None = Field(default=None, description="Latest panel version, e.g. '2.5'")
    region: str = Field(description="Source region: 'uk' or 'australia'")
    disease_group: str | None = Field(default=None, description="Top-level disease grouping")
    disease_sub_group: str | None = Field(default=None, description="Disease sub-grouping")
    status: str | None = Field(default=None, description="Panel status, e.g. 'public'")
    relevant_disorders: list[str] = Field(
        default_factory=list, description="Related disorder names/aliases for the panel"
    )
    n_genes: int = Field(default=0, description="Number of gene entities in the panel")
    n_regions: int = Field(default=0, description="Number of region/CNV entities in the panel")
    n_strs: int = Field(default=0, description="Number of STR entities in the panel")
    signed_off_version: str | None = Field(
        default=None, description="Signed-off panel version, if one exists"
    )
    signed_off_date: str | None = Field(
        default=None, description="Date the panel version was signed off (ISO date string)"
    )


class PanelDetail(PanelSummary):
    """A panel summary plus an entity-type count breakdown.

    Entities themselves are attached by the calling tool (via get_panel_genes),
    not embedded here.
    """

    hash_id: str | None = Field(default=None, description="PanelApp opaque hash id for the panel")
    version_created: str | None = Field(
        default=None, description="Timestamp the latest version was created"
    )
    description: str | None = Field(
        default=None, description="Panel description (PanelApp Australia only; else None)"
    )
    types: list[dict[str, Any]] = Field(
        default_factory=list, description="Panel type tags as returned by the API"
    )
    entity_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Count of entities by type, e.g. {'gene': 120, 'region': 3, 'str': 1}",
    )


class Entity(BaseModel):
    """A single panel entity (gene, region/CNV, or STR).

    A single model covers all three entity types; type-specific fields (region
    coordinates, STR repeats, etc.) are carried in ``extra``.
    """

    region: str = Field(description="Source region: 'uk' or 'australia'")
    panel_id: int = Field(description="PanelApp numeric panel id the entity belongs to")
    entity_type: str = Field(description="Entity kind: 'gene', 'region', or 'str'")
    entity_name: str = Field(description="PanelApp entity name (gene symbol / region / STR name)")
    gene_symbol: str | None = Field(default=None, description="Associated gene symbol, if any")
    hgnc_id: str | None = Field(default=None, description="HGNC id of the associated gene, if any")
    confidence_level: str | None = Field(
        default=None, description="Raw PanelApp confidence level (cast to str), e.g. '3'"
    )
    confidence_label: str | None = Field(
        default=None, description="Traffic-light label: 'green', 'amber', or 'red'"
    )
    mode_of_inheritance: str | None = Field(
        default=None, description="Mode of inheritance string from PanelApp"
    )
    penetrance: str | None = Field(default=None, description="Penetrance string, if recorded")
    phenotypes: list[str] = Field(default_factory=list, description="Associated phenotype strings")
    evidence: list[str] = Field(default_factory=list, description="Evidence strings/levels")
    publications: list[str] = Field(default_factory=list, description="Supporting publications")
    omim: list[str] = Field(default_factory=list, description="Associated OMIM ids")
    tags: list[str] = Field(default_factory=list, description="Free-text tags on the entity")
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific fields (region coordinates, STR repeats, etc.)",
    )


class GenePanelHit(BaseModel):
    """One panel on which a gene appears, with that gene's confidence on it."""

    region: str = Field(description="Source region: 'uk' or 'australia'")
    panel_id: int = Field(description="PanelApp numeric panel id")
    panel_name: str = Field(description="Name of the panel the gene appears on")
    version: str | None = Field(default=None, description="Panel version")
    confidence_label: str | None = Field(
        default=None, description="Traffic-light label of the gene on this panel"
    )
    confidence_level: str | None = Field(
        default=None, description="Raw confidence level (cast to str) on this panel"
    )
    mode_of_inheritance: str | None = Field(
        default=None, description="Mode of inheritance for the gene on this panel"
    )


class GeneSummary(BaseModel):
    """A gene rolled up across panels and regions (from the gene table)."""

    gene_symbol: str = Field(description="HGNC-approved gene symbol")
    hgnc_id: str | None = Field(default=None, description="HGNC id, e.g. 'HGNC:1100'")
    panel_count: int = Field(default=0, description="Number of panels the gene appears on")
    regions: list[str] = Field(
        default_factory=list, description="Regions the gene appears in ('uk', 'australia')"
    )
    max_confidence_label: str | None = Field(
        default=None, description="Strongest confidence label across all panels/regions"
    )


class BuildMeta(BaseModel):
    """Provenance for the built SQLite database (from the meta table)."""

    schema_version: str
    source_uk_url: str
    source_au_url: str
    uk_panel_count: int = 0
    au_panel_count: int = 0
    entity_count: int = 0
    gene_count: int = 0
    build_utc: str | None = None
    build_duration_s: float | None = None
