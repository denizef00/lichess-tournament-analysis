"""External pipeline and data-quality health monitor (read-only).

Usage:
    python src/monitor.py [--now ISO_TIMESTAMP]

Prints one line per check (PASS or ALARM) and exits 1 if any check raised an ALARM.
Every decision uses --now, never the wall clock, so a check can be replayed or forced to fail:
--now three hours ahead raises ALARMs for last_success, completeness and freshness.
"""
import clean_config as C
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DB_PATH = REPO_ROOT / "data" / "lichess.db"
LOG_DB_PATH = REPO_ROOT / "data" / "pipeline_log.db"

# ---- Thresholds (each with its reason). The pipeline runs every 15 minutes. ----------------

# First run of the 15-minute schedule. Earlier runs were hourly, and one stretch of them was
# missed; that period is documented as a known gap in docs/pipeline.md and must not alarm forever.

SCHEDULE_START = "2026-11-24T12:03:00Z"

# Gaps and completeness look at this many hours back (two days).
LOOKBACK_HOURS = 48

# 1. Two schedule intervals: one missed run is tolerated, two in a row alarm. Lichess lists only
#    about 70 minutes of finished tournaments (docs/pipeline.md), so this leaves roughly 40
#    minutes to react before data starts to be lost for good.
MAX_LAST_SUCCESS_HOURS = 0.5

# 2. The normal gap between runs is 15 minutes; one missed run makes it 30. 24 minutes sits between
#    the two and leaves room for start-up jitter, so any missed run raises an ALARM.
MAX_RUN_GAP_HOURS = 0.4

# 3. Completeness against a calendar of hours. Entity: tournaments of any kind that finished in that
#    hour. Lichess finishes 14 to 23 per hour (32 complete hours measured): about 3 at :27 and
#    about 11 at :57. 8 alarms when the :57 batch is missing and ignores normal quiet hours.
#    Hourly Bullet alone cannot be used: it did not run at all in 4 of 32 hours.
MIN_TOURNAMENTS_PER_HOUR = 8
# An hour is checked 90 minutes after it started, i.e. 33 minutes after its last tournament (:57)
# finished. The batch is normally fetched by the :18 run, so one failed 15-minute run is tolerated;
# two in a row alarm.
COMPLETENESS_GRACE_MINUTES = 90
# Every Hourly Bullet tournament older than the grace period must have its games.
TOURNAMENT_NAME_PATTERN = "%Hourly Bullet%"

# 4. Freshness, two entities. Tournaments finish every 30 minutes (:27, :57), so the newest finished
#    one is normally under 45 minutes old (30 + one 15-minute poll); 1.5 hours leaves a margin.
MAX_TOURNAMENT_FRESHNESS_HOURS = 1.5
#    Hourly Bullet skipped two hours in a row (2.6 hours without new games on 2026-11-23), so games
#    get 4 hours. Keep equal to FRESHNESS_MAX_HOURS in validate.py.
MAX_GAME_FRESHNESS_HOURS = 4.0

EXPECTED_STEPS = ("ingest", "load", "clean", "validate")
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only health monitor for the Lichess pipeline")
    parser.add_argument(
        "--now",
        type=str,
        default=datetime.now(timezone.utc).isoformat(),
        help="Reference timestamp in ISO-8601 UTC format (default: current UTC time)",
    )
    return parser.parse_args()


def parse_iso_utc(ts_str: str) -> datetime:
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)

