# Pipeline — lichess-tournament-analysis

## 1. The graph

```
Lichess API ──► ingest ──► data/raw ──► load ──► lichess.db (M2 tables) ──► clean ──► *_clean tables ──► validate ──► exit code
GitHub TSVs ─┘                                                                                              │
                          pipeline_log.db  ◄────────────── run_pipeline.py logs every step ────────────────┘
```

| Step | Input | Output | Rebuildable from |
|---|---|---|---|
| ingest (`src/ingest.py`) | Lichess `/api/tournament`, `/api/tournament/{id}/games`, chess-openings TSVs, last success in `pipeline_log.db` | `data/raw/tournaments/*.json`, `data/raw/games/*.pgn`, `data/raw/openings/*.tsv` (never overwritten) | Not rebuildable: the source remembers only about 70 minutes, so the raw files are the only copy |
| load (`src/load.py`) | `data/raw/**` | M2 tables in `data/lichess.db` (tournament, player, opening, opening_line, game, quarantine_game) | Raw files (`--skip-ingest`) |
| clean (`src/clean.py`) | M2 tables | `tournament_clean`, `game_clean` | M2 tables |
| validate (`src/validate.py`) | clean tables, `--run-time` | 12 checks, exit code 1 on any STOP | Nothing to rebuild |

## 2. Design decisions

| Decision | Our choice | Why |
|---|---|---|
| Ingestion: where each run starts | Last successful ingest from `pipeline_log.db` minus the overlap; first run falls back to the newest raw tournament file | A run that fails or is missed does not advance the start point, so the next run covers the whole gap |
| Overlap | 120 minutes | Larger than the deepest the source's list ever reached (111 minutes); see delay table. Re-checking is free because downloads are de-duplicated by tournament id |
| Loading | Full reload of all raw files on every run | Simple and correct: the guarded upsert makes re-reading old files harmless. `load.py` takes 2.2 to 3.7 seconds now (run log) for 32.7 MB of raw data. Raw data grows by about 30 MB per day (1.3 MB per hour, measured from file sizes), and load time grows roughly linearly (about 0.1 s per MB). The 180-second step timeout would be reached at about 1.8 GB, i.e. in roughly 8 weeks. Memory may become the limit earlier: `read_games` keeps every parsed game in memory before writing (not measured). When that happens we switch to incremental loading with a manifest table of loaded files, written in the same transaction as the rows it describes. |
| Version rule | `retrieved_at` (timestamp in the raw file name); upserts are guarded with `WHERE excluded.retrieved_at > <table>.retrieved_at` | A backfill that re-reads an old file cannot undo a newer value |
| Cleaning | Full rebuild | `clean.py` deletes and re-inserts both clean tables from the M2 tables in one transaction on every run, so flags are always computed on the whole history and batch boundaries cannot change them. Old flags can change as history grows: `stale_snapshot` is judged against the newest retrieval time and the IQR fences move with the data. Takes about 2 seconds now (run log) |
| The clock | `--run-time`, default now (UTC); every step receives it | Same run time on the same raw data gives identical tables |
| Schedule | Every 15 minutes (Windows Task Scheduler) | The source remembers about 70 minutes; 15 minutes recovers up to 3 consecutive missed runs |

### How late our data arrives

Measured from our own raw files with `src/delay_report.py` (consecutive polls at most 130 minutes apart):

| Measure | n | min | median | max (minutes) |
|---|---|---|---|---|
| Tournament finished after the previous poll, until we saw it | 34 | 21.3 | 24.5 | 57.3 |
| Tournament already finished at the previous poll but listed late | 0 | – | – | – |
| Game played_at to our download (includes our own polling interval) | 36,936 | 9.8 | 46.0 | 99.4 |
| Last game ended relative to nominal tournament end | 39 | -1.7 | -0.8 | -0.5 |

No tournament was listed late, no tournament metadata (nbPlayers, status) changed between polls (0 of 79), and all games end before the nominal end of the tournament. A tournament's games are therefore final when it first appears as finished, and one download per tournament id is enough.

### The source's memory

`/api/tournament` returns exactly the 20 most recently finished tournaments (all names; list size was 20 in all 24 polls). The oldest listed tournament was 69.4 minutes old at minimum, 81.7 at median and 111.5 at maximum. The memory is count-based and varies with how many tournaments finish per hour; we plan with the minimum of about 70 minutes.

If more than about 69 minutes pass between our last successful ingest and the next one, tournaments that finished in between are gone for good. With a 15-minute schedule, k consecutive missed runs leave a gap of (k+1) x 15 minutes, so up to 3 missed runs are recovered completely. An hourly schedule could not recover even one.

How we notice: `ingest.py` compares the time of the last successful ingest with the oldest tournament in the list and prints `WARN DATA GAP` (exit code stays 0, otherwise the pipeline could never heal); `monitor.py` raises a completeness ALARM for hours without data.

Known gap: TODO confirm from the run log. Runs at 2026-11-23 18:18 and 19:18 UTC were missed while the schedule was still hourly; tournaments that finished around 17:57 UTC could not be recovered.

## 3. Operations

- Run by hand: `python src/run_pipeline.py`
- Rebuild / backfill: `python src/run_pipeline.py --skip-ingest` rebuilds `lichess.db` and the clean tables from the landed raw data without the network. Everything after raw is rebuilt, so after fixing a cleaning rule this one command re-cleans the whole history and gives identical tables.
- Check health: `python src/monitor.py`
- Exit codes: 0 success · 1 a step failed (or validate found a STOP) · 2 authentication failure in ingest · 75 another run is in progress · 124 a step timed out
- Logs: `data/pipeline_log.db` (table `pipeline_run`), `logs/pipeline.log`, `logs/monitor.log` (TODO: confirm the redirect works under Task Scheduler)
- Schedule: `ops/task_scheduler.xml` (pipeline) and `ops/monitor_task.xml` (monitor, a few minutes later), imported in Windows Task Scheduler

## 4. Evidence

### The schedule
TODO: machine (whose computer), interval, the two committed XML files.

### The run log
TODO: one query and its output (first run, last run, number of runs, days covered, failures by step and cause).

### Missed runs
TODO: what we did, the ALARM, completeness before and after.

### Drills
TODO: six short entries (wrong credential, failing step, hanging step, two runs at once, missed runs, old file re-loaded).

### monitor.py on the day we tagged
TODO: pasted output.

## 5. What can still go wrong

TODO, fill at the end. Already known:
- An outage longer than about 70 minutes loses the tournaments that finished meanwhile for good; we detect it, we cannot repair it.
- If the computer is off, nothing runs and nothing can report it; `monitor.py` runs on the same machine.
- A run killed by a power cut leaves `data/pipeline.lock`; a lock older than 30 minutes is treated as stale.