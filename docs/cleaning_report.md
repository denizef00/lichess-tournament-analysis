# Cleaning Report — Lichess Tournament Analysis
Team: Sudenaz Yönezer, Deniz Efe Doğan

## 1. Summary
The data are Lichess arena tournaments: 278 tournaments, 1,307 players, 3,332 openings and 4,535 games, collected on 8–9 October 2026. Games exist for only 7 of the 40 finished tournaments, so Atomic and Chess960 each come from a single tournament and variant effects cannot be separated from tournament effects. The most important quality problems are 116 stale tournament snapshots (fetched once, on 8 October, for tournaments that had ended by the last fetch on 9 October), 182 tournaments with nb_players = 0 that had not started yet, and 106 abandoned games that carry a decisive result although no move was played. The cleaned layer (tournament_clean, game_clean) has the same grain, keys and row counts as the raw tables (278 and 4,535), keeps every raw value next to the cleaned one, gives the reason for each difference in a flag column, and imputes nothing. We cannot say why games were collected for only 7 tournaments, what the true final state of the stale tournaments was, or whether the 49 missing Standard-chess openings are random.
M2 issues fixed since the M2 tag: None.

## 2. Profile highlights
Full profile: notebooks/m3_profile.ipynb. It reads the database read-only and runs top to bottom on a fresh python src/load.py build.

| Finding | Evidence (notebook section) |
|---|---|
| Games cover 7 of 278 tournaments | 1a: all 7 are status 30; 33 of 40 finished tournaments have no games |
| Stale snapshots | 1b: 116 tournaments with status ≠ 30 whose finishes_at is before the latest retrieval (2026-10-09T18:19:39Z); all were retrieved 2026-10-08T12:06:52Z (102 status 10, 14 status 20) |
| nb_players = 0 only for status 10 | 1b: 182 rows, all status 10; 27 status-10 rows already have players (pre-registration) |
| max_rating NULL means "no cap" | 1c: none of the 199 NULL rows has ≤ in its name; all 79 rows with ≤ carry a value equal to the cap in the name |
| opening_key NULL depends on variant | 1d: Atomic 237/237, Chess960 101/101, Standard 49 of 4,197 |
| Rating level differs per variant | 3a: elo median/MAD Atomic 1550.5/222.5, Chess960 1854/196, Standard 1863/204 |
| Abandoned games with a decisive result | 4: 106 games (55 0-1, 51 1-0); moves per game min 0, median 0, max 1 |
| Registered players without a game | 4: between 1 and 21 per tournament; nb_players is never below the players seen in games |

## 3. Issues register

| ID | Family | Table.column | What | Where / when / how many | Evidence | Cause | Decision | Flag | Investigated by |
|---|---|---|---|---|---|---|---|---|---|
| I-01 | 1 Missing, 4 Sequence | tournament.status, nb_players | Snapshot older than the tournament's end | 116 tournaments, all fetched 2026-10-08T12:06:52Z | Latest fetch 2026-10-09T18:19:39Z is after their finishes_at; each tournament was fetched once | Inferred: one early fetch, never refreshed | NULL, row kept | stale_snapshot | Sudenaz Yönezer |
| I-02 | 1 Missing | tournament.nb_players | Value 0 | 182 tournaments, all status 10 | 1b crosstab; 27 status-10 rows have players > 0 | Inferred: placeholder before the tournament started | Value kept, marked not final | not_final | Deniz Efe Doğan |
| I-03 | 1 Missing | tournament.max_rating | NULL | 199 tournaments | No NULL row has ≤ in its name; all 79 ≤ rows equal the cap | Inferred: tournament has no rating cap | NULL kept | missing_not_applicable | Deniz Efe Doğan |
| I-04 | 1 Missing | game.opening_key | NULL | 387 games: Atomic 237, Chess960 101, Standard 49 | 1d crosstab | Atomic/Chess960: inferred (ECO openings describe standard chess). Standard 49: unknown | NULL kept | missing_variant_has_no_openings (338), missing_not_reported (49) | Deniz Efe Doğan |
| I-05 | 1 Missing | game (coverage) | Finished tournaments without games | 33 of 40 finished tournaments | 1a; validate.py WARN | Unknown: games were collected for a subset only | Documented; M4 restricted to the 7 tournaments | n/a (WARN) | Sudenaz Yönezer |
| I-06 | 3 Unusual | game.*_elo, *_rating_diff | Extreme but possible values | 74 low / 26 high white elo, 75 low / 28 high black elo; 60 white diff / 54 black diff | Robust z per variant (notebook), IQR fences per tournament (clean.py); 5.5% of flagged diffs have elo < 1000 vs 0.8% overall; 1500 is the most frequent elo (25 times) | Inferred: low-rated players' ratings move more; 1500 is the Lichess starting rating | Kept, flagged | unusual_low, unusual_high, unusual_large | Deniz Efe Doğan |
| I-07 | 1 Missing | quarantine_game | Games not loaded into game | 4 games, reason unknown_opening (ECO A51, A52, C11) | Their opening names are not in the opening table | Inferred: the opening list is incomplete | Games stay in quarantine_game | n/a | Sudenaz Yönezer |
| I-08 | 4 Cross-field | game.moves, result, termination | Decisive result without a move played | 106 abandoned games (55 0-1, 51 1-0) | n_moves min 0, median 0, max 1 for Abandoned; moves holds only the result token, so it is not empty | Inferred: Lichess awards the result after a player abandons | Moves set to NULL when only the result token is present; result kept | no_moves_played | Sudenaz Yönezer |
| I-09 | 4 Cross-field | game.white_elo, black_elo, tournament.max_rating | Elo above the tournament cap | 1 game, overshoot 1 point | Notebook section 4 | Inferred: ratings change during the tournament, the cap applies at entry | Kept, no flag | n/a | Deniz Efe Doğan |
| I-10 | 6 Identity | opening.name, eco | Same opening name under several ECO codes | 108 names (e.g. Alekhine Defense B02 / B03) | The opening table is keyed by (eco, name); no casefold variants; no PGN line belongs to two openings | Confirmed by the table design: ECO codes classify lines, names repeat | Not merged; no mapping row | n/a | Sudenaz Yönezer |

