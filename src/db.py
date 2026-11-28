import argparse
import pathlib
import sqlite3
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "lichess.db"
LOG_DB_PATH = ROOT / "data" / "pipeline_log.db"
SCHEMA_PATH = ROOT / "sql" / "schema.sql"


def connect(path=DB_PATH, create: bool = False):
    path = pathlib.Path(path)
    if not create and not path.exists():
        raise FileNotFoundError(f"Database bulunamadi: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path,timeout=30)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def init_schema(con):
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def get_log_conn() -> sqlite3.Connection:
    LOG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "logs").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(LOG_DB_PATH, timeout=30)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_run (
            run_id TEXT NOT NULL,
            run_time TEXT NOT NULL,
            step TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            duration_sec REAL NOT NULL,
            status TEXT NOT NULL,
            exit_code INTEGER NOT NULL,
            error_message TEXT,
            PRIMARY KEY (run_id, step)
        ) STRICT;
        """
    )
    con.commit()
    return con


def parse_run_time(argv=None) -> str:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--run-time",
        type=str,
        default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
    )
    args, _ = parser.parse_known_args(argv)
    return args.run_time