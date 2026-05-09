"""SQLite store for WYN — Phase 2.2.

Replaces the Phase 1 / 2.1 CSV-only flow with a relational store. The store
acts as a persistent cache for fetched signal data (OMDb + Letterboxd) and a
durable home for computed scores across multiple formula versions, so we can
query questions like "which films changed tier between v0.1 and v0.2?" without
re-running the pipeline.

Schema (all tables idempotent via CREATE TABLE IF NOT EXISTS):

    titles             — seed list (id, title, year). UNIQUE(title, year).
    omdb_data          — one row per title (PK = title_id). All OMDb fields.
    letterboxd_data    — one row per title (PK = title_id). slug + rating.
    scores             — one row per (title_id, formula_version). PK both.

The on-disk Letterboxd HTML cache (cache/letterboxd/*.html) is intentionally
kept alongside the DB: it caches *raw input* for re-parsing if the parser
improves, while the DB caches *structured output*. Different layers.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "wyn.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    year INTEGER NOT NULL,
    UNIQUE(title, year)
);

CREATE TABLE IF NOT EXISTS omdb_data (
    title_id INTEGER PRIMARY KEY REFERENCES titles(id),
    found INTEGER NOT NULL,
    metacritic INTEGER,
    imdb_rating REAL,
    imdb_votes INTEGER,
    director TEXT,
    actors TEXT,
    genre TEXT,
    runtime TEXT,
    rated TEXT,
    plot TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS letterboxd_data (
    title_id INTEGER PRIMARY KEY REFERENCES titles(id),
    found INTEGER NOT NULL,
    slug TEXT,
    rating REAL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
    title_id INTEGER NOT NULL REFERENCES titles(id),
    formula_version TEXT NOT NULL,
    score REAL,
    tier TEXT NOT NULL,
    reason TEXT,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (title_id, formula_version)
);
"""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with row_factory + foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create all WYN tables. Safe to call on every run."""
    conn.executescript(_SCHEMA)
    conn.commit()


# --- titles ------------------------------------------------------------------


def seed_titles(conn: sqlite3.Connection, titles_df: pd.DataFrame) -> None:
    """Insert seed titles into the titles table.

    Idempotent: INSERT OR IGNORE on UNIQUE(title, year). Safe to re-run.
    """
    rows = [(str(r["title"]), int(r["year"])) for _, r in titles_df.iterrows()]
    conn.executemany(
        "INSERT OR IGNORE INTO titles (title, year) VALUES (?, ?)",
        rows,
    )
    conn.commit()


def all_titles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every seed title as Row(id, title, year), ordered by insert id."""
    return list(conn.execute("SELECT id, title, year FROM titles ORDER BY id"))


# --- OMDb --------------------------------------------------------------------


def get_omdb_data(conn: sqlite3.Connection, title_id: int) -> dict[str, Any] | None:
    """Return the cached OMDb dict for a title, or None if not yet fetched.

    Returned shape matches `fetch_omdb()` exactly so callers can use the two
    interchangeably.
    """
    row = conn.execute(
        """
        SELECT t.title, t.year, o.found, o.metacritic, o.imdb_rating,
               o.imdb_votes, o.director, o.actors, o.genre, o.runtime,
               o.rated, o.plot
        FROM titles t JOIN omdb_data o ON o.title_id = t.id
        WHERE t.id = ?
        """,
        (title_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "title": row["title"],
        "year": row["year"],
        "found": bool(row["found"]),
        "metacritic": row["metacritic"],
        "imdb_rating": row["imdb_rating"],
        "imdb_votes": row["imdb_votes"] if row["imdb_votes"] is not None else 0,
        "director": row["director"],
        "actors": row["actors"],
        "genre": row["genre"],
        "runtime": row["runtime"],
        "rated": row["rated"],
        "plot": row["plot"],
    }


def upsert_omdb_data(
    conn: sqlite3.Connection, title_id: int, data: dict[str, Any]
) -> None:
    """Insert or replace omdb_data row from a `fetch_omdb()` return dict."""
    conn.execute(
        """
        INSERT OR REPLACE INTO omdb_data
            (title_id, found, metacritic, imdb_rating, imdb_votes,
             director, actors, genre, runtime, rated, plot, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title_id,
            int(bool(data.get("found"))),
            data.get("metacritic"),
            data.get("imdb_rating"),
            data.get("imdb_votes") or 0,
            data.get("director"),
            data.get("actors"),
            data.get("genre"),
            data.get("runtime"),
            data.get("rated"),
            data.get("plot"),
            _now_utc(),
        ),
    )
    conn.commit()


# --- Letterboxd --------------------------------------------------------------


def get_letterboxd_data(
    conn: sqlite3.Connection, title_id: int
) -> dict[str, Any] | None:
    """Return cached Letterboxd dict for a title, or None if not yet fetched."""
    row = conn.execute(
        """
        SELECT t.title, t.year, l.found, l.slug, l.rating
        FROM titles t JOIN letterboxd_data l ON l.title_id = t.id
        WHERE t.id = ?
        """,
        (title_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "title": row["title"],
        "year": row["year"],
        "letterboxd_found": bool(row["found"]),
        "letterboxd_slug": row["slug"],
        "letterboxd_rating": row["rating"],
    }


def upsert_letterboxd_data(
    conn: sqlite3.Connection, title_id: int, data: dict[str, Any]
) -> None:
    """Insert or replace letterboxd_data row from a `fetch_letterboxd()` dict."""
    conn.execute(
        """
        INSERT OR REPLACE INTO letterboxd_data
            (title_id, found, slug, rating, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            title_id,
            int(bool(data.get("letterboxd_found"))),
            data.get("letterboxd_slug"),
            data.get("letterboxd_rating"),
            _now_utc(),
        ),
    )
    conn.commit()


# --- scores ------------------------------------------------------------------


def upsert_score(
    conn: sqlite3.Connection,
    title_id: int,
    formula_version: str,
    score_data: dict[str, Any],
) -> None:
    """Insert or replace a score row, keyed on (title_id, formula_version)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO scores
            (title_id, formula_version, score, tier, reason, computed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            title_id,
            formula_version,
            score_data.get("score"),
            score_data["tier"],
            score_data.get("reason"),
            _now_utc(),
        ),
    )
    conn.commit()


def export_scores_df(
    conn: sqlite3.Connection, formula_version: str
) -> pd.DataFrame:
    """Return the final scored DataFrame for a given formula version.

    Shape matches the Phase 2.1 CSV (so the on-disk artifact is unchanged):
    columns include OMDb signal fields, Letterboxd signal fields, and final
    score/tier/reason. UNSCORED rows are placed last; scored rows are sorted
    by score descending.
    """
    df = pd.read_sql_query(
        """
        SELECT
            t.title, t.year,
            COALESCE(o.found, 0) AS found,
            o.metacritic, o.imdb_rating, o.imdb_votes,
            o.director, o.actors, o.genre, o.runtime, o.rated, o.plot,
            COALESCE(l.found, 0) AS letterboxd_found,
            l.slug AS letterboxd_slug,
            l.rating AS letterboxd_rating,
            s.score, s.tier, s.reason
        FROM titles t
        LEFT JOIN omdb_data o ON o.title_id = t.id
        LEFT JOIN letterboxd_data l ON l.title_id = t.id
        LEFT JOIN scores s ON s.title_id = t.id AND s.formula_version = ?
        ORDER BY (s.tier = 'UNSCORED'), s.score DESC
        """,
        conn,
        params=(formula_version,),
    )
    df["found"] = df["found"].astype(bool)
    df["letterboxd_found"] = df["letterboxd_found"].astype(bool)
    return df