### Checked and not found

| Family | Check | Result |
|---|---|---|
| 1 Missing | NULL elo / rating_diff / termination / moves in game | 0 |
| 1 Missing | Empty strings in termination, moves | 0 |
| 2 Invalid | Negative or zero elo | 0 |
| 2 Invalid | Sentinel elo (−1, 9999, 99999) | 0 |
| 2 Invalid | Negative nb_players, non-positive minutes, negative clock values | 0 |
| 2 Invalid | Clock 0+00 | 0 (the two tournaments with clock_limit = 0 have a 2 s increment) |
| 2 Invalid | finishes_at before starts_at | 0 |
| 2 Invalid | game played outside its tournament window | 0 (without tolerance; validate.py allows 10 minutes) |
| 2 Invalid | Timestamps in the future | 0 |
| 2 Invalid | ECO code outside A00–E99 | 0 |
| 2 Invalid | rating_diff above 500 in absolute value | 0 (largest observed is 424) |
| 4 Cross-field | Same player on both sides | 0 |
| 4 Cross-field | Winner loses rating or loser gains rating | 0 |
| 4 Cross-field | nb_players below the players seen in games | 0 |
| 4 Cross-field | Tournament duration differs from minutes by more than 1 minute | 0 |
| 4 Cross-field | Tournament name speed category differs from the clock | 0 of 146 (SuperBlitz excluded, not a Lichess category) |
| 4 Sequence | Same player twice at the same second | 0 |
| 5 Text | Case or spelling variants of variant / termination | 0 |
| 5 Text | Non-NFC text, stray whitespace in key columns | 0 |
| 6 Identity | Players equal ignoring case, NFC + casefold | 0 |
| 6 Identity | Duplicate tournaments (name + start time) | 0 |
| 6 Identity | Players that never appear in a game | 0 |
| All | PRAGMA foreign_key_check | 0 rows |

## 4. Missing values
Stale snapshots (I-01). All 116 stale rows come from one early fetch, so whether a value is missing or outdated depends on when the tournament was fetched, not on the tournament's own player count or status. We treat this as missing at random given the fetch time. For M4 this means any tournament-level comparison must exclude stale_snapshot rows, because their status and nb_players describe a tournament that had not finished.
nb_players = 0 (I-02). All 182 zeros are status 10, and 27 other status-10 rows already show registrations. The zero is therefore a placeholder explained by an observed variable (status), not a measurement. Averaging nb_players across statuses would be wrong; M4 should use only status 30 rows that are not stale.
max_rating NULL (I-03). The column is structurally empty for uncapped tournaments. M4 must read NULL as "no cap", never as unknown.
opening_key NULL (I-04). For Atomic and Chess960 the opening is missing for every game, which is explained by variant. For Standard chess 49 of 4,197 games have none and we do not know why. Opening analysis is only valid on Standard games, and the 49 unexplained cases may be non-random (unknown).
Games missing for 33 finished tournaments (I-05). We cannot establish a mechanism. We suspect the game download covered a subset of tournaments, but nothing in the sources states it, so the cause is unknown and missing-not-at-random cannot be excluded. Every tournament-level result in M4 covers only 7 tournaments.
Registered players without a game. Between 1 and 21 players per tournament are counted in nb_players but appear in no game. This is consistent with players who joined and never played, so the cleaned nb_players is not the number of players in the games.

