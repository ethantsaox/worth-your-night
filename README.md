# Worth Your Night (WYN)

A streaming quality scoring engine that rates movies as **WORTH**, **DECENT**, or **FILLER** by combining critic, audience, and pedigree signals into a single composite score. This repository is a portfolio project exploring how the messy, multi-source signal landscape that drives "should I watch this tonight?" decisions can be reduced to a defensible, reproducible score — and where each signal source's blind spots show up.

## Current scope (v0.2 — Phase 2.2 shipped)

The pipeline pulls 50 seed titles from the **OMDb API** and **Letterboxd**, persists the fetched signals to a local **SQLite** store, computes a v0.2 composite score blending Metacritic + Letterboxd, and exports a sorted CSV. Re-runs reuse cached signals from the DB and finish in well under a second.

**Shipped so far:**
- **Phase 1** — OMDb integration, v0.1 formula (Metacritic + IMDb), CSV pipeline, pytest suite.
- **Phase 2.1** — Letterboxd ratings via the public film page (year-disambiguated slug lookup, disk-cached HTML, multi-strategy parser), v0.2 formula.
- **Phase 2.2** — SQLite store. Separate tables per signal source plus a scores table keyed on `(title_id, formula_version)`, so v0.1 and v0.2 results coexist and can be compared in SQL.

**What's NOT included yet:**
- Pedigree features — director/cast track records (Phase 2.3)
- Trade publication coverage — THR / Variety / IndieWire (Phase 2.4)
- Full canonical formula MC 35% / LB 30% / Pedigree 20% / Trades 15% (Phase 2.5)
- Ground-truth labeling and threshold/weight optimization (Phase 3)
- Streamlit dashboard and case study writeup (Phase 4)

## Data store

A single SQLite file at `wyn.db` (gitignored; regenerable from `data/titles.csv` and `cache/letterboxd/`). Plain stdlib `sqlite3` — no ORM. Four tables:

| Table | Key | Purpose |
|---|---|---|
| `titles` | `id` | seed list loaded from `data/titles.csv` |
| `omdb_data` | `title_id` | one row per title; cached OMDb response + `fetched_at` |
| `letterboxd_data` | `title_id` | one row per title; slug + rating + `fetched_at` |
| `scores` | `(title_id, formula_version)` | one row per title per formula; lets v0.1 and v0.2 coexist |

The pipeline checks the DB before each fetch — re-runs are essentially free unless you delete the DB. The Letterboxd HTML cache (`cache/letterboxd/*.html`) is kept alongside as a re-parse layer: if the parser improves, structured DB rows can be regenerated from raw HTML without re-scraping.

## Setup (macOS)

```bash
# 1. Clone, then move into the project
cd worth-your-night

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add your OMDb API key
cp .env.example .env
# then edit .env and paste your key after OMDB_API_KEY=

# 5. Run the pipeline
python src/main.py
```

To deactivate the venv when you're done: `deactivate`.

Get a free OMDb API key at <https://www.omdbapi.com/apikey.aspx>.

## Running tests

```bash
pytest
```

## Scoring formula (v0.2)

> v0.2 is a step toward the canonical WYN formula (Metacritic 35% + Letterboxd 30% + Pedigree 20% + Trades 15%). The 35% allocated to the missing Pedigree and Trades signals is redistributed proportionally onto Metacritic and Letterboxd until those signals come online.

| Signal | Source | Normalization | Weight |
|---|---|---|---|
| Metacritic | OMDb `Ratings[]` | already 0–100 | **55%** |
| Letterboxd | `letterboxd.com/film/<slug>/` (scraped) | × 20 → 0–100 | **45%** |

**Tiers:**
- score ≥ 70 → **WORTH**
- 50 ≤ score < 70 → **DECENT**
- score < 50 → **FILLER**
- Missing Metacritic or Letterboxd → **UNSCORED** (no guessing)

> v0.1 (`compute_score_v01` in [src/score.py](src/score.py)) is kept as the historical baseline for regression and comparison. It uses only OMDb signals (Metacritic 45% + IMDb rating 40% + IMDb votes 15%).

### Known limitations of v0.2

