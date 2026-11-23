"""Data-quality expectations for the cleaned layer. Exits 1 if any STOP check fails.

Usage: python src/validate.py [db_path] [--run-time ISO_TIMESTAMP]
"""
import argparse
import statistics
import sys
from datetime import datetime, timezone

import clean_config as C
from db import connect, DB_PATH

RESULTS = []

FRESHNESS_MAX_HOURS = getattr(C, "FRESHNESS_MAX_HOURS", 6.0)

# Flags that mean "value not trusted" -> the cleaned value must be NULL
UNTRUSTED_FLAGS = ("missing_not_reported", "missing_variant_has_no_openings",
                   "missing_not_applicable", "invalid_range", "stale_snapshot",
                   "no_moves_played")

# (cleaned table, column) pairs checked by STOP 5
TRUST_COLUMNS = (
    ("tournament_clean", "status"), ("tournament_clean", "nb_players"),
    ("game_clean", "opening_key"), ("game_clean", "white_elo"), ("game_clean", "black_elo"),
    ("game_clean", "white_rating_diff"), ("game_clean", "black_rating_diff"),
    ("game_clean", "moves"),
)


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


def checks(con, run_dt: datetime):
    # ---- STOP 1: cleaned tables have the same row count as the raw tables
    for clean, raw in (("tournament_clean", "tournament"), ("game_clean", "game")):
        c = scalar(con, "SELECT COUNT(*) FROM " + clean)
        r = scalar(con, "SELECT COUNT(*) FROM " + raw)
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
    marks = ", ".join("?" * len(UNTRUSTED_FLAGS))
    leaks = []
    for table, col in TRUST_COLUMNS:
        n = scalar(con, "SELECT COUNT(*) FROM " + table + " WHERE " + col
                   + " IS NOT NULL AND " + col + "_flag IN (" + marks + ")", UNTRUSTED_FLAGS)
        if n:
            leaks.append(f"{table}.{col}={n}")
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
    empty = scalar(con, """SELECT COUNT(*) FROM tournament_clean c
        WHERE c.status = ? AND NOT EXISTS
              (SELECT 1 FROM game g WHERE g.tournament_key = c.tournament_key)""",
                   (C.TOURNAMENT_FINISHED,))
    record("WARN", empty == 0, "every finished tournament has games", f"{empty} without games")

    # ---- WARN 3: game start outside the tournament window (family 4, field comparison)
    early = "-" + str(C.WINDOW_GRACE_MINUTES) + " minutes"
    late = "+" + str(C.WINDOW_GRACE_MINUTES) + " minutes"
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

    # ---- WARN 5: data freshness relative to --run-time
    newest_played = scalar(con, "SELECT MAX(played_at) FROM game WHERE played_at IS NOT NULL")
    if not newest_played:
        record("WARN", False, "data freshness relative to --run-time", "no games in database")
    else:
        newest_dt = parse_iso_utc(newest_played)
        age_hours = (run_dt - newest_dt).total_seconds() / 3600.0
        record(
            "WARN",
            age_hours <= FRESHNESS_MAX_HOURS,
            "data freshness relative to --run-time",
            f"newest game {age_hours:.1f}h old (limit {FRESHNESS_MAX_HOURS:.0f}h, ref {run_dt.strftime('%Y-%m-%dT%H:%M:%SZ')})",
        )

    # ---- WARN 6: batch row volume anomaly (0 rows or >10x median batch size)
    batches = con.execute("""
        SELECT retrieved_at, COUNT(*) AS cnt
        FROM game
        GROUP BY retrieved_at
        ORDER BY retrieved_at
    """).fetchall()
    if not batches:
        record("WARN", False, "latest batch volume within normal range", "0 games in database")
    elif len(batches) == 1:
        record("WARN", True, "latest batch volume within normal range",
               f"latest batch {batches[0][1]:,} games (1 batch so far)")
    else:
        latest_cnt = batches[-1][1]
        prior_counts = [row[1] for row in batches[:-1]]
        median_cnt = statistics.median(prior_counts)
        is_anomaly = (latest_cnt == 0) or (median_cnt > 0 and latest_cnt > 10 * median_cnt)
        record(
            "WARN",
            not is_anomaly,
            "latest batch volume within normal range",
            f"latest {latest_cnt:,} vs median {median_cnt:,.0f} ({len(batches)} batches)",
        )


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
