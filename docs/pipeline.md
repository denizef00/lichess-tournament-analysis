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
| Overlap | 120 minutes | No tournament was ever listed late (delay table below), so the overlap is only a safety margin; re-checking costs nothing because downloads are de-duplicated by tournament id |
| Loading | Full reload of all raw files on every run | Simple and correct: the guarded upsert makes re-reading old files harmless. Since 2026-11-26 12:30 UTC `load.py` took 10.6 s on average (maximum 44.2 s, 85 runs) with 137 MB of raw data, up from 2 to 4 s on 2026-11-24 with 33 MB. Raw data grows by about 35 MB per day and load time roughly linearly (about 0.09 s per MB). The 180-second step timeout would be reached at about 2 GB, in roughly 7 to 8 weeks (mid-January 2027). Memory may become the limit earlier: `read_games` keeps every parsed game in memory before writing (not measured). Then we switch to incremental loading with a manifest table of loaded files, written in the same transaction as the rows it describes. |
| Version rule | `retrieved_at` (timestamp in the raw file name); upserts are guarded with `WHERE excluded.retrieved_at > <table>.retrieved_at` | A backfill that re-reads an old file cannot undo a newer value |
| Cleaning | Full rebuild | `clean.py` deletes and re-inserts both clean tables from the M2 tables in one transaction on every run, so flags are always computed on the whole history and batch boundaries cannot change them. Old flags can change as history grows: `stale_snapshot` is judged against the newest retrieval time and the IQR fences move with the data. Takes 9.0 s on average (maximum 19 s) at 138,862 games (run log, since 2026-11-26 12:30 UTC) |
| The clock | `--run-time`, default now (UTC); every step receives it | Same run time on the same raw data gives identical tables |
| Schedule | Every 15 minutes (Windows Task Scheduler) | The source lists only its 20 most recently finished tournaments, so we poll often to keep the gap between two polls small. TODO: state how many consecutive missed runs are safe, after the re-measurement |

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

TODO: rewrite after the re-measurement with `src/delay_report.py`. What we know so far: `/api/tournament` returns exactly the 20 most recently finished tournaments of all kinds, not a fixed time window. About 15 to 22 tournaments finish per hour (about 3 at :27 and 12 to 19 at :57), so how far back the list reaches depends on the minute of the hour: a short gap is fully covered, but a gap that spans two finish groups can exceed 20 tournaments, and the oldest ones are then lost for good.

Known incident: on 2026-11-24 the scheduled run at 05:18 UTC failed (network), so the gap between the successful runs at 04:18 and 06:18 was 120 minutes, still on the hourly schedule. 33 tournaments finished in that gap (15 in the 04:00 hour, 18 in the 05:00 hour) but the source lists only 20: we saw only 4 of the 15 and 16 of the 18 finishing (`src/completeness_report.py --hours 8 --now 2026-11-24T10:00:00Z`). The other 13 stay in the database as stale snapshots (`status_flag = stale_snapshot` in `tournament_clean`). None of them was an Hourly Bullet tournament (none finished in the 04:00 hour; both of the 05:00 hour have their games), so no game data was lost. This is why the schedule is now every 15 minutes.

How we notice: `ingest.py` compares the time of the last successful ingest with the oldest tournament in the list and prints `WARN DATA GAP` (exit code stays 0, otherwise the pipeline could never heal); `monitor.py` raises a completeness ALARM for hours with too few tournaments.

## 3. Operations

