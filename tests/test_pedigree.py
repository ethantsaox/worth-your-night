"""Tests for the pedigree module's pure logic.

The orchestration layer (`compute_pedigree`) is exercised end-to-end during
the pipeline run; here we test the building blocks directly so regressions
trip pytest before they reach production.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from src import db
from src.pedigree import (
    LAST_N_FILMS,
    MIN_PRIOR_FILMS_WITH_METACRITIC,
    _compute_pedigree_score,
    _primary_director_name,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = db.connect(":memory:")
    db.init_schema(c)
    return c


# --- _primary_director_name --------------------------------------------------


def test_primary_director_picks_first_when_multiple() -> None:
    assert _primary_director_name("Joel Coen, Ethan Coen") == "Joel Coen"


def test_primary_director_returns_single_intact() -> None:
    assert _primary_director_name("Bong Joon Ho") == "Bong Joon Ho"


def test_primary_director_returns_none_for_missing() -> None:
    assert _primary_director_name(None) is None
    assert _primary_director_name("") is None
    assert _primary_director_name("N/A") is None


# --- _compute_pedigree_score -------------------------------------------------


def _films_row(title: str, year: int | None, metacritic: int | None) -> sqlite3.Row:
    """Build a Row that looks like the get_director_films() return shape."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE x (film_title TEXT, film_year INTEGER, metacritic INTEGER)")
    conn.execute("INSERT INTO x VALUES (?, ?, ?)", (title, year, metacritic))
    return conn.execute("SELECT * FROM x").fetchone()


def test_pedigree_averages_last_5_prior_films() -> None:
    """The most recent 5 prior films with Metacritic are averaged."""
    films = [
        _films_row("F1", 2018, 90),
        _films_row("F2", 2016, 80),
        _films_row("F3", 2014, 70),
        _films_row("F4", 2012, 60),
        _films_row("F5", 2010, 50),
        _films_row("F6", 2008, 40),  # should be excluded — only 5 most recent
    ]
    score, count = _compute_pedigree_score(films, current_year=2020)
    # avg(90, 80, 70, 60, 50) = 70.0
    assert score == 70.0
    assert count == LAST_N_FILMS


def test_pedigree_excludes_films_at_or_after_current_year() -> None:
    """Only films with year < current_year count as 'prior'."""
    films = [
        _films_row("Future", 2025, 95),  # excluded — after current
        _films_row("Same year", 2020, 95),  # excluded — same year, ambiguous order
        _films_row("Prior 1", 2019, 80),
        _films_row("Prior 2", 2018, 70),
        _films_row("Prior 3", 2017, 60),
    ]
    score, count = _compute_pedigree_score(films, current_year=2020)
    # avg(80, 70, 60) = 70
    assert score == 70.0
    assert count == 3


def test_pedigree_excludes_films_with_no_metacritic() -> None:
    """Films missing Metacritic are skipped, then 'last 5' is taken from what remains."""
    films = [
        _films_row("F1", 2018, None),
        _films_row("F2", 2016, 80),
        _films_row("F3", 2014, None),
        _films_row("F4", 2012, 60),
        _films_row("F5", 2010, 70),
    ]
    score, count = _compute_pedigree_score(films, current_year=2020)
    # avg(80, 60, 70) = 70
    assert score == 70.0
    assert count == 3


def test_pedigree_returns_none_when_below_threshold() -> None:
    """Fewer than MIN_PRIOR_FILMS_WITH_METACRITIC eligible -> None (UNSCORED-eligible)."""
    films = [
        _films_row("F1", 2018, 90),
        _films_row("F2", 2016, 80),  # only 2 prior with Metacritic
    ]
    score, count = _compute_pedigree_score(films, current_year=2020)
    assert score is None
    assert count == 2
    assert MIN_PRIOR_FILMS_WITH_METACRITIC == 3  # belt-and-braces


def test_pedigree_handles_empty_filmography() -> None:
    score, count = _compute_pedigree_score([], current_year=2020)
    assert score is None
    assert count == 0


def test_pedigree_skips_rows_with_no_year() -> None:
    """TMDB occasionally returns films with no release_date -> film_year=None.
    These can't be ordered by 'prior to current year' and must be excluded."""
    films = [
        _films_row("Undated", None, 90),
        _films_row("F1", 2018, 80),
        _films_row("F2", 2016, 70),
        _films_row("F3", 2014, 60),
    ]
    score, _ = _compute_pedigree_score(films, current_year=2020)
    # Undated is dropped; avg(80, 70, 60) = 70
    assert score == 70.0
