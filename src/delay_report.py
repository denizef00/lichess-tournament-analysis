"""Evidence for docs/pipeline.md: how late does our data arrive, and how far back does the source reach?

Read-only: only reads data/raw. Run from the repository root:
    python src/delay_report.py
"""
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

RAW = Path("data/raw")
NAME_FILTER = "hourly bullet"
MAX_PAIR_GAP_MIN = 130  # only compare consecutive tournament files at most ~2 h apart
LIST_SIZE = 20          # the source lists exactly this many finished tournaments
TS_RE = re.compile(r"(\d{8}T\d{6}Z)")
UTC_RE = re.compile(r'^\[(UTCDate|UTCTime) "(.*)"\]')


def stamp_of(path):
    m = TS_RE.search(path.name)
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def from_ms(ms):
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


def minutes(later, earlier):
    return (later - earlier).total_seconds() / 60


def describe(values):
    if not values:
        return "n=0"
    v = sorted(values)
    return f"n={len(v)}  min={v[0]:.1f}  median={statistics.median(v):.1f}  max={v[-1]:.1f}"


def is_match(t):
    return NAME_FILTER in t.get("fullName", "").lower()


# ---- tournament lists -------------------------------------------------------
snapshots = []  # (file stamp, list of finished tournaments)
for p in sorted(RAW.glob("tournaments/*.json"), key=stamp_of):
    data = json.loads(p.read_text(encoding="utf-8"))
    snapshots.append((stamp_of(p), data.get("finished", [])))

list_sizes, horizon_all, horizon_match = [], [], []
normal_delay, late_listed = [], []
new_per_pair = []  # (minutes between the two polls, tournaments that are new in the later list)
revised = compared = 0

for i, (s, finished) in enumerate(snapshots):
    list_sizes.append(len(finished))
    ends = [int(t["finishesAt"]) for t in finished if t.get("finishesAt")]
    if ends:
        horizon_all.append(minutes(s, from_ms(min(ends))))
    m_ends = [int(t["finishesAt"]) for t in finished if is_match(t) and t.get("finishesAt")]
    if m_ends:
        horizon_match.append(minutes(s, from_ms(min(m_ends))))

    if i == 0:
        continue
    prev_s, prev_finished = snapshots[i - 1]
    if minutes(s, prev_s) > MAX_PAIR_GAP_MIN:
        continue
    prev_by_id = {t["id"]: t for t in prev_finished}
    new_per_pair.append((minutes(s, prev_s), sum(1 for t in finished if t["id"] not in prev_by_id)))
    for t in finished:
        old = prev_by_id.get(t["id"])
        if old is not None:
            compared += 1
            if (old.get("nbPlayers"), old.get("status")) != (t.get("nbPlayers"), t.get("status")):
                revised += 1
            continue
        if not is_match(t) or not t.get("finishesAt"):
            continue
        end = from_ms(t["finishesAt"])
        if end > prev_s:
            normal_delay.append(minutes(s, end))      # finished after the previous poll
        else:
            late_listed.append(minutes(prev_s, end))  # already over at the previous poll, but not listed then

# ---- games ------------------------------------------------------------------
finish_by_id = {}
for _, finished in snapshots:
    for t in finished:
        if t.get("finishesAt"):
            finish_by_id[t["id"]] = from_ms(t["finishesAt"])

game_delay, after_end = [], []
for p in sorted(RAW.glob("games/*.pgn"), key=stamp_of):
    s = stamp_of(p)
    tid = p.stem.split("_", 1)[1]
    date, last_played = None, None
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = UTC_RE.match(line)
            if not m:
                continue
            if m.group(1) == "UTCDate":
                date = m.group(2)
            elif date:
                played = datetime.strptime(f"{date} {m.group(2)}", "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
                game_delay.append(minutes(s, played))
                if last_played is None or played > last_played:
                    last_played = played
    if last_played and tid in finish_by_id:
        after_end.append(minutes(last_played, finish_by_id[tid]))

print("== Source memory: how far back does the 'finished' list reach (minutes before the poll)")
print("list size        ", describe(list_sizes))
print("oldest, all      ", describe(horizon_all))
print("oldest, matching ", describe(horizon_match))
print("== Listing delay, matching tournaments, consecutive polls <= 130 min apart")
print("finished after previous poll, minutes until we saw it:", describe(normal_delay))
print("already over at previous poll but listed late, minutes (lower bound):", describe(late_listed))
print(f"== Revisions: nbPlayers/status changed between consecutive polls: {revised} of {compared}")
print("== Games")
print("played_at -> our download, minutes (includes our own polling interval):", describe(game_delay))
print("last game ended after nominal tournament end, minutes:", describe(after_end))

short = [n for gap, n in new_per_pair if gap <= 20]
long_gap = [n for gap, n in new_per_pair if 40 <= gap <= MAX_PAIR_GAP_MIN]
print(f"== Newly finished tournaments between consecutive polls (the list holds {LIST_SIZE})")
print("polls <= 20 min apart: ", describe(short))
print("polls 40-130 min apart:", describe(long_gap))
print(f"polls where all {LIST_SIZE} listed tournaments were new (list may have overflowed):",
      sum(1 for _, n in new_per_pair if n >= LIST_SIZE))