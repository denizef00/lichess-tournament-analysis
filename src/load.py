import csv
import json
import re
from datetime import datetime, timezone

from db import connect, init_schema, ROOT

RAW = ROOT / "data" / "raw"
TS_RE = re.compile(r"(\d{8}T\d{6}Z)")
HEADER_RE = re.compile(r'^\[(\w+) "(.*)"\]$')
VALID_RESULTS = ("1-0", "0-1", "1/2-1/2")


def retrieved_at_of(path):
    m = TS_RE.search(path.name)
    if m:
        dt = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    else:
        dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_to_iso(ms):
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def pgn_to_iso(date_s, time_s):
    try:
        dt = datetime.strptime(date_s + " " + time_s, "%Y.%m.%d %H:%M:%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


class Stats:
    def __init__(self):
        self.cleaned = 0
        self.quarantined = 0
        self.files = []


def to_int(v, stats):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        stats.cleaned += 1
        return None


def pick(v, *keys):
    if isinstance(v, dict):
        for k in keys:
            if v.get(k) is not None:
                return v[k]
        return None
    return v


def count(con, table):
    return con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]


def newest_wins(store, key, rec):
    old = store.get(key)
    if old is None or rec["retrieved_at"] > old["retrieved_at"]:
        store[key] = rec

def read_tournaments(stats):
    out = {}
    for p in sorted((RAW / "tournaments").glob("*.json")):
        ra = retrieved_at_of(p)
        stats.files.append(ra)
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else [t for k in data.values() for t in k]
        for t in items:
            clock = t.get("clock") or {}
            rec = {
                "source_id": t["id"],
                "full_name": t.get("fullName") or t["id"],
                "starts_at": ms_to_iso(t.get("startsAt")),
                "finishes_at": ms_to_iso(t.get("finishesAt")),
                "minutes": to_int(t.get("minutes"), stats),
                "clock_limit": to_int(clock.get("limit"), stats),
                "clock_increment": to_int(clock.get("increment"), stats),
                "variant": pick(t.get("variant"), "name", "key"),
                "max_rating": to_int(pick(t.get("maxRating"), "rating"), stats),
                "nb_players": to_int(t.get("nbPlayers"), stats),
                "status": to_int(t.get("status"), stats),
                "retrieved_at": ra,
            }
            if rec["starts_at"] is None:
                stats.quarantined += 1
                continue
            newest_wins(out, rec["source_id"], rec)
    return list(out.values())


def read_openings(stats):
    out = {}
    for p in sorted((RAW / "openings").glob("*.tsv")):
        ra = retrieved_at_of(p)
        stats.files.append(ra)
        with p.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                if not row.get("eco") or not row.get("name") or not row.get("pgn"):
                    continue
                key = (row["eco"], row["name"], row["pgn"])
                newest_wins(out, key, {**row, "retrieved_at": ra})
    return list(out.values())


def tournament_id_from_filename(path):
    stem = path.name.split(".")[0]
    return TS_RE.sub("", stem).strip("_")


def split_pgn_games(text):
    text = text.replace("\r\n", "\n").strip()
    return [c.strip() for c in re.split(r"\n\s*\n(?=\[Event )", text) if c.strip()]


def parse_pgn_game(chunk):
    headers, move_lines = {}, []
    for line in chunk.splitlines():
        m = HEADER_RE.match(line.strip())
        if m:
            headers[m.group(1)] = m.group(2)
        elif line.strip():
            move_lines.append(line.strip())
    return headers, " ".join(move_lines)


