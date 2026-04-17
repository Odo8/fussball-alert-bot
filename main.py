import requests
import time
import json
import os
from datetime import datetime
import pytz

# ═══════════════════════════════════════
# KONFIGURATION
# ═══════════════════════════════════════
FD_TOKEN   = os.environ.get("FD_TOKEN", "ce2aaecf111c442e885f33c5495635d2")
TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "1599474063")

FD_BASE = "https://api.football-data.org/v4"
TG_BASE = f"https://api.telegram.org/bot{TG_TOKEN}"
HEADERS = {"X-Auth-Token": FD_TOKEN}
ZURICH  = pytz.timezone("Europe/Zurich")

# TIMING
MAX_MINUTE_ERSTE_WAHL = 80   # Alerts bis Minute 80
GRENZE_WETT_WECHSEL   = 60   # Ab dieser Minute: Over 0.5 statt Over 1.5

# ═══════════════════════════════════════
# LIGEN
# ═══════════════════════════════════════
LEAGUES = {
    "PL":  {"name": "Premier League", "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 83},
    "BL1": {"name": "Bundesliga",     "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 75},
    "PD":  {"name": "La Liga",        "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 91},
    "SA":  {"name": "Serie A",        "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 87},
    "FL1": {"name": "Ligue 1",        "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 80},
    "DED": {"name": "Eredivisie",     "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 83},
    "ELC": {"name": "Championship",   "tier": 2, "tier_str": "⭐⭐ Tier 2",   "hit_rate": 55},
    "PPL": {"name": "Primeira Liga",  "tier": 2, "tier_str": "⭐⭐ Tier 2",   "hit_rate": 68},
}

# ═══════════════════════════════════════
# STATE
# ═══════════════════════════════════════
standings_cache  = {}   # {league_code: {team_id: rank}}
alerted_matches  = set()
prev_scores      = {}   # {match_id: {"home": x, "away": y}}
match_start_time = {}   # {match_id: datetime} — fuer Minuten-Fallback

# ═══════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════
def send_telegram(text):
    if not TG_TOKEN:
        print(f"[TELEGRAM N/A] {text[:80]}")
        return
    try:
        r = requests.post(
            f"{TG_BASE}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        if r.status_code == 200:
            print(f"✅ Alert gesendet")
        else:
            print(f"❌ Telegram {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"❌ Telegram Exception: {e}")

# ═══════════════════════════════════════
# MINUTE ERMITTELN
# Primaer:  match["minute"] aus der API
# Fallback: Berechnung aus Spielstart-Zeit
# ═══════════════════════════════════════
def get_minute(match):
    match_id = match["id"]

    # Primaer: API-Feld "minute"
    api_minute = match.get("minute")
    if api_minute and api_minute > 0:
        return int(api_minute)

    # Fallback: aus utcDate berechnen
    utc_date_str = match.get("utcDate")
    if utc_date_str:
        try:
            utc = pytz.utc
            start = datetime.strptime(utc_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utc)
            now   = datetime.now(utc)
            diff  = (now - start).total_seconds() / 60
            # Halbzeit-Puffer (15 Min) ab Minute 45
            if diff > 45:
                diff = diff - 15
            minute = max(1, min(90, int(diff)))
            return minute
        except Exception:
            pass

    # Letzter Fallback: 1 (besser als 0)
    return 1

# ═══════════════════════════════════════
# SIGNALSTAERKE
# ═══════════════════════════════════════
def get_signal(diff, minute, tier):
    if diff >= 10 and minute <= 25 and tier == 1:
        return "⭐⭐⭐ STARK"
    elif diff >= 5 or minute <= 45 or tier == 2:
        return "⭐⭐ MITTEL"
    else:
        return "⭐ SCHWACH"

# ═══════════════════════════════════════
# KELLER vs KELLER CHECK
# ═══════════════════════════════════════
def is_bottom_vs_bottom(home_rank, away_rank, total_teams):
    bottom_4 = total_teams - 3
    return home_rank >= bottom_4 and away_rank >= bottom_4

# ═══════════════════════════════════════
# ALERT SENDEN
# ═══════════════════════════════════════
def send_alert(match, scorer_team, home_rank, away_rank, minute, league_code):
    league     = LEAGUES[league_code]
    home_name  = match["homeTeam"]["name"]
    away_name  = match["awayTeam"]["name"]
    home_score = match["score"]["fullTime"]["home"]
    away_score = match["score"]["fullTime"]["away"]
    diff       = abs(home_rank - away_rank)
    signal     = get_signal(diff, minute, league["tier"])

    # Ab Minute 61: Over 0.5 (noch 1 weiteres Tor noetig)
    # Bis Minute 60: Over 1.5 (noch 2 weitere Tore noetig)
    if minute > GRENZE_WETT_WECHSEL:
        wett_empfehlung = "LIVE Over 0.5 weitere Tore"
        deadline_left   = 90 - minute
        deadline_text   = f"⏰ {minute}' — noch {deadline_left}' bis Spielende"
        wett_hinweis    = "⚠️ Spaetes Tor — nur Over 0.5 empfohlen"
    else:
        wett_empfehlung = "LIVE Over 1.5 Tore ab jetzt"
        deadline_left   = 80 - minute
        deadline_text   = f"⏰ {minute}' — noch {deadline_left}' bis Deadline (Min 80)"
        wett_hinweis    = ""

    if scorer_team == "away":
        head  = "🟢 <b>ERSTE WAHL — LIVE ALERT</b>"
        trig  = f"🎯 Trigger:   {away_name} erzielte 1. Tor"
        rang  = f"📈 Rang-Diff: {diff} Plätze (Away schlechter)"
        extra = ""
    else:
        head  = "🟡 <b>ZWEITE WAHL — LIVE ALERT</b>"
        trig  = f"🎯 Trigger:   {home_name} erzielte 1. Tor"
        rang  = f"📈 Rang-Diff: {diff} Plätze (Heim schlechter)"
        extra = "⚡ Heimteam ist schlechter platziert\n"

    text = (
        f"{head}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Liga:      {league['name']}  {league['tier_str']}\n"
        f"⚽ Spiel:     {home_name} {home_score}–{away_score} {away_name}\n"
        f"⏱ Minute:    {minute}'\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{trig}\n"
        f"{extra}"
        f"📊 Tabelle:   Heim Platz {home_rank}  |  Away Platz {away_rank}\n"
        f"{rang}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Wette:     {wett_empfehlung}\n"
        f"🔥 Signal:    {signal}\n"
        f"📉 Liga HR:   {league['hit_rate']}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
    )
    if wett_hinweis:
        text += f"{wett_hinweis}\n"
    text += deadline_text

    print(f"\n{'='*55}")
    print(text)
    print(f"{'='*55}\n")
    send_telegram(text)

# ═══════════════════════════════════════
# STANDINGS LADEN
# ═══════════════════════════════════════
def load_standings(league_code):
    try:
        r = requests.get(
            f"{FD_BASE}/competitions/{league_code}/standings",
            headers=HEADERS, timeout=15
        )
        if r.status_code == 200:
            data  = r.json()
            ranks = {}
            for standing in data.get("standings", []):
                if standing.get("type") == "TOTAL":
                    for entry in standing.get("table", []):
                        ranks[entry["team"]["id"]] = entry["position"]
            standings_cache[league_code] = ranks
            print(f"  ✅ {LEAGUES[league_code]['name']}: {len(ranks)} Teams")
            return len(ranks)
        elif r.status_code == 429:
            print(f"  ⚠️ {league_code}: Rate-limit — kurz warten")
            time.sleep(12)
        else:
            print(f"  ❌ {league_code}: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ❌ {league_code}: {e}")
    return 0

def load_all_standings():
    print(f"\n[{now_str()}] Lade Tabellenränge...")
    for code in LEAGUES:
        load_standings(code)
        time.sleep(7)   # Rate-limit: max 10 req/min

# ═══════════════════════════════════════
# KERN-LOGIK: MATCH PRUEFEN
# ═══════════════════════════════════════
def check_match(match):
    match_id = match["id"]

    if match.get("status") not in ["IN_PLAY", "PAUSED"]:
        return

    league_code = match.get("competition", {}).get("code", "")
    if league_code not in LEAGUES:
        return

    if match_id in alerted_matches:
        return

    # Minute ermitteln (mit Fallback)
    minute = get_minute(match)

    # Deadline: bis Minute 80
    if minute > MAX_MINUTE_ERSTE_WAHL:
        alerted_matches.add(match_id)
        return

    # Score
    score      = match.get("score", {})
    home_score = score.get("fullTime", {}).get("home") or 0
    away_score = score.get("fullTime", {}).get("away") or 0

    # Vorheriger Stand
    prev      = prev_scores.get(match_id, {"home": 0, "away": 0})
    prev_home = prev["home"]
    prev_away = prev["away"]
    prev_scores[match_id] = {"home": home_score, "away": away_score}

    # Kein Tor
    if home_score == prev_home and away_score == prev_away:
        return

    # Nur das ERSTE Tor (Stand muss vorher 0:0 gewesen sein)
    if prev_home != 0 or prev_away != 0:
        alerted_matches.add(match_id)
        return

    # Wer hat getroffen?
    if   home_score == 1 and away_score == 0:
        scorer_team = "home"
    elif away_score == 1 and home_score == 0:
        scorer_team = "away"
    else:
        return

    # Tabellenraenge
    standings = standings_cache.get(league_code, {})
    if not standings:
        print(f"  [SKIP] Keine Standings fuer {league_code}")
        return

    home_id   = match.get("homeTeam", {}).get("id")
    away_id   = match.get("awayTeam", {}).get("id")
    home_rank = standings.get(home_id, 99)
    away_rank = standings.get(away_id, 99)
    total     = len(standings)
    diff      = abs(home_rank - away_rank)

    # Sperr-Regeln
    if diff < 3:
        print(f"  [SKIP] Rang-Diff zu klein ({diff})")
        alerted_matches.add(match_id)
        return
    if is_bottom_vs_bottom(home_rank, away_rank, total):
        print(f"  [SKIP] Keller vs Keller ({home_rank} vs {away_rank})")
        alerted_matches.add(match_id)
        return

    # Trigger pruefen
    heim  = match['homeTeam']['name']
    away  = match['awayTeam']['name']
    if scorer_team == "away" and away_rank > home_rank:
        print(f"  🟢 ERSTE WAHL: {heim} vs {away} — Min {minute}' Diff {diff}")
        send_alert(match, "away", home_rank, away_rank, minute, league_code)
        alerted_matches.add(match_id)
    elif scorer_team == "home" and home_rank > away_rank:
        print(f"  🟡 ZWEITE WAHL: {heim} vs {away} — Min {minute}' Diff {diff}")
        send_alert(match, "home", home_rank, away_rank, minute, league_code)
        alerted_matches.add(match_id)
    else:
        print(f"  [SKIP] Tor vom besseren Team ({heim} vs {away})")
        alerted_matches.add(match_id)

# ═══════════════════════════════════════
# LIVE MATCHES ABRUFEN
# ═══════════════════════════════════════
def fetch_live_matches():
    try:
        r = requests.get(
            f"{FD_BASE}/matches?status=IN_PLAY",
            headers=HEADERS, timeout=15
        )
        if r.status_code == 200:
            return r.json().get("matches", [])
        else:
            print(f"  ❌ Live Matches: HTTP {r.status_code}")
            return []
    except Exception as e:
        print(f"  ❌ Live Matches: {e}")
        return []

# ═══════════════════════════════════════
# HILFSFUNKTIONEN
# ═══════════════════════════════════════
def now_str():
    return datetime.now(ZURICH).strftime("%H:%M:%S")

def should_poll():
    h = datetime.now(ZURICH).hour
    return 11 <= h <= 23

def send_startup():
    text = (
        "🚀 <b>Fussball Alert Bot gestartet!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ 8 Ligen aktiv:\n"
        "  Premier League, Bundesliga, La Liga\n"
        "  Serie A, Ligue 1, Eredivisie\n"
        "  Championship, Primeira Liga\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ Polling: alle 10 Sekunden\n"
        f"🎯 Alerts: Erstes Tor bis Minute {MAX_MINUTE_ERSTE_WAHL}\n"
        f"💰 Wette: Over 1.5 bis Min {GRENZE_WETT_WECHSEL} | Over 0.5 bis Min {MAX_MINUTE_ERSTE_WAHL}"
    )
    send_telegram(text)

# ═══════════════════════════════════════
# HAUPTSCHLEIFE
# ═══════════════════════════════════════
def main():
    print(f"\n{'='*55}")
    print(f"  FUSSBALL ALERT BOT — v2.0")
    print(f"  {now_str()}  |  Max Minute: {MAX_MINUTE_ERSTE_WAHL}")
    print(f"  Over 1.5 bis Min {GRENZE_WETT_WECHSEL} | Over 0.5 bis Min {MAX_MINUTE_ERSTE_WAHL}")
    print(f"{'='*55}\n")

    send_startup()
    load_all_standings()

    last_standings_refresh = time.time()
    polling_active         = False

    print(f"\n[{now_str()}] Hauptschleife startet...\n")

    while True:
        now_ts = time.time()

        # Standings alle 12 Stunden neu laden
        if now_ts - last_standings_refresh > 43200:
            load_all_standings()
            last_standings_refresh = now_ts

        if not should_poll():
            if polling_active:
                print(f"[{now_str()}] Ausserhalb Spielzeit — Agent schlaeft")
                polling_active = False
            time.sleep(60)
            continue

        polling_active = True
        matches        = fetch_live_matches()

        if matches:
            relevant = [m for m in matches if m.get("competition", {}).get("code") in LEAGUES]
            if relevant:
                print(f"[{now_str()}] {len(relevant)} Spiele live:", end=" ")
                for m in relevant:
                    h   = m['homeTeam']['name'][:12]
                    a   = m['awayTeam']['name'][:12]
                    hs  = m['score']['fullTime']['home']
                    as_ = m['score']['fullTime']['away']
                    min_ = get_minute(m)
                    print(f"{h} {hs}:{as_} {a} ({min_}')", end=" | ")
                print()
                for match in relevant:
                    check_match(match)

        time.sleep(10)

if __name__ == "__main__":
    main()
