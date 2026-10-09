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

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "User-Agent": "LichessDataEngineeringProject/1.0 (iletisim@mail.com)",
}

def request_with_retry(url, headers=None, params=None, max_retries=3):
    
    delay = 2
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            
            if response.status_code in (401, 403):
                print(f"[Auth Error] Yetkilendirme hatası: {response.status_code}. İstek iptal edildi.")
                return None
            
            if response.status_code == 429:
                wait_time = int(response.headers.get("Retry-After", delay))
                print(f"[Rate Limit] 429 alındı. {wait_time}s bekleniyor... (Deneme {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                delay *= 2
                continue
            
            if 500 <= response.status_code < 600:
                print(f"[Server Error] {response.status_code}. {delay}s sonra tekrar deneniyor...")
                time.sleep(delay)
                delay *= 2
                continue
                
            response.raise_for_status()
            return response

        except requests.exceptions.RequestException as e:
            print(f"[Network Error] Ağ hatası: {e}. {delay}s bekleniyor...")
            time.sleep(delay)
            delay *= 2

    print(f"[Fail] {max_retries} deneme sonrası istek başarısız oldu: {url}")
    return None


def run_pipeline():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    
    raw_tournaments_dir = os.path.join("data", "raw", "tournaments")
    raw_games_dir = os.path.join("data", "raw", "games")
    os.makedirs(raw_tournaments_dir, exist_ok=True)
    os.makedirs(raw_games_dir, exist_ok=True)

    print("--- INGESTION BAŞLADI ---")
    
    tourn_url = "https://lichess.org/api/tournament"
    res = request_with_retry(tourn_url, headers=HEADERS)
    if not res:
        print("Turnuvalar alınamadığı için işlem durduruldu.")
        return

    tourn_file_path = os.path.join(raw_tournaments_dir, f"{timestamp}.json")
    with open(tourn_file_path, "w", encoding="utf-8") as f:
        f.write(res.text)

    data = res.json()
    finished = data.get("finished", [])[:5]
    
    saved_games_count = 0
    failed_tournaments = []

    for t in finished:
        t_id = t["id"]
        games_url = f"https://lichess.org/api/tournament/{t_id}/games"
        games_headers = {**HEADERS, "Accept": "application/x-chess-pgn"}
        params = {"clocks": "false", "opening": "true"}

        games_res = request_with_retry(games_url, headers=games_headers, params=params)
        
        if games_res and games_res.text.strip():
            raw_pgn_path = os.path.join(raw_games_dir, f"{timestamp}_{t_id}.pgn")
            with open(raw_pgn_path, "w", encoding="utf-8") as f:
                f.write(games_res.text)
            saved_games_count += 1
        else:
            failed_tournaments.append(t_id)

    print("\n--- INGESTION SUMMARY ---")
    print(f"Zaman Damgası (UTC): {timestamp}")
    print(f"Kaydedilen Turnuva Dosyası: {tourn_file_path}")
    print(f"Başarıyla Kaydedilen PGN Sayısı: {saved_games_count}")
    if failed_tournaments:
        print(f"Verisi alınamayan turnuva ID'leri: {', '.join(failed_tournaments)}")
    print("Durum: Tamamlandı.")


if __name__ == "__main__":
    run_pipeline()