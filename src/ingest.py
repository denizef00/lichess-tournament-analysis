"""M1/M5 ingestion: lands raw Lichess tournament games + opening reference with self-healing window.

Run from the repository root:  python src/ingest.py [--run-time ISO_TIMESTAMP]
"""
import argparse
import glob
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DB_PATH = REPO_ROOT / "data" / "pipeline_log.db"
RAW_DIR = REPO_ROOT / "data" / "raw"

API_TOKEN = os.getenv("LICHESS_API_TOKEN")
if not API_TOKEN:
    print("HATA: 'LICHESS_API_TOKEN' ortam değişkeni bulunamadı. Lütfen .env dosyanızı kontrol edin!", file=sys.stderr)
    sys.exit(1)

CONTACT = os.getenv("CONTACT_INFO", "student-project")
HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "User-Agent": f"LichessDataEngineeringProject/1.0 ({CONTACT})",
}

NAME_FILTER = os.getenv("LICHESS_NAME_FILTER", "Hourly Bullet")
MAX_TOURNAMENTS = int(os.getenv("MAX_TOURNAMENTS", "50"))
OVERLAP_MINUTES = int(os.getenv("INGEST_OVERLAP_MINUTES", "120"))  # Kaçırılan/geciken turnuvalar için 2 saat pay
OPENINGS_TTL_HOURS = 24
TIMEOUT = (10, 60)  # (bağlantı, okuma) saniye
OPENINGS_URL = "https://raw.githubusercontent.com/lichess-org/chess-openings/master/{}.tsv"


class AuthError(Exception):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lichess raw data ingestion")
    parser.add_argument(
        "--run-time",
        type=str,
        default=datetime.now(timezone.utc).isoformat(),
        help="Pipeline mantıksal çalışma zamanı (ISO-8601)",
    )
    return parser.parse_args()


def parse_iso_utc(ts_str: str) -> datetime:
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_stamp(run_dt: datetime) -> str:
    return run_dt.strftime("%Y%m%dT%H%M%SZ")


def get_last_successful_ingest(run_dt: datetime) -> datetime | None:
    """Önce pipeline_log.db'den son başarılı ingest zamanını okur; yoksa raw dosya adlarından bulur."""
    if LOG_DB_PATH.exists():
        try:
            conn = sqlite3.connect(LOG_DB_PATH)
            cur = conn.execute(
                """
                SELECT MAX(run_time)
                FROM pipeline_run
                WHERE step = ? AND status = ? AND run_time < ?
                """,
                ("ingest", "SUCCESS", run_dt.isoformat()),
            )
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                return parse_iso_utc(row[0])
        except sqlite3.Error:
            pass

    # Fallback: data/raw/tournaments/YYYYMMDDTHHMMSSZ.json dosya adlarından en yenisini bul
    t_files = sorted(glob.glob(str(RAW_DIR / "tournaments" / "*.json")))
    for fpath in reversed(t_files):
        stem = Path(fpath).stem
        try:
            dt = datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if dt < run_dt:
                return dt
        except ValueError:
            continue
    return None


def already_downloaded_tournament_ids() -> set[str]:
    """Diskte zaten .pgn dosyası inmiş olan turnuva ID'lerini döner."""
    existing = set()
    for fpath in glob.glob(str(RAW_DIR / "games" / "*_*.pgn")):
        stem = Path(fpath).stem  # örn: 20261008T115537Z_abcd1234
        parts = stem.split("_", 1)
        if len(parts) == 2:
            existing.add(parts[1])
    return existing


