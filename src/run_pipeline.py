import argparse
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Script nerede çalıştırılırsa çalıştırılsın repo kök dizinini (lichess-tournament-analysis) bulur
REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = REPO_ROOT / "data" / "pipeline.lock"
LOG_DB_PATH = REPO_ROOT / "data" / "pipeline_log.db"

# Kişi B scriptlere --run-time eklediğinde burayı True yapın
PASS_RUN_TIME = False

STEPS = [
    ("ingest", REPO_ROOT / "src" / "ingest.py", 300),
    ("load", REPO_ROOT / "src" / "load.py", 180),
    ("clean", REPO_ROOT / "src" / "clean.py", 180),
    ("validate", REPO_ROOT / "src" / "validate.py", 120),
]


def init_log_db(conn: sqlite3.Connection) -> None:
    conn.execute(
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
    conn.commit()


def log_step(
    conn: sqlite3.Connection,
    run_id: str,
    run_time: str,
    step: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    exit_code: int,
    error_message: str | None = None,
) -> None:
    duration = (finished_at - started_at).total_seconds()
    conn.execute(
        """
        INSERT INTO pipeline_run (
            run_id, run_time, step, started_at, finished_at,
            duration_sec, status, exit_code, error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            run_time,
            step,
            started_at.isoformat(),
            finished_at.isoformat(),
            round(duration, 3),
            status,
            exit_code,
            error_message,
        ),
    )
    conn.commit()


def acquire_lock() -> bool:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()} started_at={datetime.now(timezone.utc).isoformat()}\n")
        return True
    except FileExistsError:
        return False


def release_lock() -> None:
    if LOCK_PATH.exists():
        LOCK_PATH.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automated Data Pipeline Runner")
    parser.add_argument(
        "--run-time",
        type=str,
        default=datetime.now(timezone.utc).isoformat(),
        help="Pipeline mantiksal calisma zamani (ISO-8601)",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Aga baglanmadan mevcut ham verilerle calistir",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    run_id = str(uuid.uuid4())[:8]

    if not acquire_lock():
        print(f"[LOCKED] Baska bir pipeline sureci calisiyor ({LOCK_PATH}). Cikis: 75", file=sys.stderr)
        return 75

    LOG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(LOG_DB_PATH)
    init_log_db(conn)

    try:
        for step_name, script_path, timeout_sec in STEPS:
            if step_name == "ingest" and args.skip_ingest:
                print(f"[{run_id}] {step_name}: SKIPPED (--skip-ingest)")
                continue

            cmd = [sys.executable, str(script_path)]
            if PASS_RUN_TIME:
                cmd.extend(["--run-time", args.run_time])

            started_at = datetime.now(timezone.utc)
            print(f"[{run_id}] {step_name} baslatiliyor: {' '.join(cmd)}")

            try:
                result = subprocess.run(
                    cmd,
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_sec,
                    check=False,
                )
                finished_at = datetime.now(timezone.utc)

                if result.stdout:
                    print(result.stdout.strip())

                if result.returncode != 0:
                    err_msg = (result.stderr or result.stdout or "Unknown error").strip()[-1000:]
                    print(f"[{run_id}] {step_name} HATA (kod={result.returncode}):\n{err_msg}", file=sys.stderr)
                    log_step(
                        conn, run_id, args.run_time, step_name,
                        started_at, finished_at, "FAILED", result.returncode, err_msg
                    )
                    return result.returncode

                log_step(conn, run_id, args.run_time, step_name, started_at, finished_at, "SUCCESS", 0, None)
                print(f"[{run_id}] {step_name}: SUCCESS")

            except subprocess.TimeoutExpired:
                finished_at = datetime.now(timezone.utc)
                err_msg = f"Step timed out after {timeout_sec}s"
                print(f"[{run_id}] {step_name} TIMEOUT (kod=124): {err_msg}", file=sys.stderr)
                log_step(
                    conn, run_id, args.run_time, step_name,
                    started_at, finished_at, "TIMEOUT", 124, err_msg
                )
                return 124

        print(f"[{run_id}] Tum pipeline basariyla tamamlandi.")
        return 0

    finally:
        conn.close()
        release_lock()


if __name__ == "__main__":
    sys.exit(main())