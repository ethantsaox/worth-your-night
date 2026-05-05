"""Letterboxd average-rating fetcher for WYN Phase 2.1.

Letterboxd has no public ratings API at the free tier, so we scrape the public
film page for the aggregate weighted rating. Pages are cached to disk under
`cache/letterboxd/` so iterative formula tweaking doesn't re-hit the network.

Lookup strategy:
    1. Derive a slug from the title (e.g. "The Godfather" -> "the-godfather").
    2. Try `/film/<slug>-<year>/` FIRST — year-disambiguated URLs are
       unambiguous when they exist (e.g. /film/parasite-2019/ for the Bong
       Joon-ho film, /film/cats-2019/ for the Tom Hooper musical).
    3. If that 404s, fall back to `/film/<slug>/` — the canonical URL for
       films without title collisions.
    4. If neither yields a parseable rating, return `letterboxd_found=False`
       so the row becomes UNSCORED downstream.

    Note: trying the bare slug first is unsafe — it often returns an *older*
    film sharing the title (e.g. /film/parasite/ is a 1982 horror film, not
    Bong's 2019 film), which the parser will happily extract a rating from.

Rating is returned on Letterboxd's native 0-5 scale; the scorer normalizes.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

LETTERBOXD_BASE_URL = "https://letterboxd.com/film/"
REQUEST_TIMEOUT_SECONDS = 15.0
USER_AGENT = "WYN-portfolio-scraper/0.2 (+github.com/ethantsaox/worth-your-night)"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "cache" / "letterboxd"


def title_to_slug(title: str) -> str:
    """Convert a film title to a Letterboxd-style slug.

    Letterboxd slugs are lowercase, hyphen-separated, with apostrophes dropped
    and all non-alphanumerics collapsed to single hyphens.

    Examples:
        "The Godfather"          -> "the-godfather"
        "Schindler's List"       -> "schindlers-list"
        "Mad Max: Fury Road"     -> "mad-max-fury-road"
        "12 Years a Slave"       -> "12-years-a-slave"
    """
    s = title.lower()
    s = re.sub(r"[‘’']", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _cache_path(slug: str) -> Path:
    return CACHE_DIR / f"{slug}.html"


def _read_cache(slug: str) -> str | None:
    path = _cache_path(slug)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _write_cache(slug: str, html: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(slug).write_text(html, encoding="utf-8")


def _fetch_page(slug: str, client: httpx.Client) -> str | None:
    """Fetch raw HTML for a slug. Returns None on 404, raises on other errors."""
    url = f"{LETTERBOXD_BASE_URL}{slug}/"
    response = client.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.text


_CDATA_WRAPPER = re.compile(r"/\*\s*(<!\[CDATA\[|\]\]>)\s*\*/")


def _strip_cdata_wrapper(raw: str) -> str:
    """Remove `/* <![CDATA[ */ ... /* ]]> */` wrappers from a script body.

    Letterboxd serves its JSON-LD blocks wrapped in HTML CDATA comments — a
    legacy XHTML convenience — which makes the body invalid JSON until the
    wrappers are stripped.
    """
    return _CDATA_WRAPPER.sub("", raw).strip()


def parse_rating(html: str) -> float | None:
    """Extract the Letterboxd weighted-average rating (0-5) from a film page.

    Tries multiple sources in order:
      1. JSON-LD `aggregateRating.ratingValue` (most reliable when present).
      2. `<meta name="twitter:data2" content="X.XX out of 5">`.
      3. Any element with a `data-average-rating` attribute.

    Returns None when no rating can be found (e.g. obscure films with too few
    ratings — Letterboxd hides aggregate ratings below a threshold).
    """
    soup = BeautifulSoup(html, "lxml")

    for script in soup.find_all("script", type="application/ld+json"):
        body = _strip_cdata_wrapper(script.string or "")
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            rating = (entry.get("aggregateRating") or {}).get("ratingValue")
            if rating is not None:
                try:
                    return float(rating)
                except (TypeError, ValueError):
                    continue

    twitter = soup.find("meta", attrs={"name": "twitter:data2"})
    if twitter and twitter.get("content"):
        match = re.search(r"([0-9]+\.?[0-9]*)\s*out\s*of\s*5", twitter["content"])
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass

    avg_node = soup.find(attrs={"data-average-rating": True})
    if avg_node:
        try:
            return float(avg_node["data-average-rating"])
        except (TypeError, ValueError):
            pass

    return None


def fetch_letterboxd(
    title: str,
    year: int,
    client: httpx.Client | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Fetch a film's Letterboxd average rating with slug + slug-year fallback.

    Returned keys:
        title, year, letterboxd_found (bool),
        letterboxd_slug (str|None), letterboxd_rating (float|None, 0-5)

    Network and parse failures are swallowed and surfaced as
    `letterboxd_found=False` so the caller's batch run cannot crash on a
    single bad title. Pages are cached under cache/letterboxd/<slug>.html.
    """
    base_slug = title_to_slug(title)
    candidates = [f"{base_slug}-{year}", base_slug]

    record: dict[str, Any] = {
        "title": title,
        "year": year,
        "letterboxd_found": False,
        "letterboxd_slug": None,
        "letterboxd_rating": None,
    }

    owns_client = client is None
    if owns_client:
        client = httpx.Client(
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    try:
        for slug in candidates:
            html: str | None = None

            if use_cache:
                html = _read_cache(slug)

            if html is None:
                try:
                    html = _fetch_page(slug, client)
                except httpx.HTTPError as exc:
                    print(f"  ! letterboxd request failed for {slug}: {exc}")
                    continue
                if html is None:
                    continue
                if use_cache:
                    _write_cache(slug, html)

            rating = parse_rating(html)
            if rating is not None:
                record.update(
                    {
                        "letterboxd_found": True,
                        "letterboxd_slug": slug,
                        "letterboxd_rating": rating,
                    }
                )
                return record

        print(f"  ! letterboxd: no rating for {title} ({year})")
        return record
    finally:
        if owns_client:
            client.close()
