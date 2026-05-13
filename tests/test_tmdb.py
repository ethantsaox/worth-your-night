"""Pure tests for the TMDB module — no network.

We test the parsing helpers directly. The HTTP boundary is exercised
end-to-end by the pipeline run; mocking httpx for the public functions is
not worth the complexity for a portfolio project of this size.
"""
from __future__ import annotations

from src.tmdb import _parse_year


def test_parse_year_full_iso_date() -> None:
    assert _parse_year("2019-05-30") == 2019


def test_parse_year_handles_year_only_string() -> None:
    assert _parse_year("2019") == 2019


def test_parse_year_returns_none_for_empty() -> None:
    assert _parse_year("") is None
    assert _parse_year(None) is None


def test_parse_year_returns_none_for_garbage() -> None:
    assert _parse_year("xxxx-yy-zz") is None
