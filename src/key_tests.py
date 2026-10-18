"""Key tests (assignment 2.2): run on the landed raw data and the built database.

Usage (from the repository root, after `python src/load.py`):
    python src/key_tests.py
"""
import collections
import csv
import pathlib
import re
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "lichess.db"
HEADER_RE = re.compile(r'^\[(\w+) "(.*)"\]$')


def read_game_headers():
    blocks = []
    for p in sorted((RAW / "games").iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        text = p.read_text(encoding="utf-8").replace("\r\n", "\n")
        for chunk in re.split(r"\n\s*\n(?=\[Event )", text.strip()):
            h = {}
            for line in chunk.splitlines():
                m = HEADER_RE.match(line.strip())
                if m:
                    h[m.group(1)] = m.group(2)
            if h:
                blocks.append(h)
    return blocks


def main():
    blocks = read_game_headers()
    by_id = collections.defaultdict(list)
    for h in blocks:
        by_id[h.get("GameId", "")].append(h)

    print("== GameId ==")
    print("game blocks in raw files:", len(blocks))
    print("distinct GameId:", len(by_id))
    print("re-fetched copies (blocks - distinct):", len(blocks) - len(by_id))
    changing = 0
    for gid, hs in by_id.items():
        if len(hs) > 1:
            for field in ("Result", "WhiteElo", "BlackElo", "ECO", "Opening"):
                if len({x.get(field) for x in hs}) > 1:
                    changing += 1
                    break
    print("GameIds whose Result/Elo/ECO/Opening differ between copies:", changing)

    games = [hs[0] for hs in by_id.values()]
    print()
    print("== Missing or odd values (distinct games) ==")
    no_open = sum(1 for h in games if h.get("ECO") in (None, "", "?") or h.get("Opening") in (None, "", "?"))
    print("games without ECO/Opening (opening_key will be NULL):", no_open)
    print("games with non-numeric WhiteElo/BlackElo:",
          sum(1 for h in games if not (h.get("WhiteElo", "").isdigit() and h.get("BlackElo", "").isdigit())))
    print("games with Result not in 1-0 / 0-1 / 1/2-1/2:",
          sum(1 for h in games if h.get("Result") not in ("1-0", "0-1", "1/2-1/2")))

    names = [n for h in games for n in (h.get("White"), h.get("Black")) if n]
    print()
    print("== Usernames ==")
    print("distinct usernames:", len(set(names)))
    print("distinct usernames, case-insensitive:", len({n.lower() for n in names}))

    print()
    print("== Opening reference (TSV) ==")
    rows = set()
    for f in sorted((RAW / "openings").glob("*.tsv")):
        with f.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                rows.add((r["eco"], r["name"], r["pgn"]))
    pgn_names = collections.defaultdict(set)
    for eco, name, pgn in rows:
        pgn_names[pgn].add((eco, name))
    print("same move sequence under more than one (eco, name):",
          sum(1 for v in pgn_names.values() if len(v) > 1))

    print()
    print("== Database ==")
    con = sqlite3.connect(DB)
    print("games played outside their tournament's starts_at..finishes_at:",
          con.execute(
              """SELECT COUNT(*) FROM game g
                 JOIN tournament t ON t.tournament_key = g.tournament_key
                 WHERE t.finishes_at IS NOT NULL
                   AND (g.played_at < t.starts_at OR g.played_at > t.finishes_at)"""
          ).fetchone()[0])
    print("games in database:", con.execute("SELECT COUNT(*) FROM game").fetchone()[0])
    print("games with opening_key NULL:",
          con.execute("SELECT COUNT(*) FROM game WHERE opening_key IS NULL").fetchone()[0])
    print("quarantined games by reason:",
          con.execute("SELECT reason, COUNT(*) FROM quarantine_game GROUP BY reason").fetchall())
    con.close()


if __name__ == "__main__":
    main()