def read_games(stats):
    out = {}
    for p in sorted((RAW / "games").iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        ra = retrieved_at_of(p)
        stats.files.append(ra)
        file_tid = tournament_id_from_filename(p)
        for chunk in split_pgn_games(p.read_text(encoding="utf-8")):
            h, moves = parse_pgn_game(chunk)
            gid = h.get("GameId") or h.get("Site", "").rstrip("/").split("/")[-1]
            if not gid:
                stats.quarantined += 1      
                continue
            g = {"id": gid, "_tid": file_tid, "h": h, "moves": moves,
                 "retrieved_at": ra}
            newest_wins(out, gid, g)
    return list(out.values())


def upsert_tournament(con, r):
    con.execute(
        """INSERT INTO tournament (source_id, full_name, starts_at, finishes_at, minutes,
               clock_limit, clock_increment, variant, max_rating, nb_players, status, retrieved_at)
           VALUES (:source_id, :full_name, :starts_at, :finishes_at, :minutes,
               :clock_limit, :clock_increment, :variant, :max_rating, :nb_players, :status, :retrieved_at)
           ON CONFLICT(source_id) DO UPDATE SET
               full_name=excluded.full_name, starts_at=excluded.starts_at,
               finishes_at=excluded.finishes_at, minutes=excluded.minutes,
               clock_limit=excluded.clock_limit, clock_increment=excluded.clock_increment,
               variant=excluded.variant, max_rating=excluded.max_rating,
               nb_players=excluded.nb_players, status=excluded.status,
               retrieved_at=excluded.retrieved_at
           WHERE excluded.retrieved_at > tournament.retrieved_at""", r)


def upsert_player(con, source_id, ra):
    con.execute(
        """INSERT INTO player (source_id, retrieved_at) VALUES (?, ?)
           ON CONFLICT(source_id) DO UPDATE SET retrieved_at=excluded.retrieved_at
           WHERE excluded.retrieved_at > player.retrieved_at""", (source_id, ra))
    return con.execute("SELECT player_key FROM player WHERE source_id=?", (source_id,)).fetchone()[0]


def upsert_opening(con, o):
    con.execute(
        """INSERT INTO opening (eco, name, retrieved_at) VALUES (?, ?, ?)
           ON CONFLICT(eco, name) DO UPDATE SET retrieved_at=excluded.retrieved_at
           WHERE excluded.retrieved_at > opening.retrieved_at""",
        (o["eco"], o["name"], o["retrieved_at"]))
    key = con.execute("SELECT opening_key FROM opening WHERE eco=? AND name=?",
                      (o["eco"], o["name"])).fetchone()[0]
    con.execute(
        """INSERT INTO opening_line (opening_key, pgn, retrieved_at) VALUES (?, ?, ?)
           ON CONFLICT(opening_key, pgn) DO UPDATE SET retrieved_at=excluded.retrieved_at
           WHERE excluded.retrieved_at > opening_line.retrieved_at""",
        (key, o["pgn"], o["retrieved_at"]))


def quarantine(con, stats, g, reason):
    stats.quarantined += 1
    con.execute(
        """INSERT INTO quarantine_game (game_id, reason, raw_record, retrieved_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(game_id, reason) DO UPDATE SET
               raw_record=excluded.raw_record, retrieved_at=excluded.retrieved_at
           WHERE excluded.retrieved_at > quarantine_game.retrieved_at""",
        (g["id"], reason, json.dumps(g["h"], ensure_ascii=False), g["retrieved_at"]))


def load_game(con, stats, g):
    h = g["h"]
    wname, bname = h.get("White"), h.get("Black")
    if not wname or not bname or wname in ("?", "Anonymous") or bname in ("?", "Anonymous"):
        return quarantine(con, stats, g, "missing_player")

    played_at = pgn_to_iso(h.get("UTCDate"), h.get("UTCTime"))
    if played_at is None:
        return quarantine(con, stats, g, "bad_timestamp")

    result = h.get("Result")
    if result not in VALID_RESULTS:
        return quarantine(con, stats, g, "bad_result")

    t = con.execute("SELECT tournament_key FROM tournament WHERE source_id=?", (g["_tid"],)).fetchone()
    if t is None:
        return quarantine(con, stats, g, "unknown_tournament")

    eco, oname = h.get("ECO"), h.get("Opening")
    opening_key = None                     
    if eco and oname and eco != "?" and oname != "?":
        o = con.execute("SELECT opening_key FROM opening WHERE eco=? AND name=?",
                        (eco, oname)).fetchone()
        if o is None:                       
            return quarantine(con, stats, g, "unknown_opening")
        opening_key = o[0]

    wk = upsert_player(con, wname, g["retrieved_at"])
    bk = upsert_player(con, bname, g["retrieved_at"])

    con.execute(
        """INSERT INTO game (game_id, tournament_key, white_player_key, black_player_key,
               opening_key, played_at, result, white_elo, black_elo, white_rating_diff,
               black_rating_diff, white_berserk, black_berserk, termination, moves, retrieved_at)
           VALUES (:game_id, :tk, :wk, :bk, :ok, :played_at, :result, :we, :be, :wd, :bd,
               :wb, :bb, :termination, :moves, :retrieved_at)
           ON CONFLICT(game_id) DO UPDATE SET
               tournament_key=excluded.tournament_key, white_player_key=excluded.white_player_key,
               black_player_key=excluded.black_player_key, opening_key=excluded.opening_key,
               played_at=excluded.played_at, result=excluded.result,
               white_elo=excluded.white_elo, black_elo=excluded.black_elo,
               white_rating_diff=excluded.white_rating_diff,
               black_rating_diff=excluded.black_rating_diff,
               white_berserk=excluded.white_berserk, black_berserk=excluded.black_berserk,
               termination=excluded.termination, moves=excluded.moves,
               retrieved_at=excluded.retrieved_at
           WHERE excluded.retrieved_at > game.retrieved_at""",
        {"game_id": g["id"], "tk": t[0], "wk": wk, "bk": bk, "ok": opening_key,
         "played_at": played_at, "result": result,
         "we": to_int(h.get("WhiteElo"), stats), "be": to_int(h.get("BlackElo"), stats),
         "wd": to_int(h.get("WhiteRatingDiff"), stats), "bd": to_int(h.get("BlackRatingDiff"), stats),
         "wb": 1 if h.get("WhiteBerserk") == "true" else 0,
         "bb": 1 if h.get("BlackBerserk") == "true" else 0,
         "termination": h.get("Termination"), "moves": g["moves"] or None,
         "retrieved_at": g["retrieved_at"]})


def load_all(con, stats, tournaments, openings, games):
    with con:
        for r in tournaments:
            upsert_tournament(con, r)
        for o in openings:
            upsert_opening(con, o)
        for g in games:
            load_game(con, stats, g)


def main():
    stats = Stats()
    con = connect()
    init_schema(con)

    tournaments = read_tournaments(stats)
    openings = read_openings(stats)
    games = read_games(stats)

    tables = ("tournament", "player", "opening", "game", "quarantine_game")
    before = {t: count(con, t) for t in tables}

    load_all(con, stats, tournaments, openings, games)

    after = {t: count(con, t) for t in tables}
    fk = con.execute("PRAGMA foreign_key_check").fetchall()

    def line(t):
        return f"{after[t]} in database ({after[t] - before[t]:+d} this run)"

    print(f"files read:        {len(stats.files)} raw files")
    print(f"tournaments:       {line('tournament')}")
    print(f"players:           {line('player')}")
    print(f"openings:          {line('opening')}")
    print(f"games:             {line('game')}")
    print(f"values cleaned:    {stats.cleaned} (non-numeric -> NULL)")
    print(f"quarantined:       {stats.quarantined} records")
    print(f"foreign_key_check: {'OK' if not fk else fk}")
    print("STATUS: COMPLETE" if not fk else "STATUS: FAILED")


if __name__ == "__main__":
    main()