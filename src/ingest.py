"""M1 ingestion: lands raw Lichess tournament games + opening reference, untouched.

Run from the repository root:  python src/ingest.py
"""
import os
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("LICHESS_API_TOKEN")
if not API_TOKEN:
    print("HATA: 'LICHESS_API_TOKEN' ortam değişkeni bulunamadı. Lütfen .env dosyanızı kontrol edin!")
    sys.exit(1)

CONTACT = os.getenv("CONTACT_INFO", "student-project")
HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "User-Agent": f"LichessDataEngineeringProject/1.0 ({CONTACT})",
}

NAME_FILTER = os.getenv("LICHESS_NAME_FILTER", "Hourly Bullet")
MAX_TOURNAMENTS = int(os.getenv("MAX_TOURNAMENTS", "20"))
TIMEOUT = (10, 60)  # (bağlantı, okuma) saniye
OPENINGS_URL = "https://raw.githubusercontent.com/lichess-org/chess-openings/master/{}.tsv"


class AuthError(Exception):
    pass


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def save_raw(path, content):
    """Bytes olduğu gibi yazılır; var olan dosya asla ezilmez."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "xb") as f:  # "x": dosya varsa hata verir, ezmez
        f.write(content)


def ingest_lichess(summary):
    games = summary["lichess_games"] = {"files": 0, "records": 0, "failed": [], "empty": []}

    res = request_with_retry("https://lichess.org/api/tournament", headers=HEADERS)
    if not res:
        games["failed"].append("tournament list")
        return
    save_raw(os.path.join("data", "raw", "tournaments", f"{utc_stamp()}.json"), res.content)

    try:
        finished = res.json()["finished"]
    except (ValueError, KeyError, TypeError):
        games["failed"].append("tournament list: beklenmeyen yapı")
        return
    selected = [t for t in finished if NAME_FILTER.lower() in t.get("fullName", "").lower()]
    selected = selected[:MAX_TOURNAMENTS]
    print(f"{len(finished)} biten turnuva, filtreye ('{NAME_FILTER}') uyan: {len(selected)}")
    if not selected:
        games["failed"].append(f"filtreye uyan turnuva yok: '{NAME_FILTER}'")
        return

    for t in selected:
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
            path = os.path.join("data", "raw", "games", f"{utc_stamp()}_{t_id}.pgn")
            save_raw(path, games_res.content)
            n_games = games_res.content.count(b"[Event ")
            games["files"] += 1
            games["records"] += n_games
            print(f"{t_id}: {n_games} oyun -> {path}")
        time.sleep(1)


def ingest_openings(summary):
    ref = summary["opening_reference"] = {"files": 0, "records": 0, "failed": [], "empty": []}
    for letter in "abcde":
        res = request_with_retry(OPENINGS_URL.format(letter))
        if res is None:
            ref["failed"].append(f"{letter}.tsv")
            continue
        lines = res.content.decode("utf-8", errors="replace").splitlines()
        if len(lines) < 2 or not lines[0].lower().startswith("eco"):
            ref["failed"].append(f"{letter}.tsv: beklenmeyen içerik")
            continue
        save_raw(os.path.join("data", "raw", "openings", f"{utc_stamp()}_{letter}.tsv"), res.content)
        ref["files"] += 1
        ref["records"] += len(lines) - 1
        print(f"openings {letter}.tsv: {len(lines) - 1} satır")


def print_summary(summary, started):
    print("\n--- INGESTION SUMMARY ---")
    healthy = True
    for name, s in summary.items():
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


def run_pipeline():
    started = time.time()
    summary = {}
    print("--- INGESTION BAŞLADI ---")
    try:
        ingest_lichess(summary)
        ingest_openings(summary)
    except AuthError as e:
        print(f"\n[Auth Error] {e}\nToken'ı kontrol edin. İşlem durduruldu.", file=sys.stderr)
        print_summary(summary, started)
        sys.exit(2)
    sys.exit(0 if print_summary(summary, started) else 1)


if __name__ == "__main__":
    run_pipeline()