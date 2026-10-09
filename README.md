# Lichess Opening Outcomes Pipeline

## The question

We want to investigate which chess openings are associated with different game outcomes after accounting for the rating difference between the players and each player's color, and whether these patterns change across rating groups. We will first explore win rates and sample sizes by opening and rating band. We will then build a model that predicts game result using rating difference, color, and opening, with a time-based train/test split and a rating-only baseline to measure whether opening information adds predictive value. The results can show which openings perform differently from what rating alone would predict at different rating levels, providing evidence that can inform which openings to study for bullet chess.

## Data sources

Full details for each source are in [docs/sources.md](docs/sources.md).

- **Lichess recent finished tournaments** (metadata, JSON) via the Lichess API.
- **Lichess tournament games** (Hourly Bullet Arena, PGN export) via the Lichess API.
- **Lichess chess-openings** (ECO reference table, TSV) from [lichess-org/chess-openings](https://github.com/lichess-org/chess-openings).

Data from [lichess.org](https://lichess.org), released under CC0 1.0 (no attribution required, credited here anyway). A bounded sample of the raw data is committed to `data/raw/` so the repository can be inspected without an API token; the raw game files contain player usernames, which are not part of the analysis and are removed in M3. Anyone with a Lichess token can regenerate or extend the data by running the pipeline.

## How to run

1. Clone the repository and enter it:
   ```
   git clone https://github.com/denizef00/lichess-tournament-analysis
   cd lichess-tournament-analysis
   ```
2. Create and activate a virtual environment:
   ```
   python -m venv .venv
   # Windows:  .venv\Scripts\activate
   # macOS/Linux:  source .venv/bin/activate
   ```
3. Install requirements:
   ```
   pip install -r requirements.txt
   ```
4. Create a Lichess API token at https://lichess.org/account/oauth/token (no special scopes needed).
5. Copy `.env.example` to `.env` and put your token in it:
   ```
   LICHESS_API_TOKEN=your-token-here
   ```
   Optional variables (defaults in brackets): `LICHESS_NAME_FILTER` [Hourly Bullet], `MAX_TOURNAMENTS` [20], `MAX_RETRIES` [5], `CONTACT_INFO` [student-project].
6. Run the ingestion from the repository root:
   ```
   python src/ingest.py
   ```

Each run creates new timestamped files under `data/raw/` and prints a run summary at the end (files, records, failures). Earlier files are never overwritten.

## Repository structure

```
├── README.md
├── .gitignore
├── .env.example          variable names with placeholder values
├── requirements.txt
├── data/raw/             landed raw data (committed sample, CC0)
│   ├── tournaments/      tournament list responses (JSON)
│   ├── games/            games per tournament (PGN)
│   └── openings/         ECO reference files (TSV)
├── src/ingest.py         ingestion script
└── docs/sources.md       data source cards
```

## Status

M1 — ingestion complete

## Team

- Deniz Efe Doğan (230717061), GitHub: `denizef00`
- Sudenaz Yönezer (230717025), GitHub: `Sudenazyonezer`