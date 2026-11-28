"""Completeness report: tournaments per finish hour, same calendar and rule as monitor.py (read-only).

Usage: python src/completeness_report.py [--hours 6] [--now ISO_TIMESTAMP]
"""
import argparse
from datetime import datetime, timedelta, timezone

import clean_config as C
from monitor import (COMPLETENESS_GRACE_MINUTES, DATA_DB_PATH, MIN_TOURNAMENTS_PER_HOUR,
                     TOURNAMENT_NAME_PATTERN, connect_ro, floor_hour, iso_hour, parse_iso_utc)

SQL = """
WITH RECURSIVE periods(p) AS (
    SELECT :first_period
    UNION ALL
    SELECT strftime('%Y-%m-%dT%H:00:00Z', p, '+1 hour') FROM periods WHERE p < :last_period
)
SELECT p,
       COUNT(t.tournament_key),
       COALESCE(SUM(t.status = :finished), 0),
       COALESCE(SUM(t.full_name LIKE :pattern), 0),
       COALESCE(SUM(t.full_name LIKE :pattern AND EXISTS
                    (SELECT 1 FROM game g WHERE g.tournament_key = t.tournament_key)), 0)
FROM periods
LEFT JOIN tournament t ON strftime('%Y-%m-%dT%H:00:00Z', t.finishes_at) = p
GROUP BY p
ORDER BY p
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Tournaments per finish hour (read-only)")
    parser.add_argument("--hours", type=int, default=6)
    parser.add_argument("--now", type=str, default=datetime.now(timezone.utc).isoformat())
    args = parser.parse_args()

    now_dt = parse_iso_utc(args.now)
    last = floor_hour(now_dt)
    first = last - timedelta(hours=args.hours - 1)
    last_due = iso_hour(floor_hour(now_dt - timedelta(minutes=COMPLETENESS_GRACE_MINUTES)))
    con = connect_ro(DATA_DB_PATH)
    try:
        rows = con.execute(SQL, {
            "first_period": iso_hour(first),
            "last_period": iso_hour(last),
            "pattern": TOURNAMENT_NAME_PATTERN,
            "finished": C.TOURNAMENT_FINISHED,
        }).fetchall()
    finally:
        con.close()

    print(f"as of {now_dt:%Y-%m-%d %H:%M}Z; monitor needs >= {MIN_TOURNAMENTS_PER_HOUR} finished per hour; "
          f"hours after {last_due[:13]}h are not due yet (grace {COMPLETENESS_GRACE_MINUTES} min)")
    print(f"{'hour (UTC)':22s}{'listed':>8s}{'finished':>10s}{'hourly_bullet':>15s}{'hb_with_games':>15s}")
    for period, listed, finished, hb, hb_games in rows:
        if period > last_due:
            flag = "  (not due yet)"
        elif finished < MIN_TOURNAMENTS_PER_HOUR:
            flag = "  <-- below minimum"
        else:
            flag = ""
        print(f"{period:22s}{listed:8d}{finished:10d}{hb:15d}{hb_games:15d}{flag}")


if __name__ == "__main__":
    main()