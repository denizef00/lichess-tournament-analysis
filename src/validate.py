"""Data-quality expectations for the cleaned layer. Exits 1 if any STOP check fails.

Usage: python src/validate.py [db_path] [--run-time ISO_TIMESTAMP]
"""
import argparse
import statistics
import sys
from datetime import datetime, timedelta, timezone

import clean_config as C
from db import connect, DB_PATH

RESULTS = []

# The newest game can be about 60 minutes old between two hourly tournaments, plus up to 15 minutes
# until the next poll and the listing delay. 2 hours leaves a margin without hiding a stalled feed.
# Keep equal to MAX_DATA_FRESHNESS_HOURS in monitor.py.
FRESHNESS_MAX_HOURS = 2.0

# Volume: games landed in the last VOLUME_WINDOW_HOURS before --run-time are compared with a typical
# window (median hourly count x window). One 15-minute run often lands nothing because tournaments
# finish hourly, so a per-run comparison would warn permanently.
VOLUME_WINDOW_HOURS = 2
VOLUME_MIN_HISTORY_HOURS = 6  # no baseline before this many hours with data
VOLUME_HIGH_FACTOR = 10

# Flags that mean "value not trusted" -> the cleaned value must be NULL
UNTRUSTED_FLAGS = ("missing_not_reported", "missing_variant_has_no_openings",
                   "missing_not_applicable", "invalid_range", "stale_snapshot",
                   "no_moves_played")

COUNT_SQL = {
    "tournament": "SELECT COUNT(*) FROM tournament",
    "tournament_clean": "SELECT COUNT(*) FROM tournament_clean",
    "game": "SELECT COUNT(*) FROM game",
    "game_clean": "SELECT COUNT(*) FROM game_clean",
}

