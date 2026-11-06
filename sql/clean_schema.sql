
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS category_map (
    table_name   TEXT NOT NULL,
    column_name  TEXT NOT NULL,
    raw_value    TEXT NOT NULL,
    clean_value  TEXT,                         
    decided_by   TEXT NOT NULL,
    decided_at   TEXT NOT NULL,
    note         TEXT NOT NULL,                
    PRIMARY KEY (table_name, column_name, raw_value)
) STRICT;

CREATE TABLE IF NOT EXISTS player_alias (
    alias_source_id      TEXT NOT NULL PRIMARY KEY,
    canonical_source_id  TEXT NOT NULL,
    alias_player_key     INTEGER REFERENCES player(player_key),
    canonical_player_key INTEGER REFERENCES player(player_key),
    kind                 TEXT NOT NULL CHECK (kind IN ('name', 'source_id')),
    decided_by           TEXT NOT NULL,
    decided_at           TEXT NOT NULL,
    note                 TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS tournament_clean (
    tournament_key  INTEGER NOT NULL PRIMARY KEY REFERENCES tournament(tournament_key),

    status_raw      INTEGER,
    status          INTEGER,
    status_flag     TEXT NOT NULL CHECK (status_flag IN ('ok', 'stale_snapshot')),

    nb_players_raw  INTEGER,
    nb_players      INTEGER,
    nb_players_flag TEXT NOT NULL CHECK (nb_players_flag IN (
                        'ok', 'stale_snapshot', 'not_final', 'unusual_high')),

    max_rating_raw  INTEGER,
    max_rating      INTEGER,
    max_rating_flag TEXT NOT NULL CHECK (max_rating_flag IN ('ok', 'missing_not_applicable'))
) STRICT;

CREATE TABLE IF NOT EXISTS game_clean (
    game_id               TEXT NOT NULL PRIMARY KEY REFERENCES game(game_id),

    opening_key_raw       INTEGER,
    opening_key           INTEGER REFERENCES opening(opening_key),
    opening_key_flag      TEXT NOT NULL CHECK (opening_key_flag IN (
                              'ok', 'missing_variant_has_no_openings', 'missing_not_reported')),

    white_elo_raw         INTEGER,
    white_elo             INTEGER,
    white_elo_flag        TEXT NOT NULL CHECK (white_elo_flag IN ('ok', 'unusual_low', 'unusual_high')),
    black_elo_raw         INTEGER,
    black_elo             INTEGER,
    black_elo_flag        TEXT NOT NULL CHECK (black_elo_flag IN ('ok', 'unusual_low', 'unusual_high')),

    white_rating_diff_raw INTEGER,
    white_rating_diff     INTEGER,
    white_rating_diff_flag TEXT NOT NULL CHECK (white_rating_diff_flag IN ('ok', 'unusual_large')),
    black_rating_diff_raw INTEGER,
    black_rating_diff     INTEGER,
    black_rating_diff_flag TEXT NOT NULL CHECK (black_rating_diff_flag IN ('ok', 'unusual_large')),

    moves_raw             TEXT,
    moves                 TEXT,
    moves_flag            TEXT NOT NULL CHECK (moves_flag IN ('ok', 'no_moves_played'))
) STRICT;

CREATE VIEW IF NOT EXISTS player_resolved AS
SELECT p.player_key,
       COALESCE(a.canonical_player_key, p.player_key) AS resolved_player_key
FROM player p
LEFT JOIN player_alias a ON a.alias_player_key = p.player_key;

CREATE VIEW IF NOT EXISTS game_resolved AS
SELECT g.tournament_key,
       wr.resolved_player_key AS white_resolved_key,
       br.resolved_player_key AS black_resolved_key,
       g.result, g.played_at, g.termination,
       c.*
FROM game g
JOIN game_clean c ON c.game_id = g.game_id
JOIN player_resolved wr ON wr.player_key = g.white_player_key
JOIN player_resolved br ON br.player_key = g.black_player_key;

CREATE VIEW IF NOT EXISTS tournament_named AS
SELECT t.source_id, t.full_name, t.variant, t.starts_at, t.finishes_at, t.minutes,
       t.clock_limit, t.clock_increment, c.*
FROM tournament t
JOIN tournament_clean c ON c.tournament_key = t.tournament_key;
