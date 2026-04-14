import requests
import time
import json
import os
from datetime import datetime
import pytz

# ═══════════════════════════════════════
# KONFIGURATION
# ═══════════════════════════════════════
FD_TOKEN = os.environ.get("FD_TOKEN", "ce2aaecf111c442e885f33c5495635d2")
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "1599474063")

FD_BASE = "https://api.football-data.org/v4"
TG_BASE = f"https://api.telegram.org/bot{TG_TOKEN}"

HEADERS = {"X-Auth-Token": FD_TOKEN}
ZURICH = pytz.timezone("Europe/Zurich")

# ═══════════════════════════════════════
# LIGEN KONFIGURATION
# ═══════════════════════════════════════
LEAGUES = {
    "PL":  {"name": "Premier League",  "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 83},
    "BL1": {"name": "Bundesliga",      "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 75},
    "PD":  {"name": "La Liga",         "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 91},
    "SA":  {"name": "Serie A",         "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 87},
    "FL1": {"name": "Ligue 1",         "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 80},
    "DED": {"name": "Eredivisie",      "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 83},
    "ELC": {"name": "Championship",    "tier": 2, "tier_str": "⭐⭐ Tier 2",   "hit_rate": 55},
    "PPL": {"name": "Primeira Liga",   "tier": 2, "tier_str": "⭐⭐ Tier 2",   "hit_rate": 68},
}

# ═══════════════════════════════════════
# STATE (im Memory)
# ═══════════════════════════════════════
standings_cache = {}      # {league_code: {team_id: rank}}
alerted_matches = set()   # match IDs wo schon ein Alert gesendet wurde
prev_scores = {}          # {match_id: {"home": x, "away": y}}

# ═══════════════════════════════════════
# TELEGRAM SENDEN
# ═══════════════════════════════════════
def send_telegram(text):
    if not TG_TOKEN:
        print(f"[TELEGRAM nicht konfiguriert] {text}")
        return
    try:
        url = f"{TG_BASE}/sendMessage"
        payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"✅ Telegram Alert gesendet: {text[:60]}...")
        else:
            print(f"❌ Telegram Fehler: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"❌ Telegram Exception: {e}")

# ═══════════════════════════════════════
# STANDINGS LADEN
# ═══════════════════════════════════════
def load_standings(league_code):
    try:
        url = f"{FD_BASE}/competitions/{league_code}/standings"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            data = r.json()
            ranks = {}
            for standing in data.get("standings", []):
                if standing.get("type") == "TOTAL":
                    for entry in standing.get("table", []):
                        team_id = entry["team"]["id"]
                        ranks[team_id] = entry["position"]
            standings_cache[league_code] = ranks
            total = len(data.get("standings", [{}])[0].get("table", []))
            print(f"  ✅ {LEAGUES[league_code]['name']}: {len(ranks)} Teams geladen")
            return total
        else:
            print(f"  ❌ {league_code} Standings Fehler: {r.status_code}")
            return 0
    except Exception as e:
        print(f"  ❌ {league_code} Exception: {e}")
        return 0

def load_all_standings():
    print(f"\n[{now_str()}] Lade Tabellenränge...")
    for code in LEAGUES:
        load_standings(code)
        time.sleep(1)  # Rate limit respektieren

# ═══════════════════════════════════════
# SIGNALSTÄRKE BESTIMMEN
# ═══════════════════════════════════════
def get_signal(diff, minute, tier):
    if diff >= 10 and minute <= 25 and tier == 1:
        return "⭐⭐⭐ STARK"
    elif diff >= 5 or (26 <= minute <= 45) or tier == 2:
        return "⭐⭐ MITTEL"
    else:
        return "⭐ SCHWACH"

# ═══════════════════════════════════════
# SPERRREGEL: Keller vs. Keller
# ═══════════════════════════════════════
def is_bottom_vs_bottom(home_rank, away_rank, total_teams):
    bottom_4 = total_teams - 3  # letzte 4
    return home_rank >= bottom_4 and away_rank >= bottom_4

# ═══════════════════════════════════════
# ALERT FORMATIEREN UND SENDEN
# ═══════════════════════════════════════
def send_alert(match, scorer_team, home_rank, away_rank, minute, league_code):
    league = LEAGUES[league_code]
    home_name = match["homeTeam"]["name"]
    away_name = match["awayTeam"]["name"]
    home_score = match["score"]["fullTime"]["home"]
    away_score = match["score"]["fullTime"]["away"]
    diff = abs(home_rank - away_rank)
    tier = league["tier"]
    signal = get_signal(diff, minute, tier)
    deadline_left = 60 - minute
    hit_rate = league["hit_rate"]

    if scorer_team == "away":
        # Auswärtsteam (schlechter) trifft → ERSTE WAHL
        text = (
            f"🟢 <b>ERSTE WAHL — LIVE ALERT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 Liga:      {league['name']}  {league['tier_str']}\n"
            f"⚽ Spiel:     {home_name} {home_score}–{away_score} {away_name}\n"
            f"⏱ Minute:    {minute}'\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 Trigger:   {away_name} erzielte 1. Tor\n"
            f"📊 Tabelle:   Heim Platz {home_rank}  |  Away Platz {away_rank}\n"
            f"📈 Rang-Diff: {diff} Plätze (Away schlechter)\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Wette:     LIVE Over 1.5 Tore ab jetzt\n"
            f"🔥 Signal:    {signal}\n"
            f"📉 Liga HR:   {hit_rate}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {minute}' — noch {deadline_left}' bis Deadline"
        )
    else:
        # Heimteam (schlechter) trifft → ZWEITE WAHL
        text = (
            f"🟡 <b>ZWEITE WAHL — LIVE ALERT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 Liga:      {league['name']}  {league['tier_str']}\n"
            f"⚽ Spiel:     {home_name} {home_score}–{away_score} {away_name}\n"
            f"⏱ Minute:    {minute}'\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 Trigger:   {home_name} erzielte 1. Tor\n"
            f"⚡ Heimteam ist schlechter platziert\n"
            f"📊 Tabelle:   Heim Platz {home_rank}  |  Away Platz {away_rank}\n"
            f"📈 Rang-Diff: {diff} Plätze (Heim schlechter)\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Wette:     LIVE Over 1.5 Tore ab jetzt\n"
            f"🔥 Signal:    {signal}\n"
            f"📉 Liga HR:   {hit_rate}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Zweite Priorität — nur bei guter Quote\n"
            f"⏰ {minute}' — noch {deadline_left}' bis Deadline"
        )

    print(f"\n{'='*50}")
    print(text)
    print(f"{'='*50}\n")
    send_telegram(text)

# ═══════════════════════════════════════
# KERNLOGIK: MATCH PRÜFEN
# ═══════════════════════════════════════
def check_match(match):
    match_id = match["id"]
    status = match.get("status", "")

    # Nur laufende Spiele prüfen
    if status not in ["IN_PLAY", "PAUSED"]:
        return

    # Liga ermitteln
    league_code = match.get("competition", {}).get("code", "")
    if league_code not in LEAGUES:
        return

    # Schon alerted?
    if match_id in alerted_matches:
        return

    # Aktueller Stand
    score = match.get("score", {})
    home_score = score.get("fullTime", {}).get("home") or 0
    away_score = score.get("fullTime", {}).get("away") or 0
    minute = match.get("minute", 0) or 0

    # Deadline check
    if minute > 60:
        alerted_matches.add(match_id)  # nicht mehr prüfen
        return

    # Vorheriger Stand
    prev = prev_scores.get(match_id, {"home": 0, "away": 0})
    prev_home = prev["home"]
    prev_away = prev["away"]

    # Stand aktualisieren
    prev_scores[match_id] = {"home": home_score, "away": away_score}

    # Kein Tor → nichts zu tun
    if home_score == prev_home and away_score == prev_away:
        return

    # Nur erstes Tor (Stand war 0:0)
    if prev_home != 0 or prev_away != 0:
        alerted_matches.add(match_id)
        return

    # Wer hat getroffen?
    if home_score == 1 and away_score == 0:
        scorer_team = "home"
    elif away_score == 1 and home_score == 0:
        scorer_team = "away"
    else:
        return

    # Tabellenränge laden
    standings = standings_cache.get(league_code, {})
    if not standings:
        return

    home_team_id = match.get("homeTeam", {}).get("id")
    away_team_id = match.get("awayTeam", {}).get("id")
    home_rank = standings.get(home_team_id, 99)
    away_rank = standings.get(away_team_id, 99)
    total_teams = len(standings)

    # Rangdifferenz
    diff = abs(home_rank - away_rank)
    if diff < 3:
        print(f"  [SKIP] {match['homeTeam']['name']} vs {match['awayTeam']['name']} — Rang-Diff zu klein ({diff})")
        return

    # Keller vs. Keller Sperre
    if is_bottom_vs_bottom(home_rank, away_rank, total_teams):
        print(f"  [SKIP] {match['homeTeam']['name']} vs {match['awayTeam']['name']} — Keller vs Keller")
        return

    # Trigger prüfen
    if scorer_team == "away" and away_rank > home_rank:
        # ERSTE WAHL: Away (schlechter) trifft
        send_alert(match, "away", home_rank, away_rank, minute, league_code)
        alerted_matches.add(match_id)
    elif scorer_team == "home" and home_rank > away_rank:
        # ZWEITE WAHL: Heim (schlechter) trifft
        send_alert(match, "home", home_rank, away_rank, minute, league_code)
        alerted_matches.add(match_id)
    else:
        print(f"  [SKIP] Tor vom besseren Team — keine Wette")

# ═══════════════════════════════════════
# LIVE MATCHES ABRUFEN
# ═══════════════════════════════════════
def fetch_live_matches():
    try:
        url = f"{FD_BASE}/matches?status=IN_PLAY"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json().get("matches", [])
        else:
            print(f"  ❌ Live Matches Fehler: {r.status_code}")
            return []
    except Exception as e:
        print(f"  ❌ Live Matches Exception: {e}")
        return []

# ═══════════════════════════════════════
# HEUTE SPIELE PRÜFEN
# ═══════════════════════════════════════
def has_matches_today():
    try:
        url = f"{FD_BASE}/matches"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            matches = r.json().get("matches", [])
            relevant = [m for m in matches if m.get("competition", {}).get("code") in LEAGUES]
            print(f"  📅 Heute {len(relevant)} relevante Spiele")
            return len(relevant) > 0
        return False
    except Exception as e:
        print(f"  ❌ Matches Today Exception: {e}")
        return False

# ═══════════════════════════════════════
# HILFSFUNKTIONEN
# ═══════════════════════════════════════
def now_str():
    return datetime.now(ZURICH).strftime("%H:%M:%S")

def should_poll():
    """Nur zwischen 11:00 und 23:30 Uhr aktiv"""
    now = datetime.now(ZURICH)
    return 11 <= now.hour <= 23

# ═══════════════════════════════════════
# STARTUP NACHRICHT
# ═══════════════════════════════════════
def send_startup():
    text = (
        "🚀 <b>Fussball Alert Bot gestartet!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Überwache 8 Ligen:\n"
        "  Premier League, Bundesliga, La Liga\n"
        "  Serie A, Ligue 1, Eredivisie\n"
        "  Championship, Primeira Liga\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ Polling alle 10 Sekunden\n"
        "🎯 Alerts nur für erstes Tor bis Minute 60"
    )
    send_telegram(text)

# ═══════════════════════════════════════
# HAUPTSCHLEIFE
# ═══════════════════════════════════════
def main():
    print(f"\n{'='*50}")
    print(f"  FUSSBALL ALERT BOT GESTARTET")
    print(f"  {now_str()} Uhr")
    print(f"{'='*50}\n")

    send_startup()

    # Standings einmal laden
    load_all_standings()

    last_standings_refresh = time.time()
    last_day_check = time.time()
    polling_active = False

    print(f"\n[{now_str()}] Hauptschleife startet...")

    while True:
        now_ts = time.time()

        # Standings täglich neu laden (alle 12 Stunden)
        if now_ts - last_standings_refresh > 43200:
            load_all_standings()
            last_standings_refresh = now_ts

        # Nur in Spielzeiten aktiv
        if not should_poll():
            if polling_active:
                print(f"[{now_str()}] Keine Spielzeit — Agent schläft")
                polling_active = False
            time.sleep(60)
            continue

        # Alle 5 Minuten prüfen ob heute Spiele sind
        if now_ts - last_day_check > 300:
            has_matches_today()
            last_day_check = now_ts

        # Live Matches abrufen und prüfen
        polling_active = True
        matches = fetch_live_matches()

        if matches:
            relevant = [m for m in matches if m.get("competition", {}).get("code") in LEAGUES]
            if relevant:
                print(f"[{now_str()}] {len(relevant)} Spiele live:", end=" ")
                for m in relevant:
                    h = m['homeTeam']['name'][:10]
                    a = m['awayTeam']['name'][:10]
                    hs = m['score']['fullTime']['home']
                    as_ = m['score']['fullTime']['away']
                    min_ = m.get('minute', '?')
                    print(f"{h} {hs}:{as_} {a} ({min_}')", end=" | ")
                print()

                for match in relevant:
                    check_match(match)

        time.sleep(10)  # 10 Sekunden warten

if __name__ == "__main__":
    main()
