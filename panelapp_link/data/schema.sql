-- PanelApp-Link SQLite schema (schema_version = 1).
-- Built atomically by panelapp_link.ingest.builder from crawled PanelApp
-- payloads (Genomics England UK + PanelApp Australia). Every table is dropped
-- and rebuilt on each run.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = OFF;

-- One row per panel per region. Signed-off version/date merged from
-- /panels/signedoff/ at build time.
CREATE TABLE panel (
    region                  TEXT,
    panel_id                INTEGER,
    hash_id                 TEXT,
    name                    TEXT NOT NULL,
    name_upper              TEXT NOT NULL,
    version                 TEXT,
    version_created         TEXT,
    disease_group           TEXT,
    disease_sub_group       TEXT,
    status                  TEXT,
    description             TEXT,                    -- AU only, else NULL
    relevant_disorders_json TEXT DEFAULT '[]',
    types_json              TEXT DEFAULT '[]',
    number_of_genes         INTEGER DEFAULT 0,
    number_of_regions       INTEGER DEFAULT 0,
    number_of_strs          INTEGER DEFAULT 0,
    signed_off_version      TEXT,
    signed_off_date         TEXT,
    PRIMARY KEY (region, panel_id)
);
CREATE INDEX idx_panel_name_upper ON panel(name_upper);
CREATE INDEX idx_panel_disease_group ON panel(disease_group);

-- One row per entity (gene | region | str) within a panel. JSON columns hold
-- pre-computed lists/blobs so the repository avoids re-aggregation.
CREATE TABLE entity (
    region              TEXT,
    panel_id            INTEGER,
    entity_type         TEXT,                        -- gene | region | str
    entity_name         TEXT,
    gene_symbol         TEXT,
    gene_symbol_upper   TEXT,
    hgnc_id             TEXT,
    confidence_level    TEXT,
    confidence_label    TEXT,                        -- green | amber | red
    confidence_rank     INTEGER,                     -- green=3 amber=2 red=1
    mode_of_inheritance TEXT,
    penetrance          TEXT,
    phenotypes_json     TEXT DEFAULT '[]',
    evidence_json       TEXT DEFAULT '[]',
    publications_json   TEXT DEFAULT '[]',
    omim_json           TEXT DEFAULT '[]',
    tags_json           TEXT DEFAULT '[]',
    extra_json          TEXT DEFAULT '{}',           -- region/str-specific fields
    panel_name          TEXT,                        -- denormalized for gene->panels
    PRIMARY KEY (region, panel_id, entity_type, entity_name)
);
CREATE INDEX idx_entity_panel ON entity(region, panel_id);
CREATE INDEX idx_entity_gene_symbol_upper ON entity(gene_symbol_upper);
CREATE INDEX idx_entity_hgnc_id ON entity(hgnc_id);

-- Ingest-time roll-up of each gene across panels and regions.
CREATE TABLE gene (
    gene_symbol_upper   TEXT PRIMARY KEY,
    gene_symbol         TEXT,
    hgnc_id             TEXT,
    panel_count         INTEGER,
    regions_json        TEXT DEFAULT '[]',
    max_confidence_label TEXT,
    max_confidence_rank INTEGER
);
CREATE INDEX idx_gene_hgnc_id ON gene(hgnc_id);

-- FTS5 over panel name + relevant disorders + disease group for search_panels.
CREATE VIRTUAL TABLE panel_fts USING fts5(
    region UNINDEXED,
    panel_id UNINDEXED,
    name,
    relevant_disorders,
    disease_group,
    tokenize = 'unicode61'
);

-- Single-row build provenance.
CREATE TABLE meta (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version      TEXT NOT NULL,
    source_uk_url       TEXT NOT NULL,
    source_au_url       TEXT NOT NULL,
    uk_panel_count      INTEGER NOT NULL DEFAULT 0,
    au_panel_count      INTEGER NOT NULL DEFAULT 0,
    entity_count        INTEGER NOT NULL DEFAULT 0,
    gene_count          INTEGER NOT NULL DEFAULT 0,
    build_utc           TEXT,
    build_duration_s    REAL,
    panel_versions_json TEXT NOT NULL DEFAULT '{}'   -- {region:{panel_id:version}}
);
