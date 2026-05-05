"""WYN orchestrator.

Reads data/titles.csv, fetches each title from OMDb and Letterboxd, scores
with the v0.2 formula (MC 55% + Letterboxd 45%), writes a sorted
output/wyn_scores.csv, and prints summary stats.

Run from the project root:
    python src/main.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.fetch import fetch_omdb  # noqa: E402
from src.letterboxd import USER_AGENT, fetch_letterboxd  # noqa: E402
from src.score import compute_score_v02  # noqa: E402

TITLES_PATH = PROJECT_ROOT / "data" / "titles.csv"
OUTPUT_PATH = PROJECT_ROOT / "output" / "wyn_scores.csv"
SLEEP_BETWEEN_TITLES_SECONDS = 1.0  # respectful pacing for Letterboxd scraping


def run() -> pd.DataFrame:
    """Fetch, score, sort, and persist results. Returns the final DataFrame."""
    titles = pd.read_csv(TITLES_PATH)
    rows: list[dict] = []

    with httpx.Client(
        timeout=15.0,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as letterboxd_client:
        for _, t in titles.iterrows():
            title = str(t["title"])
            year = int(t["year"])
            print(f"Fetching: {title} ({year})")

            omdb_data = fetch_omdb(title, year)
            letterboxd_data = fetch_letterboxd(title, year, client=letterboxd_client)

            merged = {**omdb_data, **letterboxd_data}
            scored = compute_score_v02(merged)
            rows.append({**merged, **scored})
            time.sleep(SLEEP_BETWEEN_TITLES_SECONDS)

    df = pd.DataFrame(rows)

    df["_unscored"] = df["tier"] == "UNSCORED"
    df["_sort_score"] = df["score"].fillna(-1.0)
    df = df.sort_values(
        by=["_unscored", "_sort_score"],
        ascending=[True, False],
    ).drop(columns=["_unscored", "_sort_score"])

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    return df


def summarize(df: pd.DataFrame) -> None:
    """Print headline stats: total processed, tier counts, top 10 by score."""
    print(f"\nTotal titles processed: {len(df)}")
    print("\nTier distribution:")
    print(df["tier"].value_counts().to_string())

    print("\nTop 10 by score:")
    top = df.head(10)[["title", "year", "score", "tier"]]
    print(top.to_string(index=False))

    print(f"\nResults written to: {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


def main() -> None:
    df = run()
    summarize(df)


if __name__ == "__main__":
    main()
