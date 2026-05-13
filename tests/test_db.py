"""Unit tests for the SQLite store — Phase 2.2.

All tests use an in-memory SQLite connection (`:memory:`) so they're fast,
isolated, and leave no filesystem artifacts.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from src import db


@pytest.fixture
def conn() -> sqlite3.Connection:
    """A fresh in-memory DB with the WYN schema applied."""
    c = db.connect(":memory:")
    db.init_schema(c)
    return c


# --- schema + seeding --------------------------------------------------------


def test_init_schema_is_idempotent(conn: sqlite3.Connection) -> None:
    """Re-initializing the schema on an existing DB must not error or wipe data."""
    db.seed_titles(conn, pd.DataFrame([{"title": "X", "year": 2020}]))
    db.init_schema(conn)  # second call
    assert len(db.all_titles(conn)) == 1


def test_seed_titles_inserts_rows(conn: sqlite3.Connection) -> None:
    df = pd.DataFrame(
        [{"title": "Parasite", "year": 2019}, {"title": "Cats", "year": 2019}]
    )
    db.seed_titles(conn, df)
    rows = db.all_titles(conn)
    assert [(r["title"], r["year"]) for r in rows] == [
        ("Parasite", 2019),
        ("Cats", 2019),
    ]


def test_seed_titles_is_idempotent(conn: sqlite3.Connection) -> None:
    """Re-seeding the same titles must not duplicate rows."""
    df = pd.DataFrame([{"title": "Parasite", "year": 2019}])
    db.seed_titles(conn, df)
    db.seed_titles(conn, df)
    assert len(db.all_titles(conn)) == 1


# --- OMDb cache --------------------------------------------------------------


def test_omdb_roundtrip_preserves_all_fields(conn: sqlite3.Connection) -> None:
    db.seed_titles(conn, pd.DataFrame([{"title": "Parasite", "year": 2019}]))
    title_id = db.all_titles(conn)[0]["id"]

    fetched = {
        "title": "Parasite",
        "year": 2019,
        "found": True,
        "metacritic": 96,
        "imdb_rating": 8.5,
        "imdb_votes": 1148210,
        "director": "Bong Joon Ho",
        "actors": "Song Kang-ho, Lee Sun-kyun",
        "genre": "Drama, Thriller",
        "runtime": "132 min",
        "rated": "R",
        "plot": "Greed and class discrimination...",
    }
    db.upsert_omdb_data(conn, title_id, fetched)
    cached = db.get_omdb_data(conn, title_id)
    assert cached is not None
    for key in (
        "title", "year", "found", "metacritic", "imdb_rating", "imdb_votes",
        "director", "actors", "genre", "runtime", "rated", "plot",
    ):
        assert cached[key] == fetched[key], f"mismatch on {key}"


def test_get_omdb_data_returns_none_when_missing(conn: sqlite3.Connection) -> None:
    db.seed_titles(conn, pd.DataFrame([{"title": "Parasite", "year": 2019}]))
    title_id = db.all_titles(conn)[0]["id"]
    assert db.get_omdb_data(conn, title_id) is None


def test_omdb_upsert_replaces_on_second_write(conn: sqlite3.Connection) -> None:
    """Re-running the pipeline must overwrite stale signals, not error out."""
    db.seed_titles(conn, pd.DataFrame([{"title": "Parasite", "year": 2019}]))
    tid = db.all_titles(conn)[0]["id"]

    db.upsert_omdb_data(conn, tid, {"found": True, "metacritic": 90})
    db.upsert_omdb_data(conn, tid, {"found": True, "metacritic": 96})

    assert db.get_omdb_data(conn, tid)["metacritic"] == 96


# --- Letterboxd cache --------------------------------------------------------


def test_letterboxd_roundtrip(conn: sqlite3.Connection) -> None:
    db.seed_titles(conn, pd.DataFrame([{"title": "Parasite", "year": 2019}]))
    tid = db.all_titles(conn)[0]["id"]

    fetched = {
        "title": "Parasite",
        "year": 2019,
        "letterboxd_found": True,
        "letterboxd_slug": "parasite-2019",
        "letterboxd_rating": 4.53,
    }
    db.upsert_letterboxd_data(conn, tid, fetched)
    cached = db.get_letterboxd_data(conn, tid)
    assert cached["letterboxd_found"] is True
    assert cached["letterboxd_slug"] == "parasite-2019"
    assert cached["letterboxd_rating"] == 4.53


# --- scores ------------------------------------------------------------------


def test_multiple_formula_versions_coexist(conn: sqlite3.Connection) -> None:
    """v0.1 and v0.2 scores for the same title must not collide."""
    db.seed_titles(conn, pd.DataFrame([{"title": "Parasite", "year": 2019}]))
    tid = db.all_titles(conn)[0]["id"]

    db.upsert_score(conn, tid, "v0.1", {"score": 92.65, "tier": "WORTH", "reason": "ok"})
    db.upsert_score(conn, tid, "v0.2", {"score": 94.12, "tier": "WORTH", "reason": "ok"})

    rows = list(conn.execute("SELECT formula_version, score FROM scores ORDER BY formula_version"))
    assert [(r["formula_version"], r["score"]) for r in rows] == [
        ("v0.1", 92.65),
        ("v0.2", 94.12),
    ]


def test_score_upsert_replaces_same_version(conn: sqlite3.Connection) -> None:
    """A second write under the same (title_id, formula_version) overwrites."""
    db.seed_titles(conn, pd.DataFrame([{"title": "Parasite", "year": 2019}]))
    tid = db.all_titles(conn)[0]["id"]

    db.upsert_score(conn, tid, "v0.2", {"score": 90.0, "tier": "WORTH", "reason": "ok"})
    db.upsert_score(conn, tid, "v0.2", {"score": 94.12, "tier": "WORTH", "reason": "ok"})

    rows = list(conn.execute("SELECT score FROM scores WHERE formula_version = 'v0.2'"))
    assert len(rows) == 1
    assert rows[0]["score"] == 94.12


# --- export ------------------------------------------------------------------


def test_export_orders_unscored_last(conn: sqlite3.Connection) -> None:
    """Export must put scored rows first (DESC by score), UNSCORED rows last."""
    df = pd.DataFrame(
        [
            {"title": "Top", "year": 2020},
            {"title": "Mid", "year": 2020},
            {"title": "Missing", "year": 2020},
        ]
    )
    db.seed_titles(conn, df)
    rows = db.all_titles(conn)
    by_title = {r["title"]: r["id"] for r in rows}

    db.upsert_score(conn, by_title["Top"], "v0.2", {"score": 92.0, "tier": "WORTH", "reason": "ok"})
    db.upsert_score(conn, by_title["Mid"], "v0.2", {"score": 60.0, "tier": "DECENT", "reason": "ok"})
    db.upsert_score(
        conn,
        by_title["Missing"],
        "v0.2",
        {"score": None, "tier": "UNSCORED", "reason": "missing: metacritic"},
    )

    out = db.export_scores_df(conn, "v0.2")
    assert list(out["title"]) == ["Top", "Mid", "Missing"]
    assert out.iloc[-1]["tier"] == "UNSCORED"


def test_export_bool_columns_are_python_bools(conn: sqlite3.Connection) -> None:
    """`found` and `letterboxd_found` must serialize to True/False in CSV, not 0/1."""
    db.seed_titles(conn, pd.DataFrame([{"title": "Parasite", "year": 2019}]))
    tid = db.all_titles(conn)[0]["id"]

    db.upsert_omdb_data(conn, tid, {"found": True, "metacritic": 96, "imdb_rating": 8.5})
    db.upsert_letterboxd_data(
        conn, tid, {"letterboxd_found": True, "letterboxd_slug": "parasite-2019", "letterboxd_rating": 4.53}
    )
    db.upsert_score(conn, tid, "v0.2", {"score": 94.12, "tier": "WORTH", "reason": "ok"})

    out = db.export_scores_df(conn, "v0.2")
    assert out["found"].dtype == bool
    assert out["letterboxd_found"].dtype == bool


def test_export_includes_titles_with_no_signal_data(conn: sqlite3.Connection) -> None:
    """A seed title with no fetched data must still appear in the export
    (LEFT JOIN), with NULL signals — useful for diagnosing 'why didn't this run'."""
    db.seed_titles(conn, pd.DataFrame([{"title": "Untouched", "year": 2020}]))

    out = db.export_scores_df(conn, "v0.2")
    assert len(out) == 1
    assert out.iloc[0]["title"] == "Untouched"
    assert pd.isna(out.iloc[0]["metacritic"])


