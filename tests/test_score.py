"""Unit tests for the v0.1 and v0.2 WYN scoring formulas."""
from __future__ import annotations

from src.score import compute_score_v01, compute_score_v02


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


# --- v0.2 tests --------------------------------------------------------------


def test_v02_worth_high_signals() -> None:
    """Strong critic + strong audience -> WORTH (well above 70)."""
    row = {"metacritic": 95, "letterboxd_rating": 4.5}
    result = compute_score_v02(row)
    # 95 * 0.55 + 90 * 0.45 = 52.25 + 40.5 = 92.75
    assert result["tier"] == "WORTH"
    assert result["score"] == 92.75


def test_v02_decent_mid_signals() -> None:
    """Mid critic + mid audience -> DECENT."""
    row = {"metacritic": 60, "letterboxd_rating": 3.2}
    result = compute_score_v02(row)
    # 60 * 0.55 + 64 * 0.45 = 33 + 28.8 = 61.8
    assert result["tier"] == "DECENT"
    assert 50 <= result["score"] < 70


def test_v02_filler_low_signals() -> None:
    """Low critic + low audience -> FILLER."""
    row = {"metacritic": 20, "letterboxd_rating": 1.5}
    result = compute_score_v02(row)
    # 20 * 0.55 + 30 * 0.45 = 11 + 13.5 = 24.5
    assert result["tier"] == "FILLER"
    assert result["score"] < 50


def test_v02_unscored_when_letterboxd_missing() -> None:
    """No Letterboxd rating -> UNSCORED, even with strong Metacritic."""
    row = {"metacritic": 90, "letterboxd_rating": None}
    result = compute_score_v02(row)
    assert result["tier"] == "UNSCORED"
    assert "letterboxd" in result["reason"]


def test_v02_unscored_when_metacritic_missing() -> None:
    """No Metacritic -> UNSCORED, even with strong Letterboxd."""
    row = {"metacritic": None, "letterboxd_rating": 4.5}
    result = compute_score_v02(row)
    assert result["tier"] == "UNSCORED"
    assert "metacritic" in result["reason"]
