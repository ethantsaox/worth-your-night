"""WYN scoring engine.

Three formulas live here:

- `compute_score_v01` — OMDb-only (Metacritic + IMDb rating + IMDb votes).
  Phase 1 baseline. Kept for regression and historical comparison.

- `compute_score_v02` — Phase 2.1. Drops IMDb entirely and blends
  Metacritic + Letterboxd. Step toward the canonical WYN formula
  (Metacritic 35% + Letterboxd 30% + Pedigree 20% + Trades 15%) — the
  missing 35% (Pedigree + Trades) is redistributed proportionally.

- `compute_score_v03` — Phase 2.3. Adds the pedigree signal (avg Metacritic
  of the director's last 5 prior films). Re-normalizes the present 85%
  (canonical Trades 15% still missing) onto MC / LB / Pedigree.
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


def compute_score_v02(row: dict[str, Any]) -> dict[str, Any]:
    """Compute the v0.2 WYN composite score for a single film.

    Phase 2.1 formula — Metacritic + Letterboxd only:
        Metacritic (0-100):                            55%
        Letterboxd (0-5, normalized to 0-100 via x20): 45%

    These weights come from re-normalizing the canonical Metacritic 35% /
    Letterboxd 30% blend so they sum to 100% while Pedigree (20%) and
    Trades (15%) are still missing. They will shrink again in v0.3+.

    Tiers (unchanged from v0.1):
        score >= 70  -> WORTH
        50 <= score  -> DECENT
        score < 50   -> FILLER

    Missing Metacritic OR Letterboxd -> UNSCORED (no guessing).
    """
    metacritic = row.get("metacritic")
    letterboxd = row.get("letterboxd_rating")

    missing: list[str] = []
    if metacritic is None:
        missing.append("metacritic")
    if letterboxd is None:
        missing.append("letterboxd")
    if missing:
        return {
            "score": None,
            "tier": "UNSCORED",
            "reason": f"missing: {', '.join(missing)}",
        }

    letterboxd_norm = float(letterboxd) * 20.0
    score = round(float(metacritic) * 0.55 + letterboxd_norm * 0.45, 2)

    if score >= 70:
        tier = "WORTH"
    elif score >= 50:
        tier = "DECENT"
    else:
        tier = "FILLER"

    return {"score": score, "tier": tier, "reason": "ok"}


def compute_score_v03(row: dict[str, Any]) -> dict[str, Any]:
    """Compute the v0.3 WYN composite score for a single film.

    Phase 2.3 formula — Metacritic + Letterboxd + Pedigree:
        Metacritic (0-100):                            41% (35/85)
        Letterboxd (0-5, normalized to 0-100 via x20): 35% (30/85)
        Pedigree   (0-100, avg MC of director's prior
                     last 5 films with Metacritic):    24% (20/85)

    These weights re-normalize the canonical 35/30/20 split onto 100% while
    Trades (15%) is still missing. They will shrink in v0.4 when Trades arrives.

    Tiers (unchanged):
        score >= 70  -> WORTH
        50 <= score  -> DECENT
        score < 50   -> FILLER

    Missing Metacritic OR Letterboxd OR Pedigree -> UNSCORED.
    """
    metacritic = row.get("metacritic")
    letterboxd = row.get("letterboxd_rating")
    pedigree = row.get("pedigree_score")

    missing: list[str] = []
    if metacritic is None:
        missing.append("metacritic")
    if letterboxd is None:
        missing.append("letterboxd")
    if pedigree is None:
        missing.append("pedigree")
    if missing:
        return {
            "score": None,
            "tier": "UNSCORED",
            "reason": f"missing: {', '.join(missing)}",
        }

    letterboxd_norm = float(letterboxd) * 20.0
    score = round(
        float(metacritic) * 0.41
        + letterboxd_norm * 0.35
        + float(pedigree) * 0.24,
        2,
    )

    if score >= 70:
        tier = "WORTH"
    elif score >= 50:
        tier = "DECENT"
    else:
        tier = "FILLER"

    return {"score": score, "tier": tier, "reason": "ok"}
