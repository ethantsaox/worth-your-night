"""Unit tests for the v0.1 WYN scoring formula."""
from __future__ import annotations

from src.score import compute_score_v01


def test_worth_tier_high_metacritic_and_imdb() -> None:
    """A critically acclaimed, widely-rated film should land in WORTH."""
    row = {"metacritic": 95, "imdb_rating": 9.0, "imdb_votes": 1_000_000}
    result = compute_score_v01(row)
    assert result["tier"] == "WORTH"
    assert result["score"] is not None
    assert result["score"] >= 70


def test_decent_tier_mid_range_signals() -> None:
    """A solidly mid-tier film (60 MC, 6.5 IMDb, 50k votes) should be DECENT."""
    row = {"metacritic": 60, "imdb_rating": 6.5, "imdb_votes": 50_000}
    result = compute_score_v01(row)
    assert result["tier"] == "DECENT"
    assert 50 <= result["score"] < 70


def test_filler_tier_low_signals() -> None:
    """Critical bombs with low Metacritic and IMDb should be FILLER."""
    row = {"metacritic": 20, "imdb_rating": 3.0, "imdb_votes": 5_000}
    result = compute_score_v01(row)
    assert result["tier"] == "FILLER"
    assert result["score"] < 50


def test_unscored_when_metacritic_missing() -> None:
    """No guessing: if Metacritic is missing, the row is UNSCORED."""
    row = {"metacritic": None, "imdb_rating": 7.5, "imdb_votes": 50_000}
    result = compute_score_v01(row)
    assert result["tier"] == "UNSCORED"
    assert result["score"] is None
    assert "metacritic" in result["reason"]


def test_unscored_when_imdb_rating_missing() -> None:
    """Missing IMDb rating also forces UNSCORED, regardless of Metacritic."""
    row = {"metacritic": 80, "imdb_rating": None, "imdb_votes": 100_000}
    result = compute_score_v01(row)
    assert result["tier"] == "UNSCORED"
    assert "imdb_rating" in result["reason"]