# STOP 5: one fixed query per cleaned column; the 6 placeholders are UNTRUSTED_FLAGS
TRUST_SQL = {
    "tournament_clean.status": """SELECT COUNT(*) FROM tournament_clean
        WHERE status IS NOT NULL AND status_flag IN (?, ?, ?, ?, ?, ?)""",
    "tournament_clean.nb_players": """SELECT COUNT(*) FROM tournament_clean
        WHERE nb_players IS NOT NULL AND nb_players_flag IN (?, ?, ?, ?, ?, ?)""",
    "game_clean.opening_key": """SELECT COUNT(*) FROM game_clean
        WHERE opening_key IS NOT NULL AND opening_key_flag IN (?, ?, ?, ?, ?, ?)""",
    "game_clean.white_elo": """SELECT COUNT(*) FROM game_clean
        WHERE white_elo IS NOT NULL AND white_elo_flag IN (?, ?, ?, ?, ?, ?)""",
    "game_clean.black_elo": """SELECT COUNT(*) FROM game_clean
        WHERE black_elo IS NOT NULL AND black_elo_flag IN (?, ?, ?, ?, ?, ?)""",
    "game_clean.white_rating_diff": """SELECT COUNT(*) FROM game_clean
        WHERE white_rating_diff IS NOT NULL AND white_rating_diff_flag IN (?, ?, ?, ?, ?, ?)""",
    "game_clean.black_rating_diff": """SELECT COUNT(*) FROM game_clean
        WHERE black_rating_diff IS NOT NULL AND black_rating_diff_flag IN (?, ?, ?, ?, ?, ?)""",
    "game_clean.moves": """SELECT COUNT(*) FROM game_clean
        WHERE moves IS NOT NULL AND moves_flag IN (?, ?, ?, ?, ?, ?)""",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Data-quality expectations for the cleaned layer")
    parser.add_argument(
        "db_path",
        nargs="?",
        default=str(DB_PATH),
        help="Optional database path (used by test_stops.py on a temporary copy)",
    )
    parser.add_argument(
        "--run-time",
        type=str,
        default=datetime.now(timezone.utc).isoformat(),
        help="Logical pipeline run timestamp in ISO-8601 UTC format",
    )
    return parser.parse_args()


def parse_iso_utc(ts_str: str) -> datetime:
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def record(level, ok, name, detail):
    RESULTS.append(("PASS" if ok else level, name, detail))


def scalar(con, sql, params=()):
    return con.execute(sql, params).fetchone()[0]


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def checks(con, run_dt: datetime):
    # ---- STOP 1: cleaned tables have the same row count as the raw tables
    for clean, raw in (("tournament_clean", "tournament"), ("game_clean", "game")):
        c = scalar(con, COUNT_SQL[clean])
        r = scalar(con, COUNT_SQL[raw])
        record("STOP", c == r, f"{clean} row count equals {raw}", f"{c:,} = {r:,}")

    # ---- STOP 2: no impossible value survives in a cleaned column (family 2 rules)
    bad_elo = scalar(con, """SELECT COUNT(*) FROM game_clean
        WHERE (white_elo IS NOT NULL AND white_elo NOT BETWEEN ? AND ?)
           OR (black_elo IS NOT NULL AND black_elo NOT BETWEEN ? AND ?)""",
                     (C.ELO_VALID_MIN, C.ELO_VALID_MAX, C.ELO_VALID_MIN, C.ELO_VALID_MAX))
    bad_diff = scalar(con, """SELECT COUNT(*) FROM game_clean
        WHERE ABS(COALESCE(white_rating_diff, 0)) > ? OR ABS(COALESCE(black_rating_diff, 0)) > ?""",
                      (C.RATING_DIFF_ABS_MAX, C.RATING_DIFF_ABS_MAX))
    bad_nb = scalar(con, "SELECT COUNT(*) FROM tournament_clean WHERE nb_players < 0")
    record("STOP", bad_elo + bad_diff + bad_nb == 0, "no impossible cleaned value",
           f"elo {bad_elo}, rating_diff {bad_diff}, nb_players {bad_nb}")

    # ---- STOP 3: every reference resolves
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    record("STOP", not fk, "foreign_key_check", "OK" if not fk else f"{len(fk)} violations")

    # ---- STOP 4: every _raw column equals the value in the M2 table
    g_diff = scalar(con, """SELECT COUNT(*) FROM game_clean c JOIN game g ON g.game_id = c.game_id
        WHERE c.opening_key_raw IS NOT g.opening_key OR c.white_elo_raw IS NOT g.white_elo
           OR c.black_elo_raw IS NOT g.black_elo OR c.white_rating_diff_raw IS NOT g.white_rating_diff
           OR c.black_rating_diff_raw IS NOT g.black_rating_diff OR c.moves_raw IS NOT g.moves""")
    t_diff = scalar(con, """SELECT COUNT(*) FROM tournament_clean c
        JOIN tournament t ON t.tournament_key = c.tournament_key
        WHERE c.status_raw IS NOT t.status OR c.nb_players_raw IS NOT t.nb_players
           OR c.max_rating_raw IS NOT t.max_rating""")
    record("STOP", g_diff + t_diff == 0, "_raw columns equal M2 values",
           f"{g_diff} game rows, {t_diff} tournament rows differ")

    # ---- STOP 5: an untrusted flag always comes with a NULL cleaned value
    leaks = []
    for name, sql in TRUST_SQL.items():
        n = scalar(con, sql, UNTRUSTED_FLAGS)
        if n:
            leaks.append(f"{name}={n}")
    record("STOP", not leaks, "untrusted flags have NULL cleaned value",
           ", ".join(leaks) or "0 leaks")

    # ---- WARN 1: share of games with both elo values usable, per tournament
    low = con.execute("""
        SELECT t.source_id, AVG(c.white_elo IS NOT NULL AND c.black_elo IS NOT NULL) AS share
        FROM game_clean c JOIN game g ON g.game_id = c.game_id
        JOIN tournament t ON t.tournament_key = g.tournament_key
        GROUP BY t.tournament_key HAVING share < ? ORDER BY share""",
                      (C.USABLE_ELO_SHARE_MIN,)).fetchall()
    worst = f" (worst {low[0][0]} {low[0][1]:.0%})" if low else ""
    record("WARN", not low, f"usable elo share >= {C.USABLE_ELO_SHARE_MIN:.0%} per tournament",
           f"{len(low)} tournaments below{worst}")

    # ---- WARN 2: coverage, every finished tournament has games
    with_games = scalar(con, """SELECT COUNT(*) FROM tournament_clean c
        WHERE c.status = ? AND EXISTS
              (SELECT 1 FROM game g WHERE g.tournament_key = c.tournament_key)""",
                        (C.TOURNAMENT_FINISHED,))
    empty = scalar(con, """SELECT COUNT(*) FROM tournament_clean c
        WHERE c.status = ? AND NOT EXISTS
              (SELECT 1 FROM game g WHERE g.tournament_key = c.tournament_key)""",
                   (C.TOURNAMENT_FINISHED,))
    record("WARN", with_games > 0, "finished tournaments have ingested games",
           f"{with_games} with games, {empty} metadata-only")

    # ---- WARN 3: game start outside the tournament window (family 4, field comparison)
    early = f"-{C.WINDOW_GRACE_MINUTES} minutes"
    late = f"+{C.WINDOW_GRACE_MINUTES} minutes"
    outside = scalar(con, """SELECT COUNT(*) FROM game g
        JOIN tournament t ON t.tournament_key = g.tournament_key
        WHERE g.played_at < strftime('%Y-%m-%dT%H:%M:%SZ', t.starts_at, ?)
           OR (t.finishes_at IS NOT NULL
               AND g.played_at > strftime('%Y-%m-%dT%H:%M:%SZ', t.finishes_at, ?))""",
                     (early, late))
    record("WARN", outside == 0, "games inside tournament window", f"{outside} outside")

    # ---- WARN 4: every player alias resolves to existing players
    broken = scalar(con, """SELECT COUNT(*) FROM player_alias
        WHERE alias_player_key IS NULL OR canonical_player_key IS NULL""")
    record("WARN", broken == 0, "all player aliases resolve", f"{broken} unresolved")

    # ---- WARN 5: data freshness measured from --run-time (sees the whole feed stopping)
    newest_played = scalar(con, "SELECT MAX(played_at) FROM game WHERE played_at IS NOT NULL")
    if not newest_played:
        record("WARN", False, "data freshness relative to --run-time", "no games in database")
    else:
        age_hours = (run_dt - parse_iso_utc(newest_played)).total_seconds() / 3600.0
        record("WARN", age_hours <= FRESHNESS_MAX_HOURS, "data freshness relative to --run-time",
               f"newest game {age_hours:.1f}h old (limit {FRESHNESS_MAX_HOURS:.1f}h, ref {iso(run_dt)})")

    # ---- WARN 6: volume landed in the last VOLUME_WINDOW_HOURS vs a typical window
    # Zero, or more than VOLUME_HIGH_FACTOR times the typical amount, is a WARN.
    window_start = iso(run_dt - timedelta(hours=VOLUME_WINDOW_HOURS))
    recent = scalar(con, "SELECT COUNT(*) FROM game WHERE retrieved_at > ? AND retrieved_at <= ?",
                    (window_start, iso(run_dt)))
    hourly = [row[0] for row in con.execute(
        """SELECT COUNT(*) FROM game WHERE retrieved_at <= ?
           GROUP BY strftime('%Y-%m-%dT%H', retrieved_at)""", (window_start,)).fetchall()]
    name = f"games landed in last {VOLUME_WINDOW_HOURS}h within normal range"
    if len(hourly) < VOLUME_MIN_HISTORY_HOURS:
        record("WARN", True, name, f"{recent:,} landed; only {len(hourly)} hours of history, no baseline yet")
    else:
        typical = statistics.median(hourly) * VOLUME_WINDOW_HOURS
        anomaly = recent == 0 or (typical > 0 and recent > VOLUME_HIGH_FACTOR * typical)
        record("WARN", not anomaly, name, f"{recent:,} landed vs typical {typical:,.0f}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    run_dt = parse_iso_utc(args.run_time)
    con = connect(args.db_path)
    try:
        checks(con, run_dt)
    except Exception as e:
        print(f"ERROR: {e!r}")
        sys.exit(1)
    finally:
        con.close()

    for status, name, detail in RESULTS:
        print(f"{status:5s} {name:50s} {detail}")
    stops = [r for r in RESULTS if r[0] == "STOP"]
    print(f"{len(RESULTS)} checks | {len(stops)} STOP | "
          f"{sum(r[0] == 'WARN' for r in RESULTS)} WARN")
    sys.exit(1 if stops else 0)


if __name__ == "__main__":
    main()
