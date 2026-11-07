"""All thresholds and constants of the cleaning and validation steps.

Every constant is justified in docs/cleaning_report.md (section 5).
"""
import re

# --- Game: moves ---------------------------------------------------------
# movetext that is only the result token = no move was played
NO_MOVES_RE = re.compile(r"(1-0|0-1|1/2-1/2)")

# --- Tournament -------------------------------------------------------
TOURNAMENT_FINISHED = 30        # Lichess status code: finished (10 = created, 20 = started)
NB_PLAYERS_IQR_K = 3.0          # upper fence = Q3 + k * IQR; 3.0 = "far out", only extreme sell-outs
NB_PLAYERS_MIN_GROUP = 8        # min finished tournaments per variant to compute its own fence

# --- Game: validity rules (family 2) ---------------------------------
ELO_VALID_MIN = 100             # a rating below this is impossible
ELO_VALID_MAX = 4000            # a rating above this is impossible
RATING_DIFF_ABS_MAX = 500       # |rating change| in a single game cannot exceed this

# --- Game: unusual values (family 3), robust method: IQR fences ---------
ELO_IQR_K = 1.5                 # per-tournament elo fence: Q1 - k*IQR, Q3 + k*IQR
ELO_MIN_GROUP = 30              # fewer elo values than this in a tournament -> global fence
DIFF_IQR_K = 3.0                # global rating_diff fence

# --- Categories --------------------------------------------------------
STANDARD_VARIANTS = {"standard"}  # ECO openings are only expected in these variants (casefolded)

# --- Mapping CSVs ------------------------------------------------------
MAPPINGS_DIRNAME = "mappings"   # folder under data/

# --- validate.py ---------------------------------------------------------
USABLE_ELO_SHARE_MIN = 0.90     # per tournament: share of games with both elo values usable
WINDOW_GRACE_MINUTES = 10       # a game may start this far outside the tournament window (clock skew)