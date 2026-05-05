"""Unit tests for the Letterboxd module — slug derivation and rating parsing.

These tests are pure (no network): they exercise `title_to_slug` and
`parse_rating` against synthetic HTML fixtures so the parser can be validated
without scraping live pages.
"""
from __future__ import annotations

from src.letterboxd import parse_rating, title_to_slug


# --- slug derivation ---------------------------------------------------------


def test_slug_simple_title() -> None:
    assert title_to_slug("The Godfather") == "the-godfather"


def test_slug_drops_apostrophe() -> None:
    assert title_to_slug("Schindler's List") == "schindlers-list"


def test_slug_collapses_punctuation() -> None:
    assert title_to_slug("Mad Max: Fury Road") == "mad-max-fury-road"


def test_slug_keeps_digits() -> None:
    assert title_to_slug("12 Years a Slave") == "12-years-a-slave"


def test_slug_handles_long_title() -> None:
    assert (
        title_to_slug("Everything Everywhere All at Once")
        == "everything-everywhere-all-at-once"
    )


# --- rating parsing — JSON-LD ------------------------------------------------


def test_parse_rating_from_json_ld() -> None:
    html = """
    <html><head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Movie",
        "name": "The Godfather",
        "aggregateRating": {
          "@type": "AggregateRating",
          "ratingValue": "4.59",
          "bestRating": "5"
        }
      }
      </script>
    </head><body></body></html>
    """
    assert parse_rating(html) == 4.59


def test_parse_rating_from_json_ld_array() -> None:
    """JSON-LD blocks sometimes contain arrays of objects."""
    html = """
    <script type="application/ld+json">
    [{"@type": "BreadcrumbList"},
     {"@type": "Movie", "aggregateRating": {"ratingValue": 3.8}}]
    </script>
    """
    assert parse_rating(html) == 3.8


def test_parse_rating_strips_letterboxd_cdata_wrapper() -> None:
    """Letterboxd wraps its JSON-LD in /* <![CDATA[ */ ... /* ]]> */ comments.

    Without the wrapper-stripping helper, json.loads fails and the parser
    silently falls through to the twitter:data2 fallback — which works, but
    leaves us one Letterboxd template change away from a regression.
    """
    html = """
    <script type="application/ld+json">
    /* <![CDATA[ */
    {"@type": "Movie", "aggregateRating": {"ratingValue": "4.59"}}
    /* ]]> */
    </script>
    """
    assert parse_rating(html) == 4.59


# --- rating parsing — fallbacks ---------------------------------------------


def test_parse_rating_from_twitter_meta_when_no_json_ld() -> None:
    html = """
    <html><head>
      <meta name="twitter:data2" content="4.12 out of 5">
    </head></html>
    """
    assert parse_rating(html) == 4.12


def test_parse_rating_from_data_attribute() -> None:
    html = '<div data-average-rating="3.45">histogram</div>'
    assert parse_rating(html) == 3.45


# --- rating parsing — graceful failure --------------------------------------


def test_parse_rating_returns_none_when_absent() -> None:
    """Films with too few ratings have no aggregate rating on the page."""
    html = "<html><head><title>Some Film</title></head><body></body></html>"
    assert parse_rating(html) is None


def test_parse_rating_handles_malformed_json_ld() -> None:
    html = """
    <script type="application/ld+json">{ this is not valid json }</script>
    <meta name="twitter:data2" content="3.0 out of 5">
    """
    assert parse_rating(html) == 3.0
