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
    ("game_clean row count equals game",
     "DELETE FROM game_clean WHERE rowid = 1"),
    ("no impossible cleaned value",
     "UPDATE game_clean SET white_elo = -5 WHERE rowid = 1"),
    ("foreign_key_check",
     "UPDATE game SET tournament_key = 99999 WHERE rowid = 1"),
    ("_raw columns equal M2 values",
     "UPDATE game_clean SET white_elo_raw = 1 WHERE rowid = 1"),
    ("untrusted flags have NULL cleaned value",
     "UPDATE game_clean SET white_elo = 1500, white_elo_flag = 'invalid_range' WHERE rowid = 1"),
)


def main():
    failed_to_fail = 0
    env = {**os.environ, "PYTHONUTF8": "1"}   # child prints utf-8 regardless of console code page
    for name, sql in BREAKS:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            copy = Path(tmp) / "test.db"
            shutil.copy(DB_PATH, copy)
            con = sqlite3.connect(copy)   # plain connection: foreign keys stay OFF
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
            if res.returncode != 1 or not line.strip().startswith("STOP"):
                failed_to_fail += 1
    print("ALL STOP CHECKS CAN FAIL" if failed_to_fail == 0
          else f"PROBLEM: {failed_to_fail} check(s) did not fail")


if __name__ == "__main__":
    main()