# Data Source Cards

## Source 1: Lichess recent finished tournaments

```
source_name:      Lichess recent finished tournaments (metadata)
provider:         Lichess.org
url:              https://lichess.org/api/tournament
access_method:    REST API, Bearer token (JSON response)
licence:          CC0 1.0 (Lichess releases its game data under CC0; see https://database.lichess.org and https://lichess.org/terms-of-service)
terms_notes:      CC0 permits storing and redistributing the data. A bounded sample of the raw JSON is committed to Git.
update_cadence:   continuously updated; new tournaments finish every hour
coverage:         recently finished public tournaments only; no historical backfill
record_meaning:   one JSON object in the "finished" list = one tournament (id, fullName, system, ...)
join_key:         id  (joins to: games, via the tournament id used in the games endpoint)
first_retrieved:  <TODO: UTC timestamp from first raw file name in data/raw/tournaments/>
known_issues:     - Only recently finished tournaments are listed, older ones cannot be fetched
                  - The list is not paginated, so history cannot be extended
                  - Repeated runs return overlapping tournaments (de-duplicate in M3)
                  - <TODO: add anything you noticed in the raw JSON>
```

## Source 2: Lichess tournament games

```
source_name:      Lichess Arena tournament games (PGN export, Hourly Bullet)
provider:         Lichess.org
url:              https://lichess.org/api/tournament/{id}/games
access_method:    REST API, Bearer token, Accept: application/x-chess-pgn
licence:          CC0 1.0 (see source 1)
terms_notes:      See source 1. CC0 permits redistribution. A bounded sample of the raw PGN files is committed to Git (size kept small, the rest stays local).
update_cadence:   Hourly Bullet Arena tournaments run every hour; a tournament's games are final once it finishes
coverage:         all games of every tournament selected by the name filter "Hourly Bullet" (at most MAX_TOURNAMENTS per run)
record_meaning:   one PGN record = one game in one Arena tournament
join_key:         ECO tag / Opening tag  (joins to: Lichess chess-openings via eco)
first_retrieved:  <TODO: UTC timestamp from first raw file name in data/raw/games/>
known_issues:     - Player usernames are present in the raw data and are published in the repository (public Lichess data under CC0);
                    they are removed in M3 and not retained in the project dataset, per our M0 proposal
                  - Some games are abandoned before play began
                  - Some games have missing ECO/opening tags
                  - Bot accounts take part in tournaments
                  - The games endpoint streams all games of a tournament in one response, so no pagination is needed
                  - <TODO: add real numbers from your run, e.g. games fetched, how many have no ECO>
```

## Source 3: Lichess chess-openings (ECO reference)

```
source_name:      Lichess chess-openings (ECO reference table)
provider:         Lichess (lichess-org/chess-openings GitHub repository)
url:              https://github.com/lichess-org/chess-openings
access_method:    File download (a.tsv ... e.tsv) from raw.githubusercontent.com, no authentication
licence:          CC0 1.0 (stated in the repository; verify on the repository page)
terms_notes:      CC0: redistribution and storage permitted, no attribution required
update_cadence:   irregular (repository updates)
coverage:         ECO volumes A to E, all catalogued opening names
record_meaning:   one row = one named opening variation (eco, name, pgn move sequence)
join_key:         eco  (joins to: games ECO tag)
first_retrieved:  <TODO: UTC timestamp from first raw file name in data/raw/openings/>
known_issues:     - One ECO code maps to many opening names
                  - Opening names in game records may not match reference names exactly
                  - Five separate files must be combined (done in M3, not at ingestion)
```