## 5. Flags and rules
Rules are applied per column in the order below; the first rule that applies wins. The full per-column order is the docstring of src/clean.py.

| Order | Flag | Meaning | Rule | Cleaned value | Count |
|---|---|---|---|---|---|
| 1 | missing_not_reported | The source gave no value | raw value is NULL | NULL | 0 in tournament, 49 for opening_key |
| 2 | stale_snapshot | Snapshot older than the tournament's end | status ≠ 30 and finishes_at < latest retrieval in the table | NULL | 0 |
| 3 | not_final | Tournament not finished when fetched | status ≠ 30, not stale | value kept | 238 |
| 4 | invalid_range | Impossible value | elo outside ELO_VALID_MIN..MAX, or abs(diff) > RATING_DIFF_ABS_MAX | NULL | 0 |
| 5 | unusual_low, unusual_high, unusual_large | Extreme but possible | outside the IQR fence | value kept | elo low 74 + 75, high 26 + 28; diff 60 + 54 |
| 6 | missing_not_applicable | No cap for this tournament | max_rating NULL | NULL | 199 |
| 6 | missing_variant_has_no_openings | Variant without ECO openings | opening NULL and variant not in STANDARD_VARIANTS | NULL | 338 |
| 6 | no_moves_played | Only the result token, no moves | moves matches NO_MOVES_REGEX | NULL | 0 |

### Constants
- `TOURNAMENT_FINISHED = 30`: Lichess status code for finished (10 created, 20 started).
- `NB_PLAYERS_IQR_K = 3.0`: only extreme sell-outs should be flagged; 1.5 would flag ordinary large tournaments. Lowering it raises the unusual_high count only.
- `NB_PLAYERS_MIN_GROUP = 8`: fewer finished tournaments per variant give an unstable fence, so the global fence is used.
- `ELO_VALID_MIN = 100, ELO_VALID_MAX = 4000`: logical bounds of the rating system. They never triggered; the observed values are well inside them, so moving them changes nothing in this data.
- `RATING_DIFF_ABS_MAX = 500`: a logical bound for one game. It never triggered (largest observed 424).
- `ELO_IQR_K = 1.5`: the standard Tukey fence, per tournament. Flagged values are kept, so the constant only changes labels.
- `ELO_MIN_GROUP = 30`: below 30 elo values a tournament fence is unreliable, so the global fence is used.
- `DIFF_IQR_K = 3.0`: rating changes are heavy-tailed, so 1.5 would flag too many ordinary results. This is a judgement, not a tuned value; moving it changes only the unusual_large count.
- `NO_MOVES_REGEX`: a moves string made only of a result token (or empty) means no move was played.
- `USABLE_ELO_SHARE_MIN = 0.90`: ensures at least 90% of rating data is valid per tournament; in this data every tournament has both elo values, so it never triggers.
- `WINDOW_GRACE_MINUTES = 10`: tolerance for clock skew between game and tournament timestamps; the notebook found 0 games outside the window even without tolerance.

Why the notebook and clean.py give different counts. The notebook screens with a per-variant robust z-score (median and MAD) to explore the data; clean.py applies per-tournament IQR fences as a rule. They use different methods on different groups, so their counts differ (notebook: 21 low and 0 high elo; clean.py: 149 low and 54 high). Both avoid mean ± 3 SD and neither deletes anything. We inspected what the fences catch: flagged rating changes are concentrated among low-rated players and are consistent with real results (no winner loses rating, no loser gains).