- Run by hand: `python src/run_pipeline.py`
- Rebuild / backfill: `python src/run_pipeline.py --skip-ingest` rebuilds `lichess.db` and the clean tables from the landed raw data without the network. Everything after raw is rebuilt, so after fixing a cleaning rule this one command re-cleans the whole history and gives identical tables.
- Check health: `python src/monitor.py`
- Exit codes: 0 success · 1 a step failed (or validate found a STOP) · 2 authentication failure in ingest · 75 another run is in progress · 124 a step timed out
- Logs: `data/pipeline_log.db` (table `pipeline_run`), `logs/pipeline.log`, `logs/monitor.log` (TODO: confirm the redirect works under Task Scheduler)
- Schedule: `ops/task_scheduler.xml` (pipeline) and `ops/monitor_task.xml` (monitor, a few minutes later), imported in Windows Task Scheduler
- Drill hooks (off by default, only for the failure drills in section 4): `PIPELINE_DRILL_TIMEOUT=clean=1` overrides one step's timeout in `run_pipeline.py`; `PIPELINE_DRILL_FAIL_CLEAN=1` makes `clean.py` raise after it deleted `game_clean`, inside its transaction.

## 4. Evidence

### The schedule
TODO: machine (whose computer), interval, the two committed XML files.

### The run log
TODO: one query and its output (first run, last run, number of runs, days covered, failures by step and cause).

### Missed runs

On 2026-11-26 we disabled the scheduled task `LichessPipeline` after its 11:34 UTC run (the `LichessMonitor` task stayed enabled) and enabled it again at about 12:15 UTC. The scheduled runs at 11:49 and 12:04 were missed.

Run log (`pipeline_run`): 11:34:01 (scheduled), then 12:15:15 (started by hand when the task was re-enabled), then 12:18:59 (scheduled). The first run after the gap fetched everything that was missed.

What `monitor.py` said:
- 12:10 UTC (monitor task, `logs/monitor.log`): `ALARM last_success`, last complete run 0.57 h ago (limit 0.5 h).
- After the recovery run: `ALARM gaps`, max gap 0.69 h (11:34 to 12:15, limit 0.4 h); `last_success` back to PASS. The gaps ALARM stays for 48 hours (the look-back window).

Completeness before and after (`src/completeness_report.py`, evaluated as of 12:35 UTC, when the 11:00 hour is due):

| Hour (UTC) | listed | finished | Hourly Bullet | with games | `monitor.py` completeness |
|---|---|---|---|---|---|
| 11:00, before (snapshot 12:14) | 19 | 5 | 1 | 0 | ALARM (5 < 8) |
| 11:00, after (snapshot 12:21) | 19 | 18 | 1 | 1 | PASS |

Row counts went from 1,640 to 1,653 tournaments and from 105,964 to 106,941 games (+977). The next run (12:18:59) added nothing. Nothing was lost because only one finish burst (:57) fell into the 41-minute gap, fewer than the 20 tournaments the source lists. No `WARN DATA GAP` was printed.

### Drills
TODO: six short entries (wrong credential, failing step, hanging step, two runs at once, missed runs, old file re-loaded).

### Validate timeouts (2026-11-26 and 2026-11-27)

`validate` ended with exit code 124 (its 120-second timeout) in 73 runs between 2026-11-26 10:48 UTC and 2026-11-27 10:03 UTC. In all of them `ingest`, `load` and `clean` had succeeded, so the database stayed up to date, but the 12 checks did not finish and none of those runs counts as a complete success. `monitor.py` raised `ALARM last_success` for that whole period; we did not look at it for about 16 hours because nobody is notified (section 5).

The run time of `validate` grew from day to day (all runs of the day):

| Day (UTC) | runs | average (s) | maximum (s) |
|---|---|---|---|
| 2026-11-23 | 9 | 1.2 | 2.0 |
| 2026-11-24 | 55 | 12.5 | 29.4 |
| 2026-11-25 | 93 | 47.1 | 72.8 |
| 2026-11-26 | 93 | 102.6 | 121.0 |

Every run from 12:33 UTC on 2026-11-26 took 102 to 121 s. The first timeout was at 10:48; from 13:19 to 17:48, 7 of the 17 runs that reached `validate` were killed; every run from 18:03 on was killed.

