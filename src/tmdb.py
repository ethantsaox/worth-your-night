"""TMDB API client for WYN Phase 2.3.

We use TMDB for *one specific job*: looking up a director's filmography. The
critic / audience signals still come from OMDb + Letterboxd; TMDB just answers
"what other films has this director made, and when?". Each entry in the
returned filmography is later cross-referenced against OMDb to get a
Metacritic score for the pedigree calculation.

API basics:
    GET /3/search/person?query=NAME    -> {"results": [{id, name, ...}, ...]}
    GET /3/person/{id}/movie_credits    -> {"crew": [{title, release_date, job, ...}]}

The module exposes two pure-ish functions: `find_person_id` and
`get_director_filmography`. Both swallow network/parse errors and return
empty/None on failure so a single bad lookup never crashes the batch run.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

TMDB_BASE_URL = "https://api.themoviedb.org/3"
REQUEST_TIMEOUT_SECONDS = 10.0


def _api_key() -> str:
    key = os.getenv("TMDB_API_KEY")
    if not key:
        raise RuntimeError(
            "TMDB_API_KEY is not set. Add it to .env "
            "(get a free key at https://www.themoviedb.org/settings/api)."
        )
    return key


def _parse_year(release_date: str | None) -> int | None:
    """Extract a 4-digit year from a TMDB release_date like '2019-05-30'."""
    if not release_date or len(release_date) < 4:
        return None
    try:
        return int(release_date[:4])
    except ValueError:
        return None


def find_person_id(
    name: str, client: httpx.Client | None = None
) -> int | None:
    """Search TMDB for a person by name and return the top-ranked match's id.

    TMDB's /search/person sorts results by `popularity` — for well-known
    directors the first hit is overwhelmingly the right one. Ambiguity for
    common names is a known limitation; we accept it for v0.3 and document.

    Returns None on no match, network error, or parse error.
    """
    if not name:
        return None
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        try:
            resp = client.get(
                f"{TMDB_BASE_URL}/search/person",
                params={"api_key": _api_key(), "query": name},
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"  ! tmdb person search failed for {name!r}: {exc}")
            return None

        results = data.get("results") or []
        if not results:
            return None
        return results[0].get("id")
    finally:
        if owns_client:
            client.close()


def get_director_filmography(
    person_id: int, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    """Return every film where the person was credited as Director.

    Each entry is `{"film_title": str, "film_year": int|None}`.

    Filters TMDB's `crew` array on `job == "Director"` (a person can be
    credited as Producer or Writer for the same film; we only want directing
    credits). De-duplicates by (title, year) since some films appear twice
    with slightly different metadata.
    """
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        try:
            resp = client.get(
                f"{TMDB_BASE_URL}/person/{person_id}/movie_credits",
                params={"api_key": _api_key()},
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"  ! tmdb filmography failed for person_id={person_id}: {exc}")
            return []

        crew = data.get("crew") or []
        seen: set[tuple[str, int | None]] = set()
        films: list[dict[str, Any]] = []
        for entry in crew:
            if entry.get("job") != "Director":
                continue
            title = entry.get("title") or entry.get("original_title")
            if not title:
                continue
            year = _parse_year(entry.get("release_date"))
            key = (title, year)
            if key in seen:
                continue
            seen.add(key)
            films.append({"film_title": title, "film_year": year})
        return films
    finally:
        if owns_client:
            client.close()
