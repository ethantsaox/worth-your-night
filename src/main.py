"""WYN orchestrator — Phase 2.3 (pedigree-augmented).

Pipeline flow:

    1. Initialize SQLite schema (idempotent).
    2. Seed titles into the DB from data/titles.csv (idempotent).
    3. For each title:
       - Reuse cached OMDb data if present, else fetch and persist.
       - Reuse cached Letterboxd data if present, else fetch and persist.
       - Reuse cached pedigree if present, else compute (which may itself
         trigger a TMDB person lookup + filmography + per-film OMDb fetches
         on first encounter; all results cached for subsequent runs).
       - Compute the v0.3 score and UPSERT into the scores table.
    4. Export the final JOIN to output/wyn_scores.csv.

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

from src import db  # noqa: E402
from src.fetch import fetch_omdb  # noqa: E402
from src.letterboxd import USER_AGENT, fetch_letterboxd  # noqa: E402
from src.pedigree import compute_pedigree  # noqa: E402
from src.score import compute_score_v03  # noqa: E402

TITLES_PATH = PROJECT_ROOT / "data" / "titles.csv"
OUTPUT_PATH = PROJECT_ROOT / "output" / "wyn_scores.csv"
FORMULA_VERSION = "v0.3"
SLEEP_BETWEEN_FETCHES_SECONDS = 1.0


def run() -> pd.DataFrame:
    """Fetch (or reuse cached), score, persist, and export. Returns the DataFrame."""
    titles_df = pd.read_csv(TITLES_PATH)

    with db.connect() as conn:
        db.init_schema(conn)
        db.seed_titles(conn, titles_df)

        letterboxd_client = httpx.Client(
            timeout=15.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        tmdb_client = httpx.Client(timeout=10.0)

        try:
            for row in db.all_titles(conn):
                title_id, title, year = row["id"], row["title"], row["year"]
                fetched_anything = False

                omdb_data = db.get_omdb_data(conn, title_id)
                if omdb_data is None:
                    print(f"Fetching OMDb:    {title} ({year})")
                    omdb_data = fetch_omdb(title, year)
                    db.upsert_omdb_data(conn, title_id, omdb_data)
                    fetched_anything = True
                else:
                    print(f"Cached OMDb:      {title} ({year})")

                letterboxd_data = db.get_letterboxd_data(conn, title_id)
                if letterboxd_data is None:
                    print(f"Fetching LB:      {title} ({year})")
                    letterboxd_data = fetch_letterboxd(
                        title, year, client=letterboxd_client
                    )
                    db.upsert_letterboxd_data(conn, title_id, letterboxd_data)
                    fetched_anything = True
                else:
                    print(f"Cached LB:        {title} ({year})")

                pedigree_cached = db.get_pedigree_data(conn, title_id) is not None
                if pedigree_cached:
                    print(f"Cached pedigree:  {title} ({year})")
                else:
                    print(f"Computing pedigree: {title} ({year})")
                pedigree_data = compute_pedigree(
                    conn, title_id, omdb_data, year, tmdb_client=tmdb_client
                )

                merged = {**omdb_data, **letterboxd_data, **pedigree_data}
                scored = compute_score_v03(merged)
                db.upsert_score(conn, title_id, FORMULA_VERSION, scored)

                if fetched_anything:
                    time.sleep(SLEEP_BETWEEN_FETCHES_SECONDS)
        finally:
            letterboxd_client.close()
            tmdb_client.close()

        df = db.export_scores_df(conn, FORMULA_VERSION)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    return df


def summarize(df: pd.DataFrame) -> None:
    """Print headline stats: total processed, tier counts, top 10 by score."""
    print(f"\nTotal titles processed: {len(df)}")
    print("\nTier distribution:")
    print(df["tier"].value_counts().to_string())

    print("\nTop 10 by score:")
    top = df.head(10)[["title", "year", "score", "tier", "pedigree_score"]]
    print(top.to_string(index=False))

    print(f"\nResults written to: {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"DB lives at:        {db.DB_PATH.relative_to(PROJECT_ROOT)}")


def main() -> None:
    df = run()
    summarize(df)


if __name__ == "__main__":
    main()
