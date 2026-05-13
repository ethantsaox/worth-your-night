# Worth Your Night (WYN)

A streaming quality scoring engine that rates movies as **WORTH**, **DECENT**, or **FILLER** by combining critic, audience, and pedigree signals into a single composite score. This repository is a portfolio project exploring how the messy, multi-source signal landscape that drives "should I watch this tonight?" decisions can be reduced to a defensible, reproducible score — and where each signal source's blind spots show up.

## Current scope (v0.3 — Phase 2.3 shipped)

The pipeline pulls 50 seed titles from **OMDb** and **Letterboxd**, derives a director-pedigree signal from **TMDB** filmographies + OMDb Metacritic lookups, persists everything to a local **SQLite** store, computes a v0.3 composite score blending Metacritic + Letterboxd + Pedigree, and exports a sorted CSV. Re-runs reuse cached signals and finish in well under a second.

**Shipped so far:**
- **Phase 1** — OMDb integration, v0.1 formula (Metacritic + IMDb), CSV pipeline, pytest suite.
- **Phase 2.1** — Letterboxd ratings via the public film page (year-disambiguated slug lookup, disk-cached HTML, multi-strategy parser), v0.2 formula.
- **Phase 2.2** — SQLite store. Separate tables per signal source plus a scores table keyed on `(title_id, formula_version)`, so v0.1 / v0.2 / v0.3 results coexist and can be compared in SQL.
- **Phase 2.3** — Director pedigree. For each seed title's director, we hit TMDB for the full filmography, look up each prior film's Metacritic via OMDb, and store the avg of the most recent 5 prior films as the pedigree score. Directors with fewer than 3 prior Metacritic-known films are UNSCORED rather than guessed.

**What's NOT included yet:**
- Trade publication coverage — THR / Variety / IndieWire (Phase 2.4)
- Full canonical formula MC 35% / LB 30% / Pedigree 20% / Trades 15% (Phase 2.5)
- Ground-truth labeling and threshold/weight optimization (Phase 3)
- Streamlit dashboard and case study writeup (Phase 4)

## Data store

A single SQLite file at `wyn.db` (gitignored; regenerable from `data/titles.csv`, `cache/letterboxd/`, and the live APIs). Plain stdlib `sqlite3` — no ORM. Seven tables:

| Table | Key | Purpose |
|---|---|---|
| `titles` | `id` | seed list loaded from `data/titles.csv` |
| `omdb_data` | `title_id` | one row per title; cached OMDb response + `fetched_at` |
| `letterboxd_data` | `title_id` | one row per title; slug + rating + `fetched_at` |
| `directors` | `id` | one row per unique director name; cached TMDB `person_id` |
| `director_films` | `(director_id, film_title, film_year)` | one row per film in a director's TMDB filmography; cached Metacritic |
| `pedigree_data` | `title_id` | one row per title; computed pedigree score + prior-film count |
| `scores` | `(title_id, formula_version)` | one row per title per formula; lets v0.1 / v0.2 / v0.3 coexist |

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

# 4. Add API keys
cp .env.example .env
# then edit .env and paste your keys after the = signs:
#   OMDB_API_KEY  — get a free key at https://www.omdbapi.com/apikey.aspx
#   TMDB_API_KEY  — get a free key at https://www.themoviedb.org/settings/api
#                   (TMDB only; v3 auth, the older one. NOT the "Read Access Token".)

