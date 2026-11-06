"""Builds the cleaned layer from the M2 tables. Raw tables are never modified.

Rule order (the first rule that applies wins):
  tournament.status      : missing_not_reported > stale_snapshot > ok
  tournament.nb_players  : missing_not_reported > stale_snapshot > not_final > unusual_high > ok
  tournament.max_rating  : missing_not_applicable > ok
  game.opening_key       : missing_variant_has_no_openings > missing_not_reported > ok
  game.*_elo             : missing_not_reported > invalid_range > unusual_low / unusual_high > ok
  game.*_rating_diff     : missing_not_reported > invalid_range > unusual_large > ok
  game.moves             : no_moves_played > ok

Nothing is imputed: untrusted values become NULL, everything else is kept.
"""
import csv
import statistics
import sys
from collections import Counter, defaultdict

import clean_config as C
from db import connect, ROOT

CLEAN_SCHEMA = ROOT / "sql" / "clean_schema.sql"
MAPPINGS = ROOT / "data" / C.MAPPINGS_DIRNAME

TOURNAMENT_COLS = ("tournament_key", "status_raw", "status", "status_flag",
                   "nb_players_raw", "nb_players", "nb_players_flag",
                   "max_rating_raw", "max_rating", "max_rating_flag")
GAME_COLS = ("game_id", "opening_key_raw", "opening_key", "opening_key_flag",
             "white_elo_raw", "white_elo", "white_elo_flag",
             "black_elo_raw", "black_elo", "black_elo_flag",
             "white_rating_diff_raw", "white_rating_diff", "white_rating_diff_flag",
             "black_rating_diff_raw", "black_rating_diff", "black_rating_diff_flag",
             "moves_raw", "moves", "moves_flag")
SUMMARY_TABLES = (("tournament_clean", "tournament"), ("game_clean", "game"))


# ---------- helpers -------------------------------------------------------
def iqr_fences(values, k):
    """Return (lower, upper) IQR fences, or None if there are too few values."""
    if len(values) < 4:
        return None
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    iqr = q3 - q1
    return q1 - k * iqr, q3 + k * iqr


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"mapping file missing: {path}")
    # utf-8-sig strips a BOM if the file was saved from Excel
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def insert_sql(table, cols):
    """Build an INSERT with named columns and placeholders only (no values in the SQL text)."""
    return ("INSERT INTO " + table + " (" + ", ".join(cols) + ") VALUES ("
            + ", ".join("?" * len(cols)) + ")")


# ---------- tournament ----------------------------------------------------
def clean_tournaments(con):
    rows = con.execute(
        """SELECT tournament_key, status, nb_players, max_rating, variant,
                  finishes_at, retrieved_at FROM tournament""").fetchall()

    # Fences are computed per variant (levels differ); small groups fall back to the global fence.
    by_variant, all_finished = defaultdict(list), []
    for r in rows:
        if r["status"] == C.TOURNAMENT_FINISHED and r["nb_players"] is not None:
            by_variant[(r["variant"] or "").casefold()].append(r["nb_players"])
            all_finished.append(r["nb_players"])
    global_fence = (iqr_fences(all_finished, C.NB_PLAYERS_IQR_K)
                    if len(all_finished) >= C.NB_PLAYERS_MIN_GROUP else None)
    variant_fence = {v: (iqr_fences(vals, C.NB_PLAYERS_IQR_K)
                         if len(vals) >= C.NB_PLAYERS_MIN_GROUP else global_fence)
                     for v, vals in by_variant.items()}

    out = []
    for r in rows:
        st, nb, mr = r["status"], r["nb_players"], r["max_rating"]
        # finish time already passed when the snapshot was taken, but status is not "finished"
        # -> the snapshot is stale (inferred; the true final state is unknown)
        stale = (st is not None and st != C.TOURNAMENT_FINISHED
                 and r["finishes_at"] is not None
                 and r["finishes_at"] < r["retrieved_at"])
        running = (st is not None and st != C.TOURNAMENT_FINISHED and not stale)
        fence = variant_fence.get((r["variant"] or "").casefold(), global_fence)

        if st is None:
            st_c, st_f = None, "missing_not_reported"
        elif stale:
            st_c, st_f = None, "stale_snapshot"       # inferring the real status would be imputation
        else:
            st_c, st_f = st, "ok"

        if nb is None:
            nb_c, nb_f = None, "missing_not_reported"
        elif stale:
            nb_c, nb_f = None, "stale_snapshot"
        elif running:
            nb_c, nb_f = nb, "not_final"              # lower bound only; value kept
        elif fence and nb > fence[1]:
            nb_c, nb_f = nb, "unusual_high"           # unusual but possible: flag and keep
        else:
            nb_c, nb_f = nb, "ok"

        mr_f = "missing_not_applicable" if mr is None else "ok"
        out.append((r["tournament_key"], st, st_c, st_f, nb, nb_c, nb_f, mr, mr, mr_f))
    return out


# ---------- game ----------------------------------------------------------
def flag_elo(raw, fences):
    if raw is None:
        return None, "missing_not_reported"
    if not C.ELO_VALID_MIN <= raw <= C.ELO_VALID_MAX:
        return None, "invalid_range"
    if fences:
        if raw < fences[0]:
            return raw, "unusual_low"
        if raw > fences[1]:
            return raw, "unusual_high"
    return raw, "ok"


def flag_diff(raw, fences):
    if raw is None:
        return None, "missing_not_reported"
    if abs(raw) > C.RATING_DIFF_ABS_MAX:
        return None, "invalid_range"
    if fences and not fences[0] <= raw <= fences[1]:
        return raw, "unusual_large"
    return raw, "ok"


