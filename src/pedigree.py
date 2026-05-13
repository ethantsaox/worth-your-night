"""Director pedigree feature for WYN Phase 2.3.

Computes a single number per film: the average Metacritic score of the
director's last 5 prior films (released before the current title's year),
excluding the current title itself. Returns `None` (UNSCORED-eligible) when
fewer than `MIN_PRIOR_FILMS_WITH_METACRITIC` of those prior films have a
Metacritic value — first-time directors and obscure-filmography directors
get an honest "no signal" rather than a fabricated neutral default.

Data flow (all DB-cached so this is essentially free on re-runs):

    1. Pull `director` from the cached omdb_data row. Use the FIRST name
       only when OMDb returns "X, Y" (multi-director credit).
    2. If the director isn't in the `directors` table:
         a. Search TMDB for their person_id.
         b. Fetch their filmography from TMDB (Director credits only).
         c. For each film not already cached in `director_films`, look up
            its Metacritic via OMDb and store it.
    3. Pull cached prior films, filter to those with Metacritic values,
       take the most recent 5, and average.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

import httpx

from src import db
from src.fetch import fetch_omdb
from src.tmdb import find_person_id, get_director_filmography

LAST_N_FILMS = 5
MIN_PRIOR_FILMS_WITH_METACRITIC = 3
SLEEP_BETWEEN_OMDB_FETCHES_SECONDS = 0.2


def _primary_director_name(raw: str | None) -> str | None:
    """Pick the first director name when OMDb returns 'X, Y' for co-directors.

    Returns None when raw is missing, "N/A", or empty. The first credit is
    the canonical author signal; co-directors typically share filmographies,
    so picking the first is rarely lossy in practice.
    """
    if not raw or raw == "N/A":
        return None
    first = raw.split(",")[0].strip()
    return first or None


def _hydrate_director_films(
    conn: sqlite3.Connection,
    director_id: int,
    director_name: str,
    tmdb_person_id: int | None,
    tmdb_client: httpx.Client | None = None,
) -> None:
    """Populate `director_films` for a director: TMDB filmography + OMDb Metacritic.

    No-op when `tmdb_person_id` is None (TMDB didn't find the director) or
    when `director_films` already has entries for this director (we trust
    the cache; users can clear `director_films` rows to force a refresh).
    """
    if tmdb_person_id is None:
        return
    if db.get_director_films(conn, director_id):
        return

    print(f"    fetching filmography for {director_name}")
    films = get_director_filmography(tmdb_person_id, client=tmdb_client)
    if not films:
        return

    enriched: list[dict[str, Any]] = []
    for film in films:
        title = film["film_title"]
        year = film.get("film_year")
        if year is None:
            enriched.append({**film, "metacritic": None})
            continue
        omdb_data = fetch_omdb(title, year)
        time.sleep(SLEEP_BETWEEN_OMDB_FETCHES_SECONDS)
        enriched.append({**film, "metacritic": omdb_data.get("metacritic")})

    db.insert_director_films(conn, director_id, enriched)


def _compute_pedigree_score(
    prior_films: list[sqlite3.Row], current_year: int
) -> tuple[float | None, int]:
    """Average Metacritic of the most recent N prior films with a Metacritic.

    Returns `(score, eligible_count)`. Score is None when the eligible count
    is below MIN_PRIOR_FILMS_WITH_METACRITIC.
    """
    eligible = [
        row["metacritic"]
        for row in prior_films
        if row["film_year"] is not None
        and row["film_year"] < current_year
        and row["metacritic"] is not None
    ][:LAST_N_FILMS]

    if len(eligible) < MIN_PRIOR_FILMS_WITH_METACRITIC:
        return None, len(eligible)
    return round(sum(eligible) / len(eligible), 2), len(eligible)


def compute_pedigree(
    conn: sqlite3.Connection,
    title_id: int,
    omdb_data: dict[str, Any],
    current_year: int,
    tmdb_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Compute (or fetch from cache) the pedigree row for a single title.

    Side-effects: may insert/update rows in `directors`, `director_films`,
    and `pedigree_data`. Idempotent — repeated calls for the same title are
    cheap and produce the same result unless underlying TMDB / OMDb data
    has been refreshed.

    Returns a dict with keys: director_name, pedigree_score, prior_film_count.
    """
    cached = db.get_pedigree_data(conn, title_id)
    if cached is not None:
        return cached

    director_name = _primary_director_name(omdb_data.get("director"))
    if director_name is None:
        result = {
            "director_name": None,
            "pedigree_score": None,
            "prior_film_count": 0,
        }
        db.upsert_pedigree_data(conn, title_id, result)
        return result

    director_id = db.get_director_id(conn, director_name)
    if director_id is None:
        tmdb_id = find_person_id(director_name, client=tmdb_client)
        director_id = db.upsert_director(conn, director_name, tmdb_id)
        _hydrate_director_films(
            conn, director_id, director_name, tmdb_id, tmdb_client=tmdb_client
        )

    prior_films = db.get_director_films(conn, director_id)
    score, count = _compute_pedigree_score(prior_films, current_year)

    result = {
        "director_name": director_name,
        "pedigree_score": score,
        "prior_film_count": count,
    }
    db.upsert_pedigree_data(conn, title_id, result)
    return result