# 5. Run the pipeline
python src/main.py
```

To deactivate the venv when you're done: `deactivate`.

Get a free OMDb API key at <https://www.omdbapi.com/apikey.aspx>.

## Running tests

```bash
pytest
```

## Scoring formula (v0.3)

> v0.3 adds the pedigree signal to v0.2's MC + LB blend. The canonical full formula is MC 35% + LB 30% + Pedigree 20% + Trades 15%; with Trades still missing, the present 85% is re-normalized to 100%.

| Signal | Source | Normalization | Weight |
|---|---|---|---|
| Metacritic | OMDb `Ratings[]` | already 0–100 | **41%** (35/85) |
| Letterboxd | `letterboxd.com/film/<slug>/` (scraped) | × 20 → 0–100 | **35%** (30/85) |
| Pedigree | TMDB filmography + OMDb Metacritic of director's prior films | avg of last 5 prior; already 0–100 | **24%** (20/85) |

**Tiers:**
- score ≥ 70 → **WORTH**
- 50 ≤ score < 70 → **DECENT**
- score < 50 → **FILLER**
- Missing Metacritic OR Letterboxd OR Pedigree → **UNSCORED** (no guessing)

### Pedigree calculation details

For each seed title's director (first credit only when OMDb returns "X, Y"):
1. Search TMDB for the director's `person_id`.
2. Fetch their movie credits (filtered to `job == "Director"`).
3. For each film, fetch Metacritic via OMDb.
4. Filter to films released *before* the current title's year.
5. Take the most recent 5 of those with a Metacritic value.
6. Pedigree score = arithmetic mean. If fewer than **3** prior films have Metacritic, the pedigree score is `None` and the row becomes UNSCORED.

All three layers (TMDB person id, TMDB filmography, per-film OMDb Metacritic) are cached in SQLite — first-time hydration takes a few minutes, every subsequent run is sub-second.

> Historical formulas: v0.1 = MC 45% + IMDb-rating 40% + IMDb-votes 15%. v0.2 = MC 55% + LB 45%. Both are still callable as `compute_score_v01` / `compute_score_v02` in [src/score.py](src/score.py) for regression.

### Known limitations of v0.3

- **Tier cutoffs are unvalidated.** 70 / 50 are intuition, not optimization. Phase 3 fits these against a labeled set.
- **Single critic source.** Metacritic alone misses trade-publication signal (THR / Variety / IndieWire), which Phase 2.4 adds.
- **First-time directors are UNSCORED.** This is deliberate (no guessing) but it means debut films — including legitimately interesting ones — drop out of the comparison. e.g. Chad Stahelski's *John Wick* (2014) is now UNSCORED in our seed set.
- **OMDb's Metacritic coverage is incomplete for older catalog films.** Films before the early-2000s often have no Metacritic value, which thins the pedigree signal for older directors (and for the current title itself — Schindler's List remains UNSCORED for this reason).
- **TMDB name search is ambiguity-prone.** "Christopher Nolan" returns the right person, but common names can map to the wrong person. We take the top-ranked search hit (TMDB sorts by popularity) and accept the residual ambiguity. Manual override would be a Phase 3 hardening item.
- **Pedigree treats co-directors as one author.** OMDb "Joel Coen, Ethan Coen" is reduced to "Joel Coen" for the lookup. In practice this is harmless because co-directors share filmographies, but it's a documented simplification.
- **Letterboxd has no public ratings API on the free tier.** The scraper hits public film pages with respectful throttling and disk-caches responses. Multi-strategy parser reduces but doesn't eliminate fragility to template changes.
- **Slug-disambiguation pitfall (Letterboxd).** Bare `/film/<slug>/` URLs often point to *older* films sharing the title. The fetcher tries `/film/<slug>-<year>/` first and falls back to the bare slug, blocking silent wrong-film matches at the source.

## Findings (v0.3)

Phase 2.3 added director pedigree (avg Metacritic of the director's last 5 prior films) at 24% weight. The result is a real discrimination win paired with a real coverage problem.

| Tier | v0.1 | v0.2 | v0.3 |
|---|---|---|---|
| WORTH | 37 | 38 | **19** |
| DECENT | 1 | 1 | **4** |
| FILLER | 10 | 10 | **5** |
| UNSCORED | 2 | 1 | **22** |

**The discrimination v0.2 couldn't produce, v0.3 did.** The four films that landed in DECENT — *Edge of Tomorrow*, *The Martian*, *Crazy Rich Asians*, *The Equalizer* — are exactly the "competent mid-tier crowdpleaser with okay-but-not-prestige director" archetype that the Letterboxd-only formula kept locked in WORTH. Pedigree pulls them down because their directors' track records are mixed, not stellar. The five FILLER films (*Cats*, *Dragonball Evolution*, *Gigli*, *Jack and Jill*, *The Last Airbender*) are all genuine flops, confirming the lower tier is calibrated correctly when it has signal.

**But pedigree's reach is limited by Metacritic's catalog coverage.** Metacritic launched in 1999 and only sparsely backfills earlier films. So pre-2000s directors look like first-timers to the pedigree signal:

- *The Godfather* (1972) → UNSCORED. Coppola's pre-1972 films (*The Rain People*, *Finian's Rainbow*, *You're a Big Boy Now*, *Dementia 13*) have no Metacritic.
- *Pulp Fiction* (1994) → UNSCORED. Tarantino had only *Reservoir Dogs* before it — 1 film, below the 3-film threshold.
- *Moonlight* (2016) → UNSCORED. Barry Jenkins had only *Medicine for Melancholy* prior.
- *Whiplash* (2014) → UNSCORED. Damien Chazelle had only *Guy and Madeline on a Park Bench*.

22 of 50 films (44%) drop out for this reason — a significant share of the seed set. The films that do score, score well, but the loss of *The Godfather* and *Pulp Fiction* from a "what should I watch tonight" leaderboard is a real problem.

**The implication for Phase 3.** Calibration alone won't fix this — the underlying issue is that pedigree relies on a critic-source (Metacritic) for the signal it's *meant* to complement. Phase 2.4's trade-publication coverage may help: films that pre-date Metacritic often have trade-publication archives extending decades earlier. The deeper Phase 3 question is whether to add a fallback "no-pedigree-but-other-strong-signals" tier that allows e.g. *The Godfather* to be scored on critic + audience alone with a documented confidence penalty.

The frozen v0.3 results table lives at [output/wyn_scores_v0.3.csv](output/wyn_scores_v0.3.csv) for inspection.

## Findings (v0.2)

The Phase 2.1 result, retained as context for the v0.3 story: **adding Letterboxd to the formula didn't change the tier distribution.** v0.1 (Metacritic + IMDb only) classified 37 of 50 as WORTH; v0.2 (Metacritic + Letterboxd) classified 38 of 50 as WORTH. Letterboxd users love mid-tier crowdpleasers about as much as critics do, so a critic-plus-audience blend kept films like *John Wick* and *Knives Out* firmly in WORTH. The v0.3 pedigree signal corrects this — but introduces its own coverage problem (above).

The frozen v0.2 results table lives at [output/wyn_scores_v0.2.csv](output/wyn_scores_v0.2.csv).

## Sample output (top 10)

From a 50-title v0.3 run on 2026-05-12.

| Rank | Title | Year | Score | Pedigree | Tier |
|---|---|---|---|---|---|
| 1 | Parasite | 2019 | 90.01 | 77.20 | WORTH |
| 2 | There Will Be Blood | 2007 | 88.71 | 80.67 | WORTH |
| 3 | Goodfellas | 1990 | 88.57 | 81.80 | WORTH |
| 4 | No Country for Old Men | 2007 | 84.88 | 70.80 | WORTH |
| 5 | The Departed | 2006 | 83.71 | 77.00 | WORTH |
| 6 | The Lord of the Rings: The Fellowship of the Ring | 2001 | 83.65 | 63.33 | WORTH |
| 7 | The Dark Knight | 2008 | 83.40 | 71.33 | WORTH |
| 8 | The Social Network | 2010 | 82.97 | 68.80 | WORTH |
| 9 | Mad Max: Fury Road | 2015 | 82.51 | 68.40 | WORTH |
| 10 | Baby Driver | 2017 | 79.58 | 76.75 | WORTH |

Full tier distribution: **19 WORTH / 4 DECENT / 5 FILLER / 22 UNSCORED.** See [Findings (v0.3)](#findings-v03) above for what changed and why so many titles became UNSCORED.

For comparison:
- v0.2 (MC + LB): 38 / 1 / 10 / 1
- v0.1 (MC + IMDb only): 37 / 1 / 10 / 2

## Project structure

```
worth-your-night/
├── data/
│   └── titles.csv             # 50 seed titles (title, year)
├── src/
│   ├── db.py                  # SQLite store: schema, upserts, export query
│   ├── fetch.py               # OMDb API client
│   ├── letterboxd.py          # Letterboxd scraper + slug fallback + HTML cache
│   ├── tmdb.py                # TMDB client: person search + filmography
│   ├── pedigree.py            # director pedigree feature (orchestration + calc)
│   ├── score.py               # v0.1 + v0.2 + v0.3 scoring logic
│   └── main.py                # orchestrator (DB-backed)
├── output/
│   ├── wyn_scores.csv         # current run (gitignored)
│   └── wyn_scores_v0.2.csv    # frozen v0.2 snapshot (committed artifact)
├── cache/
│   └── letterboxd/            # disk cache of Letterboxd HTML (gitignored)
├── tests/
│   ├── test_db.py             # in-memory SQLite roundtrips + ordering
│   ├── test_letterboxd.py     # slug derivation + rating-parser fixtures
│   ├── test_pedigree.py       # pedigree calc logic
│   ├── test_score.py          # unit tests for v0.1 + v0.2 + v0.3
│   └── test_tmdb.py           # pure parsing helpers (no network)
├── conftest.py
├── wyn.db                     # SQLite database (gitignored)
├── .env.example
├── requirements.txt
└── README.md
```

## Roadmap

- **Phase 2.1 — Letterboxd integration.** _Shipped._ Public film-page scraping with year-disambiguated slug lookup, disk-cached HTML, multi-strategy parser (JSON-LD → twitter:data2 → data-average-rating). v0.2 formula blends Metacritic + Letterboxd.
- **Phase 2.2 — SQLite migration.** _Shipped._ Four-table store (titles / omdb_data / letterboxd_data / scores). Signal data is persisted on first fetch; re-runs are sub-second. Multiple formula versions coexist in the scores table.
- **Phase 2.3 — Pedigree feature.** _Shipped._ Director track record via TMDB filmography + OMDb Metacritic. avg of last 5 prior films, `None` (UNSCORED) for directors with fewer than 3 prior films with Metacritic. v0.3 formula adds this at 24% weight.
- **Phase 2.4 — Trade publication coverage.** THR / Variety / IndieWire — likely a binary "did they review it?" signal as a prestige proxy, since scraping full review scores at scale is brittle.
- **Phase 2.5 — Full canonical formula + recalibration.** Blend Metacritic 35% / Letterboxd 30% / Pedigree 20% / Trades 15%. Re-evaluate tier cutoffs against the test set.
- **Phase 3 — Ground-truth labeling and optimization.** Hand-label a subset of titles, then fit weights and tier cutoffs against the labels. Report MAE / accuracy vs. the v0.1 and v0.2 baselines.
- **Phase 4 — Dashboard and case study.** Streamlit dashboard for browsing scored titles, filtering by tier / genre / decade, and inspecting per-title signal breakdowns. Case study writeup of methodology and findings for the portfolio.
