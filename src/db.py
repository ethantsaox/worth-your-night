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

    -- Phase 2.3 (pedigree) additions:
    directors          — one row per unique director name. Caches the TMDB
                         person-id lookup so we don't re-search every run.
    director_films     — one row per (director_id, film_title, film_year).
                         Each entry caches the film's Metacritic score from
                         OMDb so a director's full filmography is fetched
                         once, scored many times.
    pedigree_data      — one row per title_id. Stores the computed pedigree
                         score (avg Metacritic of director's last 5 prior
                         films) and the prior-film count, for explainability.

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

CREATE TABLE IF NOT EXISTS directors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    tmdb_person_id INTEGER,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS director_films (
    director_id INTEGER NOT NULL REFERENCES directors(id),
    film_title TEXT NOT NULL,
    film_year INTEGER,
    metacritic INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (director_id, film_title, film_year)
);

CREATE TABLE IF NOT EXISTS pedigree_data (
    title_id INTEGER PRIMARY KEY REFERENCES titles(id),
    director_name TEXT,
    pedigree_score REAL,
    prior_film_count INTEGER NOT NULL,
    computed_at TEXT NOT NULL
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

    Columns include OMDb signals, Letterboxd signals, pedigree fields (since
    Phase 2.3), and final score/tier/reason. UNSCORED rows are placed last;
    scored rows are sorted by score descending.
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
            p.director_name AS pedigree_director,
            p.pedigree_score,
            p.prior_film_count AS pedigree_prior_film_count,
            s.score, s.tier, s.reason
        FROM titles t
        LEFT JOIN omdb_data o ON o.title_id = t.id
        LEFT JOIN letterboxd_data l ON l.title_id = t.id
        LEFT JOIN pedigree_data p ON p.title_id = t.id
        LEFT JOIN scores s ON s.title_id = t.id AND s.formula_version = ?
        ORDER BY (s.tier = 'UNSCORED'), s.score DESC
        """,
        conn,
        params=(formula_version,),
    )
    df["found"] = df["found"].astype(bool)
    df["letterboxd_found"] = df["letterboxd_found"].astype(bool)
    return df


# --- directors / pedigree (Phase 2.3) ----------------------------------------


def get_director_id(conn: sqlite3.Connection, name: str) -> int | None:
    """Return the directors row id for a given name, or None if not yet stored."""
    row = conn.execute(
        "SELECT id FROM directors WHERE name = ?", (name,)
    ).fetchone()
    return row["id"] if row else None


def upsert_director(
    conn: sqlite3.Connection, name: str, tmdb_person_id: int | None
) -> int:
    """Idempotently store (or update) a director and return their row id.

    `tmdb_person_id` may be None when TMDB returned no match — the row still
    gets inserted so we don't keep re-searching the same name.
    """
    conn.execute(
        """
        INSERT INTO directors (name, tmdb_person_id, fetched_at)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            tmdb_person_id = excluded.tmdb_person_id,
            fetched_at = excluded.fetched_at
        """,
        (name, tmdb_person_id, _now_utc()),
    )
    conn.commit()
    director_id = get_director_id(conn, name)
    assert director_id is not None
    return director_id


def insert_director_films(
    conn: sqlite3.Connection,
    director_id: int,
    films: list[dict[str, Any]],
) -> None:
    """Bulk-insert filmography rows. INSERT OR REPLACE so re-fetches refresh
    Metacritic values without erroring on the composite primary key."""
    rows = [
        (
            director_id,
            f["film_title"],
            f.get("film_year"),
            f.get("metacritic"),
            _now_utc(),
        )
        for f in films
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO director_films
            (director_id, film_title, film_year, metacritic, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def get_director_films(
    conn: sqlite3.Connection, director_id: int
) -> list[sqlite3.Row]:
    """Return all cached films for a director, newest first."""
    return list(
        conn.execute(
            """
            SELECT film_title, film_year, metacritic
            FROM director_films
            WHERE director_id = ?
            ORDER BY film_year DESC
            """,
            (director_id,),
        )
    )


def get_pedigree_data(
    conn: sqlite3.Connection, title_id: int
) -> dict[str, Any] | None:
    """Return the cached pedigree row for a title, or None."""
    row = conn.execute(
        """
        SELECT director_name, pedigree_score, prior_film_count
        FROM pedigree_data
        WHERE title_id = ?
        """,
        (title_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "director_name": row["director_name"],
        "pedigree_score": row["pedigree_score"],
        "prior_film_count": row["prior_film_count"],
    }


def upsert_pedigree_data(
    conn: sqlite3.Connection,
    title_id: int,
    data: dict[str, Any],
) -> None:
    """Insert or replace a pedigree_data row."""
    conn.execute(
        """
        INSERT OR REPLACE INTO pedigree_data
            (title_id, director_name, pedigree_score, prior_film_count, computed_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            title_id,
            data.get("director_name"),
            data.get("pedigree_score"),
            int(data.get("prior_film_count", 0)),
            _now_utc(),
        ),
    )
    conn.commit()