def clean_games(con):
    rows = con.execute(
        """SELECT g.game_id, g.tournament_key, g.opening_key, g.white_elo, g.black_elo,
                  g.white_rating_diff, g.black_rating_diff, g.moves, t.variant
           FROM game g JOIN tournament t ON t.tournament_key = g.tournament_key""").fetchall()

    # Fences use only valid values, so impossible values cannot distort them.
    elo_by_t, all_elo, diffs = defaultdict(list), [], []
    for r in rows:
        for e in (r["white_elo"], r["black_elo"]):
            if e is not None and C.ELO_VALID_MIN <= e <= C.ELO_VALID_MAX:
                elo_by_t[r["tournament_key"]].append(e)
                all_elo.append(e)
        for d in (r["white_rating_diff"], r["black_rating_diff"]):
            if d is not None and abs(d) <= C.RATING_DIFF_ABS_MAX:
                diffs.append(d)
    global_elo = iqr_fences(all_elo, C.ELO_IQR_K)
    elo_fence = {t: (iqr_fences(v, C.ELO_IQR_K) if len(v) >= C.ELO_MIN_GROUP else global_elo)
                 for t, v in elo_by_t.items()}
    diff_fence = iqr_fences(diffs, C.DIFF_IQR_K)

    out = []
    for r in rows:
        ok_raw = r["opening_key"]
        if ok_raw is not None:
            ok_f = "ok"
        elif (r["variant"] or "").casefold() not in C.STANDARD_VARIANTS:
            ok_f = "missing_variant_has_no_openings"
        else:
            ok_f = "missing_not_reported"

        ef = elo_fence.get(r["tournament_key"], global_elo)
        we, we_f = flag_elo(r["white_elo"], ef)
        be, be_f = flag_elo(r["black_elo"], ef)
        wd, wd_f = flag_diff(r["white_rating_diff"], diff_fence)
        bd, bd_f = flag_diff(r["black_rating_diff"], diff_fence)

        mv = r["moves"]
        mv_c, mv_f = (None, "no_moves_played") if not (mv and mv.strip()) else (mv, "ok")

        out.append((r["game_id"],
                    ok_raw, ok_raw, ok_f,
                    r["white_elo"], we, we_f,
                    r["black_elo"], be, be_f,
                    r["white_rating_diff"], wd, wd_f,
                    r["black_rating_diff"], bd, bd_f,
                    mv, mv_c, mv_f))
    return out


# ---------- mapping tables --------------------------------------------------
def load_mappings(con):
    report = {"player_alias": 0, "category_map": 0, "unmatched": []}

    rows = []
    for a in read_csv(MAPPINGS / "player_alias.csv"):
        keys = []
        for sid in (a["alias_source_id"], a["canonical_source_id"]):
            r = con.execute("SELECT player_key FROM player WHERE source_id = ?", (sid,)).fetchone()
            if r is None:
                report["unmatched"].append(sid)   # entry matched nothing in the player table
            keys.append(r[0] if r else None)
        rows.append((a["alias_source_id"], a["canonical_source_id"], keys[0], keys[1],
                     a["kind"], a["decided_by"], a["decided_at"], a["note"]))
    con.execute("DELETE FROM player_alias")
    con.executemany(
        """INSERT INTO player_alias (alias_source_id, canonical_source_id, alias_player_key,
               canonical_player_key, kind, decided_by, decided_at, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    report["player_alias"] = len(rows)

    cats = read_csv(MAPPINGS / "category_map.csv")
    con.execute("DELETE FROM category_map")
    con.executemany(
        """INSERT INTO category_map (table_name, column_name, raw_value, clean_value,
               decided_by, decided_at, note) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(c["table_name"], c["column_name"], c["raw_value"], c["clean_value"] or None,
          c["decided_by"], c["decided_at"], c["note"]) for c in cats])
    report["category_map"] = len(cats)
    return report


# ---------- write + summary -----------------------------------------------
def build(con):
    t_rows, g_rows = clean_tournaments(con), clean_games(con)
    with con:   # one transaction: on any error everything is rolled back
        report = load_mappings(con)
        con.execute("DELETE FROM tournament_clean")
        con.executemany(insert_sql("tournament_clean", TOURNAMENT_COLS), t_rows)
        con.execute("DELETE FROM game_clean")
        con.executemany(insert_sql("game_clean", GAME_COLS), g_rows)
    return report


def print_summary(con, report):
    def n(table):
        return con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    for clean, raw in SUMMARY_TABLES:
        print(f"{clean}: {n(clean):,} rows (raw: {n(raw):,})")
        flag_cols = [r[0] for r in con.execute(
            "SELECT name FROM pragma_table_info(?)", (clean,)) if r[0].endswith("_flag")]
        for c in flag_cols:
            counts = Counter({r[0]: r[1] for r in con.execute(
                "SELECT " + c + ", COUNT(*) FROM " + clean + " GROUP BY " + c)})
            print(f"  {c:24s} " + "  ".join(f"{k}={v:,}" for k, v in sorted(counts.items())))
    print(f"mappings: player_alias {report['player_alias']} rows | "
          f"category_map {report['category_map']} rows | "
          f"unmatched: {report['unmatched'] or 'none'}")


def main():
    con = connect()
    try:
        con.executescript(CLEAN_SCHEMA.read_text(encoding="utf-8"))   # IF NOT EXISTS: idempotent
        report = build(con)
        print_summary(con, report)
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            print("foreign_key_check FAILED:", fk)
            print("STATUS: FAILED")
            sys.exit(1)
        print("STATUS: COMPLETE")
    except Exception as e:
        print(f"ERROR: {e!r}")
        print("STATUS: FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()