- **The audience signal didn't fix the calibration drift.** Adding Letterboxd reshuffled the top of the leaderboard but the overall distribution barely moved (38 / 1 / 10 / 1 in the 50-title test set, vs. 37 / 1 / 10 / 2 in v0.1). Letterboxd users love mid-tier mainstream films about as much as critics do, so a critic+audience blend doesn't discriminate them from prestige films. The real fix is Phase 3 calibration plus the Pedigree and Trades signals from Phases 2.3 and 2.4.
- **Tier cutoffs are unvalidated.** 70 / 50 are intuition, not optimization. Phase 3 fits these against a labeled set.
- **Single critic source.** Metacritic alone misses trade-publication signal (THR / Variety / IndieWire), which Phase 2.4 adds.
- **OMDb's Metacritic coverage is incomplete for older catalog films.** Schindler's List (1993) returned no Metacritic value, leaving it UNSCORED in the 50-title test set.
- **Letterboxd has no public ratings API on the free tier.** The scraper hits public film pages with respectful throttling (1 req/sec) and disk-caches responses. The parser tries JSON-LD `aggregateRating` first and falls back to `twitter:data2` — multiple strategies reduce but don't eliminate fragility to template changes.
- **Slug-disambiguation pitfall.** Letterboxd's bare `/film/<slug>/` URL often points to an *older* film sharing the title rather than 404'ing (e.g. `/film/parasite/` → 1982 horror film, not Bong Joon-ho's 2019 film). The fetcher tries `/film/<slug>-<year>/` *first* and only falls back to the bare slug, so silent wrong-film matches are now blocked at the source.

## Findings (v0.2)

The headline result from Phase 2.1: **adding Letterboxd to the formula didn't change the tier distribution**. v0.1 (Metacritic + IMDb only) classified 37 of 50 titles as WORTH. v0.2 (Metacritic + Letterboxd) classified 38 of 50 as WORTH. Same shape, different films at the top of the leaderboard.

The intuition behind v0.2 was that Letterboxd — a film-buff audience source weighted toward considered taste — would discriminate between prestige films and mid-tier crowdpleasers in a way that IMDb couldn't. **It didn't.** Letterboxd users love *John Wick*, *Knives Out*, and *Logan* about as much as Metacritic critics do, so a critic-plus-audience blend keeps mid-tier mainstream films firmly in WORTH.

The implication for the rest of the project: a better audience source isn't enough. The signals that *should* discriminate are pedigree (does the director / lead cast have a track record of acclaimed films?) and trade-publication coverage (did THR / Variety / IndieWire bother reviewing it?). Both are on the roadmap as Phase 2.3 and 2.4. Phase 3 is where unvalidated tier cutoffs (70 / 50) get fit against a hand-labeled subset to confirm or reject this.

The frozen v0.2 results table lives at [output/wyn_scores_v0.2.csv](output/wyn_scores_v0.2.csv) for inspection without re-running the pipeline.

## Sample output (top 10)

From a 50-title v0.2 run on 2026-05-05.

| Rank | Title | Year | Score | Tier |
|---|---|---|---|---|
| 1 | The Godfather | 1972 | 95.68 | WORTH |
| 2 | Parasite | 2019 | 94.12 | WORTH |
| 3 | Moonlight | 2016 | 92.25 | WORTH |
| 4 | There Will Be Blood | 2007 | 91.29 | WORTH |
| 5 | Goodfellas | 1990 | 90.74 | WORTH |
| 6 | Pulp Fiction | 1994 | 90.41 | WORTH |
| 7 | The Lord of the Rings: The Fellowship of the Ring | 2001 | 90.11 | WORTH |
| 8 | No Country for Old Men | 2007 | 89.39 | WORTH |
| 9 | La La Land | 2016 | 88.78 | WORTH |
| 10 | Whiplash | 2014 | 88.73 | WORTH |

Full tier distribution: **38 WORTH / 1 DECENT / 10 FILLER / 1 UNSCORED.**

For comparison, v0.1 (OMDb-only) on the same 50 titles: **37 WORTH / 1 DECENT / 10 FILLER / 2 UNSCORED** — almost identical shape, which is the central finding driving Phase 2.3+ and Phase 3.

## Project structure

```
worth-your-night/
├── data/
│   └── titles.csv             # 50 seed titles (title, year)
├── src/
│   ├── db.py                  # SQLite store: schema, upserts, export query
│   ├── fetch.py               # OMDb API client
│   ├── letterboxd.py          # Letterboxd scraper + slug fallback + HTML cache
│   ├── score.py               # v0.1 + v0.2 scoring logic
│   └── main.py                # orchestrator (DB-backed)
├── output/
│   ├── wyn_scores.csv         # current run (gitignored)
│   └── wyn_scores_v0.2.csv    # frozen v0.2 snapshot (committed artifact)
├── cache/
│   └── letterboxd/            # disk cache of Letterboxd HTML (gitignored)
├── tests/
│   ├── test_db.py             # in-memory SQLite roundtrips + ordering
│   ├── test_letterboxd.py     # slug derivation + rating-parser fixtures
│   └── test_score.py          # unit tests for v0.1 and v0.2
├── conftest.py
├── wyn.db                     # SQLite database (gitignored)
├── .env.example
├── requirements.txt
└── README.md
```

## Roadmap

- **Phase 2.1 — Letterboxd integration.** _Shipped._ Public film-page scraping with year-disambiguated slug lookup, disk-cached HTML, multi-strategy parser (JSON-LD → twitter:data2 → data-average-rating). v0.2 formula blends Metacritic + Letterboxd.
- **Phase 2.2 — SQLite migration.** _Shipped._ Four-table store (titles / omdb_data / letterboxd_data / scores). Signal data is persisted on first fetch; re-runs are sub-second. Multiple formula versions coexist in the scores table.
- **Phase 2.3 — Pedigree feature.** Director and lead-cast track records (e.g. average Metacritic of their last 5 films, via OMDb). The new signal that v0.2's findings most need.
- **Phase 2.4 — Trade publication coverage.** THR / Variety / IndieWire — likely a binary "did they review it?" signal as a prestige proxy, since scraping full review scores at scale is brittle.
- **Phase 2.5 — Full canonical formula + recalibration.** Blend Metacritic 35% / Letterboxd 30% / Pedigree 20% / Trades 15%. Re-evaluate tier cutoffs against the test set.
- **Phase 3 — Ground-truth labeling and optimization.** Hand-label a subset of titles, then fit weights and tier cutoffs against the labels. Report MAE / accuracy vs. the v0.1 and v0.2 baselines.
- **Phase 4 — Dashboard and case study.** Streamlit dashboard for browsing scored titles, filtering by tier / genre / decade, and inspecting per-title signal breakdowns. Case study writeup of methodology and findings for the portfolio.
