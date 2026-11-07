"""Breaks a temporary copy of the database once per STOP check and runs validate.py on it.

The real database is never touched. Usage: python src/test_stops.py
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from db import DB_PATH, ROOT

VALIDATE = ROOT / "src" / "validate.py"

# (check name as printed by validate.py, statement that breaks the copy)
BREAKS = (
    ("game_clean row count equals game", "STOP",
     "DELETE FROM game_clean WHERE rowid = 1"),
    ("no impossible cleaned value", "STOP",
     "UPDATE game_clean SET white_elo = -5 WHERE rowid = 1"),
    ("foreign_key_check", "STOP",
     "UPDATE game SET tournament_key = 99999 WHERE rowid = 1"),
    ("_raw columns equal M2 values", "STOP",
     "UPDATE game_clean SET white_elo_raw = 1 WHERE rowid = 1"),
    ("untrusted flags have NULL cleaned value", "STOP",
     "UPDATE game_clean SET white_elo = 1500, white_elo_flag = 'invalid_range' WHERE rowid = 1"),
    ("tournament_clean row count equals tournament", "STOP",
     "DELETE FROM tournament_clean WHERE rowid = 1"),
    ("usable elo share >= 90% per tournament", "WARN",
     """UPDATE game_clean SET white_elo = NULL, white_elo_flag = 'missing_not_reported'
        WHERE game_id IN (SELECT game_id FROM game WHERE tournament_key =
            (SELECT tournament_key FROM game GROUP BY tournament_key
             ORDER BY COUNT(*) DESC LIMIT 1))"""),
    ("games inside tournament window", "WARN",
     "UPDATE game SET played_at = '2000-01-01T00:00:00Z' WHERE rowid = 1"),
    ("all player aliases resolve", "WARN",
     """INSERT INTO player_alias (alias_source_id, canonical_source_id, alias_player_key,
            canonical_player_key, kind, decided_by, decided_at, note)
        VALUES ('ghost_a', 'ghost_b', NULL, NULL, 'test', 'test', '2026-01-01T00:00:00Z', '')"""),
)

def main():
    failed_to_fail = 0
    env = {**os.environ, "PYTHONUTF8": "1"}   # child prints utf-8 regardless of console code page
    for name, level, sql in BREAKS:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            copy = Path(tmp) / "test.db"
            shutil.copy(DB_PATH, copy)
            con = sqlite3.connect(copy)   # plain connection: foreign keys stay OFF
            con.execute("PRAGMA ignore_check_constraints = ON")   # copy only: lets us write broken rows on purpose
            try:
                with con:
                    con.execute(sql)
            finally:
                con.close()               # always close, or Windows cannot delete the temp file
            res = subprocess.run([sys.executable, str(VALIDATE), str(copy)],
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", env=env)
            line = next((l for l in res.stdout.splitlines() if name in l), "(line not found)")
            print(f"exit={res.returncode} | {line.strip()}")
            want_exit = 1 if level == "STOP" else 0
            if res.returncode != want_exit or not line.strip().startswith(level):
                failed_to_fail += 1
    print("ALL CHECKS FIRE AS DESIGNED" if failed_to_fail == 0
          else f"PROBLEM: {failed_to_fail} check(s) did not behave as expected")

if __name__ == "__main__":
    main()