def request_with_retry(url, headers=None, params=None, max_retries=int(os.getenv("MAX_RETRIES", "5"))):
    """Başarılı response veya None döner. 401/403'te AuthError fırlatır (durdurur)."""
    delay = 2
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)

            if response.status_code in (401, 403):
                raise AuthError(f"Yetkilendirme hatası {response.status_code}: {url}")

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "")
                wait_time = int(retry_after) if retry_after.isdigit() else 60 * (attempt + 1)
                print(f"[Rate Limit] 429. {wait_time}s bekleniyor... (Deneme {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue

            if 500 <= response.status_code < 600:
                print(f"[Server Error] {response.status_code}. {delay}s sonra tekrar... ({attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay *= 2
                continue

            if response.status_code >= 400:
                print(f"[Client Error] {response.status_code}: {url}. Tekrar denenmiyor.")
                return None

            return response

        except (requests.Timeout, requests.ConnectionError) as e:
            print(f"[Network Error] {e.__class__.__name__}. {delay}s bekleniyor... ({attempt + 1}/{max_retries})")
            time.sleep(delay)
            delay *= 2

    print(f"[Fail] {max_retries} deneme sonrası başarısız: {url}")
    return None


def save_raw(path: Path, content: bytes) -> None:
    """Bytes olduğu gibi yazılır; var olan dosya asla ezilmez."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as f:
        f.write(content)


def ingest_lichess(summary: dict, run_dt: datetime) -> None:
    games = summary["lichess_games"] = {"files": 0, "records": 0, "failed": [], "empty": []}
    stamp = utc_stamp(run_dt)

    res = request_with_retry("https://lichess.org/api/tournament", headers=HEADERS)
    if not res:
        games["failed"].append("tournament list")
        return

    t_path = RAW_DIR / "tournaments" / f"{stamp}.json"
    save_raw(t_path, res.content)
    games["files"] += 1

    try:
        finished = res.json()["finished"]
    except (ValueError, KeyError, TypeError):
        games["failed"].append("tournament list: beklenmeyen yapı")
        return

    last_ok = get_last_successful_ingest(run_dt)
    since_dt = (last_ok - timedelta(minutes=OVERLAP_MINUTES)) if last_ok else None
    since_ms = int(since_dt.timestamp() * 1000) if since_dt else 0

    existing_ids = already_downloaded_tournament_ids()
    matched = [t for t in finished if NAME_FILTER.lower() in t.get("fullName", "").lower()]

    if since_dt:
        in_window = [t for t in matched if int(t.get("finishesAt", 0)) >= since_ms]
        print(f"Son başarılı ingest: {last_ok.isoformat()} | Overlap penceresi başlangıcı: {since_dt.isoformat()}")
    else:
        in_window = matched
        print("İlk çalıştırma veya geçmiş log yok: mevcut tüm eşleşen turnuvalar taranıyor.")

    to_fetch = [t for t in in_window if t["id"] not in existing_ids][:MAX_TOURNAMENTS]
    print(
        f"{len(finished)} biten turnuva | '{NAME_FILTER}' eşleşen: {len(matched)} | "
        f"pencerede: {len(in_window)} | yeni indirilecek: {len(to_fetch)}"
    )

    for t in to_fetch:
        t_id = t["id"]
        games_headers = {**HEADERS, "Accept": "application/x-chess-pgn"}
        params = {"clocks": "false", "opening": "true"}
        games_res = request_with_retry(
            f"https://lichess.org/api/tournament/{t_id}/games", headers=games_headers, params=params
        )
        if games_res is None:
            games["failed"].append(t_id)
        elif not games_res.content.strip():
            games["empty"].append(t_id)
        else:
            path = RAW_DIR / "games" / f"{stamp}_{t_id}.pgn"
            save_raw(path, games_res.content)
            n_games = games_res.content.count(b"[Event ")
            games["files"] += 1
            games["records"] += n_games
            print(f"{t_id}: {n_games} oyun -> {path.relative_to(REPO_ROOT)}")
        time.sleep(1)


def openings_are_fresh(run_dt: datetime) -> bool:
    """Açılış TSV dosyaları son 24 saat içinde indirilmişse True döner."""
    for letter in "abcde":
        matches = sorted(glob.glob(str(RAW_DIR / "openings" / f"*_{letter}.tsv")))
        if not matches:
            return False
        latest_stem = Path(matches[-1]).stem.split("_")[0]
        try:
            file_dt = datetime.strptime(latest_stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if (run_dt - file_dt) > timedelta(hours=OPENINGS_TTL_HOURS):
                return False
        except ValueError:
            return False
    return True


def ingest_openings(summary: dict, run_dt: datetime) -> None:
    ref = summary["opening_reference"] = {"files": 0, "records": 0, "failed": [], "empty": [], "skipped": False}
    if openings_are_fresh(run_dt):
        ref["skipped"] = True
        print(f"openings: son {OPENINGS_TTL_HOURS} saat içinde zaten güncel, indirme atlandı.")
        return

    stamp = utc_stamp(run_dt)
    for letter in "abcde":
        res = request_with_retry(OPENINGS_URL.format(letter))
        if res is None:
            ref["failed"].append(f"{letter}.tsv")
            continue
        lines = res.content.decode("utf-8", errors="replace").splitlines()
        if len(lines) < 2 or not lines[0].lower().startswith("eco"):
            ref["failed"].append(f"{letter}.tsv: beklenmeyen içerik")
            continue
        save_raw(RAW_DIR / "openings" / f"{stamp}_{letter}.tsv", res.content)
        ref["files"] += 1
        ref["records"] += len(lines) - 1
        print(f"openings {letter}.tsv: {len(lines) - 1} satır")


def print_summary(summary: dict, started: float) -> bool:
    print("\n--- INGESTION SUMMARY ---")
    healthy = True
    for name, s in summary.items():
        if s.get("skipped"):
            print(f"{name}: SKIPPED (güncel)")
            continue
        print(f"{name}: {s['files']} dosya, {s['records']} kayıt, "
              f"{len(s['failed'])} hata, {len(s['empty'])} boş")
        for item in s["failed"]:
            print(f"   HATA: {item}")
        for item in s["empty"]:
            print(f"   BOŞ: {item}")
        if s["failed"] or s["files"] == 0:
            healthy = False
    print(f"Süre: {time.time() - started:.1f}s")
    print("Durum:", "SAĞLIKLI" if healthy else "SORUN VAR")
    return healthy


def run_pipeline() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    run_dt = parse_iso_utc(args.run_time)

    started = time.time()
    summary = {}
    print(f"--- INGESTION BAŞLADI (run_time={run_dt.isoformat()}) ---")
    try:
        ingest_lichess(summary, run_dt)
        ingest_openings(summary, run_dt)
    except AuthError as e:
        print(f"\n[Auth Error] {e}\nToken'ı kontrol edin. İşlem durduruldu.", file=sys.stderr)
        print_summary(summary, started)
        sys.exit(2)
    sys.exit(0 if print_summary(summary, started) else 1)


if __name__ == "__main__":
    run_pipeline()