# --- directors / pedigree (Phase 2.3) ---------------------------------------


def test_upsert_director_returns_id_and_is_idempotent(conn: sqlite3.Connection) -> None:
    """A director can be re-upserted (e.g. to refresh tmdb_person_id) without duplicating."""
    first_id = db.upsert_director(conn, "Bong Joon Ho", 21684)
    second_id = db.upsert_director(conn, "Bong Joon Ho", 21684)
    assert first_id == second_id

    rows = list(conn.execute("SELECT name, tmdb_person_id FROM directors"))
    assert len(rows) == 1
    assert rows[0]["tmdb_person_id"] == 21684


def test_upsert_director_handles_missing_tmdb_id(conn: sqlite3.Connection) -> None:
    """When TMDB doesn't find the director, store the row anyway with NULL id
    so we don't keep retrying the same lookup on every run."""
    director_id = db.upsert_director(conn, "Obscure Filmmaker", None)
    rows = list(conn.execute("SELECT name, tmdb_person_id FROM directors"))
    assert len(rows) == 1
    assert rows[0]["tmdb_person_id"] is None


def test_director_films_roundtrip(conn: sqlite3.Connection) -> None:
    director_id = db.upsert_director(conn, "Test Director", 1)
    db.insert_director_films(
        conn,
        director_id,
        [
            {"film_title": "Film A", "film_year": 2010, "metacritic": 80},
            {"film_title": "Film B", "film_year": 2015, "metacritic": None},
            {"film_title": "Film C", "film_year": 2020, "metacritic": 65},
        ],
    )
    films = db.get_director_films(conn, director_id)
    # Ordered newest first.
    assert [(f["film_title"], f["film_year"]) for f in films] == [
        ("Film C", 2020),
        ("Film B", 2015),
        ("Film A", 2010),
    ]


