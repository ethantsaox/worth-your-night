"""OMDb API client for WYN Phase 1.

Wraps a single OMDb lookup into a normalized dict shape the rest of the pipeline
can consume. Network errors and 'Movie not found' responses are swallowed and
surfaced as `found=False` rows so a single bad title cannot crash a 50-title run.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

OMDB_BASE_URL = "http://www.omdbapi.com/"
REQUEST_TIMEOUT_SECONDS = 10.0


def _parse_metacritic(ratings: list[dict[str, str]] | None) -> int | None:
    """Pull the Metacritic score out of OMDb's nested `Ratings` array.

    OMDb's Ratings field looks like:
        [{"Source": "Internet Movie Database", "Value": "9.2/10"},
         {"Source": "Rotten Tomatoes",         "Value": "97%"},
         {"Source": "Metacritic",              "Value": "100/100"}]

    Returns the integer 0-100 score, or None if Metacritic isn't present.
    """
    for entry in ratings or []:
        if entry.get("Source") == "Metacritic":
            value = entry.get("Value", "")
            try:
                return int(value.split("/", 1)[0])
            except (ValueError, AttributeError):
                return None
    return None


def _to_float(value: Any) -> float | None:
    """Coerce OMDb string values to float, treating 'N/A' / blanks as None."""
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int_votes(value: Any) -> int:
    """Parse IMDb vote counts (e.g. '1,234,567') into an int. Defaults to 0."""
    if value in (None, "", "N/A"):
        return 0
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0


def _empty_record(title: str, year: int) -> dict[str, Any]:
    """Shape returned when a title can't be fetched or isn't found."""
    return {
        "title": title,
        "year": year,
        "found": False,
        "metacritic": None,
        "imdb_rating": None,
        "imdb_votes": 0,
        "director": None,
        "actors": None,
        "genre": None,
        "runtime": None,
        "rated": None,
        "plot": None,
    }


def fetch_omdb(title: str, year: int) -> dict[str, Any]:
    """Fetch one movie from OMDb and return a normalized record.

    Reads `OMDB_API_KEY` from the environment (loaded from .env on import).

    Returned keys:
        title, year, found, metacritic, imdb_rating, imdb_votes,
        director, actors, genre, runtime, rated, plot

    On any failure (timeout, HTTP error, JSON error, "Movie not found") the
    function returns a record with `found=False` and prints a one-line warning
    so the caller can keep going.
    """
    api_key = os.getenv("OMDB_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OMDB_API_KEY is not set. Copy .env.example to .env and add your key."
        )

    record = _empty_record(title, year)

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = client.get(
                OMDB_BASE_URL,
                params={"apikey": api_key, "t": title, "y": year},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  ! request failed for {title} ({year}): {exc}")
        return record

    if data.get("Response") != "True":
        print(f"  ! not found: {title} ({year}) — {data.get('Error', 'unknown')}")
        return record

    record.update(
        {
            "found": True,
            "metacritic": _parse_metacritic(data.get("Ratings")),
            "imdb_rating": _to_float(data.get("imdbRating")),
            "imdb_votes": _to_int_votes(data.get("imdbVotes")),
            "director": data.get("Director"),
            "actors": data.get("Actors"),
            "genre": data.get("Genre"),
            "runtime": data.get("Runtime"),
            "rated": data.get("Rated"),
            "plot": data.get("Plot"),
        }
    )
    return record
