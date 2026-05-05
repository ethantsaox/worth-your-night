"""WYN scoring engine — Phase 1, formula v0.1.

This is a stand-in formula that uses only OMDb-derived signals (Metacritic + IMDb).
The full WYN formula (Metacritic 35% + Letterboxd 30% + Pedigree 20% + Trades 15%)
arrives in Phase 2 once Letterboxd scraping and trade-pub signals are wired up.
"""
from __future__ import annotations

import math
from typing import Any


def compute_score_v01(row: dict[str, Any]) -> dict[str, Any]:
    """Compute the v0.1 WYN composite score and tier for a single movie.

    Pure function: takes a normalized OMDb dict, returns scoring fields only.

    Weights:
      - Metacritic (0-100):                          45%
      - IMDb rating x10 (normalized to 0-100):       40%
      - IMDb votes, log-scaled and capped at 100:    15%

    Tiers:
      - score >= 70  -> WORTH
      - 50 <= score  -> DECENT
      - score < 50   -> FILLER
      - missing Metacritic or IMDb rating -> UNSCORED (no guessing)
    """
    metacritic = row.get("metacritic")
    imdb_rating = row.get("imdb_rating")
    imdb_votes = row.get("imdb_votes") or 0

    missing: list[str] = []
    if metacritic is None:
        missing.append("metacritic")
    if imdb_rating is None:
        missing.append("imdb_rating")
    if missing:
        return {
            "score": None,
            "tier": "UNSCORED",
            "reason": f"missing: {', '.join(missing)}",
        }

    imdb_norm = float(imdb_rating) * 10.0
    votes_norm = min(100.0, math.log10(max(int(imdb_votes), 1)) * 20.0)

    score = round(
        float(metacritic) * 0.45 + imdb_norm * 0.40 + votes_norm * 0.15,
        2,
    )

    if score >= 70:
        tier = "WORTH"
    elif score >= 50:
        tier = "DECENT"
    else:
        tier = "FILLER"

    return {"score": score, "tier": tier, "reason": "ok"}