def iso_hour(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:00:00Z")

def iso_second(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def connect_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def check_last_success(log_con: sqlite3.Connection, now_dt: datetime) -> tuple[str, str]:
    rows = log_con.execute(
        """
        SELECT run_id, MAX(finished_at) AS last_finished,
               COUNT(*) AS step_count, MAX(exit_code) AS max_exit
        FROM pipeline_run
        GROUP BY run_id
        HAVING step_count = ? AND max_exit = 0
        ORDER BY last_finished DESC
        """,
        (len(EXPECTED_STEPS),),
    ).fetchall()

    valid = [r for r in rows if parse_iso_utc(r["last_finished"]) <= now_dt + timedelta(minutes=1)]
    if not valid:
        return "ALARM", "no complete 4-step successful run found"

    latest = valid[0]
    age_hours = (now_dt - parse_iso_utc(latest["last_finished"])).total_seconds() / 3600.0
    detail = (f"last complete run {latest['run_id']} finished {age_hours:.2f}h ago "
              f"(limit {MAX_LAST_SUCCESS_HOURS:.1f}h)")
    return ("PASS" if age_hours <= MAX_LAST_SUCCESS_HOURS else "ALARM"), detail


def check_gaps(log_con: sqlite3.Connection, now_dt: datetime) -> tuple[str, str]:
    since_dt = max(now_dt - timedelta(hours=LOOKBACK_HOURS), parse_iso_utc(SCHEDULE_START))
    rows = log_con.execute("SELECT DISTINCT run_id, run_time FROM pipeline_run").fetchall()
    runs = sorted((parse_iso_utc(r["run_time"]), r["run_id"]) for r in rows)
    runs = [(dt, rid) for dt, rid in runs if since_dt <= dt <= now_dt + timedelta(minutes=1)]
    if len(runs) < 2:
        return "PASS", f"{len(runs)} run(s) since {since_dt:%Y-%m-%d %H:%M}; no gap to check"

    worst_h, worst_a, worst_b = 0.0, None, None
    for (dt_a, _), (dt_b, _) in zip(runs, runs[1:]):
        gap_h = (dt_b - dt_a).total_seconds() / 3600.0
        if gap_h > worst_h:
            worst_h, worst_a, worst_b = gap_h, dt_a, dt_b

    detail = (f"max gap {worst_h:.2f}h ({worst_a:%Y-%m-%d %H:%M} -> {worst_b:%H:%M}, "
              f"limit {MAX_RUN_GAP_HOURS:.1f}h, {len(runs)} runs since {since_dt:%Y-%m-%d %H:%M})")
    return ("PASS" if worst_h <= MAX_RUN_GAP_HOURS else "ALARM"), detail

CALENDAR_SQL = """
WITH RECURSIVE periods(p) AS (
    SELECT :first_period
    UNION ALL
    SELECT strftime('%Y-%m-%dT%H:00:00Z', p, '+1 hour') FROM periods WHERE p < :last_period
)
SELECT p AS period, COUNT(t.tournament_key) AS tournaments
FROM periods
LEFT JOIN tournament t ON strftime('%Y-%m-%dT%H:00:00Z', t.finishes_at) = p
                      AND t.status = :finished
GROUP BY p
HAVING COUNT(t.tournament_key) < :expected
ORDER BY p
"""

NO_GAMES_SQL = """
SELECT t.source_id, t.finishes_at
FROM tournament t
WHERE t.full_name LIKE :pattern
  AND t.finishes_at >= :first_period
  AND t.finishes_at <= :cutoff
  AND NOT EXISTS (SELECT 1 FROM game g WHERE g.tournament_key = t.tournament_key)
ORDER BY t.finishes_at
"""
def check_completeness(data_con: sqlite3.Connection, now_dt: datetime) -> tuple[str, str]:
    last_period = floor_hour(now_dt - timedelta(minutes=COMPLETENESS_GRACE_MINUTES))
    first_period = floor_hour(max(now_dt - timedelta(hours=LOOKBACK_HOURS), parse_iso_utc(SCHEDULE_START)))
    if first_period > last_period:
        return "PASS", "no full period to check yet"
    n_periods = int((last_period - first_period).total_seconds() // 3600) + 1

    holes = data_con.execute(CALENDAR_SQL, {
        "first_period": iso_hour(first_period),
        "last_period": iso_hour(last_period),
        "expected": MIN_TOURNAMENTS_PER_HOUR,
        "finished": C.TOURNAMENT_FINISHED,
    }).fetchall()
    no_games = data_con.execute(NO_GAMES_SQL, {
        "pattern": TOURNAMENT_NAME_PATTERN,
        "first_period": iso_hour(first_period),
        "cutoff": iso_second(now_dt - timedelta(minutes=COMPLETENESS_GRACE_MINUTES)),
    }).fetchall()

    problems = []
    if holes:
        listed = ", ".join(f"{r['period'][:13]}h={r['tournaments']}" for r in holes[:6])
        more = f" (+{len(holes) - 6} more)" if len(holes) > 6 else ""
        problems.append(f"{len(holes)}/{n_periods} hours below {MIN_TOURNAMENTS_PER_HOUR} tournaments: {listed}{more}")
    if no_games:
        ids = ", ".join(f"{r['source_id']}@{r['finishes_at'][11:16]}" for r in no_games[:4])
        problems.append(f"{len(no_games)} Hourly Bullet tournaments without games: {ids}")
    if problems:
        return "ALARM", "; ".join(problems)
    return "PASS", (f"{n_periods}/{n_periods} hours have >= {MIN_TOURNAMENTS_PER_HOUR} tournaments, "
                    f"every Hourly Bullet tournament has games ({iso_hour(first_period)} .. {iso_hour(last_period)})")


def check_freshness(data_con: sqlite3.Connection, now_dt: datetime) -> tuple[str, str]:
    newest_finished = data_con.execute(
        "SELECT MAX(finishes_at) FROM tournament WHERE status = ? AND finishes_at <= ?",
        (C.TOURNAMENT_FINISHED, iso_second(now_dt)),
    ).fetchone()[0]
    newest_game = data_con.execute(
        "SELECT MAX(played_at) FROM game WHERE played_at IS NOT NULL"
    ).fetchone()[0]
    if not newest_finished or not newest_game:
        return "ALARM", "no finished tournaments or no games in database"

    t_age = (now_dt - parse_iso_utc(newest_finished)).total_seconds() / 3600.0
    g_age = (now_dt - parse_iso_utc(newest_game)).total_seconds() / 3600.0
    ok = t_age <= MAX_TOURNAMENT_FRESHNESS_HOURS and g_age <= MAX_GAME_FRESHNESS_HOURS
    detail = (f"newest finished tournament {t_age:.2f}h old (limit {MAX_TOURNAMENT_FRESHNESS_HOURS:.1f}h), "
              f"newest game {g_age:.2f}h old (limit {MAX_GAME_FRESHNESS_HOURS:.1f}h)")
    return ("PASS" if ok else "ALARM"), detail


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    now_dt = parse_iso_utc(args.now)

    results: list[tuple[str, str, str]] = []
    log_con = None
    data_con = None

    try:
        log_con = connect_ro(LOG_DB_PATH)
        data_con = connect_ro(DATA_DB_PATH)

        for name, fn in (
            ("last_success", lambda: check_last_success(log_con, now_dt)),
            ("gaps", lambda: check_gaps(log_con, now_dt)),
            ("completeness", lambda: check_completeness(data_con, now_dt)),
            ("freshness", lambda: check_freshness(data_con, now_dt)),
        ):
            status, detail = fn()
            results.append((status, name, detail))

    except Exception as exc:
        print(f"ALARM monitor_error        {exc!r}")
        return 1
    finally:
        if log_con is not None:
            log_con.close()
        if data_con is not None:
            data_con.close()

    for status, name, detail in results:
        print(f"{status:5s} {name:16s} {detail}")

    alarms = sum(1 for s, _, _ in results if s == "ALARM")
    passes = sum(1 for s, _, _ in results if s == "PASS")
    print(f"{len(results)} checks | {passes} PASS | {alarms} ALARM")
    return 1 if alarms > 0 else 0


if __name__ == "__main__":
    sys.exit(main())