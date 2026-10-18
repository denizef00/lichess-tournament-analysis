-- M2 schema: Lichess opening outcomes pipeline (SQLite >= 3.37, STRICT tables)
-- Safe to run more than once (CREATE TABLE IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS tournament (
    tournament_key  INTEGER PRIMARY KEY,
    source_id       TEXT NOT NULL UNIQUE,      -- Lichess tournament id
    full_name       TEXT NOT NULL,
    starts_at       TEXT NOT NULL,             -- ISO 8601 UTC, e.g. '2026-10-09T17:30:20Z'
    finishes_at     TEXT,
    minutes         INTEGER,
    clock_limit     INTEGER,                   -- seconds
    clock_increment INTEGER,                   -- seconds
    variant         TEXT,
    max_rating      INTEGER,                   -- NULL = no rating cap
    nb_players      INTEGER,
    status          INTEGER,                   -- 10 created, 20 started, 30 finished
    retrieved_at    TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS player (
    player_key   INTEGER PRIMARY KEY,
    source_id    TEXT NOT NULL UNIQUE,         -- Lichess username (removed in M3)
    retrieved_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS opening (
    opening_key  INTEGER PRIMARY KEY,
    eco          TEXT NOT NULL,
    name         TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE (eco, name)                         -- composite: eco alone is NOT unique
) STRICT;

CREATE TABLE IF NOT EXISTS opening_line (
    opening_key  INTEGER NOT NULL REFERENCES opening(opening_key),
    pgn          TEXT    NOT NULL,             -- move sequence, e.g. '1. f3 d5 2. e4 ...'
    retrieved_at TEXT    NOT NULL,
    PRIMARY KEY (opening_key, pgn)
) STRICT;

CREATE TABLE IF NOT EXISTS game (
    game_id           TEXT    NOT NULL PRIMARY KEY,   -- Lichess GameId (identifies one event)
    tournament_key    INTEGER NOT NULL REFERENCES tournament(tournament_key),
    white_player_key  INTEGER NOT NULL REFERENCES player(player_key),
    black_player_key  INTEGER NOT NULL REFERENCES player(player_key),
    opening_key       INTEGER REFERENCES opening(opening_key),  -- NULL = no opening recorded (no ECO/Opening tag or '?')
    played_at         TEXT    NOT NULL,               -- UTCDate + UTCTime -> '2026-10-08T11:55:37Z'
    result            TEXT    NOT NULL CHECK (result IN ('1-0','0-1','1/2-1/2')),
    white_elo         INTEGER,
    black_elo         INTEGER,
    white_rating_diff INTEGER,                        -- post-game change: must NOT be a model feature in M4
    black_rating_diff INTEGER,
    white_berserk     INTEGER NOT NULL DEFAULT 0 CHECK (white_berserk IN (0,1)),
    black_berserk     INTEGER NOT NULL DEFAULT 0 CHECK (black_berserk IN (0,1)),
    termination       TEXT,
    moves             TEXT,
    retrieved_at      TEXT    NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS quarantine_game (
    quarantine_id INTEGER PRIMARY KEY,
    game_id       TEXT NOT NULL,
    reason        TEXT NOT NULL,                      -- 'unknown_opening' | 'unknown_tournament' | 'bad_result'
    raw_record    TEXT NOT NULL,                      -- PGN headers as JSON
    retrieved_at  TEXT NOT NULL,
    UNIQUE (game_id, reason)                          -- re-runs must not create duplicates
) STRICT;