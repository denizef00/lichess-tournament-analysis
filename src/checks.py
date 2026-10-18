import pathlib
import shutil
import sqlite3
import sys
import tempfile

from db import connect, DB_PATH
import load


def get_table_counts(con: sqlite3.Connection) -> dict[str, int]:
    tables = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    return {t: con.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in tables}


def test_duplicate_natural_key(con: sqlite3.Connection) -> bool:
    try:
        row = con.execute("SELECT source_id, full_name, starts_at, retrieved_at FROM tournament LIMIT 1").fetchone()
        if not row:
            con.execute(
                """INSERT INTO tournament (source_id, full_name, starts_at, retrieved_at)
                   VALUES (?, ?, ?, ?)""",
                ("test_tourney_unique", "Test Arena", "2026-10-18T10:00:00Z", "2026-10-18T10:00:00Z"),
            )
            dup_id = "test_tourney_unique"
        else:
            dup_id = row["source_id"]

        con.execute(
            """INSERT INTO tournament (source_id, full_name, starts_at, retrieved_at)
               VALUES (?, ?, ?, ?)""",
            (dup_id, "Duplicate Arena", "2026-10-18T11:00:00Z", "2026-10-18T11:00:00Z"),
        )
        return False
    except sqlite3.IntegrityError:
        return True


def test_orphan_event(con: sqlite3.Connection) -> bool:
    try:
        p_row = con.execute("SELECT player_key FROM player LIMIT 1").fetchone()
        o_row = con.execute("SELECT opening_key FROM opening LIMIT 1").fetchone()

        p_key = p_row["player_key"] if p_row else 1
        o_key = o_row["opening_key"] if o_row else None

        con.execute(
            """INSERT INTO game (
                   game_id, tournament_key, white_player_key, black_player_key,
                   opening_key, played_at, result, retrieved_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("orphan_game_test", -9999, p_key, p_key, o_key, "2026-10-18T12:00:00Z", "1-0", "2026-10-18T12:00:00Z"),
        )
        return False
    except sqlite3.IntegrityError:
        return True


def test_null_key(con: sqlite3.Connection) -> bool:
    try:
        t_row = con.execute("SELECT tournament_key FROM tournament LIMIT 1").fetchone()
        p_row = con.execute("SELECT player_key FROM player LIMIT 1").fetchone()

        t_key = t_row["tournament_key"] if t_row else 1
        p_key = p_row["player_key"] if p_row else 1

        con.execute(
            """INSERT INTO game (
                   game_id, tournament_key, white_player_key, black_player_key,
                   opening_key, played_at, result, retrieved_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (None, t_key, p_key, p_key, None, "2026-10-18T12:00:00Z", "1-0", "2026-10-18T12:00:00Z"),
        )
        return False
    except sqlite3.IntegrityError:
        return True


def test_wrong_type(con: sqlite3.Connection) -> bool:
    try:
        t_row = con.execute("SELECT tournament_key FROM tournament LIMIT 1").fetchone()
        p_row = con.execute("SELECT player_key FROM player LIMIT 1").fetchone()

        t_key = t_row["tournament_key"] if t_row else 1
        p_key = p_row["player_key"] if p_row else 1

        con.execute(
            """INSERT INTO game (
                   game_id, tournament_key, white_player_key, black_player_key,
                   opening_key, played_at, result, white_elo, retrieved_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "strict_type_game_test",
                t_key,
                p_key,
                p_key,
                None,
                "2026-10-18T12:00:00Z",
                "1-0",
                "invalid_elo_string",
                "2026-10-18T12:00:00Z",
            ),
        )
        return False
    except (sqlite3.IntegrityError, sqlite3.OperationalError):
        return True


def test_idempotence(temp_db_path: pathlib.Path) -> bool:
    con1 = connect(temp_db_path)
    before_counts = get_table_counts(con1)
    con1.close()

    stats = load.Stats()
    tournaments = load.read_tournaments(stats)
    openings = load.read_openings(stats)
    games = load.read_games(stats)

    con_temp = connect(temp_db_path)
    load.load_all(con_temp, stats, tournaments, openings, games)
    con_temp.close()

    con2 = connect(temp_db_path)
    after_counts = get_table_counts(con2)
    con2.close()

    return before_counts == after_counts


def test_atomicity(temp_db_path: pathlib.Path) -> bool:
    con1 = connect(temp_db_path)
    before_counts = get_table_counts(con1)

    try:
        with con1:
            con1.execute(
                """INSERT INTO player (source_id, retrieved_at)
                   VALUES (?, ?)""",
                ("atomic_rollback_test_user", "2026-10-18T12:00:00Z"),
            )
            con1.execute(
                """INSERT INTO tournament (source_id, full_name, starts_at, minutes, retrieved_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("faulty_tourney", "Faulty Arena", "2026-10-18T12:00:00Z", "invalid_minutes", "2026-10-18T12:00:00Z"),
            )
    except Exception:
        pass
    finally:
        con1.close()

    con2 = connect(temp_db_path)
    after_counts = get_table_counts(con2)
    con2.close()

    return before_counts == after_counts


def main():
    if not DB_PATH.exists():
        print(f"ERROR: Database file not found ({DB_PATH}).")
        print("Please run `python src/load.py` first to initialize the database.")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_db = pathlib.Path(tmp_dir) / "check_temp.db"
        shutil.copyfile(DB_PATH, temp_db)

        con = connect(temp_db)
        checks = [
            ("Duplicate natural key", test_duplicate_natural_key(con)),
            ("Orphan event", test_orphan_event(con)),
            ("NULL key", test_null_key(con)),
            ("Wrong type (STRICT)", test_wrong_type(con)),
        ]
        con.close()

        checks.append(("Idempotence", test_idempotence(temp_db)))
        checks.append(("Atomicity", test_atomicity(temp_db)))

        all_passed = True
        for name, passed in checks:
            status = "PASS" if passed else "FAIL"
            if not passed:
                all_passed = False
            print(f"{name:<25}: {status}")

        if not all_passed:
            sys.exit(1)


if __name__ == "__main__":
    main()