def test_pedigree_roundtrip(conn: sqlite3.Connection) -> None:
    db.seed_titles(conn, pd.DataFrame([{"title": "Parasite", "year": 2019}]))
    title_id = db.all_titles(conn)[0]["id"]

    db.upsert_pedigree_data(
        conn,
        title_id,
        {"director_name": "Bong Joon Ho", "pedigree_score": 78.5, "prior_film_count": 5},
    )
    cached = db.get_pedigree_data(conn, title_id)
    assert cached == {
        "director_name": "Bong Joon Ho",
        "pedigree_score": 78.5,
        "prior_film_count": 5,
    }


def test_pedigree_roundtrip_with_none_score(conn: sqlite3.Connection) -> None:
    """First-time directors store pedigree_score=NULL with film_count below threshold."""
    db.seed_titles(conn, pd.DataFrame([{"title": "Debut", "year": 2020}]))
    title_id = db.all_titles(conn)[0]["id"]

    db.upsert_pedigree_data(
        conn,
        title_id,
        {"director_name": "First Timer", "pedigree_score": None, "prior_film_count": 0},
    )
    cached = db.get_pedigree_data(conn, title_id)
    assert cached["pedigree_score"] is None
    assert cached["prior_film_count"] == 0


def test_export_includes_pedigree_columns(conn: sqlite3.Connection) -> None:
    db.seed_titles(conn, pd.DataFrame([{"title": "Parasite", "year": 2019}]))
    title_id = db.all_titles(conn)[0]["id"]

    db.upsert_pedigree_data(
        conn,
        title_id,
        {"director_name": "Bong Joon Ho", "pedigree_score": 78.5, "prior_film_count": 5},
    )
    out = db.export_scores_df(conn, "v0.3")
    assert "pedigree_score" in out.columns
    assert "pedigree_director" in out.columns
    assert "pedigree_prior_film_count" in out.columns
    assert out.iloc[0]["pedigree_score"] == 78.5