## 6. Mapping and identity decisions
We checked identity three ways: player ids equal after NFC and casefold (0), duplicate tournaments by name and start time (0), and openings. For openings, 108 names appear under more than one ECO code, but the opening table is keyed by (eco, name), no casefold variants exist and no PGN line belongs to two openings. These are different ECO classifications of lines with a shared family name, so we did not merge them (I-10). Because no candidate pairs were found at any step (exact, normalised, fuzzy), data/mappings/player_alias.csv and data/mappings/category_map.csv contain only their header rows, and clean.py reports player_alias 0 rows | category_map 0 rows | unmatched: none. Match counts: exact 0, normalised 0, fuzzy 0, human decisions 0. The files and tables exist so a future identity or category decision enters as data with decided_by, decided_at and note. Links: player_alias.csv, category_map.csv.

## 7. Counts and validation

```text
PS C:\Users\suden\OneDrive\Desktop\Lichessproje2\lichess-tournament-analysis> python src/clean.py
tournament_clean: 278 rows (raw: 278)
  status_flag           ok=278
  nb_players_flag       not_final=238  ok=40
  max_rating_flag       missing_not_applicable=199  ok=79
game_clean: 4,535 rows (raw: 4,535)
  opening_key_flag      missing_not_reported=49  missing_variant_has_no_openings=338  ok=4,148
  white_elo_flag        ok=4,435  unusual_high=26  unusual_low=74
  black_elo_flag        ok=4,432  unusual_high=28  unusual_low=75
  white_rating_diff_flag ok=4,475  unusual_large=60
  black_rating_diff_flag ok=4,481  unusual_large=54
  moves_flag            ok=4,535
mappings: player_alias 0 rows | category_map 0 rows | unmatched: none
STATUS: COMPLETE

PS C:\Users\suden\OneDrive\Desktop\Lichessproje2\lichess-tournament-analysis> python src/validate.py
PASS  tournament_clean row count equals tournament     278 = 278
PASS  game_clean row count equals game                 4,535 = 4,535
PASS  no impossible cleaned value                      elo 0, rating_diff 0, nb_players 0
PASS  foreign_key_check                                OK
PASS  _raw columns equal M2 values                     0 game rows, 0 tournament rows differ
PASS  untrusted flags have NULL cleaned value          0 leaks
PASS  usable elo share >= 90% per tournament           0 tournaments below
WARN  every finished tournament has games              33 without games
PASS  games inside tournament window                   0 outside
PASS  all player aliases resolve                       0 unresolved
10 checks | 0 STOP | 1 WARN

### Classification of Warnings
- `WARN every finished tournament has games`: Measurement issue. The data pipeline collected game records for only 7 out of 40 finished tournaments.

### How each STOP check was made to fail once
Tested via `python src/test_stops.py` on a temporary database copy:

| STOP check | How it was broken | Result |
| --- | --- | --- |
| Row count | One row deleted from game_clean | 4,534 = 4,535, STOP, exit 1 |
| Impossible value | white_elo = -5 | elo 1, STOP, exit 1 |
| Foreign keys | game.tournament_key = 99999 | 1 violations, STOP, exit 1 |
| _raw equals M2 | white_elo_raw = 11 | game rows differ, STOP, exit 1 |
| Untrusted flag has NULL value | white_elo = 1500 with flag invalid_range | game_clean.white_elo=1, STOP, exit 1 |

- **Atomicity:** An exception was injected into `build()` right after `DELETE FROM game_clean`. `clean.py` exited with code 1, and `validate.py` afterwards still reported `4,535 = 4,535`, verifying the delete was rolled back.
- **Idempotency:** `clean.py` was run twice consecutively with identical output.
- **Raw data integrity:** `clean.py` contains no `UPDATE`, `DELETE` or `DROP` on an M2 table, and `validate.py` checks that every `_raw` column equals the M2 value.

## 8. What we can't say
Games come from 7 tournaments played on two days, so every game-level statistic describes that sample, not Lichess in general. Atomic and Chess960 are each one tournament, which makes variant and tournament inseparable; a difference between variants in M4 may be a difference between two tournaments. We do not know why 33 finished tournaments have no games, and the true final status and player count of the 116 stale tournaments are unknown, so those rows cannot enter tournament-level comparisons. We do not know whether the 49 missing Standard openings are random. Our fences are heuristics: flagged ratings were screened with robust statistics but not verified against Lichess. The 106 abandoned games keep a decisive result, and M4 should decide explicitly whether to count them. M4 must therefore use only status 30, non-stale tournaments, treat NULL max_rating as "no cap", restrict opening analysis to Standard games, and report results with the 7-tournament coverage in mind.
