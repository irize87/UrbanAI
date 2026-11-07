from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.config import DATA_DIR, get_settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer TEXT NOT NULL,
    source TEXT NOT NULL,
    feature_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    message TEXT,
    synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS buildings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ext_id TEXT,
    use_cat TEXT,
    use_raw TEXT,
    year INTEGER,
    floors INTEGER,
    height_m REAL,
    gfa_m2 REAL,
    geom_wkt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'fixture',
    sgg_cd TEXT
);

CREATE TABLE IF NOT EXISTS zoning (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_cat TEXT,
    zone_name TEXT,
    geom_wkt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'fixture'
);

CREATE TABLE IF NOT EXISTS green (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,
    name TEXT,
    geom_wkt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'fixture'
);

CREATE TABLE IF NOT EXISTS roads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    road_class TEXT,
    name TEXT,
    geom_wkt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'fixture'
);

CREATE TABLE IF NOT EXISTS transit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,
    name TEXT,
    line_name TEXT,
    geom_wkt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'fixture'
);

CREATE TABLE IF NOT EXISTS parcels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pnu TEXT,
    jimok TEXT,
    geom_wkt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'fixture'
);

CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    geom_wkt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'fixture'
);

CREATE TABLE IF NOT EXISTS district (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,
    name TEXT,
    geom_wkt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'fixture'
);

CREATE TABLE IF NOT EXISTS planned (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,
    name TEXT,
    geom_wkt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'fixture'
);

CREATE TABLE IF NOT EXISTS sgg_bounds (
    sgg_cd TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    geom_wkt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS street_trees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cgg TEXT,
    rte TEXT,
    trspc TEXT,
    road_nm_addr TEXT,
    lotno_addr TEXT,
    lng REAL NOT NULL,
    lat REAL NOT NULL,
    geom_wkt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'seoul:streetTreeInfo',
    sgg_cd TEXT
);

CREATE INDEX IF NOT EXISTS idx_buildings_use ON buildings(use_cat);
CREATE INDEX IF NOT EXISTS idx_buildings_year ON buildings(year);
CREATE INDEX IF NOT EXISTS idx_street_trees_sgg ON street_trees(sgg_cd);
CREATE INDEX IF NOT EXISTS idx_street_trees_lnglat ON street_trees(lng, lat);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    settings = get_settings()
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-16000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_db(db_path: Path | None = None) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = db_path or get_settings().db_path
    with connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        from app.db.sgg import ensure_sgg_columns

        ensure_sgg_columns(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            ("schema_version", "3"),
        )
        conn.commit()
    return path
