CREATE TABLE IF NOT EXISTS tournament (
    tournament_key  INTEGER PRIMARY KEY,
    source_id       TEXT NOT NULL UNIQUE,      
    full_name       TEXT NOT NULL,
    starts_at       TEXT NOT NULL,             
    finishes_at     TEXT,
    minutes         INTEGER,
    clock_limit     INTEGER,                   
    clock_increment INTEGER,                   
    variant         TEXT,
    max_rating      INTEGER,                  
    nb_players      INTEGER,
    status          INTEGER,                   
    retrieved_at    TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS player (
    player_key   INTEGER PRIMARY KEY,
    source_id    TEXT NOT NULL UNIQUE,         
    retrieved_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS opening (
    opening_key  INTEGER PRIMARY KEY,
    eco          TEXT NOT NULL,
    name         TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE (eco, name)                         
) STRICT;

CREATE TABLE IF NOT EXISTS opening_line (
    opening_key  INTEGER NOT NULL REFERENCES opening(opening_key),
    pgn          TEXT    NOT NULL,             
    retrieved_at TEXT    NOT NULL,
    PRIMARY KEY (opening_key, pgn)
) STRICT;

CREATE TABLE IF NOT EXISTS game (
    game_id           TEXT    NOT NULL PRIMARY KEY,   
    tournament_key    INTEGER NOT NULL REFERENCES tournament(tournament_key),
    white_player_key  INTEGER NOT NULL REFERENCES player(player_key),
    black_player_key  INTEGER NOT NULL REFERENCES player(player_key),
    opening_key       INTEGER REFERENCES opening(opening_key),  
    played_at         TEXT    NOT NULL,               
    result            TEXT    NOT NULL CHECK (result IN ('1-0','0-1','1/2-1/2')),
    white_elo         INTEGER,
    black_elo         INTEGER,
    white_rating_diff INTEGER,                        
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
    reason        TEXT NOT NULL,                      
    raw_record    TEXT NOT NULL,                      
    retrieved_at  TEXT NOT NULL,
    UNIQUE (game_id, reason)                          
) STRICT;