Cause: the check "finished tournaments have ingested games" ran one query per tournament over the `game` table, and `game.tournament_key` had no index, so for every finished tournament without games the whole table was scanned. The work is about (finished tournaments without games) x (games): 508 x 53,882 = 27 million rows read on 2026-11-24, 1,111 x 106,941 = 119 million on 2026-11-26, 1,447 x 138,862 = 201 million on 2026-11-27.

Fix: `CREATE INDEX IF NOT EXISTS idx_game_tournament ON game (tournament_key)` in `sql/schema.sql`, so a rebuild with `--skip-ingest` gets it too. `validate.py` now takes 6.9 s on 138,862 games with all 12 checks PASS, and the run at 2026-11-27 10:18 UTC finished all four steps successfully.

### Ingest timeouts (2026-11-26)

The runs at 14:19 and 14:34 UTC ended with `ingest` exit code 124 after 300 s (`Step timed out after 300s`); the runner stopped at that step and ran nothing after it. The run at 14:49 completed all steps. We do not know why the requests hung: the runner did not keep the step's partial output (section 5).

### Ingest that did nothing (2026-11-28)

The run at 12:49 UTC logged its `ingest` step as SUCCESS (exit code 0) although it landed no data: a copy-and-paste mistake had put the cleaning code into `src/ingest.py`, so the "ingest" step ran `clean`. `data/raw/tournaments` has no file for that run's time (files for 12:19:01, 12:34:01 and 12:53:21 exist), and `check_landed.py` found no other successful ingest run without a file. We noticed it when the repository scan for SQL string concatenation reported cleaning code inside `src/ingest.py`, restored the file, and ran the pipeline by hand at 12:53 with a 12-hour overlap: nothing was left to download (in window 2, to download 0), so no data was lost.

Two design choices limited the damage: the false success moved the start of the next window forward by only one 15-minute run, and the 120-minute overlap would have covered more. Lesson: an exit code of 0 does not prove that a step did its work. `run_pipeline.py` now also checks that ingest landed a tournaments file and otherwise stops with exit code 3 (`ingest exited 0 but landed no tournaments file`).

### What monitor.py raised on its own

- Early version of the completeness check: it alarmed for 17 consecutive monitor runs (about 4 hours, 2026-11-24, `logs/monitor.log` lines 136 to 234) because it expected an Hourly Bullet tournament in every hour and Lichess did not run one at 16:00. We changed the check to count tournaments of any kind per hour (minimum 8) and to require games for every Hourly Bullet tournament that exists; the alarms stopped.
- `ALARM last_success` on 2026-11-26 15:10 local (12:10 UTC) and `ALARM gaps` at 15:25 local: the missed-run drill above.
- `ALARM last_success` from about 2026-11-26 18:05 UTC until the fix on 2026-11-27 10:15 UTC: the validate timeouts above.

### monitor.py on the day we tagged
TODO: pasted output.

## 5. What can still go wrong

TODO, fill at the end. Already known:
- An outage during which more than 20 tournaments finish (roughly one to two hours, depending on the minute of the hour) loses the oldest ones for good; we detect it, we cannot repair it. It happened once, on the old hourly schedule (see "The source's memory"); only tournaments we do not download games for were affected.
- A run killed by a power cut leaves `data/pipeline.lock`; a lock older than 30 minutes is treated as stale.
- Nobody is notified when `monitor.py` raises an ALARM; it only writes to `logs/monitor.log`. On 2026-11-26/27 the pipeline had no complete successful run for about 16 hours before we noticed. A real deployment would send an e-mail or message when the monitor exits with a non-zero code.
- Step timeouts are fixed constants (ingest 300 s, load 180 s, clean 180 s, validate 120 s) and the work grows with the data. `validate` reached its limit after three days; `load` (average 10.6 s since 2026-11-26) and `clean` (9.0 s) will follow, and the monitor then sees it only as a failed run.
- When a step is killed by its timeout the log records only `Step timed out after Ns`, not what the step had printed until then, so the cause of the two ingest timeouts on 2026-11-26 is unknown.